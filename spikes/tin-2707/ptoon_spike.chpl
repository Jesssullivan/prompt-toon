/* TIN-2707 C0 spike: normalize_text + redact_text ported to Chapel.
 *
 * Parity spec is prompt_toon/cli.py (normalize_text lines 119-126,
 * redact_text 138-145, SECRET_PATTERNS 21-31, CONFUSABLES 42-52).
 * Output protocol (mirrored by py_driver.py): one "findings:..." line,
 * then the redacted text verbatim.
 *
 * NFKC comes from vendored utf8proc v2.9.0 (Unicode 15.1, matching the
 * devshell CPython 3.13) via the remote-juggler Keychain.chpl FFI pattern.
 * Chapel regex is RE2: linear-time. RE2 \b is ASCII-only while Python re \b
 * is Unicode-aware. TIN-2708 C1 closes that divergence (see README):
 * because RE2's word class ([A-Za-z0-9_]) is a strict subset of Python's
 * (\p{L}\p{N}_), RE2 can only ever see *extra* boundaries that Python does
 * not — never fewer. So the fix keeps the 9 SECRET_PATTERNS verbatim (they
 * are Python's exact zero-width \b matcher, which also reproduces the
 * trailing-separator consumption of pattern-9/1/2/4/5 that a consuming
 * anchor could not) and post-filters each match: a match anchored on a
 * secret-adjacent neighbor that is a non-ASCII Unicode letter/number
 * ([\p{L}\p{N}]) is one Python would reject, so it is dropped. ASCII-only
 * text takes the fast replaceAndCount path (RE2 \b == Python \b there).
 */
use IO, Regex, Map, CTypes, List;

require "c_src/normalize_ffi.h", "c_src/normalize_ffi.c", "c_src/utf8proc.c";

extern proc pt_nfkc(inBuf: c_ptrConst(c_char), inlen: c_long,
                    ref outBuf: c_ptr(c_char), ref outlen: c_long): c_int;
extern proc pt_free(p: c_ptr(void));

/* Exactly CONTROL_RE / ZERO_WIDTH_RE / BIDI_RE / TAG_RE from cli.py. */
inline proc isStripped(cp: int(32)): bool {
  if cp <= 0x08 then return true;                    // \x00-\x08
  if cp == 0x0B || cp == 0x0C then return true;      // \x0b \x0c
  if cp >= 0x0E && cp <= 0x1F then return true;      // \x0e-\x1f
  if cp == 0x7F then return true;                    // \x7f
  if cp == 0x00AD || cp == 0xFEFF then return true;  // soft hyphen, BOM
  if cp >= 0x200B && cp <= 0x200D then return true;  // zero-width
  if cp >= 0x2060 && cp <= 0x2064 then return true;  // word-joiner block
  if cp == 0x061C then return true;                  // arabic letter mark
  if cp == 0x200E || cp == 0x200F then return true;  // LRM/RLM
  if cp >= 0x202A && cp <= 0x202E then return true;  // bidi embedding
  if cp >= 0x2066 && cp <= 0x2069 then return true;  // bidi isolates
  if cp >= 0xE0000 && cp <= 0xE007F then return true; // tag block
  return false;
}

/* CONFUSABLES, generated from the Python table (45 pairs, sorted). */
const fromChars = "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧνοЅІЈАВЕКМНОРСТУХаеорсухѕіјғԁԛԝ";
const toChars = "ABEZHIKMNOPTYXvoSIJABEKMHOPCTYXaeopcyxsijfdqw";

proc buildConfusables(): map(int(32), int(32)) {
  var m = new map(int(32), int(32));
  const src = [cp in fromChars.codepoints()] cp;
  const dst = [cp in toChars.codepoints()] cp;
  assert(src.size == dst.size);
  for i in src.domain do m.add(src[i], dst[i]);
  return m;
}

