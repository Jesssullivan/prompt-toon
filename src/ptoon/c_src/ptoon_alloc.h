/* TIN-2708 C1: generic malloc-owned buffer helper for the libptoon C ABI.
 *
 * Keychain ownership contract (mirrors spikes/tin-2707's pt_nfkc/pt_free
 * pattern): every buffer handed back across the ptoon_* export boundary is
 * malloc-owned by the callee (this library) and MUST be released by the
 * caller via ptoon_free (Ptoon.chpl), which forwards to pt_free below.
 * Never call libc free() directly on a Ptoon buffer from the caller side.
 *
 * This is plumbing only — no redaction/normalize/defang semantics live
 * here. It exists so Chapel string/bytes literals produced inside the
 * library (e.g. ptoon_engine_caps' JSON) can be copied into a malloc'd,
 * C-ABI-safe buffer the same way utf8proc's own allocator output is
 * threaded back through pt_free.
 */
#ifndef PTOON_ALLOC_H
#define PTOON_ALLOC_H

/* Copy `len` bytes from `src` into a freshly malloc'd buffer and return it.
 * Returns NULL on allocation failure (caller must treat NULL as
 * PTOON_ERR_ALLOC_FAILED, never dereference). `len` is byte-explicit, not
 * NUL-terminated-implied — callers that want a C string must include the
 * trailing NUL byte in `len` themselves if they need one. */
char *pt_alloc_cstring(const char *src, long len);

#endif
