#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${TEST_SRCDIR:-}" ]]; then
  for candidate in \
    "$TEST_SRCDIR/${TEST_WORKSPACE:-_main}" \
    "$TEST_SRCDIR/_main" \
    "$TEST_SRCDIR/prompt_toon"; do
    if [[ -d "$candidate/prompt_toon" && -d "$candidate/tests" ]]; then
      cd "$candidate"
      break
    fi
  done
fi

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
test_python="${PROMPT_TOON_TEST_PYTHON:-python3}"
if ! "$test_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "prompt-toon tests require Python 3.11 or newer" >&2
  exit 1
fi
exec "$test_python" -m unittest discover -s tests -p 'test_*.py'
