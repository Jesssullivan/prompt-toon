#include "ptoon_alloc.h"

#include <stdlib.h>
#include <string.h>

char *pt_alloc_cstring(const char *src, long len) {
    if (len < 0) return NULL;
    char *buf = (char *)malloc((size_t)len);
    if (buf == NULL) return NULL;
    if (len > 0) memcpy(buf, src, (size_t)len);
    return buf;
}
