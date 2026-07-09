/* TIN-2708 C1: libptoon module shell.
 *
 * This is the build-lane skeleton only — it proves the `chpl --library`
 * toolchain end-to-end (remote-only, per AGENTS.md / operator doctrine
 * 2026-07-09) and fixes the six-symbol C ABI surface behind
 * `--engine=chapel`. It carries NO redaction/normalize/defang/encode
 * semantics; every text-transform export below returns
 * PTOON_ERR_UNIMPLEMENTED until the opus lane ports the real logic from
 * prompt_toon/cli.py (see spikes/tin-2707/ptoon_spike.chpl for the proven
 * C0 parity port that Phase 2 graduates into this module).
 *
 * Ownership contract ("Keychain ownership contract", mirrors the
 * pt_nfkc/pt_free pattern in c_src/normalize_ffi.c): every `outBuf` /
 * `findingsBuf` written by an export proc here is malloc-owned by the
 * callee (this library). The caller MUST release it via ptoon_free and
 * MUST NOT call libc free() directly on it, and MUST NOT free the same
 * pointer twice. A nil buffer is always safe to pass to ptoon_free.
 *
 * Fail-open contract (INV-5, docs/mythos-delivery-design.md §7 C1 entry):
 * any negative return code is a signal to the ctypes wrapper (future
 * work, not this skeleton) to fail OPEN to the Python engine — never to
 * treat a negative-code call's outBuf as a partial or trustworthy result.
 */
module Ptoon {
  use CTypes;

  require "c_src/normalize_ffi.h", "c_src/normalize_ffi.c", "c_src/utf8proc.c";
  require "c_src/ptoon_alloc.h", "c_src/ptoon_alloc.c";

  /* pt_nfkc/pt_free/pt_has_utf8proc/pt_unicode_version come from the
   * vendored utf8proc shim (c_src/normalize_ffi.*, copied verbatim from
   * the TIN-2707 C0 spike — infra, not redaction logic). Phase 2 wires
   * pt_nfkc into ptoon_normalize/ptoon_redact the same way
   * spikes/tin-2707/ptoon_spike.chpl already does. */
  extern proc pt_nfkc(inBuf: c_ptrConst(c_char), inlen: c_long,
                       ref outBuf: c_ptr(c_char), ref outlen: c_long): c_int;
  extern proc pt_free(p: c_ptr(void)): void;
  extern proc pt_has_utf8proc(): c_int;
  extern proc pt_unicode_version(): c_ptrConst(c_char);

  /* pt_alloc_cstring is generic malloc-copy plumbing (c_src/ptoon_alloc.*)
   * used to hand Chapel-built strings back across the C ABI boundary. */
  extern proc pt_alloc_cstring(src: c_ptrConst(c_char), len: c_long): c_ptr(c_char);

  /* Status codes returned by every ptoon_* entry point below. Zero is
   * success; every negative value is a sentinel the C ABI caller must
   * treat as fail-open-to-Python (INV-5) — never as a usable result. */
  param PTOON_OK = 0;
  param PTOON_ERR_UNIMPLEMENTED = -1;
  param PTOON_ERR_INVALID_ARG = -2;
  param PTOON_ERR_ALLOC_FAILED = -3;

  /* Baked at compile time by the nix derivation (flake.nix `libptoon`).
   * Defaults are placeholders so the skeleton is self-contained without
   * requiring `-s` overrides; Phase 2/packaging may thread real values
   * through `-s` if that proves worth the extra build-graph coupling. */
  config const ptoonEngineVersion = "0.0.0-c1-skeleton";
  config const ptoonGitRev = "unknown";

