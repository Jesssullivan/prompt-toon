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

# TIN-2949: analysis-only platform/toolchain proof. This runs no Chapel action:
# both platforms resolve their exact implementation and remain non-cacheable;
# Darwin must also declare the native smoke action.
bazel-chapel-toolchain-contract:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{root}}
    output_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}"
    bazel=(bazelisk --output_user_root="${output_root}")
    linux_log="$(mktemp "${TMPDIR:-/tmp}/prompt-toon-linux-toolchain.XXXXXX")"
    darwin_log="$(mktemp "${TMPDIR:-/tmp}/prompt-toon-darwin-toolchain.XXXXXX")"
    darwin_smoke_log="$(mktemp "${TMPDIR:-/tmp}/prompt-toon-darwin-smoke.XXXXXX")"
    trap 'rm -f "${linux_log}" "${darwin_log}" "${darwin_smoke_log}"' EXIT
    "${bazel[@]}" cquery \
      --platforms=//tools/bazel/platforms:linux_x86_64 \
      --extra_execution_platforms=//tools/bazel/platforms:linux_x86_64 \
      //src/ptoon:ptoon >/dev/null
    "${bazel[@]}" aquery \
      --platforms=//tools/bazel/platforms:linux_x86_64 \
      --extra_execution_platforms=//tools/bazel/platforms:linux_x86_64 \
      --output=text \
      'mnemonic(ChapelCompile, //src/ptoon:ptoon)' >"${linux_log}"
    grep -F "ExecutionInfo: {no-cache: 1}" "${linux_log}" >/dev/null
    "${bazel[@]}" cquery \
      --platforms=//tools/bazel/platforms:darwin_aarch64 \
      --extra_execution_platforms=//tools/bazel/platforms:darwin_aarch64 \
      //src/ptoon:ptoon >/dev/null
    "${bazel[@]}" aquery \
      --platforms=//tools/bazel/platforms:darwin_aarch64 \
      --extra_execution_platforms=//tools/bazel/platforms:darwin_aarch64 \
      --output=text \
      'mnemonic(ChapelCompile, //src/ptoon:ptoon)' >"${darwin_log}"
    grep -F "Mnemonic: ChapelCompile" "${darwin_log}" >/dev/null
    grep -F "ExecutionInfo: {no-cache: 1}" "${darwin_log}" >/dev/null
    "${bazel[@]}" aquery \
      --platforms=//tools/bazel/platforms:darwin_aarch64 \
      --extra_execution_platforms=//tools/bazel/platforms:darwin_aarch64 \
      --output=text \
      'mnemonic(PtoonNativeSmoke, //src/ptoon:ptoon)' >"${darwin_smoke_log}"
    grep -F "Mnemonic: PtoonNativeSmoke" "${darwin_smoke_log}" >/dev/null
    grep -F "ExecutionInfo: {no-cache: 1}" "${darwin_smoke_log}" >/dev/null
    "${bazel[@]}" query --output=build \
      //tools/bazel/platforms:darwin_aarch64 \
      | grep -F '"gf.toolchain-policy": "chapel-ab552d8"' >/dev/null
    echo "CHAPEL TOOLCHAIN CONTRACT: PASS (Linux/Darwin compile and Darwin native smoke are non-cacheable)"

bazel-test:
    cd {{root}} && test_python="$(python3 -c 'import sys; print(sys.executable)')" && bazelisk --output_user_root="${BAZEL_OUTPUT_USER_ROOT:-${TMPDIR:-/tmp}/prompt-toon-bazel-user-root}" test --test_env=PROMPT_TOON_TEST_PYTHON="$test_python" //...

check: compile-check secrets-scan test quality-fixtures bazel-graph bazel-chapel-toolchain-contract bazel-test

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

