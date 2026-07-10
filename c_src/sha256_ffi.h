/* TIN-2709 C2d: sha256 hex digest FFI shim for the ptoon binary.
 *
 * sha256.c/sha256.h are vendored verbatim from B-Con/crypto-algorithms
 * (Brad Conte, public domain, https://github.com/B-Con/crypto-algorithms),
 * the same vendoring pattern as utf8proc. Verified against NIST FIPS 180-2
 * vectors and Python hashlib before vendoring (abc / empty / fox digests).
 * One local patch: sha256_transform made `static` (internal helper; chpl's
 * backend compiles C with -Werror,-Wmissing-prototypes).
 *
 * One entry point: hash `len` bytes and write 64 lowercase hex chars plus a
 * NUL into out_hex (caller provides >= 65 bytes). Matches Python's
 * hashlib.sha256(data).hexdigest() byte-for-byte -- the provenance digest
 * INV-1 hangs card sha256 fields on.
 */
#ifndef PTOON_SHA256_FFI_H
#define PTOON_SHA256_FFI_H

#include <stddef.h>

void pt_sha256_hex(const char *data, size_t len, char *out_hex);

#endif
