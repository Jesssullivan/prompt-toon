/* TIN-2709 C2d: source-card extraction — the provenance surface of condense.
 *
 * Parity spec: prompt_toon/cli.py — pattern constants (URL_RE:34,
 * CRITICAL_RE:57-62, INJECTION_RE:64-67), line_excerpt:209-212,
 * clean_claim:215-220, flags_for:223-231, cards_from_text:234-277, and the
 * source-cards.jsonl serialization in write_jsonl:391-394 (json.dumps with
 * sort_keys=True, ensure_ascii=False, DEFAULT separators — ", " and ": ").
 * cardJson below must reproduce those bytes exactly; the stream parity gate
 * diffs it against the Python oracle per fixture.
 *
 * Python-semantics notes (each a real divergence trap):
 *   - str.splitlines() splits on \n, U+0085 (NEL), U+2028 (LS), U+2029 (PS)
 *     in text that
 *     has been through normalizeText (CR folded, C0/\x7f controls stripped —
 *     but NEL/LS/PS are NOT in CONTROL_RE's ranges and survive NFKC).
 *     splitLinesPy reproduces exactly that separator set, including the
 *     no-trailing-empty-segment rule.
 *   - CRITICAL_RE and INJECTION_RE carry Python \b at both ends; RE2's \b is
 *     ASCII-only. Searched via Redact.pySearchBounded (RE2 candidates +
 *     Python Unicode edge revalidation). The curl\s+http injection alternative
 *     and URL_RE both need Python \s semantics too, so U+1680 is handled by
 *     explicit CPython-whitespace checks rather than trusting RE2 \s.
 *   - Python str.strip()/rstrip() strip Unicode whitespace. NFKC/control
 *     stripping folds or removes most of it, but U+1680 survives and remains
 *     whitespace to Python, so stripPy carries CPython's whitespace set.
 *   - clean_claim truncation counts CODEPOINTS (Python len/slice), not bytes.
 *
 * Cards read the REDACTED text (redaction precedes card extraction, INV-2);
 * the sha256 field carries the digest of the RAW input bytes (INV-1). */
module Cards {
  use Regex, List, Set;
  use Normalize;
  use Redact;

  record Card {
    var id: string;
    var source: string;
    var trustTier: string;
    var sha: string;
    var lineStart: int;
    var lineEnd: int;
    var claim: string;
    var evidence: string;
    var confidence: string;
    var flags: list(string);
  }

  /* Compiled once at module init, shared read-only across coforall tasks
   * (same rationale as Redact.gens; try!: a bad pattern is a build bug). */
  private proc mkRegex(src: string): regex(string) {
    return try! new regex(src);
  }
  private const urlGen = mkRegex("https?://[^\\s)>\\]]+");
  private const criticalGen = mkRegex(
    "(?i)\\b(must|must not|never|only|required|forbidden|approval|approve|" +
    "deny|scope|deadline|due|owner|assignee|blocked|blocks|secret|redact|" +
    "source|provenance|trust|unsafe|safe default)\\b");
  private const injectionGen = mkRegex(
    "(?i)\\b(ignore previous|system:|developer:|assistant:|user:|tool:|" +
    "reveal secrets|exfiltrate|send to|curl\\s+http|base64)\\b");

  /* Public (not private): Summary.chpl reuses this predicate for the
   * OPEN_QUESTION_RE scan and rough_token_count port. */
  inline proc isPyWhitespace(cp: int(32)): bool {
    if cp >= 0x09 && cp <= 0x0D then return true;
    if cp >= 0x1C && cp <= 0x1F then return true;
    if cp == 0x20 || cp == 0x85 || cp == 0xA0 || cp == 0x1680 then return true;
    if cp >= 0x2000 && cp <= 0x200A then return true;
    if cp == 0x2028 || cp == 0x2029 || cp == 0x202F ||
       cp == 0x205F || cp == 0x3000 then return true;
    return false;
  }

  private inline proc edgeOk(insideCp: int(32), hasOutside: bool,
                             outsideCp: int(32)): bool {
    const insideWord = isWordCp(insideCp);
    const outsideWord = if hasOutside then isWordCp(outsideCp) else false;
    return insideWord != outsideWord;
  }

  private inline proc asciiLower(cp: int(32)): int(32) {
    if cp >= 0x41 && cp <= 0x5A then return cp + 0x20;
    return cp;
  }

  private inline proc asciiEq(cp: int(32), ascii: int(32)): bool {
    return asciiLower(cp) == ascii;
  }

