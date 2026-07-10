/* TIN-2709 C2d: sha256 hex digests for stream provenance (INV-1).
 *
 * Chapel 2.x has no stdlib crypto (the old OpenSSL-wrapping Crypto package
 * module is gone), so this follows the utf8proc precedent exactly: a small
 * public-domain C implementation vendored under c_src/ (B-Con
 * crypto-algorithms, verified against hashlib/NIST vectors) pulled in via a
 * file-relative `require`, wrapped by one extern proc.
 *
 * sha256Hex(b) == Python hashlib.sha256(b).hexdigest() byte-for-byte: 64
 * lowercase hex chars. Hashing is over the RAW input bytes (pre-normalize,
 * pre-redact) -- the digest is the re-derivability key a source card carries,
 * so it must identify what actually arrived, not any transform of it. */
module Sha256 {
  use CTypes;

  require "../../c_src/sha256.h", "../../c_src/sha256.c",
          "../../c_src/sha256_ffi.h", "../../c_src/sha256_ffi.c";

  extern proc pt_sha256_hex(data: c_ptrConst(c_char), len: c_size_t,
                            outHex: c_ptr(c_char));

  proc sha256Hex(const ref b: bytes): string {
    var hex: [0..<65] c_char;
    pt_sha256_hex(b.c_str(), b.size: c_size_t, c_ptrTo(hex[0]));
    // 64 ASCII hex chars: decode cannot fail, so try! is a build-bug trap.
    return try! bytes.createCopyingBuffer(c_ptrTo(hex[0]): c_ptrConst(c_char),
                                          64).decode();
  }
}