# TIN-2949: on an attended Darwin bridge host, verify one exact GF proof output,
# add its byte-identical package tree to the Nix store, and emit a transfer
# bundle. No Chapel compilation occurs in this recipe.
gf-darwin-bridge $evidence $output $native_smoke $revision $gf_revision $bundle:
    #!/usr/bin/env bash
    set -Eeuo pipefail
    cd {{root}}
    [ ! -e "$bundle" ] || { echo "bridge bundle path already exists: $bundle" >&2; exit 1; }
    mkdir -p "$bundle"
    bridge_completed=0
    cleanup_bridge() {
      status="$?"
      trap - EXIT INT TERM HUP
      if [ "$bridge_completed" = "0" ]; then
        rm -rf "$bundle"
      fi
      exit "$status"
    }
    trap cleanup_bridge EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    python3 tools/packaging/import_gf_ptoon.py import \
      --evidence-dir "$evidence" \
      --output "$output" \
      --native-smoke "$native_smoke" \
      --expected-revision "$revision" \
      --expected-gf-revision "$gf_revision" \
      --closure-export "$bundle/ptoon-aarch64-darwin.nar" \
      --entrypoint-export "$bundle/ptoon-aarch64-darwin.bin" \
      --record "$bundle/ptoon-aarch64-darwin.gf-nix-bridge.json"
    bridge_completed=1
    echo "GF Darwin bridge bundle: $bundle"

