/* TIN-2708 C1: defang_text port (parity spec: prompt_toon/cli.py:129-135,
 * INV-4). Neutralizes markdown image/link syntax and dangerous URI schemes
 * before model-facing emission, in exactly the Python sub/replace order:
 *
 *   1. MD_IMAGE_RE  -> "[defanged-image: NAME]" (NAME = group1 or "unnamed")
 *   2. MD_LINK_RE   -> "TEXT [defanged-link]"    (TEXT = group1)
 *   3. DANGEROUS_URI_RE -> lowercased-scheme + "-defanged:"
 *   4. "https://" -> "hxxps://", then "http://" -> "hxxp://"
 *   5. "`" -> "'"
 *
 * Regex-API notes (verified against chapel 2.7/2.8 sources,
 * modules/standard/Regex.chpl):
 *
 * - RE2 (Chapel's `Regex` module) cannot express the Python MD_LINK_RE
 *   negative lookbehind `(?<!!)` — RE2 has no lookaround. Because step 1
 *   (image substitution) always runs first and fully consumes any
 *   "![...](...)" span, no "![" prefix can remain in the text by the time
 *   step 2 runs, so the lookbehind is redundant here and is intentionally
 *   omitted: the pattern below is the plain
 *   `\[([^\]]*)\]\(([^)]+)\)` with no lookbehind.
 *
 * - Group-dependent replacement (defaulting an empty capture to "unnamed",
 *   lowercasing a capture) cannot be expressed as a `string.replaceAndCount`
 *   rewrite string (rewrite strings are literal `\1`-style backreference
 *   substitutions with no conditional/case-folding logic), so every
 *   substitution here is done by hand: `regex.matches(text, numCaptures=N)`
 *   walks non-overlapping matches with their byte offsets, and the result
 *   is built by splicing the literal text between matches together with a
 *   computed replacement for each match — mirroring the exact splice
 *   pattern `regex.split`/`regex.matches` use internally in the Chapel
 *   standard library (`text[last..<m.byteOffset]` / `text[last..]`).
 *
 * - `text[m]` for a `regexMatch m` produced against `text` returns the
 *   matched substring directly (empty string if the (sub-)match did not
 *   participate), per `proc string.this(m:regexMatch)` in Regex.chpl.
 */
module Defang {
  use Regex;

  // "!\[([^\]]*)\]\(([^)]+)\)" — Python MD_IMAGE_RE, unchanged.
  private const mdImageRe = try! new regex("!\\[([^\\]]*)\\]\\(([^)]+)\\)");

  // Python MD_LINK_RE minus the unexpressible `(?<!!)` lookbehind — see
  // module-header comment for why dropping it is safe here.
  private const mdLinkRe = try! new regex("\\[([^\\]]*)\\]\\(([^)]+)\\)");

  // "(?i)\b(javascript|vbscript|data):" — Python DANGEROUS_URI_RE, unchanged;
  // RE2 supports inline (?i) and ASCII \b directly.
  private const dangerousUriRe = try! new regex("(?i)\\b(javascript|vbscript|data):");

  /* [defanged-image: NAME] where NAME is capture group 1, or "unnamed" if
   * that capture was empty (Python: `m.group(1) or 'unnamed'`). */
  private proc defangImages(text: string): string {
    var acc = "";
    var last: byteIndex;
    last = 0;
    for (whole, g1, g2) in mdImageRe.matches(text, numCaptures=2) {
      try! {
        acc += text[last..<whole.byteOffset];
      }
      const captured = if g1.matched then text[g1] else "";
      const name = if captured.isEmpty() then "unnamed" else captured;
      acc += "[defanged-image: " + name + "]";
      last = whole.byteOffset + whole.numBytes;
    }
    try! {
      acc += text[last..];
    }
    return acc;
  }

  /* "TEXT [defanged-link]" where TEXT is capture group 1. Runs after
   * defangImages, so no "![" prefix survives to require the dropped
   * lookbehind (see module-header comment). */
  private proc defangLinks(text: string): string {
    var acc = "";
    var last: byteIndex;
    last = 0;
    for (whole, g1, g2) in mdLinkRe.matches(text, numCaptures=2) {
      try! {
        acc += text[last..<whole.byteOffset];
      }
      const captured = if g1.matched then text[g1] else "";
      acc += captured + " [defanged-link]";
      last = whole.byteOffset + whole.numBytes;
    }
    try! {
      acc += text[last..];
    }
    return acc;
  }

  /* lowercase(scheme) + "-defanged:" where scheme is capture group 1
   * (Python: `m.group(1).lower()`). */
  private proc defangDangerousUris(text: string): string {
    var acc = "";
    var last: byteIndex;
    last = 0;
    for (whole, g1) in dangerousUriRe.matches(text, numCaptures=1) {
      try! {
        acc += text[last..<whole.byteOffset];
      }
      const captured = if g1.matched then text[g1] else "";
      acc += captured.toLower() + "-defanged:";
      last = whole.byteOffset + whole.numBytes;
    }
    try! {
      acc += text[last..];
    }
    return acc;
  }

  /* Model-facing claim transform. Dynamic code-span fencing in Summary keeps
   * literal backticks inert, so this layer only neutralizes links and URIs. */
  proc defangClaimText(text: string): string {
    var result = text;
    result = defangImages(result);
    result = defangLinks(result);
    result = defangDangerousUris(result);
    result = result.replace("https://", "hxxps://");
    result = result.replace("http://", "hxxp://");
    return result;
  }

  /* Port of prompt_toon/cli.py defang_text. The standalone primitive retains
   * its byte-parity contract, including backtick replacement. */
  proc defangText(text: string): string {
    return defangClaimText(text).replace("`", "'");
  }
}
