/* NFKC normalization shim over vendored utf8proc (TIN-2707, INV-2).
 *
 * Length-explicit (no NULLTERM): inputs may contain NUL bytes, which the
 * pipeline strips only AFTER NFKC, matching Python's normalize_text order.
 * Caller frees *out with pt_free. Returns 0 on success, nonzero on error —
 * callers must treat failure as UNSUPPORTED and fail over to the Python
 * engine (never silently pass text through un-normalized).
 */
#ifndef PT_NORMALIZE_FFI_H
#define PT_NORMALIZE_FFI_H

#include <stddef.h>

int pt_nfkc(const char *in, long inlen, char **out, long *outlen);
void pt_free(void *p);
int pt_has_utf8proc(void);
const char *pt_unicode_version(void);

#endif