# TIN-2706/TIN-2949 gh_release lane: local-operated release. Linux remains
# remote/cache-first; Darwin consumes the exact attended GF-to-Nix bridge
# bundle named by PROMPT_TOON_DARWIN_BRIDGE_DIR. The release never compiles
# Chapel on the publisher host or substitutes an independent Darwin build.
# CI tag-push automation stays gated on a publicly reachable chapel cache.
release $version:
    #!/usr/bin/env bash
    set -Eeuo pipefail
    cd {{root}}
    tag="v$version"
    stage=""
    rev=""
    local_tag_object=""
    remote_tag_push_attempted=0
    release_create_attempted=0
    created_release_id=""
    release_marker=""
    release_completed=0
    canonical_repo="Jesssullivan/prompt-toon"
    assert_release_checkout() {
      [ "$(git rev-parse HEAD)" = "$rev" ] \
        && [ -z "$(git status --porcelain)" ] \
        || { echo "release checkout changed after source snapshot" >&2; return 1; }
    }
    confirm_release_absent() {
      release_http="$(gh api --include "repos/$canonical_repo/releases/tags/$tag" 2>&1)"
      release_api_status="$?"
      release_http_status="$(printf '%s\n' "$release_http" | awk '/^HTTP\// { status=$2 } END { print status }')"
      [ "$release_api_status" -ne 0 ] && [ "$release_http_status" = "404" ]
    }
    cleanup_release() {
      status="$?"
      trap - EXIT INT TERM HUP
      set +e
      if [ "$status" -ne 0 ] && [ "$release_completed" = "0" ]; then
        release_absent_confirmed=0
        if [ "$release_create_attempted" = "1" ] && [ -n "$release_marker" ]; then
          current_release_json="$(gh release view "$tag" --repo "$canonical_repo" --json id,body 2>/dev/null)"
          current_release_status="$?"
          current_release_id="$(printf '%s' "$current_release_json" | jq -r '.id // empty' 2>/dev/null)"
          current_release_body="$(printf '%s' "$current_release_json" | jq -r '.body // empty' 2>/dev/null)"
          if [ "$current_release_status" -eq 0 ] \
            && [ -n "$current_release_id" ] \
            && [[ "$current_release_body" == *"$release_marker"* ]] \
            && { [ -z "$created_release_id" ] || [ "$current_release_id" = "$created_release_id" ]; }; then
            if gh release delete "$tag" --repo "$canonical_repo" --yes >/dev/null 2>&1; then
              if confirm_release_absent; then
                release_absent_confirmed=1
              else
                echo "release cleanup could not confirm deletion of owned GitHub release $tag; retaining signed tag" >&2
              fi
            else
              echo "release cleanup could not remove owned GitHub release $tag; retaining signed tag" >&2
            fi
          elif [ "$current_release_status" -eq 0 ]; then
            echo "release cleanup refused to remove $tag because release ownership changed" >&2
          elif confirm_release_absent; then
            release_absent_confirmed=1
          else
            echo "release cleanup could not determine whether GitHub release $tag exists; retaining signed tag" >&2
          fi
        elif [ "$release_create_attempted" = "1" ]; then
          echo "release cleanup lacks its ownership marker; retaining release and signed tag" >&2
        fi
        if [ "$remote_tag_push_attempted" = "1" ] && [ -n "$rev" ] && [ -n "$local_tag_object" ]; then
          if [ "$release_absent_confirmed" = "1" ]; then
            echo "release cleanup retained exact signed tag $tag for operator inspection" >&2
          else
            echo "release cleanup retained exact signed tag $tag because release absence is unconfirmed" >&2
          fi
        fi
        if [ -n "$local_tag_object" ] && [ "$(git rev-parse -q --verify "refs/tags/$tag" 2>/dev/null)" = "$local_tag_object" ]; then
          if [ "$remote_tag_push_attempted" = "0" ]; then
            git tag -d "$tag" >/dev/null 2>&1 || \
              echo "release cleanup could not remove exact local tag $tag" >&2
          else
            echo "release cleanup retained exact local tag $tag with its remote counterpart" >&2
          fi
        fi
      fi
      [ -z "$stage" ] || rm -rf "$stage"
      exit "$status"
    }
    trap cleanup_release EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "release from main only" >&2; exit 1; }
    [ -z "$(git status --porcelain)" ] || { echo "release requires a clean tree including untracked files" >&2; exit 1; }
    origin_url="$(git remote get-url origin)"
    case "$origin_url" in
      "https://github.com/$canonical_repo.git"|"git@github.com:$canonical_repo.git") ;;
      *) echo "release origin must be canonical $canonical_repo, got $origin_url" >&2; exit 1 ;;
    esac
    [ "$(gh repo view "$canonical_repo" --json nameWithOwner --jq .nameWithOwner)" = "$canonical_repo" ] || { echo "GitHub CLI cannot resolve canonical repository $canonical_repo" >&2; exit 1; }
    [ "$(python3 -c 'import prompt_toon; print(prompt_toon.__version__)')" = "$version" ] || { echo "SSOT version != $version; bump prompt_toon/__init__.py first" >&2; exit 1; }
    command -v gpg >/dev/null || { echo "release requires OpenPGP signing via gpg" >&2; exit 1; }
    signing_key="$(git config --get user.signingkey || true)"
    [ -n "$signing_key" ] || { echo "release requires git user.signingkey" >&2; exit 1; }
    signing_format="$(git config --get gpg.format || true)"
    [ -z "$signing_format" ] || [ "$signing_format" = "openpgp" ] || { echo "release requires an OpenPGP git signing key" >&2; exit 1; }
    gpg --list-secret-keys "$signing_key" >/dev/null 2>&1 || { echo "release signing key is unavailable: $signing_key" >&2; exit 1; }
    signing_fingerprint="$(gpg --batch --with-colons --list-secret-keys --fingerprint "$signing_key" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
    signing_fingerprint="$(printf '%s' "$signing_fingerprint" | tr '[:lower:]' '[:upper:]')"
    [[ "$signing_fingerprint" =~ ^[0-9A-Fa-f]{40,64}$ ]] || { echo "release signing fingerprint is unavailable" >&2; exit 1; }
    trusted_signing_fingerprint="$(python3 -c 'import json; print(json.load(open("packaging/release-signers.json", encoding="utf-8"))["active"]["fingerprint"])')"
    anchored_signing_fingerprint="$(gpg --batch --with-colons --show-keys --fingerprint packaging/release-signing-key.asc 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
    [ "$anchored_signing_fingerprint" = "$trusted_signing_fingerprint" ] || { echo "committed release key does not match release-signers.json" >&2; exit 1; }
    [ "$signing_fingerprint" = "$trusted_signing_fingerprint" ] || { echo "configured release key is not the reviewed repository signer" >&2; exit 1; }
    rev="$(git rev-parse HEAD)"
    remote_main="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
    [ -n "$remote_main" ] && [ "$rev" = "$remote_main" ] || { echo "release HEAD must equal origin/main" >&2; exit 1; }
    darwin_bridge_source_dir="${PROMPT_TOON_DARWIN_BRIDGE_DIR:-}"
    [ -n "$darwin_bridge_source_dir" ] && [ -d "$darwin_bridge_source_dir" ] || { echo "release requires PROMPT_TOON_DARWIN_BRIDGE_DIR from the attended GF-to-Nix bridge" >&2; exit 1; }
    darwin_bridge_closure="$darwin_bridge_source_dir/ptoon-aarch64-darwin.nar"
    darwin_bridge_entrypoint="$darwin_bridge_source_dir/ptoon-aarch64-darwin.bin"
    darwin_bridge_record="$darwin_bridge_source_dir/ptoon-aarch64-darwin.gf-nix-bridge.json"
    darwin_bridge_proof="$darwin_bridge_source_dir/ptoon-aarch64-darwin.gf-proof-result.json"
    darwin_bridge_outputs="$darwin_bridge_source_dir/ptoon-aarch64-darwin.gf-exported-outputs.json"
    darwin_bridge_attestation="$darwin_bridge_source_dir/ptoon-aarch64-darwin.gf-proof-result.attestation.json"
    darwin_bridge_smoke="$darwin_bridge_source_dir/ptoon-aarch64-darwin.native-smoke.json"
    gf_revision="${PROMPT_TOON_GF_REVISION:-}"
    [[ "$gf_revision" =~ ^[0-9a-f]{40}$ ]] || { echo "release requires PROMPT_TOON_GF_REVISION as the exact lowercase GF proof commit" >&2; exit 1; }
    for bridge_input in "$darwin_bridge_closure" "$darwin_bridge_entrypoint" "$darwin_bridge_record" "$darwin_bridge_proof" "$darwin_bridge_outputs" "$darwin_bridge_attestation" "$darwin_bridge_smoke"; do
      [ -f "$bridge_input" ] && [ ! -L "$bridge_input" ] || { echo "missing or unsafe Darwin bridge input: $bridge_input" >&2; exit 1; }
    done
    ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || { echo "$tag already exists locally" >&2; exit 1; }
    [ -z "$(git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")" ] || { echo "$tag already exists on origin" >&2; exit 1; }
    confirm_release_absent || { echo "GitHub release $tag must be confirmed absent before release" >&2; exit 1; }
    stage="$(mktemp -d)"
    cp "$darwin_bridge_closure" "$stage/ptoon-aarch64-darwin.nar"
    cp "$darwin_bridge_entrypoint" "$stage/ptoon-aarch64-darwin.bin"
    cp "$darwin_bridge_record" "$stage/ptoon-aarch64-darwin.gf-nix-bridge.json"
    cp "$darwin_bridge_proof" "$stage/ptoon-aarch64-darwin.gf-proof-result.json"
    cp "$darwin_bridge_outputs" "$stage/ptoon-aarch64-darwin.gf-exported-outputs.json"
    cp "$darwin_bridge_attestation" "$stage/ptoon-aarch64-darwin.gf-proof-result.attestation.json"
    cp "$darwin_bridge_smoke" "$stage/ptoon-aarch64-darwin.native-smoke.json"
    darwin_bridge_closure="$stage/ptoon-aarch64-darwin.nar"
    darwin_bridge_entrypoint="$stage/ptoon-aarch64-darwin.bin"
    darwin_bridge_record="$stage/ptoon-aarch64-darwin.gf-nix-bridge.json"
    darwin_bridge_proof="$stage/ptoon-aarch64-darwin.gf-proof-result.json"
    darwin_bridge_outputs="$stage/ptoon-aarch64-darwin.gf-exported-outputs.json"
    darwin_bridge_attestation="$stage/ptoon-aarch64-darwin.gf-proof-result.attestation.json"
    darwin_bridge_smoke="$stage/ptoon-aarch64-darwin.native-smoke.json"
    mkdir "$stage/source"
    git archive "$rev" | tar -x -C "$stage/source"
    git archive --format=tar.gz --prefix="prompt-toon-$version/" "$rev" > "$stage/prompt-toon-$version-source.tar.gz"
    release_source_store="$(nix store add --name "prompt-toon-source-$version-$rev" "$stage/source")"
    release_source_archive_store="$(nix store add --mode flat --name "prompt-toon-$version-source.tar.gz" "$stage/prompt-toon-$version-source.tar.gz")"
    nix-store --add-root "$stage/release-source-gc-root" --indirect --realise "$release_source_store" >/dev/null
    nix-store --add-root "$stage/release-source-archive-gc-root" --indirect --realise "$release_source_archive_store" >/dev/null
    release_flake="path:$release_source_store"
    assert_release_checkout
    python3 "$release_source_store/tools/packaging/import_gf_ptoon.py" replay \
      --record "$darwin_bridge_record" \
      --closure-export "$darwin_bridge_closure" \
      --entrypoint-export "$darwin_bridge_entrypoint" \
      --expected-revision "$rev" \
      --expected-gf-revision "$gf_revision"
    python3 "$release_source_store/tools/packaging/gen_manifest.py" --check
    just check
    just gateway-harness-probe
    just responses-gateway-harness-probe
    assert_release_checkout
    # A release may never claim gates it did not run: realize the full
    # parity + hook-canary derivation at this rev before anything is tagged.
    nix build "$release_flake#packages.x86_64-linux.ptoon-parity" --no-link --print-build-logs
    linux_ptoon_store="$(nix build "$release_flake#packages.x86_64-linux.ptoon" --no-link --print-out-paths --print-build-logs)"
    nix build "$release_flake#packages.x86_64-linux.prompt-toon" --no-link --print-build-logs
    # Raw executables are Nix-store linked and are not portable standalone
    # assets. Export each complete runtime closure for exact import with
    # nix-store --import; the tagged flake remains the source definition.
    nix-store --export $(nix-store --query --requisites "$linux_ptoon_store" | sort) > "$stage/ptoon-x86_64-linux.nar"
    test -s "$stage/ptoon-x86_64-linux.nar"
    test -s "$stage/ptoon-aarch64-darwin.nar"
    UV_CACHE_DIR="$stage/uv-cache" uv build --wheel --out-dir "$stage" "$release_source_archive_store"
    wheels=("$stage"/prompt_toon-"$version"-py3-none-any.whl)
    [ "${#wheels[@]}" -eq 1 ] && [ -f "${wheels[0]}" ] || { echo "release expected exactly one prompt-toon wheel" >&2; exit 1; }
    wheel="${wheels[0]}"
    UV_CACHE_DIR="$stage/uv-cache" uv venv "$stage/venv"
    UV_CACHE_DIR="$stage/uv-cache" uv pip install --python "$stage/venv/bin/python" "$wheel"
    (cd "$stage" && "$stage/venv/bin/prompt-toon" --version && "$stage/venv/bin/prompt-toon" corpus-report --help >/dev/null && "$stage/venv/bin/prompt-toon" claude-profile direct > claude-profile.json && "$stage/venv/bin/prompt-toon" codex-profile direct > codex-profile.json)
    PROMPT_TOON_PTOON="$darwin_bridge_entrypoint" \
      "$stage/venv/bin/prompt-toon" dogfood \
      "$release_source_store/fixtures/inputs/13-research-note.md" \
      "$release_source_store/fixtures/inputs/10-clean-doc.txt" \
      --id release-installed-dogfood \
      --output-dir "$stage/installed-dogfood" \
      --engine auto > "$stage/installed-dogfood.json"
    jq -e '
      .execution.engine_requested == "auto" and
      .execution.engine_resolved == "chapel" and
      .execution.shape == "chapel-one-shot-coforall-batch" and
      .claim_boundary.provider_requests == 0
    ' "$stage/installed-dogfood/efficiency.json" >/dev/null
    manifest="$stage/manifest-$tag.json"
    python3 "$release_source_store/tools/packaging/gen_manifest.py" --git-rev "$rev" --tag "$tag" \
      --with-closure "x86_64-linux=$stage/ptoon-x86_64-linux.nar" \
      --with-entrypoint "x86_64-linux=$linux_ptoon_store/bin/ptoon" \
      --with-nix-store-path "x86_64-linux=$linux_ptoon_store" \
      --with-closure "aarch64-darwin=$stage/ptoon-aarch64-darwin.nar" \
      --with-entrypoint "aarch64-darwin=$stage/ptoon-aarch64-darwin.bin" \
      --with-build-provenance "aarch64-darwin=$stage/ptoon-aarch64-darwin.gf-nix-bridge.json" \
      --with-wheel "$wheel" > "$manifest"
    assert_release_checkout
    git tag -s -u "$signing_key" "$tag" -m "prompt-toon $tag" "$rev"
    local_tag_object="$(git rev-parse "refs/tags/$tag")"
    [ "$(git cat-file -t "$local_tag_object")" = "tag" ] || { echo "release tag is not an annotated tag object" >&2; exit 1; }
    git verify-tag "$tag"
    gpg --local-user "$signing_key" --armor --detach-sign \
      --output "$manifest.asc" "$manifest"
    gpg --verify "$manifest.asc" "$manifest"
    remote_tag_push_attempted=1
    git push --force-with-lease="refs/tags/$tag:" origin "$local_tag_object:refs/tags/$tag"
    pushed_tags="$(git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")"
    pushed_tag_object="$(printf '%s\n' "$pushed_tags" | awk -v ref="refs/tags/$tag" '$2 == ref { print $1 }')"
    pushed_tag_commit="$(printf '%s\n' "$pushed_tags" | awk -v ref="refs/tags/$tag^{}" '$2 == ref { print $1 }')"
    [ "$pushed_tag_object" = "$local_tag_object" ] && [ "$pushed_tag_commit" = "$rev" ] || { echo "origin tag $tag does not match the exact signed release tag object" >&2; exit 1; }
    release_marker="<!-- prompt-toon-release-owner:$tag:$local_tag_object -->"
    release_notes="$stage/release-notes.md"
    printf '%s\n\n%s\n' \
      "$release_marker" \
      "OpenPGP signer: $signing_fingerprint. The signed tag authenticates source; the detached manifest signature authenticates both importable Nix closure exports, the universal wheel, and the GF Darwin-to-Nix bridge record. Darwin build_provenance binds the exact forced GF output, source revision, physical worker closure identity, Sigstore-attested GF proof, byte-identical Nix entrypoint, remote native caps/normalize/resident smoke, and closure export. Replayable GF proof inputs are published with the release. Linux parity, hook canary, Claude/Codex real-CLI harness probes, native arm64/ad-hoc signature checks, package install, and repository gates are green at $rev." \
      >"$release_notes"
    release_create_attempted=1
    gh release create "$tag" "$stage/ptoon-x86_64-linux.nar" "$stage/ptoon-aarch64-darwin.nar" "$stage/ptoon-aarch64-darwin.bin" "$stage/ptoon-aarch64-darwin.gf-nix-bridge.json" "$stage/ptoon-aarch64-darwin.gf-proof-result.json" "$stage/ptoon-aarch64-darwin.gf-exported-outputs.json" "$stage/ptoon-aarch64-darwin.gf-proof-result.attestation.json" "$stage/ptoon-aarch64-darwin.native-smoke.json" "$wheel" "$manifest" "$manifest.asc" \
      --repo "$canonical_repo" \
      --draft \
      --verify-tag \
      --title "prompt-toon v$version" \
      --notes-file "$release_notes"
    created_release_id="$(gh release view "$tag" --repo "$canonical_repo" --json id --jq .id)"
    [ -n "$created_release_id" ] || { echo "created release has no stable GitHub id" >&2; exit 1; }
    gh release edit "$tag" --repo "$canonical_repo" --draft=false
    release_completed=1
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
      BAZEL_OUTPUT_BASE="${proof_root}/forced-output" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --remote_accept_cached=false --nocache_test_results --test_output=errors --execution_log_json_file="${execution_log}" "{{target}}" 2>&1 | tee "${log}"
      exit 0
    fi
    warm_log="${proof_root}/warm.log"
    warm_execution_log="${proof_root}/warm.execution.json"
    warm_output_base="${proof_root}/warm-output"
    measured_output_base="${proof_root}/measured-output"
    if ! BAZEL_OUTPUT_BASE="${warm_output_base}" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --remote_accept_cached=false --nocache_test_results --test_output=errors --execution_log_json_file="${warm_execution_log}" "{{target}}" >"${warm_log}" 2>&1; then
      cat "${warm_log}" >&2
      exit 1
    fi
    echo "GF warm pass completed; measuring remote execution plus cache reuse"
    BAZEL_OUTPUT_BASE="${measured_output_base}" GF_BAZEL_SUBSTRATE_MODE=executor-backed GF_BAZEL_REMOTE_UPLOAD=false gloriousflywheel-bazel test --config=executor-backed --nocache_test_results --test_output=errors --execution_log_json_file="${execution_log}" "{{target}}" 2>&1 | tee "${log}"

