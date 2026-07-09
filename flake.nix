{
  description = "prompt-toon: local agent research condenser with measured TOON support";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
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
        packages.default = promptToon;
        packages.prompt-toon = promptToon;

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            bazelisk
            buildifier
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
