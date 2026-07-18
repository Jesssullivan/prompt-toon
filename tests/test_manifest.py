"""tests/test_manifest.py -- TIN-2706 packaging-SSOT manifest coverage.

The committed packaging/manifest.json is the consumption point (nix
importJSON reads version + skills from it), so three properties are
load-bearing: it must not drift from repo truth (the generator is the only
writer), the version SSOT chain must hold (one string in
prompt_toon/__init__.py, everything else derived or drift-gated), and the
policy[] digests must actually authenticate the policy artifacts they
name (packaging integrity ties to the delegation/io SSOTs).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "tools" / "packaging" / "gen_manifest.py"
MANIFEST = ROOT / "packaging" / "manifest.json"
HOME_MANAGER_CONTRACT = ROOT / "packaging" / "home-manager.json"
sys.path.insert(0, str(ROOT / "tools" / "packaging"))
import gen_manifest  # noqa: E402
from tools.packaging.import_gf_ptoon import (  # noqa: E402
    CAPS_SMOKE_SHA256,
    NATIVE_SMOKE_EXPECTED,
    NATIVE_SMOKE_EXPECTED_REPO_PATH,
    NATIVE_SMOKE_SCRIPT,
    NATIVE_SMOKE_SCRIPT_REPO_PATH,
    ONE_SHOT_SMOKE_SHA256,
    REDACTION_SMOKE_SHA256,
    RESIDENT_SMOKE_SHA256,
)


def write_test_wheel(
    path: Path,
    *,
    root_is_purelib: str = "true",
    tags: tuple[str, ...] = ("py3-none-any",),
) -> None:
    metadata = [
        "Wheel-Version: 1.0",
        "Generator: prompt-toon-test",
        f"Root-Is-Purelib: {root_is_purelib}",
        *(f"Tag: {tag}" for tag in tags),
        "",
        "",
    ]
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "prompt_toon-0.3.0.dist-info/WHEEL",
            "\n".join(metadata).encode("ascii"),
        )


def write_test_bridge_record(
    path: Path,
    *,
    revision: str,
    closure: Path,
    entrypoint: Path,
) -> None:
    store_path = "/nix/store/" + "a" * 32 + "-ptoon"
    entrypoint_sha256 = sha256(entrypoint.read_bytes()).hexdigest()
    proof_result = path.with_name("ptoon-aarch64-darwin.gf-proof-result.json")
    exported_outputs = path.with_name(
        "ptoon-aarch64-darwin.gf-exported-outputs.json"
    )
    attestation = path.with_name(
        "ptoon-aarch64-darwin.gf-proof-result.attestation.json"
    )
    native_smoke = path.with_name("ptoon-aarch64-darwin.native-smoke.json")
    proof_result.write_bytes(b'{"fixture":"proof-result"}\n')
    exported_outputs.write_bytes(b'{"fixture":"exported-outputs"}\n')
    attestation.write_bytes(b'{"fixture":"attestation-bundle"}\n')
    native_smoke_result = {
        "schema_version": 1,
        "kind": "prompt-toon-darwin-native-smoke",
        "artifact_sha256": f"sha256:{entrypoint_sha256}",
        "caps_engine": "chapel",
        "caps_serve_protocol": 1,
        "caps_sha256": CAPS_SMOKE_SHA256,
        "normalize_sha256": sha256(b"A\n").hexdigest(),
        "one_shot_sha256": ONE_SHOT_SMOKE_SHA256,
        "redaction_canary_absent": True,
        "redaction_sha256": REDACTION_SMOKE_SHA256,
        "resident_round_trip_sha256": RESIDENT_SMOKE_SHA256,
    }
    native_smoke.write_text(
        json.dumps(native_smoke_result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    record = {
        "schema_version": 1,
        "kind": "prompt-toon-gf-darwin-nix-bridge",
        "gf": {
            "authority": "tinyland-inc/GloriousFlywheel",
            "proof_result_filename": proof_result.name,
            "proof_result_sha256": sha256(proof_result.read_bytes()).hexdigest(),
            "exported_outputs_filename": exported_outputs.name,
            "exported_outputs_sha256": sha256(exported_outputs.read_bytes()).hexdigest(),
            "attestation": {
                "bundle_filename": attestation.name,
                "bundle_sha256": sha256(attestation.read_bytes()).hexdigest(),
                "predicate_type": "https://slsa.dev/provenance/v1",
                "repository": "tinyland-inc/GloriousFlywheel",
                "signer_workflow": (
                    "tinyland-inc/GloriousFlywheel/.github/workflows/"
                    "gf-reapi-cell-proof.yml"
                ),
                "source_ref": "refs/heads/main",
                "source_digest": "e" * 40,
            },
            "request": {
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "workflow_run_url": (
                    "https://github.com/tinyland-inc/GloriousFlywheel/actions/runs/123"
                ),
                "consumer_repository": "Jesssullivan/prompt-toon",
                "consumer_ref": revision,
                "target": "//src/ptoon:ptoon",
                "target_platform": "//tools/bazel/platforms:darwin_aarch64",
                "bazel_command": "build",
            },
            "platform": "gloriousflywheel-rbe-darwin-aarch64",
            "worker_identity": {
                "kind": "physical-darwin-worker",
                "name": "darwin-aarch64-pzm-01",
                "host": "petting-zoo-mini",
                "architecture": "arm64",
                "os": "darwin",
                "closure_digest": "sha256:" + "f" * 64,
            },
        },
        "exported_output": {
            "label": "//src/ptoon:ptoon",
            "path": "exported-outputs/darwin_arm64-fastbuild/bin/src/ptoon/ptoon",
            "sha256": f"sha256:{entrypoint_sha256}",
            "size_bytes": entrypoint.stat().st_size,
            "executable": True,
            "transfer_filename": entrypoint.name,
        },
        "native_smoke": {
            "evidence_filename": native_smoke.name,
            "evidence_sha256": sha256(native_smoke.read_bytes()).hexdigest(),
            "result": native_smoke_result,
        },
        "nix": {
            "store_path": store_path,
            "entrypoint": {
                "path": "bin/ptoon",
                "store_path": f"{store_path}/bin/ptoon",
                "sha256": entrypoint_sha256,
            },
            "closure_export": {
                "filename": closure.name,
                "sha256": sha256(closure.read_bytes()).hexdigest(),
                "size_bytes": closure.stat().st_size,
            },
            "store_requisites": [
                store_path,
                "/nix/store/" + "e" * 32 + "-chapel-runtime",
            ],
            "post_import_smoke": {
                "script": NATIVE_SMOKE_SCRIPT_REPO_PATH,
                "script_sha256": sha256(NATIVE_SMOKE_SCRIPT.read_bytes()).hexdigest(),
                "expected": NATIVE_SMOKE_EXPECTED_REPO_PATH,
                "expected_sha256": sha256(
                    NATIVE_SMOKE_EXPECTED.read_bytes()
                ).hexdigest(),
                "result_sha256": sha256(native_smoke.read_bytes()).hexdigest(),
                "byte_identical_to_remote": True,
            },
        },
    }
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_no_drift_from_repo_truth(self):
        proc = subprocess.run(
            [sys.executable, str(GEN), "--check"], capture_output=True, cwd=ROOT
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"manifest drift: {proc.stderr.decode('utf-8', 'replace')}",
        )

    def test_version_ssot_chain(self):
        import prompt_toon

        self.assertEqual(self.manifest["version"], prompt_toon.__version__)
        module_text = (ROOT / "MODULE.bazel").read_text(encoding="utf-8")
        self.assertIn(f'version = "{self.manifest["version"]}"', module_text)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertNotIn(
            '\nversion = "', pyproject.split("[tool.setuptools.dynamic]")[0]
        )

    def test_policy_digests_authenticate_the_artifacts(self):
        entries = {e["file"]: e["sha256"] for e in self.manifest["policy"]}
        self.assertIn("policy/delegation.json", entries)
        self.assertIn("policy/io.json", entries)
        for rel, digest in entries.items():
            self.assertEqual(digest, sha256((ROOT / rel).read_bytes()).hexdigest(), rel)

    def test_release_signer_is_an_in_repo_trust_anchor(self):
        signers = json.loads(
            (ROOT / "packaging" / "release-signers.json").read_text(encoding="utf-8")
        )
        active = signers["active"]
        public_key = ROOT / active["public_key"]
        self.assertEqual(signers["repository"], "github.com/Jesssullivan/prompt-toon")
        self.assertEqual(active["scheme"], "openpgp")
        self.assertRegex(active["fingerprint"], r"^[0-9A-F]{40,64}$")
        self.assertFalse(public_key.is_symlink())
        self.assertEqual(
            active["public_key_sha256"], sha256(public_key.read_bytes()).hexdigest()
        )
        self.assertEqual(
            self.manifest["provenance"]["release_signer"],
            {
                "scheme": active["scheme"],
                "fingerprint": active["fingerprint"],
                "public_key": active["public_key"],
                "public_key_sha256": active["public_key_sha256"],
            },
        )

    def test_home_manager_lane_authenticates_the_contract(self):
        lane = self.manifest["derived_lanes"]["home_manager"]
        self.assertFalse(lane["enabled"])
        self.assertTrue(lane["contract_ready"])
        self.assertEqual(lane["contract"], "packaging/home-manager.json")
        self.assertEqual(
            lane["sha256"], sha256(HOME_MANAGER_CONTRACT.read_bytes()).hexdigest()
        )

    def test_c4_artifacts_declare_their_runtime_protocols(self):
        self.assertEqual(self.manifest["schema_version"], 2)
        targets = {
            (target["artifact"], target["platform"]): target
            for target in self.manifest["targets"]
        }
        for platform in gen_manifest.PTOON_PLATFORMS:
            ptoon = targets[("ptoon", platform)]
            self.assertEqual(ptoon["filename"], f"ptoon-{platform}.nar")
            self.assertEqual(ptoon["kind"], "nix-closure-export")
            self.assertEqual(ptoon["closure_format"], "nix-store-export-v1")
            self.assertEqual(ptoon["entrypoint"], "bin/ptoon")
            self.assertEqual(ptoon["capabilities"], {"serve_protocol": 1})
        self.assertEqual(
            targets[("prompt_toon", "any")]["capabilities"],
            {
                "anthropic_shadow_gateway": 1,
                "openai_responses_shadow_gateway": 1,
            },
        )
        self.assertTrue(targets[("prompt_toon", "any")]["root_is_purelib"])
        self.assertEqual(targets[("prompt_toon", "any")]["wheel_tag"], "py3-none-any")

    def test_committed_manifest_is_unstamped_and_binaryless(self):
        # The committed form is a pure function of repo content: a committed
        # self-referential git rev or binary hash would be stale by
        # construction. Stamping belongs to the release lane.
        self.assertEqual(self.manifest["git_rev"], "UNSTAMPED")
        self.assertIsNone(self.manifest["provenance"]["tag"])
        for target in self.manifest["targets"]:
            self.assertIsNone(target["sha256"])

    def test_skills_match_shipped_directories(self):
        on_disk = sorted(
            p.name for p in (ROOT / ".agents" / "skills").iterdir() if p.is_dir()
        )
        self.assertEqual(self.manifest["skills"], on_disk)
        for skill in self.manifest["skills"]:
            self.assertEqual(
                gen_manifest.validate_package_component("skill", skill), skill
            )

    def test_unsafe_skill_names_fail_before_packaging_interpolation(self):
        for name in ("bad/name", "bad name", "bad;name", "../escape"):
            with self.subTest(name=name):
                with self.assertRaises(SystemExit):
                    gen_manifest.validate_package_component("skill", name)

    def test_stamped_emission_authenticates_closures_entrypoints_and_wheel(self):
        # The fake closures live in a tempdir, never the source tree — the
        # bazel test sandbox (correctly) forbids writes to input paths.
        import tempfile

        tmp = tempfile.mkdtemp(prefix="ptoon-manifest-test-")
        linux = Path(tmp) / "ptoon-x86_64-linux.nar"
        darwin = Path(tmp) / "ptoon-aarch64-darwin.nar"
        linux_entrypoint = Path(tmp) / "ptoon-x86_64-linux"
        darwin_entrypoint = Path(tmp) / "ptoon-aarch64-darwin.bin"
        darwin_bridge = Path(tmp) / "ptoon-aarch64-darwin.gf-nix-bridge.json"
        revision = "d" * 40
        linux.write_bytes(b"not a real Linux closure")
        darwin.write_bytes(b"not a real Darwin closure")
        linux_entrypoint.write_bytes(b"exact Linux ptoon bytes")
        darwin_entrypoint.write_bytes(b"exact Darwin ptoon bytes")
        darwin_entrypoint.chmod(0o755)
        write_test_bridge_record(
            darwin_bridge,
            revision=revision,
            closure=darwin,
            entrypoint=darwin_entrypoint,
        )
        wheel = Path(tmp) / "prompt_toon-0.3.0-py3-none-any.whl"
        write_test_wheel(wheel)
        wheel_bytes = wheel.read_bytes()
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(GEN),
                    "--git-rev",
                    revision,
                    "--with-closure",
                    f"x86_64-linux={linux}",
                    "--with-entrypoint",
                    f"x86_64-linux={linux_entrypoint}",
                    "--with-closure",
                    f"aarch64-darwin={darwin}",
                    "--with-entrypoint",
                    f"aarch64-darwin={darwin_entrypoint}",
                    "--with-build-provenance",
                    f"aarch64-darwin={darwin_bridge}",
                    "--with-wheel",
                    str(wheel),
                ],
                capture_output=True,
                cwd=ROOT,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            stamped = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(stamped["git_rev"], revision)
            ptoon = {
                t["platform"]: t for t in stamped["targets"] if t["artifact"] == "ptoon"
            }
            self.assertEqual(
                ptoon["x86_64-linux"]["sha256"],
                sha256(b"not a real Linux closure").hexdigest(),
            )
            self.assertEqual(
                ptoon["x86_64-linux"]["size"], len(b"not a real Linux closure")
            )
            self.assertEqual(
                ptoon["x86_64-linux"]["entrypoint_sha256"],
                sha256(b"exact Linux ptoon bytes").hexdigest(),
            )
            self.assertEqual(
                ptoon["aarch64-darwin"]["sha256"],
                sha256(b"not a real Darwin closure").hexdigest(),
            )
            self.assertEqual(
                ptoon["aarch64-darwin"]["size"], len(b"not a real Darwin closure")
            )
            self.assertEqual(
                ptoon["aarch64-darwin"]["entrypoint_sha256"],
                sha256(b"exact Darwin ptoon bytes").hexdigest(),
            )
            provenance = ptoon["aarch64-darwin"]["build_provenance"]
            self.assertEqual(provenance["kind"], "gf-reapi-nix-bridge")
            self.assertEqual(provenance["consumer_ref"], revision)
            self.assertEqual(
                provenance["record_sha256"],
                sha256(darwin_bridge.read_bytes()).hexdigest(),
            )
            self.assertTrue(
                provenance["native_smoke"]["result"]["redaction_canary_absent"]
            )
            self.assertEqual(
                provenance["attestation"]["source_digest"],
                "e" * 40,
            )
            self.assertIsNone(ptoon["x86_64-linux"]["build_provenance"])
            prompt_toon = next(
                t for t in stamped["targets"] if t["artifact"] == "prompt_toon"
            )
            self.assertEqual(prompt_toon["filename"], wheel.name)
            self.assertEqual(prompt_toon["sha256"], sha256(wheel_bytes).hexdigest())
            self.assertEqual(prompt_toon["size"], len(wheel_bytes))
            self.assertTrue(prompt_toon["root_is_purelib"])
            self.assertEqual(prompt_toon["wheel_tag"], "py3-none-any")
            # A stamped emission must never overwrite the committed SSOT.
            self.assertEqual(
                json.loads(MANIFEST.read_text(encoding="utf-8")), self.manifest
            )
        finally:
            linux.unlink(missing_ok=True)
            darwin.unlink(missing_ok=True)
            linux_entrypoint.unlink(missing_ok=True)
            darwin_entrypoint.unlink(missing_ok=True)
            darwin_bridge.unlink(missing_ok=True)
            for replay in Path(tmp).glob("ptoon-aarch64-darwin.*.json"):
                replay.unlink(missing_ok=True)
            wheel.unlink(missing_ok=True)
            Path(tmp).rmdir()

    def test_release_wheel_must_be_exactly_pure_python(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ptoon-wheel-test-") as tmp:
            root = Path(tmp)
            valid = root / "prompt_toon-0.3.0-py3-none-any.whl"
            write_test_wheel(valid)
            gen_manifest.validate_universal_wheel(valid, "0.3.0")

            wrong_name = root / "prompt_toon-0.3.0-cp313-cp313-macosx.whl"
            write_test_wheel(wrong_name)
            with self.assertRaisesRegex(SystemExit, "must be prompt_toon"):
                gen_manifest.validate_universal_wheel(wrong_name, "0.3.0")

            write_test_wheel(valid, root_is_purelib="false")
            with self.assertRaisesRegex(SystemExit, "Root-Is-Purelib: true"):
                gen_manifest.validate_universal_wheel(valid, "0.3.0")

            write_test_wheel(valid, tags=("py3-none-any", "cp313-none-any"))
            with self.assertRaisesRegex(SystemExit, "exactly Tag: py3-none-any"):
                gen_manifest.validate_universal_wheel(valid, "0.3.0")

            valid.write_bytes(b"not a zip archive")
            with self.assertRaisesRegex(SystemExit, "not a valid wheel archive"):
                gen_manifest.validate_universal_wheel(valid, "0.3.0")

    def test_darwin_build_provenance_rejects_stale_revision(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ptoon-bridge-test-") as tmp:
            root = Path(tmp)
            closure = root / "ptoon-aarch64-darwin.nar"
            entrypoint = root / "ptoon-aarch64-darwin.bin"
            record = root / "ptoon-aarch64-darwin.gf-nix-bridge.json"
            closure.write_bytes(b"closure")
            entrypoint.write_bytes(b"ptoon")
            entrypoint.chmod(0o755)
            write_test_bridge_record(
                record,
                revision="a" * 40,
                closure=closure,
                entrypoint=entrypoint,
            )
            with self.assertRaisesRegex(SystemExit, "consumer revision is stale"):
                gen_manifest.release_build_provenance(
                    [f"aarch64-darwin={record}"],
                    git_rev="b" * 40,
                    closures={"aarch64-darwin": closure},
                    entrypoints={"aarch64-darwin": entrypoint},
                )

    def test_stamped_emission_rejects_missing_release_artifacts(self):
        proc = subprocess.run(
            [sys.executable, str(GEN), "--git-rev", "deadbeef"],
            capture_output=True,
            cwd=ROOT,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"complete release artifact digests", proc.stderr)
        self.assertIn(b"ptoon:x86_64-linux.sha256", proc.stderr)
        self.assertIn(b"prompt_toon:any.sha256", proc.stderr)

    def test_stamped_closure_entrypoint_coverage_is_all_or_none(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ptoon-manifest-test-") as tmp:
            closure = Path(tmp) / "ptoon-x86_64-linux.nar"
            entrypoint = Path(tmp) / "ptoon-x86_64-linux"
            closure.write_bytes(b"fixture")
            entrypoint.write_bytes(b"entrypoint fixture")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(GEN),
                    "--git-rev",
                    "deadbeef",
                    "--with-closure",
                    f"x86_64-linux={closure}",
                    "--with-entrypoint",
                    f"x86_64-linux={entrypoint}",
                ],
                capture_output=True,
                cwd=ROOT,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"all-or-none", proc.stderr)
        self.assertIn(b"aarch64-darwin", proc.stderr)

    def test_stamped_closure_and_entrypoint_options_must_match(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ptoon-manifest-test-") as tmp:
            linux_closure = Path(tmp) / "ptoon-x86_64-linux.nar"
            darwin_closure = Path(tmp) / "ptoon-aarch64-darwin.nar"
            linux_entrypoint = Path(tmp) / "ptoon-x86_64-linux"
            darwin_entrypoint = Path(tmp) / "ptoon-aarch64-darwin"
            for path in (
                linux_closure,
                darwin_closure,
                linux_entrypoint,
                darwin_entrypoint,
            ):
                path.write_bytes(path.name.encode("utf-8"))

            cases = (
                (
                    "missing entrypoints",
                    [
                        "--with-closure",
                        f"x86_64-linux={linux_closure}",
                        "--with-closure",
                        f"aarch64-darwin={darwin_closure}",
                    ],
                    b"provided together",
                ),
                (
                    "missing closures",
                    [
                        "--with-entrypoint",
                        f"x86_64-linux={linux_entrypoint}",
                        "--with-entrypoint",
                        f"aarch64-darwin={darwin_entrypoint}",
                    ],
                    b"provided together",
                ),
                (
                    "mismatched platforms",
                    [
                        "--with-closure",
                        f"x86_64-linux={linux_closure}",
                        "--with-entrypoint",
                        f"aarch64-darwin={darwin_entrypoint}",
                    ],
                    b"platform coverage must match",
                ),
            )
            for name, options, expected_error in cases:
                with self.subTest(name=name):
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(GEN),
                            "--git-rev",
                            "deadbeef",
                            *options,
                        ],
                        capture_output=True,
                        cwd=ROOT,
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn(expected_error, proc.stderr)

    def test_unstamped_output_is_deterministic_with_null_entrypoint_digests(self):
        command = [sys.executable, str(GEN), "--stdout"]
        first = subprocess.run(command, capture_output=True, cwd=ROOT)
        second = subprocess.run(command, capture_output=True, cwd=ROOT)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        unstamped = json.loads(first.stdout.decode("utf-8"))
        self.assertEqual(unstamped["git_rev"], "UNSTAMPED")
        for target in unstamped["targets"]:
            self.assertIsNone(target["sha256"])
            self.assertIsNone(target["size"])
            if target["kind"] == "nix-closure-export":
                self.assertIn("entrypoint_sha256", target)
                self.assertIsNone(target["entrypoint_sha256"])
                self.assertIn("build_provenance", target)
                self.assertIsNone(target["build_provenance"])

    def test_release_lane_requires_and_publishes_c4_proof_artifacts(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        release_recipe = justfile.split("release $version:", 1)[1].split(
            "\nbuild-ptoon:", 1
        )[0]
        for required in (
            "git status --porcelain",
            "git ls-remote origin refs/heads/main",
            "just check",
            "just gateway-harness-probe",
            "just responses-gateway-harness-probe",
            ".#packages.x86_64-linux.ptoon-parity",
            "PROMPT_TOON_DARWIN_BRIDGE_DIR",
            "gf-darwin-bridge $evidence $output $native_smoke $revision $gf_revision $bundle:",
            "tools/packaging/import_gf_ptoon.py",
            "--native-smoke",
            "--expected-revision",
            "--expected-gf-revision",
            "--entrypoint-export",
            "import_gf_ptoon.py replay",
            "PROMPT_TOON_GF_REVISION",
            "ptoon-aarch64-darwin.gf-nix-bridge.json",
            "uv build --wheel",
            'nix-store --query --requisites "$linux_ptoon_store"',
            'cp "$darwin_bridge_closure" "$stage/ptoon-aarch64-darwin.nar"',
            'cp "$darwin_bridge_entrypoint" "$stage/ptoon-aarch64-darwin.bin"',
            'cp "$darwin_bridge_record" "$stage/ptoon-aarch64-darwin.gf-nix-bridge.json"',
            'cp "$darwin_bridge_proof" "$stage/ptoon-aarch64-darwin.gf-proof-result.json"',
            'cp "$darwin_bridge_outputs" "$stage/ptoon-aarch64-darwin.gf-exported-outputs.json"',
            'cp "$darwin_bridge_attestation" "$stage/ptoon-aarch64-darwin.gf-proof-result.attestation.json"',
            'cp "$darwin_bridge_smoke" "$stage/ptoon-aarch64-darwin.native-smoke.json"',
            '--with-closure "x86_64-linux=$stage/ptoon-x86_64-linux.nar"',
            '--with-entrypoint "x86_64-linux=$linux_ptoon_store/bin/ptoon"',
            '--with-closure "aarch64-darwin=$stage/ptoon-aarch64-darwin.nar"',
            '--with-entrypoint "aarch64-darwin=$stage/ptoon-aarch64-darwin.bin"',
            '--with-build-provenance "aarch64-darwin=$stage/ptoon-aarch64-darwin.gf-nix-bridge.json"',
            '--with-wheel "$wheel"',
            '"$stage/ptoon-aarch64-darwin.native-smoke.json" "$wheel"',
            'manifest="$stage/manifest-$tag.json"',
            'signing_fingerprint="$(gpg --batch --with-colons',
            'trusted_signing_fingerprint="$(python3 -c',
            "packaging/release-signers.json",
            "packaging/release-signing-key.asc",
            'anchored_signing_fingerprint="$(gpg --batch --with-colons --show-keys',
            'nix-store --query --requisites "$linux_ptoon_store" | sort',
            'git tag -s -u "$signing_key"',
            'git verify-tag "$tag"',
            'gpg --local-user "$signing_key" --armor --detach-sign',
            'gpg --verify "$manifest.asc" "$manifest"',
            '"$manifest" "$manifest.asc"',
            "OpenPGP signer: $signing_fingerprint",
            "Darwin build_provenance binds the exact forced GF output",
            "Sigstore-attested GF proof",
            "remote native caps/normalize/resident smoke",
            "git push --force-with-lease=",
            'canonical_repo="Jesssullivan/prompt-toon"',
            "remote_tag_push_attempted=1",
            "release_create_attempted=1",
            'release_marker="<!-- prompt-toon-release-owner:',
            '--notes-file "$release_notes"',
            'created_release_id="$(gh release view',
            'gh release edit "$tag" --repo "$canonical_repo" --draft=false',
        ):
            with self.subTest(required=required):
                self.assertIn(required, justfile)
        for forbidden in (
            "darwin_ptoon_store",
            ".#packages.aarch64-darwin.ptoon",
            "--max-jobs 0",
            'nix-store --query --requisites "$darwin_ptoon_store"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, release_recipe)
        self.assertLess(
            release_recipe.index(
                'cp "$darwin_bridge_closure" "$stage/ptoon-aarch64-darwin.nar"'
            ),
            release_recipe.index("import_gf_ptoon.py replay"),
        )
        self.assertLess(
            release_recipe.index("remote_tag_push_attempted=1"),
            release_recipe.index(
                'git push --force-with-lease="refs/tags/$tag:" origin'
            ),
        )
        self.assertLess(
            release_recipe.index('release_marker="<!-- prompt-toon-release-owner:'),
            release_recipe.index("release_create_attempted=1"),
        )
        self.assertLess(
            release_recipe.index("release_create_attempted=1"),
            release_recipe.index('gh release create "$tag"'),
        )

    def test_release_parity_surface_requires_c4d_capacity_proof(self):
        release_surface = "\n".join(
            [
                (ROOT / "Justfile").read_text(encoding="utf-8"),
                (ROOT / "flake.nix").read_text(encoding="utf-8"),
            ]
        )
        self.assertTrue(
            "tools/gateway_capacity.py" in release_surface
            or "capacity-gateway.md" in release_surface
        )

    def test_darwin_release_proves_valid_adhoc_signature_without_identity(self):
        flake = (ROOT / "flake.nix").read_text(encoding="utf-8")
        for required in (
            "doInstallCheck = pkgs.stdenv.isDarwin",
            "/usr/bin/codesign --verify --strict --verbose=4",
            '/usr/bin/lipo -archs "$out/bin/ptoon"',
            'if [ "$native_archs" != "arm64" ]',
            "Signature=adhoc",
            "TeamIdentifier=not set",
            "^Authority=",
        ):
            with self.subTest(required=required):
                self.assertIn(required, flake)

    def test_installed_launcher_isolates_imports_and_preserves_policy_override(self):
        flake = (ROOT / "flake.nix").read_text(encoding="utf-8")
        self.assertIn('/bin/python -I "$out/lib/prompt-toon-launcher.py"', flake)
        self.assertIn(
            'runpy.run_module("prompt_toon", run_name="__main__", alter_sys=True)',
            flake,
        )
        self.assertNotIn('export PYTHONPATH="$out/lib/prompt-toon', flake)
        self.assertIn("prompt_toon/__init__.py", flake)
        self.assertIn("sitecustomize.py", flake)
        self.assertIn('printf \'print("checkout-shadow")', flake)
        self.assertIn('cd "$shadow_dir"', flake)
        self.assertIn('cp "$out/share/prompt-toon/policy/io.json"', flake)
        self.assertIn('PROMPT_TOON_IO_POLICY="$runtime_policy"', flake)


if __name__ == "__main__":
    unittest.main()