# TIN-2709/TIN-2949: fixed, action-level proof for the registered Linux Chapel
# toolchain on GF REAPI. The environmental bridge is deliberately non-cacheable;
# this gate requires a fresh remote ChapelCompile and retains its execution JSON.
flywheel-chapel-proof log execution_log:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{root}}
    log="{{log}}"
    execution_log="{{execution_log}}"
    proof_root="$(mktemp -d "${TMPDIR:-/tmp}/prompt-toon-chapel-rbe.XXXXXX")"
    trap 'rm -rf "${proof_root}"' EXIT
    mkdir -p "$(dirname "${log}")" "$(dirname "${execution_log}")"
    : >"${log}"
    : >"${execution_log}"
    BAZEL_OUTPUT_BASE="${proof_root}/chapel-output" \
      GF_BAZEL_SUBSTRATE_MODE=executor-backed \
      GF_BAZEL_REMOTE_UPLOAD=false \
      gloriousflywheel-bazel build \
        --config=executor-backed \
        --remote_accept_cached=false \
        --execution_log_json_file="${execution_log}" \
        --spawn_strategy=remote \
        --remote_local_fallback=false \
        //src/ptoon:ptoon 2>&1 | tee "${log}"
    python3 tools/bazel/assert_execution_log.py \
      "${execution_log}" \
      --target //src/ptoon:ptoon \
      --mnemonic ChapelCompile \
      --platform-property gf.platform=gloriousflywheel-rbe-linux-x86_64

# Operator convenience wrapper for the same fixed proof. Artifacts persist in
# a stable temporary directory unless PROMPT_TOON_CHAPEL_PROOF_DIR overrides it.
flywheel-chapel:
    #!/usr/bin/env bash
    set -euo pipefail
    proof_dir="${PROMPT_TOON_CHAPEL_PROOF_DIR:-${TMPDIR:-/tmp}/prompt-toon-chapel-proof}"
    mkdir -p "${proof_dir}"
    just flywheel-chapel-proof \
      "${proof_dir}/chapel-rbe.log" \
      "${proof_dir}/chapel-execution.json"
    printf 'Chapel proof artifacts: %s\n' "${proof_dir}"
