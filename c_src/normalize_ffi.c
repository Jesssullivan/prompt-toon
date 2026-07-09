#include "normalize_ffi.h"
#include "utf8proc.h"

#include <stdlib.h>

/* NFKC = COMPOSE | COMPAT; STABLE matches utf8proc's own NFKC convenience
 * wrapper. No NULLTERM: length-explicit so embedded NULs survive to the
 * control-strip stage, matching Python str semantics. */
int pt_nfkc(const char *in, long inlen, char **out, long *outlen) {
    utf8proc_uint8_t *dst = NULL;
    utf8proc_ssize_t n = utf8proc_map(
        (const utf8proc_uint8_t *)in, (utf8proc_ssize_t)inlen, &dst,
        UTF8PROC_STABLE | UTF8PROC_COMPOSE | UTF8PROC_COMPAT);
    if (n < 0) {
        *out = NULL;
        *outlen = 0;
        return (int)n;
    }
    *out = (char *)dst;
    *outlen = (long)n;
    return 0;
}

void pt_free(void *p) { free(p); }

int pt_has_utf8proc(void) { return 1; }

const char *pt_unicode_version(void) { return utf8proc_unicode_version(); }

/* CPython re \w for str = characters where str.isalnum() is true, plus '_'
 * (underscore is the caller's job). isalnum = categories Lu Ll Lt Lm Lo,
 * Nd Nl No. */
int pt_is_word(int codepoint) {
    if (codepoint < 0 || codepoint > 0x10FFFF) return 0;
    utf8proc_category_t c = utf8proc_category((utf8proc_int32_t)codepoint);
    switch (c) {
    case UTF8PROC_CATEGORY_LU:
    case UTF8PROC_CATEGORY_LL:
    case UTF8PROC_CATEGORY_LT:
    case UTF8PROC_CATEGORY_LM:
    case UTF8PROC_CATEGORY_LO:
    case UTF8PROC_CATEGORY_ND:
    case UTF8PROC_CATEGORY_NL:
    case UTF8PROC_CATEGORY_NO:
        return 1;
    default:
        return 0;
    }
}
