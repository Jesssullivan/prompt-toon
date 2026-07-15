import? "justfile.flywheel"

root := justfile_directory()

default:
    @just --list --unsorted

setup:
    @echo "prompt-toon uses the Nix dev shell; run direnv allow or nix develop."

prompt-toon *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon {{args}}

# TIN-2819 C4e: provider-free durable-spool condensation plus a claim-bounded
# byte/token-estimate ledger. `--engine auto` records any Python fallback.
dogfood *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon dogfood {{args}}

# TIN-2820 C4f: deterministic ledger-only aggregation. This reads only the
# explicitly named efficiency.json files and performs no provider IO.
dogfood-corpus *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon corpus-report {{args}}

# C4f.4: import caller-supplied usage/count and exact request artifacts into
# a path-free hash-bound sidecar. The importer makes no provider request.
provider-usage-import *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -B -m prompt_toon provider-usage-import {{args}}

# Compare one raw-input and one condensed provider-usage sidecar. Cache reads
# and writes remain separate; this makes no pricing or quality claim.
provider-usage-compare *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -B -m prompt_toon provider-usage-compare {{args}}

# C4f.2: generated synthetic fixtures evaluated against emitted artifacts.
# Python is the local oracle; remote parity invokes this with --engine chapel.
quality-fixtures *args:
    cd {{root}} && python3 tools/gen_fixtures.py && PYTHONPATH={{root}} python3 tools/quality_runner.py {{args}}

doctor:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon doctor

# TIN-2793 C4b: opt-in loopback Anthropic Messages shadow gateway. The
# gateway never reads ANTHROPIC_BASE_URL as its upstream, so the harness can
# safely point that variable at localhost without creating a proxy loop.
gateway *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon gateway {{args}}

# TIN-2794 C4c: opt-in loopback OpenAI Responses shadow gateway.
responses-gateway *args:
    cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon responses-gateway {{args}}

# Unbilled protocol proof: real Claude Code CLI, real gateway, scripted
# loopback SSE upstream, and an in-memory transform engine.
gateway-harness-probe *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/anthropic_gateway_harness_probe.py {{args}}

# Unbilled protocol proof: real Codex CLI, real Responses gateway, scripted
# loopback SSE upstream, and an in-memory transform engine.
responses-gateway-harness-probe *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/openai_gateway_harness_probe.py {{args}}

# Remote release gate when PROMPT_TOON_PTOON is set: real resident child,
# 64 concurrent loopback HTTP/SSE sessions per provider, and no external IO.
gateway-capacity *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/gateway_capacity.py {{args}}

# Inspect process-scoped routing and fail closed on conflicting provider modes.
claude-profile *args:
    @cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon claude-profile {{args}}

# Render user-level Codex profile state; no file or model is changed.
codex-profile *args:
    @cd {{root}} && PYTHONPATH={{root}} python3 -m prompt_toon codex-profile {{args}}

# Explicitly billed and disabled by default. Starts a dedicated gateway and
# resident ptoon engine; requires PROMPT_TOON_LIVE_CANARY=1,
# ANTHROPIC_API_KEY, ANTHROPIC_CANARY_MODEL, and an explicit dollar ceiling.
gateway-canary *args:
    cd {{root}} && PYTHONPATH={{root}} python3 tools/anthropic_gateway_canary.py {{args}}

compile-check:
    cd {{root}} && python3 -m compileall -q prompt_toon tests tools

secrets-scan:
    cd {{root}} && if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --source . --no-banner --redact; else echo "WARNING: gitleaks not on PATH (degraded mode) — secrets scan skipped; run inside nix develop" >&2; fi

test:
    cd {{root}} && PYTHONPATH={{root}} python3 -m unittest discover -s tests -p 'test_*.py'

policy-verify:
    cd {{root}} && PYTHONPATH={{root}} python3 -m unittest tests.test_delegation_policy tests.test_io_policy -v

gen-policy:
    cd {{root}} && if command -v dhall-to-json >/dev/null 2>&1; then dhall-to-json --pretty --file policy/dhall/delegation.dhall > policy/delegation.json && dhall-to-json --pretty --file policy/dhall/io.dhall > policy/io.json && just policy-verify; else echo "dhall-to-json not on PATH (degraded mode); validated JSON artifacts remain unchanged"; fi

