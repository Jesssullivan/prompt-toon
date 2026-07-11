/* TIN-2709 C2d: `ptoon condense-batch` — the --stream condensation surface.
 *
 * THE USER FLOW: a wide research spool returns N subagent outputs at once;
 * every one must pass fail-closed redaction AND become provenance-bearing
 * source cards before the mythos/fable synthesis seat sees any. This is the
 * one-shot fan-in shape: one process, one framed batch in, one JSONL event
 * stream out. C4a's resident `serve` command reuses the same document/run
 * transform functions across many requests; provider transport stays outside
 * Chapel.
 *
 * INPUT (length-prefixed TRIPLET framing — same philosophy as redact-batch:
 * length prefixes carry arbitrary bytes with zero escaping, and no JSON
 * parsing exists in the binary):
 *
 *   stdin:  <n>\n  then for each doc i:
 *             <sourceLen>\n <source bytes>     # provenance label
 *             <tierLen>\n <tier bytes>         # trust tier for every card
 *             <bodyLen>\n <raw body bytes>
 *
 * OUTPUT (JSONL, one event object per line, docs in INPUT ORDER):
 *
 *   {"event":"doc","i":I,"source":S,"trust_tier":T,"bytes":B,"sha256":H,
 *    "withheld":BOOL,"findings":[...]}          (+ "reason" when withheld)
 *   {"event":"card","i":I,"card":{...}}         (card = source-cards.jsonl
 *                                                object, Python-byte-exact)
 *   {"event":"end","i":I,"cards":K}             (every doc, withheld => 0)
 *   {"event":"batch","docs":N,"cards":TOTAL,"withheld":W}
 *
 * Stream records deliberately carry NO timestamps and NO run ids: output is
 * a pure function of (input, policy args), which is what makes the parity
 * gate a byte-diff and the cache key (INV-6, later slice) well-defined.
 *
 * --stream HONESTY (design record Sec8): this is not a pipeline through
 * redaction — each document is fully buffered (bounded by maxInputBytes) and
 * redacted whole, because a PEM block cannot be line-windowed. The streaming
 * is on the OUTPUT side: doc i's events are written as soon as doc i
 * completes AND all docs before it have been written (in-order incremental
 * emission via sync slots; the writer task drains while later docs still
 * compute). sha256 is over the RAW body bytes, computed before any policy
 * decision, so even a withheld doc is identifiable by digest.
 *
 * FAIL-CLOSED POLICY (reuses the C2c cap/budget semantics verbatim):
 *   - maxInputBytes > 0: an over-cap doc is withheld ("input-cap") BEFORE
 *     decode/redaction; never truncated-and-emitted.
 *   - budgetMs > 0: completion-time withhold ("budget") — a doc finishing
 *     past the batch deadline discards its cards and redaction output.
 *   - any per-doc failure (bad UTF-8, redaction/card error) withholds with
 *     reason "condense-error"; raw text and cards are NEVER emitted.
 * A withheld doc emits its doc event (withheld:true, findings:[]) and an end
 * event with cards:0 — no card events, no claim text, nothing derived from
 * the document body.
 */
module Stream {
  use IO, List, Set, CTypes, Time;
  use Batch, Redact, Cards, Sha256, Summary;

  record StreamDoc {
    var source: string;
    var tier: string;
    var body: bytes;
  }

