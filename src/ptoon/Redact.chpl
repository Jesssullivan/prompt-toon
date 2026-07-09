/* TIN-2708 C1: redact_text port (parity spec: prompt_toon/cli.py:21-31,
 * 138-145) with Python-exact \b semantics.
 *
 * RE2's \b is ASCII-only; Python's is Unicode (\w = str.isalnum + '_').
 * The C0 parity run showed this as the SOLE divergence: RE2 accepted a
 * secret whose neighbor was a CJK/accented char that Python's Unicode \b
 * treats as a word char (so Python's boundary fails and it matches nothing
 * at that position). Because every pattern's edge char classes are ASCII
 * word chars, there is never a *shorter* Python-valid match at the same
 * position — the fix is to REJECT (not shrink) candidates whose real edges
 * are not Python boundaries, and rescan from the next position (Python's
 * position-by-position scan).
 *
 * So: RE2 patterns are candidate generators; each candidate's byte edges are
 * re-validated against Python's word set (utf8proc letter/number categories
 * plus underscore via Normalize.isWordCp). Redaction is fail-closed:
 * compile failure throws.
 */
module Redact {
  use Regex, List;
  use Normalize;

  const REDACTED = "[REDACTED]";

  record PatternSpec {
    var name: string;
    var genSrc: string;   // candidate generator (RE2 form)
    var checkLeft: bool;  // Python pattern asserts a left \b
    var checkRight: bool; // Python pattern asserts a right \b
  }

  /* SECRET_PATTERNS 1-9 (cli.py:21-31). \b handling per the module comment;
   * everything else is verbatim. */
  const specs = [
    new PatternSpec("pattern-1",
      "\\b(?:sk|ghp|gho|github_pat|xox[baprs])-[-_A-Za-z0-9]{16,}\\b", true, true),
    new PatternSpec("pattern-2",
      "\\b(?:sk|ghp|gho|github_pat)_[-_A-Za-z0-9]{16,}\\b", true, true),
    new PatternSpec("pattern-3",
      "\\b(?:AKIA|ASIA)[0-9A-Z]{16}\\b", true, true),
    new PatternSpec("pattern-4",
      "\\bAIza[0-9A-Za-z_-]{35}\\b", true, true),
    new PatternSpec("pattern-5",
      "\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b", true, true),
    new PatternSpec("pattern-6",
      "-----BEGIN[A-Z ]*PRIVATE KEY-----[\\s\\S]+?-----END[A-Z ]*PRIVATE KEY-----",
      false, false),
    new PatternSpec("pattern-7",
      "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b", true, true),
    new PatternSpec("pattern-8",
      "(?i)\\b(api[_-]?key|token|password|secret)\\s*[:=]\\s*['\"]?[^'\"\\s]{8,}",
      true, false),
    new PatternSpec("pattern-9",
      "\\b(?:\\d[ -]?){13,19}\\b", true, true),
  ];

  /* C2: candidate generators compiled ONCE at module init. C1 called
   * `new regex(...)` inside redactText's pattern loop — 9 RE2 compiles per
   * document, i.e. 9*N for an N-document batch. RE2 objects are immutable
   * after compile and `regex.search` takes `const ref this`, so every
   * `coforall` batch task can share these read-only with no per-task compile
   * and no data race. try!: the nine patterns are compile-time-known-good;
   * an invalid pattern is a build-time bug that must halt at init
   * (fail-closed), never be silently skipped at redaction time. */
  private proc buildGens(): [0..<specs.size] regex(string) {
    var gens: [0..<specs.size] regex(string);
    for i in specs.domain do gens[i] = try! new regex(specs[i].genSrc);
    return gens;
  }
  private const gens = buildGens();

