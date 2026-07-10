#include "sha256_ffi.h"
#include "sha256.h"

void pt_sha256_hex(const char *data, size_t len, char *out_hex) {
    SHA256_CTX ctx;
    BYTE hash[32];
    static const char digits[] = "0123456789abcdef";
    sha256_init(&ctx);
    sha256_update(&ctx, (const BYTE *)data, len);
    sha256_final(&ctx, hash);
    for (int i = 0; i < 32; i++) {
        out_hex[2 * i] = digits[hash[i] >> 4];
        out_hex[2 * i + 1] = digits[hash[i] & 0x0F];
    }
    out_hex[64] = '\0';
}
