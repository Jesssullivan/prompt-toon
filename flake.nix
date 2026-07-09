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
            --set-default CHPL_HWLOC bundled
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
          '';
        };

        checks.prompt-toon-package = promptToon;
      }
    );
}
