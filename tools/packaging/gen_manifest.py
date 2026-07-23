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
literal "UNSTAMPED", every targets[] sha256/size is null, and every closure
target's entrypoint_sha256 is null (committed self-hashes would be stale by
construction). The RELEASE lane re-runs this generator with
--git-rev/--tag/--ci-run/--with-closure/--with-entrypoint/
--with-build-provenance/--with-wheel to stamp provenance and inject every
platform artifact, entrypoint, and wheel digest plus the authenticated GF
Darwin-to-Nix bridge; stamped output rejects incomplete target metadata.
Derived lanes (GH Release source.json, brew bottle, nfpm rpm/deb) consume THAT
stamped emission, keyed by the same schema.

Usage:
  gen_manifest.py                      # write packaging/manifest.json
  gen_manifest.py --check              # regenerate, byte-diff, exit 1 on drift
  gen_manifest.py --stdout             # print instead of writing
  gen_manifest.py --git-rev SHA --tag v0.2.0 --ci-run 123 \
                  --with-closure x86_64-linux=path/to/ptoon-linux.nar \
                  --with-entrypoint x86_64-linux=/nix/store/.../bin/ptoon \
                  --with-closure aarch64-darwin=path/to/ptoon-darwin.nar \
                  --with-entrypoint aarch64-darwin=/nix/store/.../bin/ptoon \
                  --with-build-provenance \
                    aarch64-darwin=path/to/ptoon-darwin.gf-nix-bridge.json \
                  --with-wheel path/to/prompt_toon.whl
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from email import policy as email_policy
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.packaging.import_gf_ptoon import (  # noqa: E402
    BridgeError,
    validate_bridge_record,
)

MANIFEST_PATH = ROOT / "packaging" / "manifest.json"
HOME_MANAGER_CONTRACT_PATH = ROOT / "packaging" / "home-manager.json"
RELEASE_SIGNERS_PATH = ROOT / "packaging" / "release-signers.json"
SCHEMA_VERSION = 2
SAFE_PACKAGE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PTOON_PLATFORMS = ("x86_64-linux", "aarch64-darwin")
PYTHON_WHEEL_TAG = "py3-none-any"


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
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_signer_entry() -> dict[str, str]:
    """Return the reviewed publisher trust anchor after checking its key bytes."""

    try:
        signers = json.loads(RELEASE_SIGNERS_PATH.read_text(encoding="utf-8"))
        active = signers["active"]
        fingerprint = active["fingerprint"]
        public_key_name = active["public_key"]
        public_key_sha256 = active["public_key_sha256"]
        scheme = active["scheme"]
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "gen_manifest: packaging/release-signers.json is invalid"
        ) from exc

    if (
        signers.get("schema_version") != 1
        or signers.get("repository") != "github.com/Jesssullivan/prompt-toon"
        or scheme != "openpgp"
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9A-F]{40,64}", fingerprint) is None
        or not isinstance(public_key_name, str)
        or not isinstance(public_key_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", public_key_sha256) is None
    ):
        raise SystemExit(
            "gen_manifest: packaging/release-signers.json has an invalid "
            "release signer contract"
        )

    relative_key = Path(public_key_name)
    if relative_key.is_absolute() or ".." in relative_key.parts:
        raise SystemExit("gen_manifest: release public key path must stay in the repo")
    public_key = ROOT / relative_key
    if public_key.is_symlink() or not public_key.is_file():
        raise SystemExit("gen_manifest: release public key must be a regular file")
    if file_digest(public_key) != public_key_sha256:
        raise SystemExit(
            "gen_manifest: release public key digest does not match "
            "packaging/release-signers.json"
        )

    return {
        "scheme": scheme,
        "fingerprint": fingerprint,
        "public_key": public_key_name,
        "public_key_sha256": public_key_sha256,
    }


def validate_universal_wheel(path: Path, version: str) -> None:
    """Require the exact pure-Python wheel promised by the release manifest."""

    expected_filename = f"prompt_toon-{version}-{PYTHON_WHEEL_TAG}.whl"
    if path.name != expected_filename:
        raise SystemExit(
            f"gen_manifest: release wheel must be {expected_filename}, not {path.name}"
        )
    if path.is_symlink() or not path.is_file():
        raise SystemExit("gen_manifest: release wheel must be a regular file")

    wheel_metadata_name = f"prompt_toon-{version}.dist-info/WHEEL"
    try:
        with ZipFile(path) as archive:
            wheel_metadata = archive.read(wheel_metadata_name)
    except (BadZipFile, KeyError, OSError) as exc:
        raise SystemExit(
            "gen_manifest: release wheel is not a valid wheel archive with "
            f"{wheel_metadata_name}"
        ) from exc

    metadata = BytesParser(policy=email_policy.default).parsebytes(wheel_metadata)
    if metadata.get("Root-Is-Purelib", "").lower() != "true":
        raise SystemExit(
            "gen_manifest: release wheel must declare Root-Is-Purelib: true"
        )
    if metadata.get_all("Tag", []) != [PYTHON_WHEEL_TAG]:
        raise SystemExit(
            f"gen_manifest: release wheel must declare exactly Tag: {PYTHON_WHEEL_TAG}"
        )


