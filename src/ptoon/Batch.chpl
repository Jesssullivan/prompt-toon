/* TIN-2709 C2b: `ptoon redact-batch` — the coforall fan-in entrypoint.
 *
 * THE USER FLOW THIS SERVES: a wide research spool returns N subagent outputs
 * at once (typically 5-16); every one must pass through fail-closed redaction
 * before the mythos/fable synthesis seat sees any. This is the shape Chapel is
 * uniquely good at — one process, one runtime, N documents fanned out across
 * qthreads tasks with `coforall`. All concurrency is confined INSIDE the Chapel
 * runtime (the purple-team's decisive constraint): the host never spawns N
 * threads that re-enter the runtime; it hands one framed batch to one process.
 *
 * WIRE FORMAT (length-prefixed, not JSON-string-escaped — a redacted PEM block
 * spans newlines, so raw line-delimiting cannot frame it; length prefixes carry
 * arbitrary bytes with zero escaping). Read with a single `stdin.readAll` and a
 * hand-walked byte cursor, so there is no dependency on incremental binary-IO:
 *
 *   stdin:  <n>\n  then for each doc i:  <byteLen_i>\n <raw bytes_i>
 *   stdout: <n>\n  then for each doc i, IN INPUT ORDER:
 *             <metaJson_i>\n           # one line, no embedded newlines
 *             <redactedByteLen_i>\n <redacted bytes_i>
 *
 *   metaJson = {"i":I,"withheld":BOOL,"findings":[NAMES]}  (+ "reason":STR when
 *   withheld). A WITHHELD doc emits redactedByteLen 0 and NO bytes — fail-closed
 *   (INV-5): a doc whose redaction throws is suppressed, its raw text is NEVER
 *   emitted. Output order is input order regardless of task completion order
 *   (results land in a pre-sized array indexed by position).
 *
 * DELIBERATELY NOT HERE (purple-team F1, declined): no parallel pattern-sweep;
 * each task runs the FULL SEQUENTIAL redactText over its own document. Cross-
 * document parallelism only. The wall-clock budget + straggler handling is C2c
 * (rides with `--stream`); this entrypoint's fail-closed surface is the
 * per-document redaction-error withhold.
 */
module Batch {
  use IO, List, CTypes;
  use Redact;

  record BatchResult {
    var withheld: bool;
    var reason: string;
    var findings: list(string);
    var redacted: string;
  }

  /* Byte view of a bytes value (Redact.toArr idiom): default iteration over a
   * `bytes` yields its uint(8) values in order. */
  private proc toArr(const ref b: bytes): [0..<b.size] uint(8) {
    var arr: [0..<b.size] uint(8);
    var idx = 0;
    for v in b { arr[idx] = v; idx += 1; }
    return arr;
  }

  /* Read an ASCII decimal integer starting at arr[pos], consuming through the
   * terminating '\n'. Advances pos past the newline. Fail-closed: a malformed
   * length header throws rather than guessing. */
  private proc readIntLine(const ref arr: [] uint(8), ref pos: int): int throws {
    const n = arr.size;
    var value = 0;
    var sawDigit = false;
    while pos < n && arr[pos] != 0x0A {
      const c = arr[pos];
      if c < 0x30 || c > 0x39 then
        throw new Error("redact-batch: non-digit in length header at byte " + pos: string);
      value = value * 10 + (c - 0x30): int;
      sawDigit = true;
      pos += 1;
    }
    if !sawDigit then throw new Error("redact-batch: empty length header at byte " + pos: string);
    if pos >= n then throw new Error("redact-batch: length header missing newline");
    pos += 1;  // consume '\n'
    return value;
  }

  /* Parse the framed batch into N document strings (decoded UTF-8). */
  proc parseBatch(const ref raw: bytes): [] string throws {
    var arr = toArr(raw);   // var, not const: c_ptrTo below needs a ref actual
    const total = arr.size;
    var pos = 0;
    const n = readIntLine(arr, pos);
    if n < 0 then throw new Error("redact-batch: negative document count");
    var docs: [0..<n] string;
    for i in 0..<n {
      const len = readIntLine(arr, pos);
      if pos + len > total then
        throw new Error("redact-batch: document " + i: string + " length " +
                        len: string + " overruns input");
      if len == 0 {
        docs[i] = "";
      } else {
        docs[i] = bytes.createCopyingBuffer(c_ptrTo(arr[pos]): c_ptrConst(c_char), len).decode();
      }
      pos += len;
    }
    return docs;
  }

  private proc findingsJson(const ref findings: list(string)): string {
    var acc = "[";
    var first = true;
    for f in findings {
      if !first then acc += ",";
      acc += '"' + f + '"';   // pattern names are [a-z0-9-], no JSON escaping needed
      first = false;
    }
    acc += "]";
    return acc;
  }

  /* Redact N documents concurrently and RETURN the framed result. One qthreads
   * task per document; each runs the full sequential redactText using the
   * module-level shared const RE2 generators (Redact.gens). A task whose
   * redaction throws sets withheld=true and emits no text (fail-closed).
   *
   * Returns a `string`: the whole framed output is valid UTF-8 (ASCII meta +
   * length headers, plus each redacted document which is itself a valid UTF-8
   * string), so the caller writes it with one `stdout.write`. The byte-length
   * header uses `.encode().size` so it counts UTF-8 bytes, not codepoints. */
  proc redactBatch(const ref docs: [] string): string throws {
    const n = docs.size;
    var results: [0..<n] BatchResult;

    coforall i in 0..<n {
      try {
        const (red, finds) = redactText(docs[i]);
        results[i] = new BatchResult(false, "", finds, red);
      } catch e {
        // Fail-closed: never emit the raw document on a redaction error.
        results[i] = new BatchResult(true, "redaction-error", new list(string), "");
      }
    }

    var acc = n: string + "\n";
    for i in 0..<n {
      const ref r = results[i];
      var meta = '{"i":' + i: string + ',"withheld":' +
                 (if r.withheld then "true" else "false") +
                 ',"findings":' + findingsJson(r.findings);
      if r.withheld then meta += ',"reason":"' + r.reason + '"';
      meta += "}";
      acc += meta + "\n";
      if r.withheld {
        acc += "0\n";            // fail-closed: zero-length, no raw bytes
      } else {
        acc += r.redacted.encode().size: string + "\n";
        acc += r.redacted;
      }
    }
    return acc;
  }
}