  /* Parse the triplet-framed batch. Same guard style as Batch.parseBatch:
   * count/length headers are bounded by remaining input, field reads cannot
   * overrun, trailing bytes reject. source/tier decode strictly here (they
   * are labels; a malformed label is a malformed frame => process abort);
   * the BODY stays bytes — it decodes inside the per-doc task so a bad body
   * withholds that doc instead of killing the batch. */
  proc parseStreamBatch(const ref raw: bytes, maxDocs: int = 0,
                        maxTotalBytes: int = 0,
                        maxLabelBytes: int = 0): [] StreamDoc throws {
    if maxDocs < 0 || maxTotalBytes < 0 || maxLabelBytes < 0 then
      throw new Error("condense-batch: parser limits must be nonnegative");
    const total = raw.size;
    if maxTotalBytes > 0 && total > maxTotalBytes then
      throw new Error("condense-batch: input bytes " + total: string +
                      " exceed maxTotalBytes " + maxTotalBytes: string);

    var arr = toArr(raw);   // var, not const: c_ptrTo below needs a ref actual
    var pos = 0;
    const n = readIntLine(arr, pos, total);
    if n < 0 then throw new Error("condense-batch: negative document count");
    if maxDocs > 0 && n > maxDocs then
      throw new Error("condense-batch: document count " + n: string +
                      " exceeds maxDocs " + maxDocs: string);
    // Minimum frame per doc is three "0\n" fields (source, tier, body).
    // Bound allocation by the maximum number of triplets the remaining frame
    // could possibly encode, not by raw bytes.
    if n > (total - pos) / 6 then
      throw new Error("condense-batch: document count exceeds possible frame size before allocation");
    var docs: [0..<n] StreamDoc;

    proc readField(ref pos: int, what: string, docIdx: int,
                   maxFieldBytes: int = 0): bytes throws {
      const len = readIntLine(arr, pos, total - pos);
      if maxFieldBytes > 0 && len > maxFieldBytes then
        throw new Error("condense-batch: doc " + docIdx: string + " " + what +
                        " length " + len: string + " exceeds maxLabelBytes " +
                        maxFieldBytes: string);
      if pos + len > total then
        throw new Error("condense-batch: doc " + docIdx: string + " " + what +
                        " length " + len: string + " overruns input");
      var field: bytes;
      if len > 0 {
        field = bytes.createCopyingBuffer(
          c_ptrTo(arr[pos]): c_ptrConst(c_char), len);
      }
      pos += len;
      return field;
    }

    for i in 0..<n {
      const src = readField(pos, "source", i, maxLabelBytes);
      const tier = readField(pos, "trust_tier", i, maxLabelBytes);
      const body = readField(pos, "body", i);
      docs[i] = new StreamDoc(src.decode(), tier.decode(), body);
    }
    if pos != total then
      throw new Error("condense-batch: trailing bytes after document " + n: string);
    return docs;
  }

  private proc findingsJson(const ref findings: list(string)): string {
    var acc = "[";
    var first = true;
    for f in findings {
      if !first then acc += ",";
      acc += '"' + f + '"';   // pattern names are [a-z0-9-], no escaping needed
      first = false;
    }
    acc += "]";
    return acc;
  }

  private proc docEventJson(i: int, const ref d: StreamDoc, digest: string,
                            withheld: bool, reason: string,
                            const ref findings: list(string)): string {
    var acc = '{"event":"doc","i":' + i: string +
              ',"source":"' + escapeJson(d.source) +
              '","trust_tier":"' + escapeJson(d.tier) +
              '","bytes":' + d.body.size: string +
              ',"sha256":"' + digest +
              '","withheld":' + (if withheld then "true" else "false") +
              ',"findings":' + findingsJson(findings);
    if withheld then acc += ',"reason":"' + reason + '"';
    acc += "}\n";
    return acc;
  }

  private proc endEventJson(i: int, nCards: int): string {
    return '{"event":"end","i":' + i: string +
           ',"cards":' + nCards: string + "}\n";
  }

  /* Per-document outcome: the rendered event block plus what run-level
   * aggregation (C2e summary/manifest) needs. `cards` is retained ONLY for
   * non-withheld docs — a withheld doc contributes nothing body-derived
   * (INV-5), just its digest/withheld flag. */
  record DocOutcome {
    var block: string;
    var cards: list(Card);
    var digest: string;
    var withheld: bool;
  }

  /* One document through the full fail-closed policy: cap check before
   * decode/redaction, completion-time budget withhold, catch-all
   * condense-error withhold. Shared verbatim by condense-batch (C2d) and
   * condense (C2e) so the two surfaces cannot drift. `sw` is the shared
   * batch stopwatch (concurrent read-only elapsed() calls, the proven
   * Batch.chpl idiom). */
  private proc processDoc(i: int, const ref d: StreamDoc, maxInputBytes: int,
                          budgetMs: int, maxCards: int,
                          const ref sw: stopwatch): DocOutcome {
    var outc: DocOutcome;
    outc.digest = sha256Hex(d.body);
    const noFindings = new list(string);
    if maxInputBytes > 0 && d.body.size > maxInputBytes {
      // Fail-closed input cap: withhold before decode/redaction.
      outc.block = docEventJson(i, d, outc.digest, true, "input-cap", noFindings) +
                   endEventJson(i, 0);
      outc.withheld = true;
    } else {
      try {
        const text = d.body.decode();
        const (red, finds) = redactText(text);
        const docCards = cardsFromText(d.source, red, finds.size > 0,
                                       outc.digest, d.tier, maxCards);
        const elapsedMs = sw.elapsed() * 1000.0;
        if budgetMs > 0 && elapsedMs > (budgetMs: real) {
          // Fail-closed budget: discard cards + redaction, withhold.
          outc.block = docEventJson(i, d, outc.digest, true, "budget", noFindings) +
                       endEventJson(i, 0);
          outc.withheld = true;
        } else {
          var acc = docEventJson(i, d, outc.digest, false, "", finds);
          for c in docCards do
            acc += '{"event":"card","i":' + i: string +
                   ',"card":' + cardJson(c) + "}\n";
          acc += endEventJson(i, docCards.size);
          outc.block = acc;
          outc.cards = docCards;
        }
      } catch e {
        // Fail-closed: bad UTF-8 / redaction / card failure => nothing
        // derived from the body is emitted.
        outc.block = docEventJson(i, d, outc.digest, true, "condense-error", noFindings) +
                     endEventJson(i, 0);
        outc.withheld = true;
      }
    }
    return outc;
  }

