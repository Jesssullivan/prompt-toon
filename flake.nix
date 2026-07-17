{
  description = "prompt-toon: harness-to-model streaming IO middleware with bounded Chapel transforms";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    # Chapel toolchain for the TIN-2707+ ptoon lanes. Deliberately NOT
    # following our nixpkgs: chapel-nix's own lock is what its binary cache
    # was built against. Compilation is remote-only (x86_64-linux builders /
    # GloriousFlywheel) per operator doctrine — no darwin chpl iteration.
    chapel-nix = {
      url = "github:Jesssullivan/chapel/llvm-21-support";
    };
  };

  outputs =
    { self, nixpkgs, flake-utils, chapel-nix, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        lib = nixpkgs.lib;

        # CHPL_HOME layout fixup + baked compiler env, copied from
        # remote-juggler/flake.nix (the active house chapel consumption path).
        chapelWrapped = pkgs.runCommand "chapel-wrapped"
          {
            nativeBuildInputs = [ pkgs.makeWrapper ];
          } ''
          mkdir -p $out/bin $out/share/chapel
          for item in ${chapel-nix.packages.${system}.chapel-system-llvm}/share/chapel/*; do
            ln -sf "$item" "$out/share/chapel/$(basename "$item")"
          done
          ln -sf ${chapel-nix.packages.${system}.chapel-system-llvm}/lib $out/share/chapel/lib
          makeWrapper ${chapel-nix.packages.${system}.chapel-system-llvm}/bin/.chpl-wrapped $out/bin/chpl \
            --set CHPL_HOME "$out/share/chapel" \
            --set-default CHPL_LLVM system \
            --set-default CHPL_LLVM_CONFIG "${pkgs.llvmPackages_19.llvm.dev}/bin/llvm-config" \
            --set-default CHPL_HOST_COMPILER llvm \
            --set-default CHPL_HOST_CC "${pkgs.llvmPackages_19.clang}/bin/clang" \
            --set-default CHPL_HOST_CXX "${pkgs.llvmPackages_19.clang}/bin/clang++" \
            --set-default CHPL_TARGET_CC "${pkgs.llvmPackages_19.clang}/bin/clang" \
            --set-default CHPL_TARGET_CXX "${pkgs.llvmPackages_19.clang}/bin/clang++" \
            --set-default CHPL_TARGET_CPU none \
            --set-default CHPL_GMP none \
            --set-default CHPL_RE2 bundled \
            --set-default CHPL_UNWIND ${if pkgs.stdenv.isDarwin then "system" else "bundled"} \
            --set-default CHPL_LAUNCHER none \
            --set-default CHPL_COMM none \
            --set-default CHPL_TASKS qthreads \
            --set-default CHPL_TARGET_MEM jemalloc \
            --set-default CHPL_HWLOC bundled \
            --prefix PATH : '${lib.makeBinPath [
              pkgs.coreutils pkgs.gnumake pkgs.pkg-config pkgs.python3 pkgs.which
              pkgs.llvmPackages_19.clang pkgs.llvmPackages_19.llvm
            ]}' \
            ${lib.optionalString pkgs.stdenv.isLinux
              "--add-flags '-I ${pkgs.llvmPackages_19.clang}/resource-root/include' --add-flags '-I ${pkgs.llvmPackages_19.bintools.libc.dev}/include'"
            } \
            ${lib.optionalString pkgs.stdenv.isDarwin
              "--add-flags '-I ${pkgs.llvmPackages_19.clang}/resource-root/include'"
            }
        '';

        # TIN-2706 packaging SSOT: every derivation's version and the shipped
        # skill set derive from the committed, drift-gated manifest (a Bazel
        # sh_test regenerates and byte-diffs it in //...). Bump the version in
        # prompt_toon/__init__.py, regenerate, and everything here follows.
        manifest = lib.importJSON ./packaging/manifest.json;

        # C0 spike (TIN-2707): compile the Chapel normalize+redact port and
        # run the 12-case parity corpus + benchmark against the Python
        # oracle, entirely inside the build (remote-only substrate).
        ptoonSpikeParity = pkgs.stdenv.mkDerivation {
          pname = "ptoon-spike-parity";
          version = manifest.version;
          src = self;
          nativeBuildInputs = [ chapelWrapped pkgs.python3 ];
          buildPhase = ''
            runHook preBuild
            cd spikes/tin-2707
            chpl --fast ptoon_spike.chpl -o ptoon-spike
            runHook postBuild
          '';
          doCheck = true;
          checkPhase = ''
            runHook preCheck
            python3 run_parity.py
            runHook postCheck
          '';
          installPhase = ''
            runHook preInstall
            mkdir -p $out/bin
            cp ptoon-spike $out/bin/
            cp parity-report.md $out/
            runHook postInstall
          '';
        };
        # C1 (TIN-2708): ptoonBinary. ARCHITECTURE PIVOT — this used to be a
        # ctypes-loaded shared library (libptoon), but repeated exported-proc
        # calls through a `chpl --library --dynamic` .so hit a Chapel
        # foreign-thread runtime-reentry wall (init/caps/normalize succeed,
        # the free crashes) — exactly the fragility the C1 research warned
        # about. The C0 spike (spikes/tin-2707/ptoon_spike.chpl) is a
        # standalone `proc main` binary reading stdin / writing stdout and ran
        # clean with byte-parity. Both the one-shot transforms and C4's private
        # resident child keep runtime ownership inside a process, never an
        # in-process FFI re-entry. This derivation compiles src/ptoon/Main.chpl
        # plus the transform/stream/service modules with plain `chpl --fast`,
        # and the check phase runs the fixed binary protocol's
        # smoke test directly rather than grepping a dynamic symbol table.
        #
        # Defined for every `eachDefaultSystem` system. C4d release provenance
        # covers x86_64-linux and aarch64-darwin; each is compiled and smoke
        # tested on its native remote builder. Full byte parity remains the
        # x86_64-linux gate because that derivation runs the complete corpus.
        ptoonBinary = pkgs.stdenv.mkDerivation {
          pname = "ptoon";
          version = manifest.version;
          src = self;
          nativeBuildInputs = [ chapelWrapped pkgs.binutils pkgs.python3 ];
          buildPhase = ''
            runHook preBuild
            # Compile from repo root so the modules' file-relative
            # `require "../../c_src/..."` paths resolve. Main.chpl is the
            # standalone binary entry; -M finds the sibling
            # sibling modules it uses. Toon.chpl remains intentionally
            # excluded: TOON is Chapel-property-tested but no binary protocol
            # command calls it.
            chpl --fast src/ptoon/Main.chpl -M src/ptoon -o ptoon
            runHook postBuild
          '';
          doCheck = true;
          # The nix build sandbox does not expose /sys, so the Chapel
          # runtime's hwloc topology probe aborts and segfaults inside
          # chpl_library_init/chpl_gen_init for a standalone binary too, not
          # just under ctypes — hand hwloc a synthetic topology so it skips
          # OS discovery, and pin the qthreads worker count so the runtime
          # never reads /sys. Mirrors the env used by the ptoon-parity
          # derivation below, which runs this same binary as a subprocess.
          checkPhase = ''
            runHook preCheck
            echo "== src/ptoon build output =="
            find . -maxdepth 2 -type f | sort
            if [ ! -f ./ptoon ]; then
              echo "ERROR: chpl --fast src/ptoon/Main.chpl produced no ./ptoon binary" >&2
              exit 1
            fi
            if [ ! -x ./ptoon ]; then
              echo "ERROR: ./ptoon was produced but is not executable" >&2
              exit 1
            fi
            export HWLOC_SYNTHETIC="core:2 pu:1"
            export CHPL_RT_NUM_THREADS_PER_LOCALE=2
            export QT_NUM_SHEPHERDS=1
            export QT_NUM_WORKERS_PER_SHEPHERD=2
            echo "== smoke test: echo hi | ./ptoon normalize =="
            # NB: not `out=...` -- that name is Nix's output store path;
            # clobbering it makes installPhase/fixupPhase target the wrong dir.
            smoke=$(echo hi | ./ptoon normalize)
            echo "-> $smoke"
            if [ -z "$smoke" ]; then
              echo "ERROR: ./ptoon normalize produced no output for smoke input" >&2
              exit 1
            fi
            echo "== capability smoke: serve protocol v1 =="
            ./ptoon caps > caps.json
            python3 - <<'PY'
            import json
            from pathlib import Path

            caps = json.loads(Path("caps.json").read_text(encoding="utf-8"))
            assert caps.get("serve_protocol") == 1
            assert "serve" in caps.get("features", [])
            PY
            ${lib.optionalString pkgs.stdenv.isDarwin ''
              echo "== native Darwin one-shot document-count matrix =="
              export PROMPT_TOON_PTOON="$PWD/ptoon"
              python3 tools/one_shot_count_matrix.py
              echo "== native Darwin resident serve round trip =="
              python3 tools/service_parity.py
              echo "== native Darwin offline dogfood round trip =="
              PYTHONPATH="$PWD" python3 -m unittest \
                tests.test_dogfood.DogfoodTests.test_real_chapel_dogfood_integration \
                -v
              echo "== native Darwin offline quality fixtures =="
              python3 tools/gen_fixtures.py
              PYTHONPATH="$PWD" python3 tools/quality_runner.py --engine chapel
            ''}
            echo "OK: ptoon binary built and passed the normalize smoke test"
            runHook postCheck
          '';
          installPhase = ''
            runHook preInstall
            mkdir -p $out/bin
            cp ptoon $out/bin/ptoon
            runHook postInstall
          '';
        };
        # C1 (TIN-2708): full parity of the ptoon binary vs the Python oracle.
        # Regenerates the gitignored shared corpus (gen_fixtures.py inputs +
        # gen_golden.py python-oracle goldens), then invokes the built ptoon
        # binary as a subprocess (via PROMPT_TOON_PTOON, the fixed
        # normalize/defang/redact/caps binary protocol) and diffs it against
        # cli.py two ways: per-function (parity_runner.py --functions) and
        # through the full condense pipeline (parity_runner.py:
        # source-cards.jsonl/summary.md/manifest.json byte-identical to the
        # goldens under --engine=chapel). Finally runs the engine unittest
        # with the binary present (so the degraded-mode skips actually
        # execute) and the full Python suite. Remote-only, x86_64-linux — the
        # binary is an ELF the darwin host cannot execute. This is the C1
        # correctness gate: it fails the build on ANY byte divergence, which
        # is where the C0 Unicode-\b fix in Redact.chpl gets proven.
        ptoonParity = pkgs.stdenv.mkDerivation {
          pname = "ptoon-parity";
          version = manifest.version;
          src = self;
          nativeBuildInputs = [ pkgs.python3 ];
          dontConfigure = true;
          buildPhase = ''
            runHook preBuild
            # errexit + pipefail: every gate below is `cmd | tee`, and WITHOUT
            # pipefail a pipeline's exit status is tee's (always 0), which
            # would silently mask a parity DIFF or unittest failure and let a
            # divergent build pass. This is the correctness gate — it must
            # abort on the first nonzero.
            set -eo pipefail
            export PROMPT_TOON_PTOON="${ptoonBinary}/bin/ptoon"
            export PYTHONPATH="$PWD"
            export PROMPT_TOON_STATE_HOME="$TMPDIR/state"
            # The nix build sandbox does not expose /sys, so the Chapel
            # runtime's hwloc topology probe aborts and segfaults inside
            # chpl_library_init. Hand hwloc a synthetic topology so it skips
            # OS discovery, and pin the qthreads worker count so the runtime
            # never reads /sys. The batch/stream coforall paths run right
            # here in-sandbox under this pinned 2-worker topology (batch,
            # stream, and hook-canary gates below), so budget-sensitive
            # tests must use generous budgets.
            export HWLOC_SYNTHETIC="core:2 pu:1"
            export CHPL_RT_NUM_THREADS_PER_LOCALE=2
            export QT_NUM_SHEPHERDS=1
            export QT_NUM_WORKERS_PER_SHEPHERD=2
            # Nix sandboxes do not provide /usr/bin/env. The resident unit
            # fake is an executable Python child, so give its shebang the
            # interpreter from nativeBuildInputs before the full suite.
            patchShebangs tests/fake_ptoon_serve.py
            echo "== generating fixture corpus (inputs) =="
            python3 tools/gen_fixtures.py
            echo "== generating golden corpus (python oracle) =="
            python3 tools/gen_golden.py
            echo "== offline quality fixtures (python oracle) =="
            python3 tools/quality_runner.py --engine python | tee quality-python.json
            echo "== offline quality fixtures (chapel) =="
            python3 tools/quality_runner.py --engine chapel | tee quality-chapel.json
            echo "== function-level parity (chapel vs python oracle) =="
            python3 tools/parity_runner.py --functions --require-chapel | tee parity-functions.md
            echo "== condense parity (chapel vs python goldens, byte-identical) =="
            python3 tools/parity_runner.py --require-chapel | tee parity-condense.md
            echo "== batch parity (redact-batch coforall == per-doc redact) =="
            python3 tools/batch_parity.py | tee parity-batch.md
            echo "== stream parity (condense-batch cards == python oracle cards) =="
            python3 tools/stream_parity.py | tee parity-stream.md
            echo "== one-shot Chapel document-count matrix (ordered 1/8/32/64 fan-in) =="
            python3 tools/one_shot_count_matrix.py | tee one-shot-count-matrix.md
            echo "== resident service parity (multiplexed serve == one-shot condense) =="
            python3 tools/service_parity.py | tee parity-service.md
            echo "== resident service capacity (64 streams, bounded RSS) =="
            python3 tools/service_capacity.py | tee capacity-service.md
            echo "== gateway capacity (64 HTTP/SSE streams per provider, real resident child) =="
            python3 tools/gateway_capacity.py | tee capacity-gateway.md
            echo "== hook canary (PostToolUse adapter end-to-end vs the real binary) =="
            python3 tools/hook_canary.py | tee hook-canary.md
            echo "== analyze regression (python oracle vs COMMITTED pinned baseline) =="
            python3 tools/parity_runner.py --analyze --require-chapel | tee parity-analyze.md
            echo "== engine unittest with ptoon binary present =="
            python3 -m unittest discover -s tests -p 'test_engine.py' -v 2>&1 | tee engine-tests.txt
            echo "== full suite with ptoon binary present (chapel-dependent tests now run) =="
            python3 -m unittest discover -s tests -p 'test_*.py' 2>&1 | tee full-suite.txt
            runHook postBuild
          '';
          installPhase = ''
            runHook preInstall
            mkdir -p $out
            cp quality-python.json quality-chapel.json parity-functions.md parity-condense.md parity-batch.md parity-stream.md one-shot-count-matrix.md parity-service.md capacity-service.md capacity-gateway.md parity-analyze.md hook-canary.md engine-tests.txt full-suite.txt $out/ 2>/dev/null || true
            runHook postInstall
          '';
        };
        promptToon = pkgs.stdenvNoCC.mkDerivation {
          pname = "prompt-toon";
          version = manifest.version;
          src = self;
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            mkdir -p "$out/lib/prompt-toon" "$out/bin" "$out/share/prompt-toon/skills"
            cp -R prompt_toon "$out/lib/prompt-toon/"
            ${lib.concatMapStringsSep "\n" (skill: ''
              cp -R ${lib.escapeShellArg ".agents/skills/${skill}"} "$out/share/prompt-toon/skills/"
            '') manifest.skills}
            cp -R policy "$out/share/prompt-toon/policy"
            cat > "$out/lib/prompt-toon-launcher.py" <<EOF
            import runpy
            import sys

            sys.path.insert(0, "$out/lib/prompt-toon")
            runpy.run_module("prompt_toon", run_name="__main__", alter_sys=True)
            EOF
            cat > "$out/bin/prompt-toon" <<EOF
            #!${pkgs.bash}/bin/bash
            export PROMPT_TOON_IO_POLICY="\''${PROMPT_TOON_IO_POLICY:-$out/share/prompt-toon/policy/io.json}"
            exec ${pkgs.python3}/bin/python -I "$out/lib/prompt-toon-launcher.py" "\$@"
            EOF
            chmod +x "$out/bin/prompt-toon"
            runHook postInstall
          '';
          doInstallCheck = true;
          installCheckPhase = ''
            runHook preInstallCheck
            test "$("$out/bin/prompt-toon" --version)" = "prompt-toon ${manifest.version}"
            shadow_dir="$(mktemp -d)"
            mkdir -p "$shadow_dir/prompt_toon"
            : > "$shadow_dir/prompt_toon/__init__.py"
            printf 'print("checkout-shadow")\n' > "$shadow_dir/prompt_toon/__main__.py"
            poison_path="$shadow_dir/python-env"
            mkdir -p "$poison_path"
            printf 'raise SystemExit("sitecustomize loaded")\n' > "$poison_path/sitecustomize.py"
            user_site="$(PYTHONUSERBASE="$shadow_dir/userbase" ${pkgs.python3}/bin/python -c 'import site; print(site.getusersitepackages())')"
            mkdir -p "$user_site"
            cp "$poison_path/sitecustomize.py" "$user_site/sitecustomize.py"
            (
              cd "$shadow_dir"
              test "$(PYTHONPATH="$poison_path" PYTHONUSERBASE="$shadow_dir/userbase" "$out/bin/prompt-toon" --version)" = "prompt-toon ${manifest.version}"
            )
            runtime_policy="$shadow_dir/runtime-policy.json"
            cp "$out/share/prompt-toon/policy/io.json" "$runtime_policy"
            PROMPT_TOON_IO_POLICY="$runtime_policy" "$out/bin/prompt-toon" doctor --timeout 0 > "$shadow_dir/doctor.json"
            ${pkgs.python3}/bin/python - "$runtime_policy" "$shadow_dir/doctor.json" <<'PY'
            import json
            import sys

            with open(sys.argv[2], encoding="utf-8") as handle:
                doctor = json.load(handle)
            assert doctor["adoption"]["policy"]["path"] == sys.argv[1]
            assert doctor["adoption"]["policy"]["available"] is True
            PY
            "$out/bin/prompt-toon" corpus-report --help >/dev/null
            "$out/bin/prompt-toon" provider-usage-import --help >/dev/null
            "$out/bin/prompt-toon" provider-usage-compare --help >/dev/null
            "$out/bin/prompt-toon" claude-profile direct >/dev/null
            "$out/bin/prompt-toon" codex-profile direct >/dev/null
            runHook postInstallCheck
          '';
        };
      in
      {
        packages = {
          default = promptToon;
          prompt-toon = promptToon;
          # C1 (TIN-2708): defined on every system. C4d publishes the
          # x86_64-linux and aarch64-darwin instances after native remote
          # smoke tests; the full parity derivation remains Linux-only.
          ptoon = ptoonBinary;
        } // lib.optionalAttrs (system == "x86_64-linux") {
          ptoon-spike-parity = ptoonSpikeParity;
          ptoon-parity = ptoonParity;
        };

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            bazelisk
            buildifier
            dhall
            dhall-json
            gh
            git
            gitleaks
            jq
            just
            python3
            uv
          ];
          shellHook = ''
            echo "prompt-toon dev shell"
            echo "ptoon binary (TIN-2708 C1): 'just build-ptoon' (nix remote) or 'just flywheel-chapel' (GF REAPI) — remote-only, never local chpl (see AGENTS.md)."
          '';
        };

        checks.prompt-toon-package = promptToon;
      }
    );
}
