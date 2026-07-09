/* TIN-2707 C0 spike: normalize_text + redact_text ported to Chapel.
 *
 * Parity spec is prompt_toon/cli.py (normalize_text lines 119-126,
 * redact_text 138-145, SECRET_PATTERNS 21-31, CONFUSABLES 42-52).
 * Output protocol (mirrored by py_driver.py): one "findings:..." line,
 * then the redacted text verbatim.
 *
 * NFKC comes from vendored utf8proc v2.9.0 (Unicode 15.1, matching the
 * devshell CPython 3.13) via the remote-juggler Keychain.chpl FFI pattern.
 * Chapel regex is RE2: linear-time, ASCII \b — the parity corpus measures
 * the \b divergence explicitly (see README).
 */
use IO, Regex, Map, CTypes;

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

proc main() throws {
  const raw = stdin.readAll(bytes);
  var redacted = normalizeText(raw);

  var findings: string;
  var first = true;
  for i in secretPatterns.domain {
    const re = new regex(secretPatterns[i]);
    const (replaced, n) = redacted.replaceAndCount(re, "[REDACTED]");
    if n > 0 {
      if !first then findings += ",";
      findings += "pattern-" + (i + 1): string;
      first = false;
      redacted = replaced;
    }
  }

  stdout.write("findings:", findings, "\n");
  stdout.write(redacted);
}