  /* Condense N documents concurrently, emitting JSONL incrementally in input
   * order. One qthreads task per document (cross-document parallelism only —
   * purple-team F1 doctrine; each doc runs the full sequential redactText).
   * The writer task drains slot i as soon as doc i lands, concurrently with
   * later docs still computing: output-side streaming, honestly scoped. */
  proc condenseStream(const ref docs: [] StreamDoc, maxInputBytes: int,
                      budgetMs: int, maxCards: int) throws {
    const n = docs.size;
    var slots: [0..<n] sync string;
    var totalCards: atomic int;
    var withheldCount: atomic int;

    var sw: stopwatch;
    sw.start();

    cobegin {
      {
        coforall i in 0..<n {
          const outc = processDoc(i, docs[i], maxInputBytes, budgetMs,
                                  maxCards, sw);
          if outc.withheld then withheldCount.add(1);
          else totalCards.add(outc.cards.size);
          slots[i].writeEF(outc.block);
        }
      }
      {
        for i in 0..<n do
          stdout.write(slots[i].readFE());
      }
    }

    stdout.write('{"event":"batch","docs":' + n: string +
                 ',"cards":' + totalCards.read(): string +
                 ',"withheld":' + withheldCount.read(): string + "}\n");
  }

  /* C2e run header: five caller-supplied fields the binary echoes but never
   * computes (determinism doctrine). minToonSavings and tierOverridesJson
   * are spliced VERBATIM into the manifest event's JSON, so they are
   * grammar-checked fail-closed here: no control characters (a raw newline
   * would break JSONL framing), minToonSavings must look like a bare JSON
   * number, tierOverridesJson must be brace-delimited. Semantic validity of
   * the overrides object stays with the caller (engine.py builds it with
   * json.dumps; the manifest gate re-parses and would reject corruption). */
  record CondenseRunHeader {
    var runId: string;
    var generatedAt: string;
    var minToonSavings: string;
    var defaultTier: string;
    var tierOverridesJson: string;
  }

  private proc checkNoControlChars(const ref s: string, what: string) throws {
    for cp in s.codepoints() {
      if cp: int(32) < 0x20 then
        throw new Error("condense: control character in " + what);
    }
  }

  private inline proc isDigitCp(cp: int(32)): bool {
    return cp >= 0x30 && cp <= 0x39;
  }

  private inline proc isDigitOneToNineCp(cp: int(32)): bool {
    return cp >= 0x31 && cp <= 0x39;
  }

  private proc checkJsonNumber(const ref s: string) throws {
    if s.size == 0 then throw new Error("condense: empty minToonSavings");

    var cps = new list(int(32));
    for cp0 in s.codepoints() do cps.pushBack(cp0: int(32));
    const n = cps.size;
    var i = 0;

    // RFC 8259 number grammar:
    // number = [ minus ] int [ frac ] [ exp ]
    // int    = zero / ( digit1-9 *DIGIT )
    // frac   = "." 1*DIGIT
    // exp    = ("e" / "E") [ minus / plus ] 1*DIGIT
    if cps[i] == 0x2D {  // -
      i += 1;
      if i >= n then
        throw new Error("condense: minToonSavings is not a JSON number");
    }

    if cps[i] == 0x30 {  // 0
      i += 1;
      if i < n && isDigitCp(cps[i]) then
        throw new Error("condense: minToonSavings is not a JSON number");
    } else if isDigitOneToNineCp(cps[i]) {
      while i < n && isDigitCp(cps[i]) do i += 1;
    } else {
      throw new Error("condense: minToonSavings is not a JSON number");
    }

    if i < n && cps[i] == 0x2E {  // .
      i += 1;
      if i >= n || !isDigitCp(cps[i]) then
        throw new Error("condense: minToonSavings is not a JSON number");
      while i < n && isDigitCp(cps[i]) do i += 1;
    }

    if i < n && (cps[i] == 0x65 || cps[i] == 0x45) {  // e/E
      i += 1;
      if i < n && (cps[i] == 0x2D || cps[i] == 0x2B) then i += 1;
      if i >= n || !isDigitCp(cps[i]) then
        throw new Error("condense: minToonSavings is not a JSON number");
      while i < n && isDigitCp(cps[i]) do i += 1;
    }

    if i != n then
      throw new Error("condense: minToonSavings is not a JSON number");
  }

