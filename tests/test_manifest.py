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
            proc.returncode, 0,
            f"manifest drift: {proc.stderr.decode('utf-8', 'replace')}",
        )

    def test_version_ssot_chain(self):
        import prompt_toon

        self.assertEqual(self.manifest["version"], prompt_toon.__version__)
        module_text = (ROOT / "MODULE.bazel").read_text(encoding="utf-8")
        self.assertIn(f'version = "{self.manifest["version"]}"', module_text)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertNotIn('\nversion = "', pyproject.split("[tool.setuptools.dynamic]")[0])

    def test_policy_digests_authenticate_the_artifacts(self):
        entries = {e["file"]: e["sha256"] for e in self.manifest["policy"]}
        self.assertIn("policy/delegation.json", entries)
        self.assertIn("policy/io.json", entries)
        for rel, digest in entries.items():
            self.assertEqual(
                digest, sha256((ROOT / rel).read_bytes()).hexdigest(), rel
            )

    def test_c4_artifacts_declare_their_runtime_protocols(self):
        targets = {target["artifact"]: target for target in self.manifest["targets"]}
        self.assertEqual(
            targets["ptoon"]["capabilities"], {"serve_protocol": 1}
        )
        self.assertEqual(
            targets["prompt_toon"]["capabilities"],
            {"anthropic_shadow_gateway": 1},
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
            self.assertEqual(gen_manifest.validate_package_component("skill", skill), skill)

    def test_unsafe_skill_names_fail_before_packaging_interpolation(self):
        for name in ("bad/name", "bad name", "bad;name", "../escape"):
            with self.subTest(name=name):
                with self.assertRaises(SystemExit):
                    gen_manifest.validate_package_component("skill", name)

    def test_stamped_emission_authenticates_binary_and_wheel(self):
        # The fake binary lives in a tempdir, never the source tree — the
        # bazel test sandbox (correctly) forbids writes to input paths.
        import tempfile

        tmp = tempfile.mkdtemp(prefix="ptoon-manifest-test-")
        fake = Path(tmp) / "ptoon"
        fake.write_bytes(b"not a real elf")
        wheel = Path(tmp) / "prompt_toon-0.3.0-py3-none-any.whl"
        wheel.write_bytes(b"not a real wheel")
        try:
            proc = subprocess.run(
                [sys.executable, str(GEN), "--git-rev", "deadbeef",
                 "--with-binary", str(fake), "--with-wheel", str(wheel)],
                capture_output=True, cwd=ROOT,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            stamped = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(stamped["git_rev"], "deadbeef")
            ptoon = next(t for t in stamped["targets"] if t["artifact"] == "ptoon")
            self.assertEqual(ptoon["sha256"], sha256(b"not a real elf").hexdigest())
            self.assertEqual(ptoon["size"], len(b"not a real elf"))
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
            fake.unlink(missing_ok=True)
            wheel.unlink(missing_ok=True)
            Path(tmp).rmdir()

    def test_release_lane_requires_and_publishes_c4_proof_artifacts(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        for required in (
            "git status --porcelain",
            "git ls-remote origin refs/heads/main",
            "just check",
            "just gateway-harness-probe",
            "uv build --wheel",
            '--with-wheel "$wheel"',
            '"$wheel" "$stage/manifest-$tag.json"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, justfile)


if __name__ == "__main__":
    unittest.main()
