/* TIN-2708 C1: quickchpl property tests for the Chapel ptoon core.
 *
 * Exercises three sibling modules written in parallel this same phase
 * against the parity spec (prompt_toon/cli.py):
 *   - Normalize.normalizeText(text: string): string throws
 *   - Redact.redactText(text: string): (string, list(string)) throws
 *   - Toon.toonEscape(value: string, delimiter: string = ","): string
 *   - Toon.encodeRows(header: [] string, rows: [] [] string,
 *                      delimiter: string): string throws
 *
 * quickchpl API used (cited from the sources read at
 * /Users/jess/git/quickchpl on 2026-07-09, version 1.0.2):
 *   - Properties.chpl:  proc property(name, gen, pred)
 *                       proc check(ref prop: Property(?), n: int): TestResult
 *   - Generators.chpl:  proc tupleGen(gen1, gen2)
 *                       proc listGen(elemGen, minSize, maxSize, seed): listGenerator
 *                       proc elementsGen(elements: list(?T), seed): elementsGenerator
 *   - quickchpl.chpl:   `include module ...; public use ...;` re-exports all
 *                       of the above (and Random/List) under `use quickchpl;`
 *   - Custom generator shape (record with `var rng: randomStream(int);`,
 *     `proc init(seed: int = -1)`, `proc ref next(): T`,
 *     `iter these(n: int = 100) ref : T`) copied from
 *     examples/CustomGenerators.chpl's `colorGenerator`.
 *
 * UNCERTAINTY (flagged per task instructions, cannot compile locally):
 * quickchpl 1.0.2's PropertyRunner.check() (Properties.chpl ~lines 314-321)
 * does NOT actually invoke the Shrinkers module -- the comment there reads
 * "Attempt to shrink the failure // For now, just report the original
 * (shrinking added later)", and `shrunkInfo` is set equal to the raw
 * `firstFailure` string with `shrinkSteps` staying 0. So "rely on
 * shrinking" (as asked for property 2 below) is not something the
 * installed check()/TestResult pipeline currently performs automatically;
 * failures are reported un-shrunk. The property below is written to still
 * be useful without automatic shrinking (many random perturbation shapes
 * per run via a high `numTests`), but if quickchpl gains real shrinking in
 * a later version, this file needs no changes to benefit from it -- it
 * already goes through `check()`.
 *
 * UNCERTAINTY (flagged, cannot compile locally): passing a `throws`-marked
 * top-level proc as `pred` to `property()`/into the generic, untyped
 * `predicateFn` field of `Properties.Property` and then calling it inside
 * `check()`'s `try { ... } catch e { ... }` is exercised nowhere else in
 * quickchpl's own test suite (its own PropertyTests.chpl only passes
 * non-throwing predicates). `normalizeText`/`redactText` are `throws`, so
 * properties 1 and 2 below need this to work. It is expected to work given
 * Chapel's first-class-procedure support and the try/catch already present
 * in `check()`, but is unverified against an actual chpl compiler.
 *
 * UNCERTAINTY (flagged, cannot compile locally): Chapel string literal
 * escapes used below (`\r`, `\n`, `\t`, `\\`, `\"`, `\x01`-style hex byte
 * escapes, `\u{200B}`-style Unicode escapes) are assumed to follow the
 * standard C-like Chapel string-literal escape grammar. The `\u{...}` and
 * `\x..` forms are used only in `zeroWidthPool`/`bidiPool`/`controlPool`
 * below; if the exact escape spelling is rejected by chpl, those three
 * `const` string literals are the only lines that would need adjusting.
 */
module PropertyTests {
  use quickchpl;
  use Random;
  use List;
  use Normalize;
  use Redact;
  use Toon;

  config const numTests = 150;

  /* ------------------------------------------------------------------ *
   * Shared codepoint-pool helpers
   * ------------------------------------------------------------------ */