  proc parseCondenseRun(const ref raw: bytes, ref header: CondenseRunHeader,
                        ref docs: list(StreamDoc), maxDocs: int = 0,
                        maxTotalBytes: int = 0,
                        maxLabelBytes: int = 0) throws {
    if maxDocs < 0 || maxTotalBytes < 0 || maxLabelBytes < 0 then
      throw new Error("condense: parser limits must be nonnegative");
    const total = raw.size;
    if maxTotalBytes > 0 && total > maxTotalBytes then
      throw new Error("condense: input bytes " + total: string +
                      " exceed maxTotalBytes " + maxTotalBytes: string);

    var arr = toArr(raw);   // var, not const: c_ptrTo below needs a ref actual
    var pos = 0;

    proc readField(ref pos: int, what: string,
                   maxFieldBytes: int = 0): bytes throws {
      const len = readIntLine(arr, pos, total - pos);
      if maxFieldBytes > 0 && len > maxFieldBytes then
        throw new Error("condense: " + what + " length " + len: string +
                        " exceeds maxLabelBytes " + maxFieldBytes: string);
      if pos + len > total then
        throw new Error("condense: " + what + " length " + len: string +
                        " overruns input");
      var field: bytes;
      if len > 0 {
        field = bytes.createCopyingBuffer(
          c_ptrTo(arr[pos]): c_ptrConst(c_char), len);
      }
      pos += len;
      return field;
    }

    header.runId = readField(pos, "runId", maxLabelBytes).decode();
    header.generatedAt = readField(pos, "generatedAt", maxLabelBytes).decode();
    header.minToonSavings = readField(pos, "minToonSavings", maxLabelBytes).decode();
    header.defaultTier = readField(pos, "defaultTier", maxLabelBytes).decode();
    header.tierOverridesJson = readField(pos, "tierOverridesJson", maxLabelBytes).decode();
    checkNoControlChars(header.minToonSavings, "minToonSavings");
    checkJsonNumber(header.minToonSavings);
    checkNoControlChars(header.tierOverridesJson, "tierOverridesJson");
    if !(header.tierOverridesJson.startsWith("{") &&
         header.tierOverridesJson.endsWith("}")) then
      throw new Error("condense: tierOverridesJson is not a JSON object");

    const n = readIntLine(arr, pos, total - pos);
    if n < 0 then throw new Error("condense: negative document count");
    if maxDocs > 0 && n > maxDocs then
      throw new Error("condense: document count " + n: string +
                      " exceeds maxDocs " + maxDocs: string);
    // Minimum frame per doc is three "0\n" fields (source, tier, body).
    if n > (total - pos) / 6 then
      throw new Error("condense: document count exceeds possible frame size before allocation");
    for i in 0..<n {
      const src = readField(pos, "doc " + i: string + " source", maxLabelBytes);
      const tier = readField(pos, "doc " + i: string + " trust_tier", maxLabelBytes);
      const body = readField(pos, "doc " + i: string + " body");
      docs.pushBack(new StreamDoc(src.decode(), tier.decode(), body));
    }
    if pos != total then
      throw new Error("condense: trailing bytes after document " + n: string);
  }

  record CondenseRunAggregate {
    var allCards: list(Card);
    var jsonlText: string;
    var inputs: list(ManifestInput);
    var mixed: bool;
    var totalCards: int;
    var withheld: int;
  }

