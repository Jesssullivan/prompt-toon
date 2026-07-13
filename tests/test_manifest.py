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

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "tools" / "packaging" / "gen_manifest.py"
MANIFEST = ROOT / "packaging" / "manifest.json"
HOME_MANAGER_CONTRACT = ROOT / "packaging" / "home-manager.json"
sys.path.insert(0, str(ROOT / "tools" / "packaging"))
import gen_manifest  # noqa: E402


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
        darwin_entrypoint = Path(tmp) / "ptoon-aarch64-darwin"
        linux.write_bytes(b"not a real Linux closure")
        darwin.write_bytes(b"not a real Darwin closure")
        linux_entrypoint.write_bytes(b"exact Linux ptoon bytes")
        darwin_entrypoint.write_bytes(b"exact Darwin ptoon bytes")
        wheel = Path(tmp) / "prompt_toon-0.3.0-py3-none-any.whl"
        wheel.write_bytes(b"not a real wheel")
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(GEN),
                    "--git-rev",
                    "deadbeef",
                    "--with-closure",
                    f"x86_64-linux={linux}",
                    "--with-entrypoint",
                    f"x86_64-linux={linux_entrypoint}",
                    "--with-closure",
                    f"aarch64-darwin={darwin}",
                    "--with-entrypoint",
                    f"aarch64-darwin={darwin_entrypoint}",
                    "--with-wheel",
                    str(wheel),
                ],
                capture_output=True,
                cwd=ROOT,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            stamped = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(stamped["git_rev"], "deadbeef")
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
            prompt_toon = next(
                t for t in stamped["targets"] if t["artifact"] == "prompt_toon"
            )
            self.assertEqual(prompt_toon["filename"], wheel.name)
            self.assertEqual(
                prompt_toon["sha256"], sha256(b"not a real wheel").hexdigest()
            )
            self.assertEqual(prompt_toon["size"], len(b"not a real wheel"))
            # A stamped emission must never overwrite the committed SSOT.
            self.assertEqual(
                json.loads(MANIFEST.read_text(encoding="utf-8")), self.manifest
            )
        finally:
            linux.unlink(missing_ok=True)
            darwin.unlink(missing_ok=True)
            linux_entrypoint.unlink(missing_ok=True)
            darwin_entrypoint.unlink(missing_ok=True)
            wheel.unlink(missing_ok=True)
            Path(tmp).rmdir()

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

    def test_release_lane_requires_and_publishes_c4_proof_artifacts(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        for required in (
            "git status --porcelain",
            "git ls-remote origin refs/heads/main",
            "just check",
            "just gateway-harness-probe",
            "just responses-gateway-harness-probe",
            ".#packages.x86_64-linux.ptoon-parity",
            ".#packages.aarch64-darwin.ptoon",
            "--max-jobs 0",
            "uv build --wheel",
            'nix-store --query --requisites "$linux_ptoon_store"',
            'nix-store --query --requisites "$darwin_ptoon_store"',
            '--with-closure "x86_64-linux=$stage/ptoon-x86_64-linux.nar"',
            '--with-entrypoint "x86_64-linux=$linux_ptoon_store/bin/ptoon"',
            '--with-closure "aarch64-darwin=$stage/ptoon-aarch64-darwin.nar"',
            '--with-entrypoint "aarch64-darwin=$darwin_ptoon_store/bin/ptoon"',
            '--with-wheel "$wheel"',
            '"$stage/ptoon-aarch64-darwin.nar" "$wheel"',
            '"$stage/manifest-$tag.json"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, justfile)

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


if __name__ == "__main__":
    unittest.main()