  private proc poolSize(s: string): int {
    var n = 0;
    for cp in s.codepoints() do n += 1;
    return n;
  }

  private proc nthCodepoint(s: string, idx: int): int(32) {
    var i = 0;
    for cp in s.codepoints() {
      if i == idx then return cp;
      i += 1;
    }
    return 0x20: int(32); // unreachable for a valid idx; ASCII space fallback
  }

  private proc codepointToString(cp: int(32)): string {
    var s = "";
    s.appendCodepointValues(cp: int);
    return s;
  }

  /* ------------------------------------------------------------------ *
   * Property 1: normalize(normalize(x)) == normalize(x)
   *
   * Adversarial string generator mixing plain ASCII with zero-width,
   * BIDI-control, confusable-homoglyph, raw-control, and CRLF codepoints --
   * exactly the codepoint classes Normalize.normalizeText strips or folds
   * (parity spec: cli.py:120-127; Chapel port: src/ptoon/Normalize.chpl
   * isStripped/confusables).
   * ------------------------------------------------------------------ */

  const asciiPool = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?-_/";
  // U+00AD, U+200B-200D, U+2060-2064, U+FEFF (Normalize.chpl isStripped).
  const zeroWidthPool = "\u{00AD}\u{200B}\u{200C}\u{200D}\u{2060}\u{2061}\u{2062}\u{2063}\u{2064}\u{FEFF}";
  // U+061C, U+200E-200F, U+202A-202E, U+2066-2069 (Normalize.chpl isStripped).
  const bidiPool = "\u{061C}\u{200E}\u{200F}\u{202A}\u{202B}\u{202C}\u{202D}\u{202E}\u{2066}\u{2067}\u{2068}\u{2069}";
  // Cyrillic/Greek confusable sources, copied verbatim (not re-derived) from
  // src/ptoon/Normalize.chpl's `confusablesFrom` literal.
  const confusablePool = "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧνοЅІЈАВЕКМНОРСТУХаеорсухѕіјғԁԛԝ";
  // A sample of C0 controls + DEL that Normalize.chpl's CONTROL_RE strips.
  const controlPool = "\x01\x02\x08\x0b\x0c\x0e\x1f\x7f";
  const crlfPool = "\r\n";

  record adversarialStringGenerator {
    var rng: randomStream(int);
    var minLen: int;
    var maxLen: int;

    proc init(minLen: int = 0, maxLen: int = 40, seed: int = -1) {
      this.minLen = minLen;
      this.maxLen = maxLen;
      if seed >= 0 {
        this.rng = new randomStream(int, seed);
      } else {
        this.rng = new randomStream(int);
      }
    }

    proc ref next(): string {
      const len = minLen + abs(rng.next()) % (maxLen - minLen + 1);
      var result = "";
      for 1..len {
        const poolChoice = abs(rng.next()) % 6;
        var cp: int(32);
        select poolChoice {
          when 0 do cp = nthCodepoint(asciiPool, abs(rng.next()) % poolSize(asciiPool));
          when 1 do cp = nthCodepoint(zeroWidthPool, abs(rng.next()) % poolSize(zeroWidthPool));
          when 2 do cp = nthCodepoint(bidiPool, abs(rng.next()) % poolSize(bidiPool));
          when 3 do cp = nthCodepoint(confusablePool, abs(rng.next()) % poolSize(confusablePool));
          when 4 do cp = nthCodepoint(controlPool, abs(rng.next()) % poolSize(controlPool));
          otherwise do cp = nthCodepoint(crlfPool, abs(rng.next()) % poolSize(crlfPool));
        }
        result += codepointToString(cp);
      }
      return result;
    }

    iter these(n: int = 100) ref : string {
      for 1..n {
        yield this.next();
      }
    }
  }

  proc adversarialStringGen(minLen: int = 0, maxLen: int = 40, seed: int = -1) {
    return new adversarialStringGenerator(minLen, maxLen, seed);
  }

