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
