/* TIN-2792 C4a: resident transform service for the local model gateway.
 *
 * Provider HTTP, credentials, and streaming stay in the gateway process. This
 * module keeps one Chapel runtime alive and accepts bounded, length-prefixed
 * condense requests over private stdin/stdout pipes.
 *
 * Request frame:
 *   <requestIdLen>\n<requestId bytes>
 *   <streamIdLen>\n<streamId bytes>
 *   <maxInputBytes>\n<budgetMs>\n<maxCards>\n
 *   <payloadLen>\n<existing ptoon-condense payload bytes>
 *
 * Response frame:
 *   <requestIdLen>\n<requestId bytes>
 *   <streamIdLen>\n<streamId bytes>
 *   <statusLen>\n<ok|error>
 *   <bodyLen>\n<condense JSONL or error text>
 *
 * EOF is the graceful shutdown signal. A fixed worker set drains a bounded
 * sync-slot ring; when the ring is full the producer blocks on writeEF, giving
 * the gateway real pipe backpressure without unbounded task creation.
 */
module Serve {
  use IO, List;
  use Stream;

  record ServeRequest {
    var shutdown = false;
    var requestId: string;
    var streamId: string;
    var maxInputBytes = 0;
    var budgetMs = 0;
    var maxCards = 24;
    var payload: bytes;
    var validationError: string;
  }

  private proc readDecimalLine(what: string, maxValue: int,
                               allowEof = false): (bool, int) throws {
    var line: bytes;
    const present = stdin.readLine(line, maxSize=32, stripNewline=true);
    if !present {
      if allowEof then return (false, 0);
      throw new Error("ptoon serve: unexpected EOF reading " + what);
    }
    if line.size == 0 then
      throw new Error("ptoon serve: empty " + what);

    var value = 0;
    var digits = 0;
    for byte in line {
      if byte < 0x30 || byte > 0x39 then
        throw new Error("ptoon serve: non-digit in " + what);
      digits += 1;
      if digits > 20 then
        throw new Error("ptoon serve: " + what + " is too long");
      const digit = (byte - 0x30): int;
      if value > (maxValue - digit) / 10 then
        throw new Error("ptoon serve: " + what + " exceeds limit");
      value = value * 10 + digit;
    }
    return (true, value);
  }

  private proc readExact(length: int, what: string): bytes throws {
    if length == 0 then return b"";
    var result: bytes;
    while result.size < length {
      var chunk: bytes;
      const present = stdin.readBinary(chunk, length - result.size);
      if !present || chunk.size == 0 then
        throw new Error("ptoon serve: truncated " + what);
      result += chunk;
    }
    return result;
  }

  private proc decodeId(const ref raw: bytes, what: string): string throws {
    if raw.size == 0 then throw new Error("ptoon serve: empty " + what);
    const value = raw.decode();
    for cp in value.codepoints() {
      const cpValue = cp: int(32);
      if cpValue < 0x21 || cpValue > 0x7E then
        throw new Error("ptoon serve: " + what + " must use visible ASCII");
    }
    return value;
  }