  private proc hasCurlHttpPyWhitespace(const ref s: string): bool {
    var cps = new list(int(32));
    for cp in s.codepoints() do cps.pushBack(cp: int(32));
    const n = cps.size;
    for i in 0..<n {
      if i + 4 >= n then break;
      if !(asciiEq(cps[i], 0x63) && asciiEq(cps[i + 1], 0x75) &&
           asciiEq(cps[i + 2], 0x72) && asciiEq(cps[i + 3], 0x6C)) then
        continue;
      if !edgeOk(cps[i], i > 0, if i > 0 then cps[i - 1] else 0) then
        continue;
      var j = i + 4;
      if !isPyWhitespace(cps[j]) then continue;
      while j < n && isPyWhitespace(cps[j]) do j += 1;
      if j + 3 >= n then continue;
      if !(asciiEq(cps[j], 0x68) && asciiEq(cps[j + 1], 0x74) &&
           asciiEq(cps[j + 2], 0x74) && asciiEq(cps[j + 3], 0x70)) then
        continue;
      const last = j + 3;
      if edgeOk(cps[last], last + 1 < n,
                if last + 1 < n then cps[last + 1] else 0) then
        return true;
    }
    return false;
  }

  private proc hasUrl(const ref s: string): bool throws {
    const b = s.encode();
    const n = b.size;
    var scanFrom = 0;
    while scanFrom < n {
      const tail = s.this((scanFrom: byteIndex)..);
      const m = urlGen.search(tail);
      if !m.matched then break;
      const off = scanFrom + m.byteOffset: int;
      const len = m.numBytes;
      const matched = s.this((off: byteIndex)..<((off + len): byteIndex));
      const restStart = if matched.startsWith("https://") then 8 else 7;
      var firstRest = -1: int(32);
      for cp in matched.this((restStart: byteIndex)..).codepoints() {
        firstRest = cp: int(32);
        break;
      }
      if firstRest >= 0 && !isPyWhitespace(firstRest) then return true;
      scanFrom = off + 1;
    }
    return false;
  }
  /* Public (not private): Summary.chpl reuses this for constraint
   * extraction over claim+"\n"+evidence (render_summary parity). */
  proc hasCritical(const ref s: string): bool throws {
    return pySearchBounded(s, criticalGen);
  }
  private proc hasInjection(const ref s: string): bool throws {
    return pySearchBounded(s, injectionGen) || hasCurlHttpPyWhitespace(s);
  }

  /* Python str.splitlines() over normalized text: split on \n / NEL / LS /
   * PS; a trailing separator does not yield a final empty segment. */
  proc splitLinesPy(const ref s: string): list(string) throws {
    var lines = new list(string);
    var byteOff = 0;
    var lineStart = 0;
    for (cp, item) in zip(s.codepoints(), s.items()) {
      const w = item.numBytes;
      if cp == 0x0A || cp == 0x85 || cp == 0x2028 || cp == 0x2029 {
        lines.pushBack(s.this((lineStart: byteIndex)..<(byteOff: byteIndex)));
        lineStart = byteOff + w;
      }
      byteOff += w;
    }
    if lineStart < byteOff then
      lines.pushBack(s.this((lineStart: byteIndex)..));
    return lines;
  }

  private proc stripPy(const ref s: string, leading: bool = true,
                       trailing: bool = true): string throws {
    var start = 0;
    var end = 0;
    var byteOff = 0;
    var sawNonLeading = false;
    for (cp, item) in zip(s.codepoints(), s.items()) {
      const w = item.numBytes;
      const ws = isPyWhitespace(cp: int(32));
      if leading && !sawNonLeading && ws {
        start = byteOff + w;
      } else {
        sawNonLeading = true;
        if !trailing || !ws then end = byteOff + w;
      }
      byteOff += w;
    }
    if !trailing then end = byteOff;
    if start >= end then return "";
    return s.this((start: byteIndex)..<(end: byteIndex));
  }

  private inline proc isClaimMarkerPrefix(cp: int(32)): bool {
    return cp == 0x2D || cp == 0x2A || cp == 0x23 || cp == 0x3E ||
           cp == 0x2E || (cp >= 0x30 && cp <= 0x39) || isPyWhitespace(cp);
  }

  /* clean_claim (cli.py:215-220): strip, drop the ^[-*#>\s0-9.]+ marker
   * prefix, strip again, truncate to 220 codepoints with a "..." tail. */
  private proc cleanClaim(const ref line: string): string throws {
    var s = stripPy(line);
    var drop = 0;
    for (cp, item) in zip(s.codepoints(), s.items()) {
      if isClaimMarkerPrefix(cp: int(32)) {
        drop += item.numBytes;
      } else {
        break;
      }
    }
    if drop > 0 then s = s.this((drop: byteIndex)..);
    s = stripPy(s);
    if s.size > 220 {
      var acc: string;
      var count = 0;
      for item in s.items() {
        if count == 219 then break;
        acc += item;
        count += 1;
      }
      return stripPy(acc, leading=false, trailing=true) + "...";
    }
    return s;
  }