bazel-graph:
    cd {{root}} && bazelisk --output_user_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}" mod graph >/dev/null

bazel-test:
    cd {{root}} && test_python="$(python3 -c 'import sys; print(sys.executable)')" && bazelisk --output_user_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}" test --test_env=PROMPT_TOON_TEST_PYTHON="$test_python" //...

check: compile-check secrets-scan test quality-fixtures bazel-graph bazel-test

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
# native remote substrates (never local chpl), builds and installs the universal
# wheel, stamps every asset digest, then publishes both closure exports, the
# wheel, and manifest.
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
    [ -z "$(git status --porcelain)" ] || { echo "release requires a clean tree including untracked files" >&2; exit 1; }
    [ "$(python3 -c 'import prompt_toon; print(prompt_toon.__version__)')" = "{{version}}" ] || { echo "SSOT version != {{version}}; bump prompt_toon/__init__.py first" >&2; exit 1; }
    rev="$(git rev-parse HEAD)"
    remote_main="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
    [ -n "$remote_main" ] && [ "$rev" = "$remote_main" ] || { echo "release HEAD must equal origin/main" >&2; exit 1; }
    ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || { echo "$tag already exists locally" >&2; exit 1; }
    ! git ls-remote --exit-code --tags origin "$tag" >/dev/null 2>&1 || { echo "$tag already exists on origin" >&2; exit 1; }
    ! gh release view "$tag" >/dev/null 2>&1 || { echo "GitHub release $tag already exists" >&2; exit 1; }
    python3 tools/packaging/gen_manifest.py --check
    just check
    just gateway-harness-probe
    just responses-gateway-harness-probe
    # A release may never claim gates it did not run: realize the full
    # parity + hook-canary derivation at this rev before anything is tagged.
    nix build .#packages.x86_64-linux.ptoon-parity --no-link --print-build-logs
    linux_ptoon_store="$(nix build .#packages.x86_64-linux.ptoon --no-link --print-out-paths --print-build-logs)"
    # max-jobs=0 forbids a same-architecture local Darwin build; the native
    # aarch64-darwin remote builder must realize or substitute this target.
    darwin_ptoon_store="$(nix build --max-jobs 0 .#packages.aarch64-darwin.ptoon --no-link --print-out-paths --print-build-logs)"
    nix build .#packages.x86_64-linux.prompt-toon --no-link --print-build-logs
    stage="$(mktemp -d)"
    # Raw executables are Nix-store linked and are not portable standalone
    # assets. Export each complete runtime closure for import with nix-store
    # --import; the tagged flake remains the canonical online install path.
    nix-store --export $(nix-store --query --requisites "$linux_ptoon_store") > "$stage/ptoon-x86_64-linux.nar"
    nix-store --export $(nix-store --query --requisites "$darwin_ptoon_store") > "$stage/ptoon-aarch64-darwin.nar"
    test -s "$stage/ptoon-x86_64-linux.nar"
    test -s "$stage/ptoon-aarch64-darwin.nar"
    mkdir "$stage/source"
    git archive "$rev" | tar -x -C "$stage/source"
    (cd "$stage/source" && UV_CACHE_DIR="$stage/uv-cache" uv build --wheel --out-dir "$stage")
    wheels=("$stage"/prompt_toon-{{version}}-*.whl)
    [ "${#wheels[@]}" -eq 1 ] && [ -f "${wheels[0]}" ] || { echo "release expected exactly one prompt-toon wheel" >&2; exit 1; }
    wheel="${wheels[0]}"
    UV_CACHE_DIR="$stage/uv-cache" uv venv "$stage/venv"
    UV_CACHE_DIR="$stage/uv-cache" uv pip install --python "$stage/venv/bin/python" "$wheel"
    (cd "$stage" && "$stage/venv/bin/prompt-toon" --version && "$stage/venv/bin/prompt-toon" corpus-report --help >/dev/null && "$stage/venv/bin/prompt-toon" claude-profile direct > claude-profile.json && "$stage/venv/bin/prompt-toon" codex-profile direct > codex-profile.json)
    python3 tools/packaging/gen_manifest.py --git-rev "$rev" --tag "$tag" \
      --with-closure "x86_64-linux=$stage/ptoon-x86_64-linux.nar" \
      --with-entrypoint "x86_64-linux=$linux_ptoon_store/bin/ptoon" \
      --with-closure "aarch64-darwin=$stage/ptoon-aarch64-darwin.nar" \
      --with-entrypoint "aarch64-darwin=$darwin_ptoon_store/bin/ptoon" \
      --with-wheel "$wheel" > "$stage/manifest-$tag.json"
    git tag -a "$tag" -m "prompt-toon $tag" "$rev"
    local_tag_created=1
    git push origin "$tag"
    remote_tag_pushed=1
    gh release create "$tag" "$stage/ptoon-x86_64-linux.nar" "$stage/ptoon-aarch64-darwin.nar" "$wheel" "$stage/manifest-$tag.json" \
      --verify-tag \
      --title "prompt-toon v{{version}}" \
      --notes "Stamped manifest is the provenance record: targets[].sha256 authenticates both importable Nix closure exports and the wheel, while each closure target's entrypoint_sha256 binds it to the built bin/ptoon. The tagged flake is the canonical install path. Linux parity, hook canary, Claude/Codex real-CLI harness probes, native remote Darwin smoke, package install, and repository gates green at $rev."
    trap - ERR
    rm -rf "$stage"
    echo "released $tag at $rev"