  private proc readRequest(maxRequestBytes: int, maxLabelBytes: int,
                           maxInputBytesLimit: int, maxBudgetMs: int,
                           maxCardsLimit: int): (bool, ServeRequest) throws {
    const (present, requestIdLen) =
      readDecimalLine("request id length", maxLabelBytes, allowEof=true);
    if !present then return (false, new ServeRequest());
    const requestId = decodeId(readExact(requestIdLen, "request id"), "request id");

    const (_, streamIdLen) = readDecimalLine("stream id length", maxLabelBytes);
    const streamId = decodeId(readExact(streamIdLen, "stream id"), "stream id");
    const (_, maxInputBytes) = readDecimalLine("maxInputBytes", 2_147_483_647);
    const (_, budgetMs) = readDecimalLine("budgetMs", 2_147_483_647);
    const (_, maxCards) = readDecimalLine("maxCards", 2_147_483_647);
    const (_, payloadLen) = readDecimalLine("payload length", maxRequestBytes);
    const payload = readExact(payloadLen, "payload");

    var validationError: string;
    if maxInputBytes <= 0 then
      validationError = "ptoon serve: maxInputBytes must be positive";
    else if maxInputBytes > maxInputBytesLimit then
      validationError = "ptoon serve: maxInputBytes exceeds service limit";
    else if budgetMs <= 0 then
      validationError = "ptoon serve: budgetMs must be positive";
    else if budgetMs > maxBudgetMs then
      validationError = "ptoon serve: budgetMs exceeds service limit";
    else if maxCards <= 0 then
      validationError = "ptoon serve: maxCards must be positive";
    else if maxCards > maxCardsLimit then
      validationError = "ptoon serve: maxCards exceeds service limit";

    return (true, new ServeRequest(false, requestId, streamId,
                                   maxInputBytes, budgetMs, maxCards, payload,
                                   validationError));
  }

  private proc responseFrame(const ref requestId: string,
                             const ref streamId: string,
                             const ref status: string,
                             const ref body: string): string {
    return requestId.encode().size: string + "\n" + requestId +
           streamId.encode().size: string + "\n" + streamId +
           status.encode().size: string + "\n" + status +
           body.encode().size: string + "\n" + body;
  }

  proc serveLoop(workers: int, queueDepth: int, maxDocs: int,
                 maxRequestBytes: int, maxLabelBytes: int,
                 maxInputBytes: int, maxBudgetMs: int,
                 maxCards: int, maxResponseBytes: int): int {
    var slots: [0..<queueDepth] sync ServeRequest;
    var nextWork: atomic int;
    var producerFailed: atomic bool;
    var outputFailed: atomic bool;

    cobegin {
      {
        var sequence = 0;
        try {
          while true {
            const (present, request) = readRequest(
              maxRequestBytes, maxLabelBytes, maxInputBytes, maxBudgetMs,
              maxCards
            );
            if !present then break;
            slots[sequence % queueDepth].writeEF(request);
            sequence += 1;
          }
        } catch e {
          producerFailed.write(true);
          try {
            stderr.writeln(e.message());
          } catch {
            // The sentinels below must still be enqueued if stderr is closed.
          }
        }

        // One sentinel per worker, enqueued after every accepted request.
        for workerId in 0..<workers {
          var stop = new ServeRequest();
          stop.shutdown = true;
          slots[sequence % queueDepth].writeEF(stop);
          sequence += 1;
        }
      }
      {
        coforall workerId in 0..<workers {
          while true {
            const sequence = nextWork.fetchAdd(1);
            const request = slots[sequence % queueDepth].readFE();
            if request.shutdown then break;
            if outputFailed.read() then continue;

            var status = "ok";
            var body: string;
            if request.validationError != "" {
              status = "error";
              body = request.validationError;
            } else {
              try {
                var header: CondenseRunHeader;
                var docs: list(StreamDoc);
                parseCondenseRun(request.payload, header, docs, maxDocs,
                                 maxRequestBytes, maxLabelBytes);
                if header.runId != request.requestId then
                  throw new Error("ptoon serve: request id does not match condense run id");
                body = condenseRunBlock(docs, header, request.maxInputBytes,
                                        request.budgetMs, request.maxCards);
                if body.encode().size > maxResponseBytes then
                  throw new Error("ptoon serve: response exceeds service limit");
              } catch e {
                status = "error";
                body = e.message();
              }
            }

            // stdout is locking; one write keeps a response frame indivisible.
            try {
              stdout.write(responseFrame(request.requestId, request.streamId,
                                         status, body));
              stdout.flush();
            } catch {
              outputFailed.write(true);
              producerFailed.write(true);
            }
          }
        }
      }
    }

    return if producerFailed.read() then 2 else 0;
  }
}