def policy_entries() -> list[dict[str, str]]:
    return [
        {"file": f"policy/{p.name}", "sha256": file_digest(p)}
        for p in sorted((ROOT / "policy").glob("*.json"))
    ]


def policy_digest_map() -> dict[str, str]:
    return {entry["file"]: entry["sha256"] for entry in policy_entries()}


def rendered_home_manager_contract(version: str, policy_digests: dict[str, str]) -> str:
    from prompt_toon.adoption import build_home_manager_contract, render_contract

    return render_contract(
        build_home_manager_contract(version=version, policy_digests=policy_digests)
    )


def validate_package_component(kind: str, value: str) -> str:
    if not SAFE_PACKAGE_COMPONENT_RE.fullmatch(value):
        raise SystemExit(
            f"gen_manifest: unsafe {kind} name {value!r}; use only "
            "ASCII letters, digits, dot, underscore, and dash"
        )
    return value


def platform_paths(
    specs: list[str], *, option: str, description: str
) -> dict[str, Path]:
    """Parse unambiguous PLATFORM=PATH inputs for a release artifact kind."""

    result: dict[str, Path] = {}
    for spec in specs:
        platform, separator, raw_path = spec.partition("=")
        if not separator or not raw_path:
            raise SystemExit(f"gen_manifest: {option} must be PLATFORM=PATH")
        if platform not in PTOON_PLATFORMS:
            raise SystemExit(
                f"gen_manifest: unsupported ptoon platform {platform!r}; "
                f"expected one of {', '.join(PTOON_PLATFORMS)}"
            )
        if platform in result:
            raise SystemExit(f"gen_manifest: duplicate {option} for {platform}")
        path = Path(raw_path)
        if not path.is_file():
            raise SystemExit(
                f"gen_manifest: ptoon {description} for {platform} is not a file"
            )
        result[platform] = path
    return result


