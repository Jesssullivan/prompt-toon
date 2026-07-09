{
  description = "prompt-toon: local agent research condenser with measured TOON support";

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

        # C0 spike (TIN-2707): compile the Chapel normalize+redact port and
        # run the 12-case parity corpus + benchmark against the Python
        # oracle, entirely inside the build (remote-only substrate).
        ptoonSpikeParity = pkgs.stdenv.mkDerivation {
          pname = "ptoon-spike-parity";
          version = "0.1.0";
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
        # C1 (TIN-2708): libptoon build-lane skeleton. Compiles src/ptoon/
        # (module shell + six-symbol C ABI stubs, no redaction/normalize/
        # defang semantics yet) into a shared library with `chpl --library
        # --dynamic`, then greps the exported dynamic symbol table for the
        # six ptoon_* entry points inside the check phase. This proves the
        # `chpl --library` toolchain lane end-to-end (remote-only, per
        # AGENTS.md doctrine) so Phase 2 can drop real semantics into
        # src/ptoon/Ptoon.chpl without re-deriving the build wiring.
        #
        # Defined for every `eachDefaultSystem` system (linux + darwin) so
        # a darwin variant exists structurally, but only x86_64-linux is
        # the required/CI-verified target — the pzm darwin builder is
        # still in burn-in, and `chpl --library --dynamic` on darwin emits
        # a .dylib with different nm semantics that this check phase does
        # not fully account for yet.
        libptoon = pkgs.stdenv.mkDerivation {
          pname = "libptoon";
          version = "0.1.0";
          src = self;
          nativeBuildInputs = [ chapelWrapped pkgs.binutils ];
          buildPhase = ''
            runHook preBuild
            cd src/ptoon
            chpl --fast --library --dynamic Ptoon.chpl -o ptoon
            runHook postBuild
          '';
          doCheck = true;
          # NOTE: nix's stdenv builder runs phases with `shopt -s nullglob`,
          # so an unmatched `ls lib*.so lib*.dylib` silently degrades to a
          # bare `ls` (whole-directory listing) rather than empty output —
          # a real footgun that produced a false-positive "so" value on the
          # first iteration of this derivation. Use `find` throughout so an
          # unmatched pattern is unambiguously empty. Chapel's `--library`
          # mode also does not place output next to the source file (it
          # defaults to a `lib/` subdirectory of the cwd) — search
          # recursively rather than assuming a fixed location.
          checkPhase = ''
            runHook preCheck
            echo "== src/ptoon build output (recursive) =="
            find . -maxdepth 3 -type f | sort
            so=$(find . -type f \( -name 'lib*.so' -o -name 'lib*.dylib' \) | head -n1)
            if [ -z "$so" ]; then
              echo "ERROR: chpl --library --dynamic produced no lib*.so/.dylib anywhere under the build dir" >&2
              exit 1
            fi
            hdr=$(find . -type f -name '*.h' | head -n1)
            if [ -n "$hdr" ]; then
              echo "== generated library C header: $hdr =="
              cat "$hdr"
            fi
            echo "== dynamic symbol table: $so =="
            nm -D "$so" | tee nm-output.txt
            grep ' T ' nm-output.txt | awk '{print $NF}' | sort -u > exported-symbols.txt
            missing=0
            for sym in ptoon_redact ptoon_normalize ptoon_defang ptoon_toon_encode ptoon_free ptoon_engine_caps; do
              if ! grep -qx "$sym" exported-symbols.txt; then
                echo "MISSING EXPORTED SYMBOL: $sym" >&2
                missing=1
              fi
            done
            if [ "$missing" -ne 0 ]; then
              exit 1
            fi
            echo "OK: all six ptoon_* C ABI symbols present in $so"
            echo "$so" > .ptoon-so-path
            if [ -n "$hdr" ]; then echo "$hdr" > .ptoon-hdr-path; fi
            runHook postCheck
          '';
          installPhase = ''
            runHook preInstall
            mkdir -p $out/lib $out/include $out/share/ptoon
            if [ -f .ptoon-so-path ]; then cp "$(cat .ptoon-so-path)" $out/lib/; fi
            if [ -f .ptoon-hdr-path ]; then cp "$(cat .ptoon-hdr-path)" $out/include/; fi
            cp exported-symbols.txt $out/share/ptoon/ 2>/dev/null || true
            runHook postInstall
          '';
        };
        promptToon = pkgs.stdenvNoCC.mkDerivation {
          pname = "prompt-toon";
          version = "0.1.0";
          src = self;
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            mkdir -p "$out/lib/prompt-toon" "$out/bin" "$out/share/prompt-toon/skills"
            cp -R prompt_toon "$out/lib/prompt-toon/"
            cp -R .agents/skills/prompt-toon "$out/share/prompt-toon/skills/"
            cp -R .agents/skills/mythos-delegation "$out/share/prompt-toon/skills/"
            cp -R policy "$out/share/prompt-toon/policy"
            cat > "$out/bin/prompt-toon" <<EOF
            #!${pkgs.bash}/bin/bash
            export PYTHONPATH="$out/lib/prompt-toon''${PYTHONPATH:+:''${PYTHONPATH}}"
            exec ${pkgs.python3}/bin/python -m prompt_toon "\$@"
            EOF
            chmod +x "$out/bin/prompt-toon"
            runHook postInstall
          '';
        };
      in
      {
        packages = {
          default = promptToon;
          prompt-toon = promptToon;
          # C1 (TIN-2708): defined on every system (see libptoon comment
          # above); x86_64-linux is the required/CI-verified target.
          libptoon = libptoon;
        } // lib.optionalAttrs (system == "x86_64-linux") {
          ptoon-spike-parity = ptoonSpikeParity;
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
          ];
          shellHook = ''
            echo "prompt-toon dev shell"
            echo "libptoon (TIN-2708 C1 skeleton): 'just build-lib' or 'make build-lib' — remote-only, never local chpl (see AGENTS.md)."
          '';
        };

        checks.prompt-toon-package = promptToon;
      }
    );
}