  /* Decode the codepoint starting at byte i. */
  private proc cpAt(const ref arr: [] uint(8), i: int): int(32) {
    const b0 = arr[i]: int(32);
    if b0 < 0x80 then return b0;
    if (b0 & 0xE0) == 0xC0 then
      return ((b0 & 0x1F) << 6) | (arr[i + 1]: int(32) & 0x3F);
    if (b0 & 0xF0) == 0xE0 then
      return ((b0 & 0x0F) << 12) | ((arr[i + 1]: int(32) & 0x3F) << 6)
             | (arr[i + 2]: int(32) & 0x3F);
    return ((b0 & 0x07) << 18) | ((arr[i + 1]: int(32) & 0x3F) << 12)
           | ((arr[i + 2]: int(32) & 0x3F) << 6) | (arr[i + 3]: int(32) & 0x3F);
  }

  /* Decode the codepoint ending just before byte i. */
  private proc cpBefore(const ref arr: [] uint(8), i: int): int(32) {
    var j = i - 1;
    while j > 0 && (arr[j] & 0xC0) == 0x80 do j -= 1;
    return cpAt(arr, j);
  }

  /* Start byte of the codepoint containing byte i. */
  private proc cpStart(const ref arr: [] uint(8), i: int): int {
    var j = i;
    while j > 0 && (arr[j] & 0xC0) == 0x80 do j -= 1;
    return j;
  }

  /* Python \b at an edge: exactly one side is a word char; off-text is
   * non-word. */
  private inline proc edgeOk(insideCp: int(32), hasOutside: bool,
                             outsideCp: int(32)): bool {
    const insideWord = isWordCp(insideCp);
    const outsideWord = if hasOutside then isWordCp(outsideCp) else false;
    return insideWord != outsideWord;
  }

  private proc toArr(const ref b: bytes): [0..<b.size] uint(8) {
    var arr: [0..<b.size] uint(8);
    var idx = 0;
    for v in b { arr[idx] = v; idx += 1; }  // default these() yields uint(8)
    return arr;
  }

  /* Accepted [off, off+len) ranges for one pattern, Python scan semantics. */
  private proc acceptedRanges(const ref text: string, const ref gen: regex(string),
                              const spec: PatternSpec): list((int, int)) throws {
    var ranges = new list((int, int));
    const b = text.encode();
    const n = b.size;
    if n == 0 then return ranges;
    const arr = toArr(b);

    var scanFrom = 0;
    while scanFrom < n {
      const tail = text.this((scanFrom: byteIndex)..);
      const m = gen.search(tail);
      if !m.matched then break;
      const off = scanFrom + m.byteOffset: int;
      const len = m.numBytes;

      const firstCp = cpAt(arr, off);
      const lastStart = cpStart(arr, off + len - 1);
      const lastCp = cpAt(arr, lastStart);
      const leftOk = !spec.checkLeft ||
        edgeOk(firstCp, off > 0, if off > 0 then cpBefore(arr, off) else 0);
      const rightOk = !spec.checkRight ||
        edgeOk(lastCp, off + len < n, if off + len < n then cpAt(arr, off + len) else 0);

      if leftOk && rightOk {
        ranges.pushBack((off, len));
        scanFrom = off + len;
      } else {
        var nxt = off + 1;
        while nxt < n && (arr[nxt] & 0xC0) == 0x80 do nxt += 1;
        scanFrom = nxt;
      }
    }
    return ranges;
  }

  private proc splice(const ref text: string,
                      const ref ranges: list((int, int))): string throws {
    var acc: string;
    var pos = 0;
    for (off, len) in ranges {
      acc += text.this((pos: byteIndex)..<(off: byteIndex));
      acc += REDACTED;
      pos = off + len;
    }
    acc += text.this((pos: byteIndex)..);
    return acc;
  }

  /* redact_text (cli.py:138-145): normalize, then the nine patterns in
   * order, each over the previous result. */
  proc redactText(text: string): (string, list(string)) throws {
    var redacted = normalizeText(text);
    var findings = new list(string);
    for i in specs.domain {
      const ranges = acceptedRanges(redacted, gens[i], specs[i]);
      if ranges.size > 0 {
        findings.pushBack(specs[i].name);
        redacted = splice(redacted, ranges);
      }
    }
    return (redacted, findings);
  }
}