  /* Build the run-level inputs after all per-document outcomes are complete.
   * Both the streaming and atomic response paths render their tail events from
   * this aggregate, preserving card/input order and tally semantics. */
  private proc aggregateCondenseRun(const ref docs: [] StreamDoc,
                                    const ref outcomes: [] DocOutcome): CondenseRunAggregate {
    var aggregate: CondenseRunAggregate;
    var tiersSeen = new set(string);
    for i in 0..<docs.size {
      aggregate.inputs.pushBack(new ManifestInput(docs[i].source, docs[i].tier,
                                                  outcomes[i].digest,
                                                  docs[i].body.size));
      tiersSeen.add(docs[i].tier);
      if outcomes[i].withheld {
        aggregate.withheld += 1;
      } else {
        for c in outcomes[i].cards {
          aggregate.allCards.pushBack(c);
          aggregate.jsonlText += cardJson(c) + "\n";
          aggregate.totalCards += 1;
        }
      }
    }
    aggregate.mixed = tiersSeen.size > 1;
    return aggregate;
  }

  private proc runSummaryEventJson(n: int, const ref header: CondenseRunHeader,
                                   const ref aggregate: CondenseRunAggregate): string throws {
    const summaryText = renderSummary(header.runId, header.generatedAt, n,
                                      aggregate.mixed, aggregate.allCards);
    return '{"event":"summary","text":"' + escapeJson(summaryText) + '"}\n';
  }

  private proc runManifestEventJson(const ref header: CondenseRunHeader,
                                    maxCards: int,
                                    const ref aggregate: CondenseRunAggregate): string {
    const manifest = manifestJson(header.runId, header.generatedAt,
                                  aggregate.inputs, aggregate.mixed, maxCards,
                                  header.minToonSavings, header.defaultTier,
                                  header.tierOverridesJson,
                                  roughTokenCount(aggregate.jsonlText));
    return '{"event":"manifest","manifest":' + manifest + "}\n";
  }

  private proc runBatchEventJson(n: int,
                                 const ref aggregate: CondenseRunAggregate): string {
    return '{"event":"batch","docs":' + n: string +
           ',"cards":' + aggregate.totalCards: string +
           ',"withheld":' + aggregate.withheld: string + "}\n";
  }

  /* C2e: condense-batch plus the run-level artifacts — after every doc's
   * events have streamed (input order, same incremental writer), emit ONE
   * summary event (render_summary byte-parity) and ONE manifest event
   * (value-parity; the gate masks structurally), then the batch tally. */
  proc condenseRun(const ref docs: list(StreamDoc),
                   const ref header: CondenseRunHeader, maxInputBytes: int,
                   budgetMs: int, maxCards: int) throws {
    const n = docs.size;
    var docsArr: [0..<n] StreamDoc;
    for i in 0..<n do docsArr[i] = docs[i];

    var slots: [0..<n] sync string;
    var outcomes: [0..<n] DocOutcome;

    var sw: stopwatch;
    sw.start();

    cobegin {
      {
        coforall i in 0..<n {
          const outc = processDoc(i, docsArr[i], maxInputBytes, budgetMs,
                                  maxCards, sw);
          outcomes[i] = outc;
          slots[i].writeEF(outc.block);
        }
      }
      {
        for i in 0..<n do
          stdout.write(slots[i].readFE());
      }
    }

    // The cobegin join is the barrier, so outcomes[] reads are race-free.
    const aggregate = aggregateCondenseRun(docsArr, outcomes);
    stdout.write(runSummaryEventJson(n, header, aggregate));
    stdout.write(runManifestEventJson(header, maxCards, aggregate));
    stdout.write(runBatchEventJson(n, aggregate));
  }

  /* Resident-service form of condenseRun: compute the same ordered document
   * blocks and run-level events without writing stdout, so the caller can wrap
   * one complete JSONL result in an indivisible response frame. */
  proc condenseRunBlock(const ref docs: list(StreamDoc),
                        const ref header: CondenseRunHeader,
                        maxInputBytes: int, budgetMs: int,
                        maxCards: int): string throws {
    const n = docs.size;
    var docsArr: [0..<n] StreamDoc;
    for i in 0..<n do docsArr[i] = docs[i];

    var outcomes: [0..<n] DocOutcome;
    var sw: stopwatch;
    sw.start();

    coforall i in 0..<n do
      outcomes[i] = processDoc(i, docsArr[i], maxInputBytes, budgetMs,
                               maxCards, sw);

    var acc: string;
    for i in 0..<n do acc += outcomes[i].block;

    const aggregate = aggregateCondenseRun(docsArr, outcomes);
    acc += runSummaryEventJson(n, header, aggregate);
    acc += runManifestEventJson(header, maxCards, aggregate);
    acc += runBatchEventJson(n, aggregate);
    return acc;
  }
}
