#!/usr/bin/env bash
# TIN-2706 drift gate: regenerate the packaging manifest inside the test
# sandbox and byte-diff it against the committed packaging/manifest.json.
# Same runfiles-root discovery shape as tests/run_tests.sh.
set -euo pipefail

if [[ -n "${TEST_SRCDIR:-}" ]]; then
  for candidate in \
    "$TEST_SRCDIR/${TEST_WORKSPACE:-_main}" \
    "$TEST_SRCDIR/_main"; do
    if [[ -f "$candidate/tools/packaging/gen_manifest.py" && -f "$candidate/tools/packaging/gen_home_manager_contract.py" ]]; then
      cd "$candidate"
      break
    fi
  done
fi

python3 tools/packaging/gen_home_manager_contract.py --check
exec python3 tools/packaging/gen_manifest.py --check
