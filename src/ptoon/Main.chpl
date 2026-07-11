/* TIN-2708 C1: `ptoon` standalone binary — the process entry point for the
 * Chapel condensation engine.
 *
 * WHY THIS REPLACES Abi.chpl + ctypes (libptoon):
 *   The C1 shared-library path exported C-ABI procs (Abi.chpl) consumed by
 *   prompt_toon/engine.py via ctypes. It hit a Chapel foreign-thread
 *   runtime-reentry wall: init/caps/normalize succeed, but the out-buffer
 *   free crashes on repeated exported-proc calls — the ptoon_free segfault
 *   documented in Abi.chpl's diagnostic no-op. That is the runtime fragility
 *   the C-interop research warned about: driving Chapel's runtime through
 *   repeated FFI re-entry from a foreign (Python) thread is not a supported
 *   long-lived embedding model.
 *
 *   The C0 spike (spikes/tin-2707/ptoon_spike.chpl) proved the opposite shape
 *   runs CLEAN with byte-parity: a standalone `proc main` that reads stdin,
 *   writes stdout, and exits. Chapel owns its runtime start-to-finish; there
 *   is no foreign-thread re-entry and no manual buffer free to mispair with
 *   jemalloc. The real user flow is subprocess/stream shaped anyway: one-shot
 *   hook transforms and the C4 provider gateway's private resident child,
 *   never in-process FFI. So the engine ships as this binary; engine.py invokes
 *   one-shot commands and resident.py owns `ptoon serve`.
 *
 * FIXED BINARY PROTOCOL (argv[1] is the subcommand; ALL of stdin is read as
 * raw bytes):
 *   normalize -> normalized text to stdout, nothing else, exit 0.
 *   defang    -> defanged text to stdout, nothing else, exit 0.
 *   redact    -> EXACTLY one line "findings:<comma-joined names or empty>\n"
 *                then the redacted text verbatim (identical framing to the C0
 *                spike), exit 0.
 *   serve     -> resident v1 framed requests/responses until stdin EOF.
 *   caps      -> engine-caps JSON to stdout, exit 0.
 *   unknown   -> message on stderr, exit 2.
 *
 * The Chapel modules are reused unchanged: Normalize.normalizeText (NFKC +
 * strip + confusable fold), Redact.redactText (9 RE2 patterns with
 * Python-exact Unicode-boundary validation; it re-normalizes internally),
 * Defang.defangText (markdown/URI neutralization), Normalize.unicodeVersion.
 *
 * The stdin/stdout idioms (`stdin.readAll(bytes)`, the redact framing
 * `stdout.write("findings:", joined, "\n"); stdout.write(redacted)`) are
 * lifted verbatim from the verified C0 spike so byte-parity is preserved.
 *
 * The decode()/bytes choice per subcommand preserves the former shared-library
 * parity corpus: normalize takes the `bytes` overload directly; defang and
 * redact decode stdin to a string first (redactText and defangText are
 * string-typed). For valid UTF-8 this round-trips losslessly.
 *
 * C2b adds `redact-batch`: a length-prefixed N-document framing over one
 * process with an internal `coforall` fan-out that owns its own concurrency.
 * C2c-e add fail-closed policy, card streams, and run artifacts. C4a adds a
 * fixed worker set and bounded request ring without changing the existing
 * one-shot contracts or the per-document transform modules.
 */
module Main {
  use IO, List;
  use Normalize, Redact, Defang, Batch, Stream, Serve;

