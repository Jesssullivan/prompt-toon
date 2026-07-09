import? "justfile.flywheel"

root := justfile_directory()

default:
    @just --list --unsorted

setup:
    @echo "prompt-toon uses the Nix dev shell; run direnv allow or nix develop."

prompt-toon *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon {{args}}

doctor:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon doctor

fmt-check:
    cd {{root}} && python3 -m compileall -q prompt_toon tests

test:
    cd {{root}} && PYTHONPATH={{root}} python3 -m unittest discover -s tests -p 'test_*.py'

policy-verify:
    cd {{root}} && PYTHONPATH={{root}} python3 -m unittest discover -s tests -p 'test_delegation_policy.py' -v

gen-policy:
    cd {{root}} && if command -v dhall-to-json >/dev/null 2>&1; then dhall-to-json --pretty --file policy/dhall/delegation.dhall > policy/delegation.json && just policy-verify; else echo "dhall-to-json not on PATH (degraded mode); policy/delegation.json remains hand-synced"; fi

bazel-graph:
    cd {{root}} && bazelisk --output_user_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}" mod graph >/dev/null

bazel-test:
    cd {{root}} && bazelisk --output_user_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}" test //...

check: fmt-check test bazel-graph bazel-test

package-smoke:
    cd {{root}} && nix build .#prompt-toon

package-smoke-local:
    cd {{root}} && nix build --builders "" .#prompt-toon
