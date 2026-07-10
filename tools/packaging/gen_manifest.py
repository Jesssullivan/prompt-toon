#!/usr/bin/env python3
"""TIN-2706: packaging-SSOT manifest generator (TIN-2046 pattern).

ONE Bazel-runnable generator emits `packaging/manifest.json`; the nix flake
CONSUMES it (version, skills); install lanes are DERIVED from it, never
hand-edited. The manifest is COMMITTED and drift-gated (the repo's proven
dhall->json pattern: regenerate + byte-diff in CI), because nix cannot run
Bazel inside its sandbox — the committed artifact is the consumption point.

VERSION SSOT: `prompt_toon/__init__.py:__version__` is the one string a
release bump edits. pyproject.toml derives it (setuptools dynamic version);
the nix derivations read it out of this manifest; this generator asserts
the one residual static declaration (MODULE.bazel, cosmetic until the
bazel-registry lane opens) still agrees — drift there fails the gate
loudly rather than shipping skew.

DETERMINISM SPLIT (same doctrine as the ptoon stream surface): the
COMMITTED manifest is a pure function of repo content — `git_rev` is the
literal "UNSTAMPED" and every targets[] sha256/size is null (a committed
self-hash would be stale by construction). The RELEASE lane re-runs this
generator with --git-rev/--tag/--ci-run/--with-binary to stamp provenance
and inject the real artifact digest; derived lanes (GH Release source.json,
brew bottle, nfpm rpm/deb) consume THAT stamped emission, keyed by the same
schema.

Usage:
  gen_manifest.py                      # write packaging/manifest.json
  gen_manifest.py --check              # regenerate, byte-diff, exit 1 on drift
  gen_manifest.py --stdout             # print instead of writing
  gen_manifest.py --git-rev SHA --tag v0.2.0 --ci-run 123 \
                  --with-binary path/to/ptoon   # stamped release emission
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "packaging" / "manifest.json"
SCHEMA_VERSION = 1
SAFE_PACKAGE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def version_from_init() -> str:
    """The SSOT: prompt_toon/__init__.py __version__, read via AST (no import
    side effects, no PYTHONPATH assumptions)."""
    tree = ast.parse((ROOT / "prompt_toon" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str):
                        return value
    raise SystemExit("gen_manifest: no __version__ in prompt_toon/__init__.py")


def assert_declared_versions_agree(version: str) -> None:
    """MODULE.bazel cannot derive its version string; fail loudly on skew."""
    module_text = (ROOT / "MODULE.bazel").read_text(encoding="utf-8")
    match = re.search(r'version\s*=\s*"([^"]+)"', module_text)
    if not match or match.group(1) != version:
        raise SystemExit(
            f"gen_manifest: MODULE.bazel version {match.group(1) if match else '?'!s} "
            f"!= SSOT {version} (prompt_toon/__init__.py) — update MODULE.bazel"
        )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if 'dynamic = ["version"]' not in pyproject.replace("'", '"'):
        raise SystemExit(
            "gen_manifest: pyproject.toml must declare dynamic version deriving "
            "from prompt_toon.__version__ (the SSOT), not a static string"
        )


def file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def validate_package_component(kind: str, value: str) -> str:
    if not SAFE_PACKAGE_COMPONENT_RE.fullmatch(value):
        raise SystemExit(
            f"gen_manifest: unsafe {kind} name {value!r}; use only "
            "ASCII letters, digits, dot, underscore, and dash"
        )
    return value


def build_manifest(args: argparse.Namespace) -> dict:
    version = version_from_init()
    assert_declared_versions_agree(version)

    skills = sorted(
        validate_package_component("skill", p.name)
        for p in (ROOT / ".agents" / "skills").iterdir()
        if p.is_dir()
    )
    policy = [
        {"file": f"policy/{p.name}", "sha256": file_digest(p)}
        for p in sorted((ROOT / "policy").glob("*.json"))
    ]

    ptoon_target: dict = {
        "platform": "x86_64-linux",
        "kind": "static-binary",
        "artifact": "ptoon",
        "sha256": None,
        "size": None,
    }
    if args.with_binary:
        binary = Path(args.with_binary)
        ptoon_target["sha256"] = file_digest(binary)
        ptoon_target["size"] = binary.stat().st_size

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "prompt-toon",
        "version": version,
        "git_rev": args.git_rev or "UNSTAMPED",
        "provenance": {
            "repo": "github.com/Jesssullivan/prompt-toon",
            "tag": args.tag,
            "ci_run": args.ci_run,
        },
        "build": {
            "toolchain": "chapel+python",
            "chapel": {
                "source": "github:Jesssullivan/chapel/llvm-21-support",
                "flags": "--fast",
                "substrate": "remote-only (nix remote builder / GF REAPI; never local darwin)",
            },
            "python": ">=3.11",
        },
        "targets": [
            ptoon_target,
            {
                "platform": "any",
                "kind": "python-package",
                "artifact": "prompt_toon",
                "sha256": None,
                "size": None,
            },
        ],
        "skills": skills,
        "policy": policy,
        "derived_lanes": {
            "nix": {"enabled": True},
            "home_manager": {"enabled": True},
            "pipx": {"enabled": False, "note": "warm; private-repo auth gated"},
            "bazel_registry": {
                "enabled": False,
                "note": "phase gate OPEN since C2 (static binary exists); enabling = building the lane (source.json derives from targets[].sha256)",
            },
            "gh_release": {
                "enabled": True,
                "note": "operated via `just release <version>` (remote-builder ptoon build + stamped manifest + gh release); CI tag-push automation stays gated on a publicly reachable chapel cache (operator decision)",
            },
            "brew": {"enabled": False, "note": "C3 phase gate"},
            "rpm_deb": {"enabled": False, "note": "C3 phase gate (nfpm)"},
        },
    }


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="regenerate and byte-diff against the committed manifest")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--git-rev", default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--ci-run", default=None)
    parser.add_argument("--with-binary", default=None,
                        help="inject sha256/size of a built ptoon binary (release lane)")
    args = parser.parse_args()

    stamped = bool(args.git_rev or args.tag or args.ci_run or args.with_binary)
    rendered = render(build_manifest(args))

    if args.check:
        if stamped:
            raise SystemExit("gen_manifest: --check compares the UNSTAMPED committed form")
        committed = MANIFEST_PATH.read_bytes() if MANIFEST_PATH.exists() else b""
        if committed != rendered.encode("utf-8"):
            sys.stderr.write(
                "gen_manifest: packaging/manifest.json has drifted from repo truth; "
                "run tools/packaging/gen_manifest.py and commit the result\n"
            )
            return 1
        print("manifest: no drift")
        return 0

    if args.stdout or stamped:
        # Stamped emissions never overwrite the committed SSOT manifest.
        sys.stdout.write(rendered)
        return 0

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {MANIFEST_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