  /* Chapel passes the command line via the optional `[] string` formal:
   * args[0] is the executable name and args[1..] are the arguments (0-indexed,
   * per the Chapel spec's "The main() Function"). Returning an int sets the
   * process exit status. */
  proc main(args: [] string): int throws {
    if args.size < 2 {
      stderr.writeln("ptoon: missing subcommand (want normalize|defang|redact|redact-batch|condense-batch|condense|serve|caps)");
      return 2;
    }
    const sub = args[1];

    select sub {
      when "normalize" {
        const raw = stdin.readAll(bytes);
        stdout.write(normalizeText(raw));           // bytes overload (Abi parity)
        return 0;
      }
      when "defang" {
        const raw = stdin.readAll(bytes);
        stdout.write(defangText(raw.decode()));     // string-typed (Abi parity)
        return 0;
      }
      when "redact" {
        const raw = stdin.readAll(bytes);
        // redactText re-normalizes internally, then runs the 9 patterns.
        const (redacted, findings) = redactText(raw.decode());
        var joined: string;
        var first = true;
        for f in findings {
          if !first then joined += ",";
          joined += f;
          first = false;
        }
        // Exact C0-spike framing: one findings line, then the text verbatim.
        stdout.write("findings:", joined, "\n");
        stdout.write(redacted);
        return 0;
      }
      when "caps" {
        // Same JSON shape Abi.ptoon_engine_caps emitted, same version source.
        const caps = '{"engine":"chapel","utf8proc":true,"unicode_version":"' +
                     unicodeVersion() +
                     '","patterns":9,"serve_protocol":1,"features":["normalize","redact","defang","redact-batch","condense-batch","condense","serve"]}';
        stdout.write(caps);
        return 0;
      }
      when "redact-batch" {
        // C2b fan-in: length-prefixed N-document batch on stdin, coforall
        // one task per doc, length-prefixed results in input order. All
        // concurrency stays inside this one Chapel runtime. See Batch.chpl.
        //
        // C2c optional positional policy args (0 or absent = unlimited):
        //   argv[2] = maxInputBytes  — withhold docs over the cap (fail-closed)
        //   argv[3] = budgetMs       — withhold docs past the wall-clock budget
        // With both absent this is byte-identical to C2b (parity gate calls it
        // with no extra args). Policy args are fail-closed: malformed, negative,
        // too many, or budget-without-cap args exit nonzero before any output.
        if args.size > 4 {
          stderr.writeln("ptoon redact-batch: too many policy args (want maxInputBytes budgetMs)");
          return 2;
        }
        var maxInputBytes = 0;
        var budgetMs = 0;
        try {
          if args.size >= 3 then maxInputBytes = args[2]: int;
          if args.size >= 4 then budgetMs = args[3]: int;
        } catch e {
          stderr.writeln("ptoon redact-batch: policy args must be decimal integers");
          return 2;
        }
        if maxInputBytes < 0 || budgetMs < 0 {
          stderr.writeln("ptoon redact-batch: policy args must be nonnegative");
          return 2;
        }
        if budgetMs > 0 && maxInputBytes == 0 {
          stderr.writeln("ptoon redact-batch: budgetMs requires positive maxInputBytes");
          return 2;
        }
        const raw = stdin.readAll(bytes);
        const docs = parseBatch(raw);
        stdout.write(redactBatch(docs, maxInputBytes, budgetMs));
        return 0;
      }
      when "condense-batch" {
        // C2d --stream surface: length-prefixed source/tier/body triplets on
        // stdin, coforall condensation (sha256 + redact + source cards),
        // JSONL events on stdout in input order. See Stream.chpl.
        //
        // Positional policy args mirror redact-batch (all fail-closed, exit
        // nonzero before any output on malformed values):
        //   argv[2] = maxInputBytes  (0/absent = unlimited)
        //   argv[3] = budgetMs       (0/absent = unlimited; requires cap)
        //   argv[4] = maxCards       (absent = 24; must be positive)
        if args.size > 5 {
          stderr.writeln("ptoon condense-batch: too many policy args (want maxInputBytes budgetMs maxCards)");
          return 2;
        }
        var maxInputBytes = 0;
        var budgetMs = 0;
        var maxCards = 24;
        try {
          if args.size >= 3 then maxInputBytes = args[2]: int;
          if args.size >= 4 then budgetMs = args[3]: int;
          if args.size >= 5 then maxCards = args[4]: int;
        } catch e {
          stderr.writeln("ptoon condense-batch: policy args must be decimal integers");
          return 2;
        }
        if maxInputBytes < 0 || budgetMs < 0 {
          stderr.writeln("ptoon condense-batch: policy args must be nonnegative");
          return 2;
        }
        if budgetMs > 0 && maxInputBytes == 0 {
          stderr.writeln("ptoon condense-batch: budgetMs requires positive maxInputBytes");
          return 2;
        }
        if maxCards <= 0 {
          stderr.writeln("ptoon condense-batch: maxCards must be positive");
          return 2;
        }
        const raw = stdin.readAll(bytes);
        const docs = parseStreamBatch(raw);
        condenseStream(docs, maxInputBytes, budgetMs, maxCards);
        return 0;
      }
      when "condense" {
        // C2e run-level surface: five length-prefixed run-header fields
        // (runId, generatedAt, minToonSavings, defaultTier,
        // tierOverridesJson — caller-supplied, echoed never computed), then
        // the condense-batch triplet framing. Emits everything
        // condense-batch emits plus one summary event (render_summary
        // byte-parity) and one manifest event (value-parity) before the
        // batch tally. Policy args identical to condense-batch.
        if args.size > 5 {
          stderr.writeln("ptoon condense: too many policy args (want maxInputBytes budgetMs maxCards)");
          return 2;
        }
        var maxInputBytes = 0;
        var budgetMs = 0;
        var maxCards = 24;
        try {
          if args.size >= 3 then maxInputBytes = args[2]: int;
          if args.size >= 4 then budgetMs = args[3]: int;
          if args.size >= 5 then maxCards = args[4]: int;
        } catch e {
          stderr.writeln("ptoon condense: policy args must be decimal integers");
          return 2;
        }
        if maxInputBytes < 0 || budgetMs < 0 {
          stderr.writeln("ptoon condense: policy args must be nonnegative");
          return 2;
        }
        if budgetMs > 0 && maxInputBytes == 0 {
          stderr.writeln("ptoon condense: budgetMs requires positive maxInputBytes");
          return 2;
        }
        if maxCards <= 0 {
          stderr.writeln("ptoon condense: maxCards must be positive");
          return 2;
        }
        const raw = stdin.readAll(bytes);
        var header: CondenseRunHeader;
        var docs: list(StreamDoc);
        parseCondenseRun(raw, header, docs);
        condenseRun(docs, header, maxInputBytes, budgetMs, maxCards);
        return 0;
      }
      when "serve" {
        // C4a resident transform service. Provider transport remains outside
        // Chapel; this process owns a fixed worker set and bounded request ring.
        if args.size > 11 {
          stderr.writeln("ptoon serve: too many args (want workers queueDepth maxDocs maxRequestBytes maxLabelBytes maxInputBytes maxBudgetMs maxCards maxResponseBytes)");
          return 2;
        }
        var workers = 16;
        var queueDepth = 64;
        var maxDocs = 64;
        var maxRequestBytes = 16 * 1024 * 1024;
        var maxLabelBytes = 4096;
        var maxInputBytes = 2_000_000;
        var maxBudgetMs = 2000;
        var maxCards = 24;
        var maxResponseBytes = 256 * 1024 * 1024;
        try {
          if args.size >= 3 then workers = args[2]: int;
          if args.size >= 4 then queueDepth = args[3]: int;
          if args.size >= 5 then maxDocs = args[4]: int;
          if args.size >= 6 then maxRequestBytes = args[5]: int;
          if args.size >= 7 then maxLabelBytes = args[6]: int;
          if args.size >= 8 then maxInputBytes = args[7]: int;
          if args.size >= 9 then maxBudgetMs = args[8]: int;
          if args.size >= 10 then maxCards = args[9]: int;
          if args.size >= 11 then maxResponseBytes = args[10]: int;
        } catch e {
          stderr.writeln("ptoon serve: args must be decimal integers");
          return 2;
        }
        if workers <= 0 || queueDepth <= 0 || maxDocs <= 0 ||
           maxRequestBytes <= 0 || maxLabelBytes <= 0 ||
           maxInputBytes <= 0 || maxBudgetMs <= 0 || maxCards <= 0 ||
           maxResponseBytes <= 0 {
          stderr.writeln("ptoon serve: every limit must be positive");
          return 2;
        }
        if maxInputBytes > maxRequestBytes {
          stderr.writeln("ptoon serve: maxInputBytes cannot exceed maxRequestBytes");
          return 2;
        }
        if maxResponseBytes < maxRequestBytes {
          stderr.writeln("ptoon serve: maxResponseBytes cannot be smaller than maxRequestBytes");
          return 2;
        }
        return serveLoop(workers, queueDepth, maxDocs, maxRequestBytes,
                         maxLabelBytes, maxInputBytes, maxBudgetMs, maxCards,
                         maxResponseBytes);
      }
      otherwise {
        stderr.writeln("ptoon: unknown subcommand '", sub,
                       "' (want normalize|defang|redact|redact-batch|condense-batch|condense|serve|caps)");
        return 2;
      }
    }
  }
}