/* Append a codepoint as UTF-8 to a preallocated byte buffer. */
inline proc emitUtf8(cp: int(32), ref buf: [] uint(8), ref k: int) {
  if cp < 0x80 {
    buf[k] = cp:uint(8); k += 1;
  } else if cp < 0x800 {
    buf[k] = (0xC0 | (cp >> 6)):uint(8);
    buf[k+1] = (0x80 | (cp & 0x3F)):uint(8); k += 2;
  } else if cp < 0x10000 {
    buf[k] = (0xE0 | (cp >> 12)):uint(8);
    buf[k+1] = (0x80 | ((cp >> 6) & 0x3F)):uint(8);
    buf[k+2] = (0x80 | (cp & 0x3F)):uint(8); k += 3;
  } else {
    buf[k] = (0xF0 | (cp >> 18)):uint(8);
    buf[k+1] = (0x80 | ((cp >> 12) & 0x3F)):uint(8);
    buf[k+2] = (0x80 | ((cp >> 6) & 0x3F)):uint(8);
    buf[k+3] = (0x80 | (cp & 0x3F)):uint(8); k += 4;
  }
}

proc normalizeText(raw: bytes): string throws {
  // 1. CRLF fold, matching Python's replace order.
  var folded = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n");

  // 2. NFKC via utf8proc (length-explicit; failure = abort, never passthrough).
  var outPtr: c_ptr(c_char);
  var outLen: c_long;
  const rc = pt_nfkc(folded.c_str(), folded.size: c_long, outPtr, outLen);
  if rc != 0 then throw new Error("utf8proc NFKC failed rc=" + rc:string);
  const nfkc = bytes.createCopyingBuffer(outPtr: c_ptrConst(c_char), outLen: int);
  pt_free(outPtr: c_ptr(void));

  // 3. Single pass: strip zero-width/bidi/tag/control, fold confusables.
  const s = nfkc.decode();
  const confusables = buildConfusables();
  var buf: [0..<(nfkc.size * 4 + 4)] uint(8);
  var k = 0;
  for cp in s.codepoints() {
    var c = cp: int(32);
    if isStripped(c) then continue;
    if confusables.contains(c) then c = confusables[c];
    emitUtf8(c, buf, k);
  }
  return bytes.createCopyingBuffer(c_ptrTo(buf[0]): c_ptrConst(c_char), k).decode();
}

/* SECRET_PATTERNS 1-9, ported verbatim (RE2). */
const secretPatterns = [
  "\\b(?:sk|ghp|gho|github_pat|xox[baprs])-[-_A-Za-z0-9]{16,}\\b",
  "\\b(?:sk|ghp|gho|github_pat)_[-_A-Za-z0-9]{16,}\\b",
  "\\b(?:AKIA|ASIA)[0-9A-Z]{16}\\b",
  "\\bAIza[0-9A-Za-z_-]{35}\\b",
  "\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b",
  "-----BEGIN[A-Z ]*PRIVATE KEY-----[\\s\\S]+?-----END[A-Z ]*PRIVATE KEY-----",
  "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b",
  "(?i)\\b(api[_-]?key|token|password|secret)\\s*[:=]\\s*['\"]?[^'\"\\s]{8,}",
  "\\b(?:\\d[ -]?){13,19}\\b",
];

/* Which patterns carry a leading / trailing \b (aligned to secretPatterns).
   Pattern 6 (PEM) has neither; pattern 8 (api_key=...) has only a leading \b. */
const leadAnchored  = [true, true, true, true, true, false, true, true,  true];
const trailAnchored = [true, true, true, true, true, false, true, false, true];

/* True if any byte of s is non-ASCII. RE2 \b and Python \b agree on ASCII-only
   text, so pure-ASCII input skips the boundary filter entirely. Redaction only
   removes bytes and inserts the ASCII marker, so this is stable across the
   pattern loop — compute it once. */
proc containsNonAscii(s: string): bool {
  for i in 0..<s.numBytes do
    if s.byte(i):int >= 0x80 then return true;
  return false;
}

/* Is the single codepoint occupying inclusive byte range [a, b] a non-ASCII
   Unicode word char (letter or number)? ASCII neighbors are never treated as a
   Unicode-only boundary here: RE2's ASCII \b already accounted for them, so the
   sole divergence from Python is a non-ASCII \p{L}/\p{N} neighbor. */
