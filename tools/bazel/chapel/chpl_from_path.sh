#!/usr/bin/env bash
set -euo pipefail

compiler="$(command -v chpl || true)"
if [[ -z "${compiler}" ]]; then
  echo "chapel toolchain bridge: chpl is absent from the Linux executor runtime" >&2
  exit 127
fi

exec "${compiler}" "$@"
