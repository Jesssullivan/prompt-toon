/* TIN-2708 C1: normalize_text port (parity spec: prompt_toon/cli.py:119-126).
 *
 * NFKC via vendored utf8proc (c_src/, Unicode 15.1 = CPython 3.13), then a
 * single codepoint pass stripping zero-width/BIDI/tag/control and folding
 * the 45-pair confusable table — semantically identical to Python's ordered
 * regex subs because the sets are disjoint and fold outputs are ASCII.
 * Failure throws; text is never passed through un-normalized (INV-2).
 */
module Normalize {
  use CTypes, Map;

  // File-relative per the remote-juggler Keychain.chpl exemplar: from
  // src/ptoon/ up two levels to the repo-root c_src/.
  require "../../c_src/normalize_ffi.h", "../../c_src/normalize_ffi.c",
          "../../c_src/utf8proc.c";

  extern proc pt_nfkc(inBuf: c_ptrConst(c_char), inlen: c_long,
                      ref outBuf: c_ptr(c_char), ref outlen: c_long): c_int;
  extern proc pt_free(p: c_ptr(void));
  extern proc pt_is_word(codepoint: c_int): c_int;
  extern proc pt_unicode_version(): c_ptrConst(c_char);

  /* Exactly CONTROL_RE / ZERO_WIDTH_RE / BIDI_RE / TAG_RE from cli.py. */
  inline proc isStripped(cp: int(32)): bool {
    if cp <= 0x08 then return true;
    if cp == 0x0B || cp == 0x0C then return true;
    if cp >= 0x0E && cp <= 0x1F then return true;
    if cp == 0x7F then return true;
    if cp == 0x00AD || cp == 0xFEFF then return true;
    if cp >= 0x200B && cp <= 0x200D then return true;
    if cp >= 0x2060 && cp <= 0x2064 then return true;
    if cp == 0x061C then return true;
    if cp == 0x200E || cp == 0x200F then return true;
    if cp >= 0x202A && cp <= 0x202E then return true;
    if cp >= 0x2066 && cp <= 0x2069 then return true;
    if cp >= 0xE0000 && cp <= 0xE007F then return true;
    return false;
  }

  /* CONFUSABLES, generated from the Python table (45 pairs, sorted by
   * codepoint) — never hand-transcribed; see spikes/tin-2707/gen_corpus.py
   * sibling logic. */
  const confusablesFrom = "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧνοЅІЈАВЕКМНОРСТУХаеорсухѕіјғԁԛԝ";
  const confusablesTo = "ABEZHIKMNOPTYXvoSIJABEKMHOPCTYXaeopcyxsijfdqw";

  private proc buildConfusables(): map(int(32), int(32)) {
    use Map;
    var m = new map(int(32), int(32));
    const src = [cp in confusablesFrom.codepoints()] cp;
    const dst = [cp in confusablesTo.codepoints()] cp;
    assert(src.size == dst.size);
    for i in src.domain do m.add(src[i], dst[i]);
    return m;
  }

  private const confusables = buildConfusables();

  inline proc emitUtf8(cp: int(32), ref buf: [] uint(8), ref k: int) {
    if cp < 0x80 {
      buf[k] = cp: uint(8); k += 1;
    } else if cp < 0x800 {
      buf[k] = (0xC0 | (cp >> 6)): uint(8);
      buf[k + 1] = (0x80 | (cp & 0x3F)): uint(8); k += 2;
    } else if cp < 0x10000 {
      buf[k] = (0xE0 | (cp >> 12)): uint(8);
      buf[k + 1] = (0x80 | ((cp >> 6) & 0x3F)): uint(8);
      buf[k + 2] = (0x80 | (cp & 0x3F)): uint(8); k += 3;
    } else {
      buf[k] = (0xF0 | (cp >> 18)): uint(8);
      buf[k + 1] = (0x80 | ((cp >> 12) & 0x3F)): uint(8);
      buf[k + 2] = (0x80 | ((cp >> 6) & 0x3F)): uint(8);
      buf[k + 3] = (0x80 | (cp & 0x3F)): uint(8); k += 4;
    }
  }

  /* Python's \w for str: isalnum (letter/number categories via utf8proc)
   * or underscore. */
  inline proc isWordCp(cp: int(32)): bool {
    if cp == 0x5F then return true;
    return pt_is_word(cp: c_int) != 0;
  }

  proc normalizeText(text: bytes): string throws {
    // 1. CRLF fold, matching Python's replace order (cli.py:120).
    var folded = text.replace(b"\r\n", b"\n").replace(b"\r", b"\n");

    // 2. NFKC via utf8proc; failure throws (INV-2 fail-closed).
    var outPtr: c_ptr(c_char);
    var outLen: c_long;
    const rc = pt_nfkc(folded.c_str(), folded.size: c_long, outPtr, outLen);
    if rc != 0 then throw new Error("utf8proc NFKC failed rc=" + rc: string);
    const nfkc = bytes.createCopyingBuffer(outPtr: c_ptrConst(c_char), outLen: int);
    pt_free(outPtr: c_ptr(void));

    // 3. Single pass: strip + confusable fold (cli.py:122-126).
    const s = nfkc.decode();
    var buf: [0..<(nfkc.size * 4 + 4)] uint(8);
    var k = 0;
    for cp in s.codepoints() {
      var c = cp: int(32);
      if isStripped(c) then continue;
      // C2 ASCII fast-path: every confusable key is Greek/Cyrillic (>= U+0391),
      // so an ASCII codepoint can never be in the table — skip the map probe
      // for the overwhelmingly common c < 0x80 case. Pure perf; the guarded
      // branch is byte-identical to the unguarded lookup.
      if c >= 0x80 && confusables.contains(c) then c = confusables[c];
      emitUtf8(c, buf, k);
    }
    return bytes.createCopyingBuffer(c_ptrTo(buf[0]): c_ptrConst(c_char), k).decode();
  }

  proc normalizeText(text: string): string throws {
    return normalizeText(text.encode());
  }

  proc unicodeVersion(): string {
    // utf8proc's version string is always ASCII, so decode cannot fail.
    return try! string.createCopyingBuffer(pt_unicode_version());
  }
}
