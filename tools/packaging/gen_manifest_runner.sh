#!/usr/bin/env bash
# TIN-2706: `bazel run //tools/packaging:gen_manifest` — regenerate the
# committed packaging manifest in the source tree (BUILD_WORKSPACE_DIRECTORY
# is Bazel's pointer back out of the runfiles sandbox for `run` targets).
set -euo pipefail

cd "${BUILD_WORKSPACE_DIRECTORY:?run this via: bazel run //tools/packaging:gen_manifest}"
exec python3 tools/packaging/gen_manifest.py "$@"
