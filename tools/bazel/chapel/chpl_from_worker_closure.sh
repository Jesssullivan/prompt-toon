#!/bin/sh
set -eu

expected_metadata_sha256=ac1a9d5a93cb85b2aec2b74bf2af21c8cfb0ce01ced7dc3222a9cf5532f12e09

validate_metadata() {
  metadata_path=$1
  if [ ! -f "$metadata_path" ] || [ -L "$metadata_path" ]; then
    echo "Darwin Chapel toolchain metadata is missing or unsafe: $metadata_path" >&2
    exit 126
  fi

  if [ -x /usr/bin/shasum ]; then
    hash_output=$(/usr/bin/shasum -a 256 "$metadata_path")
  elif command -v sha256sum >/dev/null 2>&1; then
    hash_output=$(sha256sum "$metadata_path")
  else
    echo "no SHA-256 implementation is available for Darwin toolchain metadata" >&2
    exit 125
  fi
  metadata_sha256=${hash_output%% *}
  if [ "$metadata_sha256" != "$expected_metadata_sha256" ]; then
    echo "Darwin Chapel toolchain metadata digest mismatch: $metadata_sha256" >&2
    exit 125
  fi
}

if [ "${1-}" = "--validate-metadata-only" ]; then
  if [ "$#" -ne 2 ]; then
    echo "usage: $0 --validate-metadata-only METADATA" >&2
    exit 2
  fi
  validate_metadata "$2"
  exit 0
fi

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
validate_metadata "$metadata"

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
