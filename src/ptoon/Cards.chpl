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
 *     Python Unicode edge revalidation). URL_RE has no \b: plain RE2 search.
 *   - Python str.strip()/rstrip() strip Unicode whitespace; inside a line
 *     post-normalization the only survivors are space and \t (NFKC folds the
 *     exotic spaces, CONTROL_RE strips the rest), and evidence joins add \n.
 *   - clean_claim truncation counts CODEPOINTS (Python len/slice), not bytes.
 *
 * Cards read the REDACTED text (redaction precedes card extraction, INV-2);
 * the sha256 field carries the digest of the RAW input bytes (INV-1). */
module Cards {
  use Regex, List, Set;
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

  private proc hasUrl(const ref s: string): bool throws {
    return urlGen.search(s).matched;
  }
  private proc hasCritical(const ref s: string): bool throws {
    return pySearchBounded(s, criticalGen);
  }
  private proc hasInjection(const ref s: string): bool throws {
    return pySearchBounded(s, injectionGen);
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

  /* clean_claim (cli.py:215-220): strip, drop the ^[-*#>\s0-9.]+ marker
   * prefix, strip again, truncate to 220 codepoints with a "..." tail. */
  private proc cleanClaim(const ref line: string): string throws {
    var s = line.strip(" \t");
    var drop = 0;
    for (cp, item) in zip(s.codepoints(), s.items()) {
      if cp == 0x2D || cp == 0x2A || cp == 0x23 || cp == 0x3E ||
         cp == 0x20 || cp == 0x09 || cp == 0x2E ||
         (cp >= 0x30 && cp <= 0x39) {
        drop += item.numBytes;
      } else {
        break;
      }
    }
    if drop > 0 then s = s.this((drop: byteIndex)..);
    s = s.strip(" \t");
    if s.size > 220 {
      var acc: string;
      var count = 0;
      for item in s.items() {
        if count == 219 then break;
        acc += item;
        count += 1;
      }
      return acc.strip(" \t", leading=false, trailing=true) + "...";
    }
    return s;
  }

  /* line_excerpt (cli.py:209-212), radius 1: 1-based inclusive line span +
   * the joined excerpt stripped Python-style (leading/trailing \n included). */
  private proc lineExcerpt(const ref lines: list(string),
                           idx: int): (int, int, string) {
    const lo = max(0, idx - 1);
    const hi = min(lines.size, idx + 2);
    var ev: string;
    for j in lo..<hi {
      if j > lo then ev += "\n";
      ev += lines[j];
    }
    return (lo + 1, hi, ev.strip(" \t\n"));
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
      const stripped = lines[j].strip(" \t");
      if stripped.size == 0 then continue;
      if hasCritical(stripped) || hasUrl(stripped) ||
         stripped.startsWith("-") || stripped.startsWith("*") ||
         stripped.startsWith("#") then
        candidates.pushBack(j);
    }
    if candidates.size == 0 {
      for j in 0..<lines.size {
        if candidates.size >= maxCards then break;
        if lines[j].strip(" \t").size > 0 then candidates.pushBack(j);
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