  /* line_excerpt (cli.py:209-212), radius 1: 1-based inclusive line span +
   * the joined excerpt stripped Python-style (leading/trailing \n included). */
  private proc lineExcerpt(const ref lines: list(string),
                           idx: int): (int, int, string) throws {
    const lo = max(0, idx - 1);
    const hi = min(lines.size, idx + 2);
    var ev: string;
    for j in lo..<hi {
      if j > lo then ev += "\n";
      ev += lines[j];
    }
    return (lo + 1, hi, stripPy(ev));
  }

  /* flags_for (cli.py:223-231): order is fixed — redacted, injection-shaped,
   * has-url. `redacted` keys off the DOCUMENT's findings, not the excerpt. */
  private proc flagsFor(const ref evidence: string,
                        docHasRedactions: bool): list(string) throws {
    var flags = new list(string);
    if docHasRedactions then flags.pushBack("redacted");
    if hasInjection(evidence) then flags.pushBack("injection-shaped");
    if hasUrl(evidence) then flags.pushBack("has-url");
    return flags;
  }

  private proc cardId(n: int): string {
    if n < 10 then return "src-00" + n: string;
    if n < 100 then return "src-0" + n: string;
    return "src-" + n: string;
  }

  /* cards_from_text (cli.py:234-277) over ALREADY-REDACTED text. The Python
   * function redacts internally; callers here pass redactText's output plus
   * whether it produced findings, so redaction runs exactly once per doc. */
  proc cardsFromText(source: string, redacted: string, docHasRedactions: bool,
                     digest: string, trustTier: string,
                     maxCards: int): list(Card) throws {
    const lines = splitLinesPy(redacted);
    var cards = new list(Card);

    var candidates = new list(int);
    for j in 0..<lines.size {
      const stripped = stripPy(lines[j]);
      if stripped.size == 0 then continue;
      if hasCritical(stripped) || hasUrl(stripped) ||
         stripped.startsWith("-") || stripped.startsWith("*") ||
         stripped.startsWith("#") then
        candidates.pushBack(j);
    }
    if candidates.size == 0 {
      for j in 0..<lines.size {
        if candidates.size >= maxCards then break;
        if stripPy(lines[j]).size > 0 then candidates.pushBack(j);
      }
    }

    var seen = new set(string);
    for j in candidates {
      if cards.size >= maxCards then break;
      const claim = cleanClaim(lines[j]);
      if claim.size == 0 || seen.contains(claim) then continue;
      seen.add(claim);
      const (ls, le, evidence) = lineExcerpt(lines, j);
      const confidence =
        if hasCritical(claim) || hasUrl(claim) then "medium" else "low";
      cards.pushBack(new Card(cardId(cards.size + 1), source, trustTier,
                              digest, ls, le, claim, evidence, confidence,
                              flagsFor(evidence, docHasRedactions)));
    }
    return cards;
  }

  private proc hexDigit(v: int(32)): string {
    select v {
      when 10 do return "a";
      when 11 do return "b";
      when 12 do return "c";
      when 13 do return "d";
      when 14 do return "e";
      when 15 do return "f";
      otherwise return (v: int): string;
    }
  }

  /* Python json.dumps(str, ensure_ascii=False) string-body escaping: quote,
   * backslash, the two-char escapes for \n \r \t \b \f, \u00xx for the rest
   * of C0; everything >= 0x20 (including 0x7f and non-ASCII) passes raw. */
  proc escapeJson(const ref s: string): string {
    var acc: string;
    for (cp, item) in zip(s.codepoints(), s.items()) {
      if cp == 0x22 then acc += "\\\"";
      else if cp == 0x5C then acc += "\\\\";
      else if cp == 0x0A then acc += "\\n";
      else if cp == 0x0D then acc += "\\r";
      else if cp == 0x09 then acc += "\\t";
      else if cp == 0x08 then acc += "\\b";
      else if cp == 0x0C then acc += "\\f";
      else if cp < 0x20 then
        acc += "\\u00" + hexDigit(cp >> 4) + hexDigit(cp & 0xF);
      else acc += item;
    }
    return acc;
  }

  /* One source-cards.jsonl object, byte-identical to Python's
   * json.dumps(card.as_dict(), ensure_ascii=False, sort_keys=True): keys in
   * alphabetical order, ", " and ": " separators. flags entries are fixed
   * [a-z-] tokens (no escaping needed). */
  proc cardJson(const ref c: Card): string {
    var flagsJson = "[";
    var first = true;
    for f in c.flags {
      if !first then flagsJson += ", ";
      flagsJson += '"' + f + '"';
      first = false;
    }
    flagsJson += "]";
    return '{"claim": "' + escapeJson(c.claim) +
           '", "confidence": "' + c.confidence +
           '", "evidence": "' + escapeJson(c.evidence) +
           '", "flags": ' + flagsJson +
           ', "id": "' + c.id +
           '", "line_end": ' + c.lineEnd: string +
           ', "line_start": ' + c.lineStart: string +
           ', "sha256": "' + c.sha +
           '", "source": "' + escapeJson(c.source) +
           '", "trust_tier": "' + escapeJson(c.trustTier) + '"}';
  }
}