  proc idempotenceHolds(text: string): bool throws {
    const once = normalizeText(text);
    const twice = normalizeText(once);
    return once == twice;
  }

  /* ------------------------------------------------------------------ *
   * Property 2: redaction completeness under confusable / zero-width
   * perturbation.
   *
   * redactText normalizes its input FIRST (Redact.chpl: `var redacted =
   * normalizeText(text);`, mirroring cli.py:140 `redacted =
   * normalize_text(text)`), so a synthetic secret whose characters have
   * been swapped for Cyrillic/Greek confusable lookalikes and interleaved
   * with zero-width characters should fold/strip back to the original
   * secret before any of the nine SECRET_PATTERNS run, and redaction
   * should still fire.
   * ------------------------------------------------------------------ */

  // Reverse of Normalize.chpl's CONFUSABLES table (ascii target -> homoglyph
  // source), built by pairing the same two literal strings Normalize.chpl
  // uses (confusablesFrom/confusablesTo) with the roles swapped -- copied
  // verbatim, not re-derived, so every substitution below is a real
  // confusable pair the Chapel Normalize module is known to fold back.
  const confusableAsciiTargets = "ABEZHIKMNOPTYXvoSIJABEKMHOPCTYXaeopcyxsijfdqw";
  const confusableLookalikes   = "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧνοЅІЈАВЕКМНОРСТУХаеорсухѕіјғԁԛԝ";

  private proc confusableFor(asciiChar: string): string {
    var idx = 0;
    for cp in confusableAsciiTargets.codepoints() {
      if codepointToString(cp) == asciiChar {
        return codepointToString(nthCodepoint(confusableLookalikes, idx));
      }
      idx += 1;
    }
    return asciiChar; // no confusable exists for this char; leave as-is
  }

  record secretPerturbationGenerator {
    var rng: randomStream(int);
    var secretLen: int;

    proc init(secretLen: int = 20, seed: int = -1) {
      this.secretLen = secretLen;
      if seed >= 0 {
        this.rng = new randomStream(int, seed);
      } else {
        this.rng = new randomStream(int);
      }
    }

    proc ref next(): string {
      // A pattern-1-shaped secret (SECRET_PATTERNS[0], cli.py:23):
      // \b(?:sk|ghp|gho|github_pat|xox[baprs])-[-_A-Za-z0-9]{16,}\b
      // Tail characters come from [-_A-Za-z0-9]; the FINAL tail character is
      // forced to be alnum (not '-'/'_') so the pre-perturbation secret has
      // a solid Python-\w right boundary before " end" regardless of what
      // confusable/zero-width perturbation later does to it (both
      // perturbation kinds are undone by normalizeText before pattern
      // matching runs, per the module doc above).
      const bodyAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_";
      const alnumAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
      var body = "";
      for 1..(secretLen - 1) {
        body += codepointToString(nthCodepoint(bodyAlphabet, abs(rng.next()) % poolSize(bodyAlphabet)));
      }
      body += codepointToString(nthCodepoint(alnumAlphabet, abs(rng.next()) % poolSize(alnumAlphabet)));
      const secret = "sk-" + body;

      // Perturb: each character independently may be swapped for a
      // confusable lookalike, and/or followed by an injected zero-width
      // character. Both perturbations are reversed by normalizeText.
      var perturbed = "";
      for cp in secret.codepoints() {
        var ch = codepointToString(cp);
        if abs(rng.next()) % 2 == 0 {
          ch = confusableFor(ch);
        }
        perturbed += ch;
        if abs(rng.next()) % 3 == 0 {
          perturbed += codepointToString(nthCodepoint(zeroWidthPool, abs(rng.next()) % poolSize(zeroWidthPool)));
        }
      }

      return "token " + perturbed + " end";
    }

    iter these(n: int = 100) ref : string {
      for 1..n {
        yield this.next();
      }
    }
  }

