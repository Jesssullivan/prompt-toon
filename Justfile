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

# TIN-2708 C1: ptoon binary build lane (src/ptoon/). Remote-only — chpl
# compilation never runs locally on darwin (AGENTS.md doctrine); this offloads
# to the x86_64-linux remote builder, nix cache-first. The artifact is a single
# ELF (proc main / stdin-stdout), not a shared library — see the pivot note in
# Makefile. `make build-ptoon` is a thin wrapper over this recipe.
# TIN-2706: regenerate the committed packaging-SSOT manifest (drift-gated
# by //tools/packaging:manifest_drift_test inside `just check`).
manifest:
    cd {{root}} && python3 tools/packaging/gen_manifest.py

# TIN-2706 gh_release lane: local-operated release. Builds ptoon on the
# remote substrate (never local chpl), stamps the manifest with provenance
# + the real binary digest, tags, and publishes a GitHub Release whose
# assets the derived lanes (brew/nfpm/bazel-registry source.json) key off.
# CI tag-push automation stays gated on a publicly reachable chapel cache.
release version:
    #!/usr/bin/env bash
    set -Eeuo pipefail
    cd {{root}}
    tag="v{{version}}"
    stage=""
    local_tag_created=0
    remote_tag_pushed=0
    cleanup_release_failure() {
      status="$?"
      if [ "$status" -ne 0 ]; then
        if [ "$remote_tag_pushed" = "1" ]; then
          gh release view "$tag" >/dev/null 2>&1 && gh release delete "$tag" --yes --cleanup-tag >/dev/null 2>&1 || git push origin ":refs/tags/$tag" >/dev/null 2>&1 || true
        fi
        if [ "$local_tag_created" = "1" ]; then
          git tag -d "$tag" >/dev/null 2>&1 || true
        fi
        [ -z "$stage" ] || rm -rf "$stage"
      fi
      exit "$status"
    }
    trap cleanup_release_failure ERR
    [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "release from main only" >&2; exit 1; }
    git diff --quiet && git diff --cached --quiet || { echo "release requires a clean tree" >&2; exit 1; }
    [ "$(python3 -c 'import prompt_toon; print(prompt_toon.__version__)')" = "{{version}}" ] || { echo "SSOT version != {{version}}; bump prompt_toon/__init__.py first" >&2; exit 1; }
    ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || { echo "$tag already exists locally" >&2; exit 1; }
    ! git ls-remote --exit-code --tags origin "$tag" >/dev/null 2>&1 || { echo "$tag already exists on origin" >&2; exit 1; }
    ! gh release view "$tag" >/dev/null 2>&1 || { echo "GitHub release $tag already exists" >&2; exit 1; }
    python3 tools/packaging/gen_manifest.py --check
    nix build .#packages.x86_64-linux.ptoon --print-build-logs
    rev="$(git rev-parse HEAD)"
    stage="$(mktemp -d)"
    cp -L result/bin/ptoon "$stage/ptoon-x86_64-linux"
    chmod +w "$stage/ptoon-x86_64-linux" >/dev/null 2>&1 || true
    python3 tools/packaging/gen_manifest.py --git-rev "$rev" --tag "$tag" --with-binary "$stage/ptoon-x86_64-linux" > "$stage/manifest-$tag.json"
    git tag -a "$tag" -m "prompt-toon $tag" "$rev"
    local_tag_created=1
    git push origin "$tag"
    remote_tag_pushed=1
    gh release create "$tag" "$stage/ptoon-x86_64-linux" "$stage/manifest-$tag.json" \
      --verify-tag \
      --title "prompt-toon v{{version}}" \
      --notes "Stamped manifest is the provenance record: targets[].sha256 authenticates the ptoon asset. Built on the remote x86_64-linux substrate; parity + hook canary gates green at $rev."
    trap - ERR
    rm -rf "$stage"
    echo "released $tag at $rev"

build-ptoon:
    cd {{root}} && nix build .#packages.x86_64-linux.ptoon --print-build-logs

# TIN-2708 C1: shared golden-corpus parity runner. By default it checks both
# python (oracle) and chapel (if PROMPT_TOON_PTOON or build/ptoon is present)
# against fixtures/golden/. Use `--functions` for primitive transform parity
# and `--require-chapel` in remote gates where SKIP must fail.
parity *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/parity_runner.py {{args}}

# GloriousFlywheel lane checks (TIN-2704). The wrapper comes from the
# fleet-managed profile per TIN-2482 (no vendored gloriousflywheel-bazel).
flywheel-check *targets="//:ci_validation_suite":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=shared-cache-backed GF_BAZEL_REMOTE_UPLOAD=false BAZEL_REMOTE_EXECUTOR= gloriousflywheel-bazel test --config=ci-cached {{targets}}

flywheel-executor-check *targets="//:ci_validation_suite":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed {{targets}}

# TIN-2709 C2, pulled into C1: compile the ptoon Chapel binary on GF REAPI.
# nix owns the chpl version (the executor image / devshell provides it); Bazel
# owns the graph, cache, and remote execution. //src/ptoon:ptoon is
# target_compatible_with linux, so this is the ONLY way it realizes — a local
# darwin build is incompatible-by-design and the executor lane fails closed
# (--remote_local_fallback=false) if BAZEL_REMOTE_EXECUTOR is not armed.
flywheel-chapel *targets="//src/ptoon:ptoon":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel build --config=executor-backed {{targets}}