proc neighborIsUnicodeWord(s: string, a: int, b: int,
                           const ref wordRe: regex(string)): bool throws {
  if s.byte(a):int < 0x80 then return false;      // ASCII lead byte
  const ch = s[(a:byteIndex)..(b:byteIndex)];
  return wordRe.search(ch).matched;
}

/* The codepoint immediately before byte offset `start`. */
proc leadingNeighborWord(s: string, start: int,
                         const ref wordRe: regex(string)): bool throws {
  if start <= 0 then return false;
  var j = start - 1;                                // walk back over UTF-8 tail bytes
  while j > 0 && (s.byte(j):int & 0xC0) == 0x80 do j -= 1;
  return neighborIsUnicodeWord(s, j, start - 1, wordRe);
}

/* The codepoint immediately after byte offset `stop`. */
proc trailingNeighborWord(s: string, stop: int,
                          const ref wordRe: regex(string)): bool throws {
  if stop >= s.numBytes then return false;
  const b0 = s.byte(stop):int;
  if b0 < 0x80 then return false;
  const len = if b0 < 0xE0 then 2 else if b0 < 0xF0 then 3 else 4;
  return neighborIsUnicodeWord(s, stop, stop + len - 1, wordRe);
}

/* Redact `text` for one pattern with Unicode-boundary filtering. Enumerates the
   same non-overlapping matches Python's re.sub would (RE2's \b is zero-width and
   its match set is a superset of Python's), drops any whose leading/trailing
   boundary rests on a non-ASCII Unicode word char, and splices [REDACTED] over
   the survivors via a single byte buffer (O(n), preserves the fast path's
   throughput characteristics). Returns (newText, anyRedacted). */
proc redactFiltered(text: string, const ref re: regex(string),
                    lead: bool, trail: bool,
                    const ref wordRe: regex(string)): (string, bool) throws {
  var starts: list(int);
  var stops: list(int);
  var matchedLen = 0;
  for m in re.matches(text) {
    const fm = m[0];
    if !fm.matched then break;
    const s = fm.byteOffset: int;
    const e = s + fm.numBytes;
    var keep = true;
    if lead && leadingNeighborWord(text, s, wordRe) then keep = false;
    if keep && trail && trailingNeighborWord(text, e, wordRe) then keep = false;
    if keep {
      starts.pushBack(s);
      stops.pushBack(e);
      matchedLen += (e - s);
    }
  }
  if starts.size == 0 then return (text, false);

  const marker = "[REDACTED]";
  const mlen = marker.numBytes;
  const outLen = text.numBytes - matchedLen + starts.size * mlen;
  var buf: [0..<outLen] uint(8);
  var k = 0;
  var emitted = 0;
  for i in 0..<starts.size {
    for bi in emitted..<starts[i] { buf[k] = text.byte(bi); k += 1; }
    for mi in 0..<mlen { buf[k] = marker.byte(mi); k += 1; }
    emitted = stops[i];
  }
  for bi in emitted..<text.numBytes { buf[k] = text.byte(bi); k += 1; }
  return (bytes.createCopyingBuffer(c_ptrTo(buf[0]): c_ptrConst(c_char), k).decode(),
          true);
}

proc main() throws {
  const raw = stdin.readAll(bytes);
  var redacted = normalizeText(raw);

  const nonAscii = containsNonAscii(redacted);
  const wordRe = new regex("[\\p{L}\\p{N}]");

  var findings: string;
  var first = true;
  for i in secretPatterns.domain {
    const re = new regex(secretPatterns[i]);
    var matched = false;
    if nonAscii {
      const (replaced, any) =
        redactFiltered(redacted, re, leadAnchored[i], trailAnchored[i], wordRe);
      if any { redacted = replaced; matched = true; }
    } else {
      const (replaced, n) = redacted.replaceAndCount(re, "[REDACTED]");
      if n > 0 { redacted = replaced; matched = true; }
    }
    if matched {
      if !first then findings += ",";
      findings += "pattern-" + (i + 1): string;
      first = false;
    }
  }

  stdout.write("findings:", findings, "\n");
  stdout.write(redacted);
}
