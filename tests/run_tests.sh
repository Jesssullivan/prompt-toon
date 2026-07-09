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
exec python3 -m unittest discover -s tests -p 'test_*.py'
