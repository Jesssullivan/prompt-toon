/* TIN-2709 C2d: `ptoon condense-batch` — the --stream condensation surface.
 *
 * THE USER FLOW: a wide research spool returns N subagent outputs at once;
 * every one must pass fail-closed redaction AND become provenance-bearing
 * source cards before the mythos/fable synthesis seat sees any. This is the
 * long-lived-process shape (MCP gateway / Claude Code PostToolBatch): one
 * process, one framed batch in, one JSONL event stream out.
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
  use IO, List, CTypes, Time;
  use Batch, Redact, Cards, Sha256;

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
  proc parseStreamBatch(const ref raw: bytes): [] StreamDoc throws {
    var arr = toArr(raw);   // var, not const: c_ptrTo below needs a ref actual
    const total = arr.size;
    var pos = 0;
    const n = readIntLine(arr, pos, total);
    if n < 0 then throw new Error("condense-batch: negative document count");
    // Minimum frame per doc is three "0\n" fields (source, tier, body).
    // Bound allocation by the maximum number of triplets the remaining frame
    // could possibly encode, not by raw bytes.
    if n > (total - pos) / 6 then
      throw new Error("condense-batch: document count exceeds possible frame size before allocation");
    var docs: [0..<n] StreamDoc;

    proc readField(ref pos: int, what: string, docIdx: int): bytes throws {
      const len = readIntLine(arr, pos, total - pos);
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
      const src = readField(pos, "source", i);
      const tier = readField(pos, "trust_tier", i);
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
          const ref d = docs[i];
          const digest = sha256Hex(d.body);
          const noFindings = new list(string);
          var acc: string;
          if maxInputBytes > 0 && d.body.size > maxInputBytes {
            // Fail-closed input cap: withhold before decode/redaction.
            acc = docEventJson(i, d, digest, true, "input-cap", noFindings) +
                  endEventJson(i, 0);
            withheldCount.add(1);
          } else {
            try {
              const text = d.body.decode();
              const (red, finds) = redactText(text);
              const docCards = cardsFromText(d.source, red, finds.size > 0,
                                             digest, d.tier, maxCards);
              const elapsedMs = sw.elapsed() * 1000.0;
              if budgetMs > 0 && elapsedMs > (budgetMs: real) {
                // Fail-closed budget: discard cards + redaction, withhold.
                acc = docEventJson(i, d, digest, true, "budget", noFindings) +
                      endEventJson(i, 0);
                withheldCount.add(1);
              } else {
                acc = docEventJson(i, d, digest, false, "", finds);
                for c in docCards do
                  acc += '{"event":"card","i":' + i: string +
                         ',"card":' + cardJson(c) + "}\n";
                acc += endEventJson(i, docCards.size);
                totalCards.add(docCards.size);
              }
            } catch e {
              // Fail-closed: bad UTF-8 / redaction / card failure => nothing
              // derived from the body is emitted.
              acc = docEventJson(i, d, digest, true, "condense-error", noFindings) +
                    endEventJson(i, 0);
              withheldCount.add(1);
            }
          }
          slots[i].writeEF(acc);
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
}