  proc redactionSurvivesPerturbation(text: string): bool throws {
    const (_, findings) = redactText(text);
    return findings.size >= 1;
  }

  /* ------------------------------------------------------------------ *
   * Properties 3 & 4: Toon.toonEscape / Toon.encodeRows.
   * ------------------------------------------------------------------ */

  // Delimiter/CR/tab/backslash/quote/bracket-heavy adversarial cell pool
  // (all single-byte ASCII, so byteIndex-based slicing below is exact).
  const cellCharPool = "abcXYZ019 ,\r\n\t\\\"[]{}:";

  record adversarialCellGenerator {
    var rng: randomStream(int);
    var minLen: int;
    var maxLen: int;

    proc init(minLen: int = 0, maxLen: int = 12, seed: int = -1) {
      this.minLen = minLen;
      this.maxLen = maxLen;
      if seed >= 0 {
        this.rng = new randomStream(int, seed);
      } else {
        this.rng = new randomStream(int);
      }
    }

    proc ref next(): string {
      const len = minLen + abs(rng.next()) % (maxLen - minLen + 1);
      var result = "";
      for 1..len {
        result += codepointToString(nthCodepoint(cellCharPool, abs(rng.next()) % poolSize(cellCharPool)));
      }
      return result;
    }

    iter these(n: int = 100) ref : string {
      for 1..n {
        yield this.next();
      }
    }
  }

  proc adversarialCellGen(minLen: int = 0, maxLen: int = 12, seed: int = -1) {
    return new adversarialCellGenerator(minLen, maxLen, seed);
  }

  /* Quote-aware splitter for one already-assembled TOON row line (as
   * produced by Toon.encodeRows' per-row `delimiter.join(escapedCells)`
   * step, parity: cli.py:314). TEST-ONLY reverse-parser -- not part of the
   * production Toon.chpl surface, which is encode-only for this phase (see
   * Toon.chpl's module doc comment). Exists solely to check the round-trip
   * property below.
   *
   * Relies on two invariants of toonEscape's needs-quote rule (Toon.chpl,
   * ported from cli.py:280-290):
   *   1. An UNQUOTED field can never contain `delimiter` or a `"` (both
   *      force quoting), so it can be scanned up to the next literal
   *      delimiter byte.
   *   2. A QUOTED field's body only ever contains 2-byte escape pairs
   *      starting with `\` (`\\`, `\"`, `\n`, `\r`, `\t`) or literal
   *      passthrough bytes, so a lone (non-escape-introducing) `"` always
   *      terminates the field.
   */
  private proc parseToonRow(line: string, delimiter: string): list(string) {
    var fields = new list(string);
    const n = line.size;
    var i = 0;
    while i < n {
      const c = line.this((i: byteIndex)..<((i + 1): byteIndex));
      if c == "\"" {
        var j = i + 1;
        while j < n {
          const cj = line.this((j: byteIndex)..<((j + 1): byteIndex));
          if cj == "\\" then j += 2;
          else if cj == "\"" then break;
          else j += 1;
        }
        fields.pushBack(line.this(((i + 1): byteIndex)..<(j: byteIndex)));
        i = j + 1;
        if i < n {
          const cd = line.this((i: byteIndex)..<((i + 1): byteIndex));
          if cd == delimiter then i += 1;
        }
      } else {
        var j = i;
        while j < n {
          const cj = line.this((j: byteIndex)..<((j + 1): byteIndex));
          if cj == delimiter then break;
          j += 1;
        }
        fields.pushBack(line.this((i: byteIndex)..<(j: byteIndex)));
        i = if j < n then j + 1 else j;
      }
    }
    return fields;
  }

