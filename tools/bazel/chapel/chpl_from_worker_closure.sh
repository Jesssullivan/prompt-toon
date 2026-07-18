#!/bin/sh
set -eu

chpl=$(command -v chpl || true)
case "$chpl" in
  /nix/store/*-gf-chapel-prompt-toon-ab552d8/bin/chpl)
    ;;
  *)
    echo "Darwin Chapel toolchain is absent from the worker-owned Nix action PATH" >&2
    exit 127
    ;;
esac

toolchain_root=${chpl%/bin/chpl}
metadata=$toolchain_root/share/gloriousflywheel/chapel-toolchain.json
if [ ! -f "$metadata" ] || [ -L "$metadata" ]; then
  echo "Darwin Chapel toolchain metadata is missing or unsafe: $metadata" >&2
  exit 126
fi

for claim in \
  '"schema_version":1' \
  '"system":"aarch64-darwin"' \
  '"compiler_build_profile":"OPTIMIZE=0 DEBUG=0"' \
  '"compiler_reported_version":"2.8.0 pre-release"' \
  '"nix_package_version":"2.7.0"' \
  '"source":"github:Jesssullivan/chapel"' \
  '"source_revision":"ab552d88630823a961cca5db5f693b3511234e6c"'
do
  if ! /usr/bin/grep -F "$claim" "$metadata" >/dev/null; then
    echo "Darwin Chapel toolchain metadata does not contain $claim" >&2
    exit 125
  fi
done

version_line=$("$chpl" --version | /usr/bin/head -n 1)
case "$version_line" in
  *"version 2.8.0 pre-release"*)
    ;;
  *)
    echo "unexpected Darwin Chapel compiler version: $version_line" >&2
    exit 124
    ;;
esac

exec "$chpl" "$@"