def release_ptoon_paths(
    closure_specs: list[str], entrypoint_specs: list[str]
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Require complete, platform-matched closure and entrypoint provenance."""

    closures = platform_paths(
        closure_specs,
        option="--with-closure",
        description="closure",
    )
    entrypoints = platform_paths(
        entrypoint_specs,
        option="--with-entrypoint",
        description="entrypoint",
    )
    if bool(closures) != bool(entrypoints):
        raise SystemExit(
            "gen_manifest: --with-closure and --with-entrypoint must be "
            "provided together"
        )

    closure_platforms = set(closures)
    entrypoint_platforms = set(entrypoints)
    if closure_platforms != entrypoint_platforms:
        details: list[str] = []
        missing_entrypoints = sorted(closure_platforms - entrypoint_platforms)
        missing_closures = sorted(entrypoint_platforms - closure_platforms)
        if missing_entrypoints:
            details.append(
                "missing --with-entrypoint for " + ", ".join(missing_entrypoints)
            )
        if missing_closures:
            details.append("missing --with-closure for " + ", ".join(missing_closures))
        raise SystemExit(
            "gen_manifest: closure and entrypoint platform coverage must match; "
            + "; ".join(details)
        )

    if closure_platforms and closure_platforms != set(PTOON_PLATFORMS):
        missing = sorted(set(PTOON_PLATFORMS) - closure_platforms)
        raise SystemExit(
            "gen_manifest: stamped ptoon closure/entrypoint coverage is "
            "all-or-none; missing " + ", ".join(missing)
        )
    return closures, entrypoints


def release_build_provenance(
    specs: list[str],
    *,
    git_rev: str | None,
    closures: dict[str, Path],
    entrypoints: dict[str, Path],
) -> dict[str, dict]:
    """Validate the one authenticated GF Darwin-to-Nix release bridge."""

    records = platform_paths(
        specs,
        option="--with-build-provenance",
        description="build provenance record",
    )
    if not records:
        return {}
    if set(records) != {"aarch64-darwin"}:
        raise SystemExit(
            "gen_manifest: --with-build-provenance currently supports exactly "
            "aarch64-darwin"
        )
    if not git_rev or "aarch64-darwin" not in closures:
        raise SystemExit(
            "gen_manifest: Darwin build provenance requires --git-rev plus "
            "complete closure and entrypoint inputs"
        )
    try:
        return {
            "aarch64-darwin": validate_bridge_record(
                records["aarch64-darwin"],
                expected_revision=git_rev,
                closure_export=closures["aarch64-darwin"],
                entrypoint_export=entrypoints["aarch64-darwin"],
            )
        }
    except BridgeError as exc:
        raise SystemExit(
            f"gen_manifest: invalid Darwin build provenance: {exc}"
        ) from exc


def validate_stamped_artifacts(manifest: dict) -> None:
    """Reject provenance emissions that leave any release target unauthenticated."""

    missing: list[str] = []
    for target in manifest["targets"]:
        label = f"{target['artifact']}:{target['platform']}"
        for field in ("sha256", "size"):
            if target.get(field) is None:
                missing.append(f"{label}.{field}")
        if (
            target.get("kind") == "nix-closure-export"
            and target.get("entrypoint_sha256") is None
        ):
            missing.append(f"{label}.entrypoint_sha256")
        if (
            target.get("artifact") == "ptoon"
            and target.get("platform") == "aarch64-darwin"
            and target.get("build_provenance") is None
        ):
            missing.append(f"{label}.build_provenance")
    if missing:
        raise SystemExit(
            "gen_manifest: stamped emission requires complete release artifact "
            "digests; missing " + ", ".join(missing)
        )


def build_manifest(args: argparse.Namespace) -> dict:
    version = version_from_init()
    assert_declared_versions_agree(version)
    release_signer = release_signer_entry()

    skills = sorted(
        validate_package_component("skill", p.name)
        for p in (ROOT / ".agents" / "skills").iterdir()
        if p.is_dir()
    )
    policy = policy_entries()
    home_manager_contract = rendered_home_manager_contract(version, policy_digest_map())
    home_manager_sha256 = sha256(home_manager_contract.encode("utf-8")).hexdigest()

    release_closures, release_entrypoints = release_ptoon_paths(
        args.with_closure, args.with_entrypoint
    )
    build_provenance = release_build_provenance(
        args.with_build_provenance,
        git_rev=args.git_rev,
        closures=release_closures,
        entrypoints=release_entrypoints,
    )
    ptoon_targets: list[dict] = []
    for platform in PTOON_PLATFORMS:
        target: dict = {
            "platform": platform,
            "kind": "nix-closure-export",
            "artifact": "ptoon",
            "filename": f"ptoon-{platform}.nar",
            "closure_format": "nix-store-export-v1",
            "entrypoint": "bin/ptoon",
            "entrypoint_sha256": None,
            "build_provenance": None,
            "capabilities": {"serve_protocol": 1},
            "sha256": None,
            "size": None,
        }
        closure = release_closures.get(platform)
        if closure is not None:
            target["sha256"] = file_digest(closure)
            target["size"] = closure.stat().st_size
            target["entrypoint_sha256"] = file_digest(release_entrypoints[platform])
            target["build_provenance"] = build_provenance.get(platform)
        ptoon_targets.append(target)

    python_target: dict = {
        "platform": "any",
        "kind": "python-wheel",
        "artifact": "prompt_toon",
        "root_is_purelib": True,
        "wheel_tag": PYTHON_WHEEL_TAG,
        "capabilities": {
            "anthropic_shadow_gateway": 1,
            "openai_responses_shadow_gateway": 1,
        },
        "filename": None,
        "sha256": None,
        "size": None,
    }
    if args.with_wheel:
        wheel = Path(args.with_wheel)
        validate_universal_wheel(wheel, version)
        python_target["filename"] = wheel.name
        python_target["sha256"] = file_digest(wheel)
        python_target["size"] = wheel.stat().st_size

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "prompt-toon",
        "version": version,
        "git_rev": args.git_rev or "UNSTAMPED",
        "provenance": {
            "repo": "github.com/Jesssullivan/prompt-toon",
            "tag": args.tag,
            "ci_run": args.ci_run,
            "release_signer": release_signer,
        },
        "build": {
            "toolchain": "chapel+python",
            "chapel": {
                "source": "github:Jesssullivan/chapel/llvm-21-support",
                "flags": "--fast",
                "substrate": "native remote-only (x86_64-linux: nix/GF REAPI; aarch64-darwin: exact forced GF output plus byte-preserving Nix bridge; never local chpl)",
            },
            "python": ">=3.11",
        },
        "targets": [*ptoon_targets, python_target],
        "skills": skills,
        "policy": policy,
        "derived_lanes": {
            "nix": {"enabled": True},
            "home_manager": {
                "enabled": False,
                "contract_ready": True,
                "contract": "packaging/home-manager.json",
                "sha256": home_manager_sha256,
                "note": "A disabled lab consumer exists; enabled becomes true only after it pins the authenticated release closure and an attended host activation records the required evidence.",
            },
            "pipx": {
                "enabled": False,
                "note": "installable (explicit setuptools packages); private-repo auth gated",
            },
            "bazel_registry": {
                "enabled": False,
                "note": "phase gate OPEN since C2; enabling = building the lane. Per tinyland-inc/bazel-registry convention source.json integrity is the SRI of the TAG TARBALL (not targets[].sha256); repo visibility (private) is the consumption gate.",
            },
            "gh_release": {
                "enabled": True,
                "note": "operated via `just release <version>` (Linux remote parity/build, Sigstore-attested GF Darwin output and artifact-bound remote native smoke, byte-preserving Nix bridge, replayable GF evidence, full Linux/Darwin Nix closure exports, universal wheel, OpenPGP-signed tag and stamped manifest, detached manifest signature, and GH release); CI tag-push automation stays gated on a publicly reachable chapel cache (operator decision)",
            },
            "brew": {"enabled": False, "note": "C3 phase gate"},
            "rpm_deb": {"enabled": False, "note": "C3 phase gate (nfpm)"},
        },
    }


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and byte-diff against the committed manifest",
    )
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--git-rev", default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--ci-run", default=None)
    parser.add_argument(
        "--with-closure",
        action="append",
        default=[],
        metavar="PLATFORM=PATH",
        help=(
            "inject sha256/size of a platform ptoon Nix closure export; repeat for "
            "x86_64-linux and aarch64-darwin"
        ),
    )
    parser.add_argument(
        "--with-entrypoint",
        action="append",
        default=[],
        metavar="PLATFORM=PATH",
        help=(
            "inject sha256 of the bin/ptoon entrypoint in a platform Nix "
            "closure; repeat for x86_64-linux and aarch64-darwin"
        ),
    )
    parser.add_argument(
        "--with-build-provenance",
        action="append",
        default=[],
        metavar="PLATFORM=PATH",
        help=(
            "inject an authenticated build-to-package bridge record; v0.3 "
            "requires exactly one aarch64-darwin record"
        ),
    )
    parser.add_argument(
        "--with-wheel",
        default=None,
        help="inject filename/sha256/size of the built Python wheel",
    )
    args = parser.parse_args()

    stamped = bool(
        args.git_rev
        or args.tag
        or args.ci_run
        or args.with_closure
        or args.with_entrypoint
        or args.with_build_provenance
        or args.with_wheel
    )
    version = version_from_init()
    policy_digests = policy_digest_map()
    home_manager_contract = rendered_home_manager_contract(version, policy_digests)
    manifest = build_manifest(args)
    if stamped:
        validate_stamped_artifacts(manifest)
    rendered = render(manifest)

    if args.check:
        if stamped:
            raise SystemExit(
                "gen_manifest: --check compares the UNSTAMPED committed form"
            )
        if not HOME_MANAGER_CONTRACT_PATH.exists():
            sys.stderr.write(
                "gen_manifest: packaging/home-manager.json is missing; "
                "run tools/packaging/gen_manifest.py and commit the result\n"
            )
            return 1
        committed_contract = HOME_MANAGER_CONTRACT_PATH.read_bytes()
        if committed_contract != home_manager_contract.encode("utf-8"):
            sys.stderr.write(
                "gen_manifest: packaging/home-manager.json has drifted from repo truth; "
                "run tools/packaging/gen_manifest.py and commit the result\n"
            )
            return 1
        committed = MANIFEST_PATH.read_bytes() if MANIFEST_PATH.exists() else b""
        if committed != rendered.encode("utf-8"):
            sys.stderr.write(
                "gen_manifest: packaging/manifest.json has drifted from repo truth; "
                "run tools/packaging/gen_manifest.py and commit the result\n"
            )
            return 1
        print("manifest and home-manager contract: no drift")
        return 0

    if args.stdout or stamped:
        # Stamped emissions never overwrite the committed SSOT manifest.
        sys.stdout.write(rendered)
        return 0

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOME_MANAGER_CONTRACT_PATH.write_text(home_manager_contract, encoding="utf-8")
    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {HOME_MANAGER_CONTRACT_PATH.relative_to(ROOT)}")
    print(f"wrote {MANIFEST_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