  proc roundTripPreservesFieldCount(cells: list(string)): bool throws {
    const delimiter = ",";
    const width = cells.size;

    var header: [0..<width] string;
    for i in header.domain do header[i] = "c" + i: string;

    var rows: [0..<1] [0..<width] string;
    for i in 0..<width do rows[0][i] = cells[i];

    const doc = encodeRows(header, rows, delimiter);
    if doc.find("\r") != -1 then return false; // no raw CR anywhere in the doc

    // doc == headerLine + "\n" + "  " + joinedCells + "\n" for a single row
    // (Toon.chpl encodeRows: docLines joined by "\n", plus a trailing "\n").
    const firstNL = doc.find("\n");
    if firstNL == -1 || doc.size < 2 then return false;
    const rowSegment = doc.this(((firstNL + 1): byteIndex)..<((doc.size - 1): byteIndex));
    if rowSegment.size < 2 then return false;
    const rowLine = rowSegment.this((2: byteIndex)..); // drop the "  " indent

    const fields = parseToonRow(rowLine, delimiter);
    return fields.size == width;
  }

  proc unquotedCellNeverContainsDelimiter(input: (string, string)): bool {
    const (cell, delim) = input;
    const escaped = toonEscape(cell, delim);
    if escaped == cell {
      // toonEscape returned the cell unchanged => it took the pass-through
      // (unquoted) branch, since the quoted branch always wraps in `"..."`
      // and therefore strictly lengthens the string. Confirm the delimiter
      // rule that pass-through branch is supposed to guarantee.
      return escaped.find(delim) == -1;
    }
    return true;
  }

  /* ------------------------------------------------------------------ *
   * Runner
   * ------------------------------------------------------------------ */

  proc main() {
    writeln("ptoon Chapel property tests (TIN-2708 C1)");
    writeln("=" * 60);
    writeln();

    var failed = 0;

    {
      writeln("Property: normalize(normalize(x)) == normalize(x)");
      var gen = adversarialStringGen(0, 40);
      var prop = property("normalize is idempotent", gen, idempotenceHolds);
      const result = check(prop, numTests);
      if result.passed {
        writeln("  PASS (", result.numTests, " cases)");
      } else {
        writeln("  FAIL: ", result.failureInfo);
        failed += 1;
      }
      writeln();
    }

    {
      writeln("Property: redact still finds a perturbed synthetic secret");
      var gen = new secretPerturbationGenerator(20);
      var prop = property("redaction survives confusable + zero-width perturbation",
                          gen, redactionSurvivesPerturbation);
      const result = check(prop, numTests);
      if result.passed {
        writeln("  PASS (", result.numTests, " cases)");
      } else {
        writeln("  FAIL: ", result.failureInfo);
        failed += 1;
      }
      writeln();
    }

    {
      writeln("Property: TOON row round-trip preserves field count, no raw CR");
      var gen = listGen(adversarialCellGen(0, 12), 1, 5);
      var prop = property("toon row split matches header width", gen,
                          roundTripPreservesFieldCount);
      const result = check(prop, numTests);
      if result.passed {
        writeln("  PASS (", result.numTests, " cases)");
      } else {
        writeln("  FAIL: ", result.failureInfo);
        failed += 1;
      }
      writeln();
    }

    {
      writeln("Property: toonEscape never emits an unquoted cell containing the delimiter");
      var delimChoices = new list(string);
      delimChoices.pushBack(",");
      delimChoices.pushBack("\t");
      var gen = tupleGen(adversarialCellGen(0, 12), elementsGen(delimChoices));
      var prop = property("unquoted cells never contain the delimiter", gen,
                          unquotedCellNeverContainsDelimiter);
      const result = check(prop, numTests);
      if result.passed {
        writeln("  PASS (", result.numTests, " cases)");
      } else {
        writeln("  FAIL: ", result.failureInfo);
        failed += 1;
      }
      writeln();
    }

    writeln("=" * 60);
    if failed == 0 {
      writeln("All ptoon property tests passed.");
    } else {
      writeln(failed, " ptoon property test(s) failed.");
      halt(1);
    }
  }
}