build-ptoon:
    cd {{root}} && nix build .#packages.x86_64-linux.ptoon --print-build-logs

build-ptoon-darwin:
    cd {{root}} && nix build --max-jobs 0 .#packages.aarch64-darwin.ptoon --print-build-logs

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

# PR identities are read-only and prove execution with one forced pass. Trusted
# main pushes warm a deterministic consumer action, then use a second isolated
# output base to prove both remote cache reuse and uncached test execution.
flywheel-executor-proof log target="//:ci_validation_suite" require_cache_reuse="false":
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{root}}
    log="{{log}}"
    require_cache_reuse="{{require_cache_reuse}}"
    case "${require_cache_reuse}" in
      true|false) ;;
      *) echo "require_cache_reuse must be true or false" >&2; exit 2 ;;
    esac
    proof_root="$(mktemp -d "${TMPDIR:-/tmp}/prompt-toon-rbe.XXXXXX")"
    trap 'rm -rf "${proof_root}"' EXIT
    execution_log="${proof_root}/execution.json"
    if [[ "${require_cache_reuse}" == "false" ]]; then
      BAZEL_OUTPUT_BASE="${proof_root}/forced-output" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --remote_accept_cached=false --nocache_test_results --execution_log_json_file="${execution_log}" "{{target}}" 2>&1 | tee "${log}"
      exit 0
    fi
    warm_log="${proof_root}/warm.log"
    warm_execution_log="${proof_root}/warm.execution.json"
    warm_output_base="${proof_root}/warm-output"
    measured_output_base="${proof_root}/measured-output"
    if ! BAZEL_OUTPUT_BASE="${warm_output_base}" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --remote_accept_cached=false --nocache_test_results --execution_log_json_file="${warm_execution_log}" "{{target}}" >"${warm_log}" 2>&1; then
      cat "${warm_log}" >&2
      exit 1
    fi
    echo "GF warm pass completed; measuring remote execution plus cache reuse"
    BAZEL_OUTPUT_BASE="${measured_output_base}" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --nocache_test_results --execution_log_json_file="${execution_log}" "{{target}}" 2>&1 | tee "${log}"

# TIN-2709 C2, pulled into C1: compile the ptoon Chapel binary on GF REAPI.
# nix owns the chpl version (the executor image / devshell provides it); Bazel
# owns the graph, cache, and remote execution. //src/ptoon:ptoon is
# target_compatible_with linux, so this is the ONLY way it realizes — a local
# darwin build is incompatible-by-design and the executor lane fails closed
# (--remote_local_fallback=false) if BAZEL_REMOTE_EXECUTOR is not armed.
flywheel-chapel *targets="//src/ptoon:ptoon":
    cd {{root}} && GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel build --config=executor-backed {{targets}}