  /* normalize_text port (prompt_toon/cli.py:119-126): CRLF fold + NFKC
   * (via pt_nfkc) + confusable fold + control/zero-width/bidi/tag strip.
   * STUB — signature only, no semantics. See ptoon_spike.chpl for the
   * proven C0 port this graduates from. */
  export proc ptoon_normalize(inBuf: c_ptrConst(c_char), inLen: c_long,
                               ref outBuf: c_ptr(c_char), ref outLen: c_long): c_int {
    outBuf = nil;
    outLen = 0;
    return PTOON_ERR_UNIMPLEMENTED;
  }

  /* redact_text port (prompt_toon/cli.py:138-145): SECRET_PATTERNS scan +
   * replace. findingsBuf mirrors the comma-joined "pattern-N" list from
   * ptoon_spike.chpl's "findings:" line (a malloc'd string, empty string
   * is a valid "no findings" result — distinct from nil/error). STUB. */
  export proc ptoon_redact(inBuf: c_ptrConst(c_char), inLen: c_long,
                            ref outBuf: c_ptr(c_char), ref outLen: c_long,
                            ref findingsBuf: c_ptr(c_char), ref findingsLen: c_long): c_int {
    outBuf = nil;
    outLen = 0;
    findingsBuf = nil;
    findingsLen = 0;
    return PTOON_ERR_UNIMPLEMENTED;
  }

  /* defang_text port (prompt_toon/cli.py:129-136, INV-4): neutralize
   * markdown image/link/data:/javascript: URIs. STUB. */
  export proc ptoon_defang(inBuf: c_ptrConst(c_char), inLen: c_long,
                            ref outBuf: c_ptr(c_char), ref outLen: c_long): c_int {
    outBuf = nil;
    outLen = 0;
    return PTOON_ERR_UNIMPLEMENTED;
  }

  /* encode_rows_to_toon port (prompt_toon/cli.py:303-316): flat
   * uniform-row TOON encoder. inBuf is a compact-JSON row array;
   * `delimiter` mirrors the Python default ('\t'). STUB. */
  export proc ptoon_toon_encode(inBuf: c_ptrConst(c_char), inLen: c_long,
                                 delimiter: c_char,
                                 ref outBuf: c_ptr(c_char), ref outLen: c_long): c_int {
    outBuf = nil;
    outLen = 0;
    return PTOON_ERR_UNIMPLEMENTED;
  }

  /* Release any buffer returned via an outBuf/findingsBuf ref param above.
   * Safe to call with nil. This is the ONLY sanctioned release path for
   * Ptoon buffers (Keychain ownership contract) — do not call libc
   * free() directly from a caller. */
  export proc ptoon_free(p: c_ptr(void)): void {
    pt_free(p);
  }

  /* Engine capability probe. Unlike the four text-transform exports above,
   * this is real (not a sentinel stub): it reports whether the vendored
   * utf8proc shim linked successfully, proving the build lane end-to-end
   * without touching any redaction/normalize semantics. `patterns` stays
   * an empty array in the skeleton — SECRET_PATTERNS is Python/opus-owned
   * and must never be hand-duplicated here; Phase 2 populates it from the
   * real table once ptoon_redact graduates past PTOON_ERR_UNIMPLEMENTED.
   *
   * Returns a malloc-owned JSON string; caller frees via ptoon_free. */
  export proc ptoon_engine_caps(ref outBuf: c_ptr(c_char), ref outLen: c_long): c_int {
    const utf8procLinked = if pt_has_utf8proc() != 0 then "true" else "false";
    const json = '{"utf8proc":' + utf8procLinked +
                 ',"patterns":[]' +
                 ',"chapel_version":"' + ptoonEngineVersion + '"' +
                 ',"git_rev":"' + ptoonGitRev + '"' +
                 ',"status":"skeleton-c1"}';
    const nbytes = json.numBytes: c_long;
    outBuf = pt_alloc_cstring(json.c_str(), nbytes);
    if outBuf == nil {
      outLen = 0;
      return PTOON_ERR_ALLOC_FAILED;
    }
    outLen = nbytes;
    return PTOON_OK;
  }
}
