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

compile-check:
    cd {{root}} && python3 -m compileall -q prompt_toon tests

secrets-scan:
    cd {{root}} && if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --source . --no-banner --redact; else echo "WARNING: gitleaks not on PATH (degraded mode) — secrets scan skipped; run inside nix develop" >&2; fi

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

check: compile-check secrets-scan test bazel-graph bazel-test

package-smoke:
    cd {{root}} && nix build .#prompt-toon

package-smoke-local:
    cd {{root}} && nix build --builders "" .#prompt-toon

# TIN-2708 C1: libptoon build-lane skeleton (src/ptoon/). Remote-only —
# chpl compilation never runs locally on darwin (AGENTS.md doctrine); this
# offloads to the x86_64-linux remote builder, nix cache-first. See also
# `make build-lib` (thin doctrine-compliant wrapper over this recipe).
build-lib:
    cd {{root}} && nix build .#packages.x86_64-linux.libptoon --print-build-logs

# TIN-2708 C1: shared golden-corpus parity runner. Checks the requested
# --engine (default python, the oracle) against fixtures/golden/; today
# this is the python-vs-its-own-goldens self-test. Once --engine=chapel
# lands on the CLI, `just parity --engine chapel` exercises it too — cases
# it can't yet serve degrade to SKIP, not FAIL.
parity *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/parity_runner.py {{args}}

# GloriousFlywheel lane checks (TIN-2704). The wrapper comes from the
# fleet-managed profile per TIN-2482 (no vendored gloriousflywheel-bazel).
flywheel-check *targets="//:ci_validation_suite":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=shared-cache-backed GF_BAZEL_REMOTE_UPLOAD=false BAZEL_REMOTE_EXECUTOR= gloriousflywheel-bazel test --config=ci-cached {{targets}}

flywheel-executor-check *targets="//:ci_validation_suite":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed {{targets}}
