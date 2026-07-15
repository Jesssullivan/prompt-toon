from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FlywheelContractTest(unittest.TestCase):
    def test_frontdoor_kit_is_read_only_and_endpoint_free(self) -> None:
        config = (ROOT / ".bazelrc.flywheel").read_text(encoding="utf-8")
        self.assertIn(
            "build:ci-cached --remote_upload_local_results=false", config
        )
        self.assertNotIn("--remote_upload_local_results=true", config)
        self.assertNotIn("grpc://", config)
        self.assertNotIn("http://", config)

    def test_manifest_declares_executor_backed(self) -> None:
        manifest = json.loads(
            (ROOT / "tinyland.repo.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["enrollment"]["substrateMode"], "executor-backed"
        )
        self.assertEqual(manifest["enrollment"]["executionPool"], "tinyland-nix")

    def test_workflow_is_required_ready_and_uses_packaged_tools(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "ci-flywheel.yml"
        ).read_text(encoding="utf-8")
        recipes = (ROOT / "Justfile").read_text(encoding="utf-8")
        for required in (
            "runs-on: tinyland-nix",
            "id-token: write",
            "flywheel-github-oidc-profile",
            "gf-reapi-credhelper",
            "flywheel-verify",
            'nix --option access-tokens "" eval --raw --impure',
            "gf-reapi-proof-result.py",
            'counts["remote_processes"] <= 0',
            'counts["remote_cache_hits"] <= 0',
        ):
            self.assertIn(required, workflow)
        for required in (
            "flywheel-executor-proof",
            "--remote_accept_cached=false",
            "--nocache_test_results",
            "--execution_log_json_file=",
            "warm-output",
            "measured-output",
        ):
            self.assertIn(required, recipes)
        for forbidden in (
            "continue-on-error",
            "ubuntu-latest",
            "cache-attachment-contract.sh",
            "mint-gf-reapi-token-from-exchange.sh",
        ):
            self.assertNotIn(forbidden, workflow)

    def test_stale_vendored_attachment_scripts_are_absent(self) -> None:
        self.assertFalse((ROOT / "scripts" / "cache-attachment-contract.sh").exists())
        self.assertFalse(
            (ROOT / "scripts" / "mint-gf-reapi-token-from-exchange.sh").exists()
        )

    def test_required_legacy_checks_use_the_gf_runner(self) -> None:
        for workflow_name in (
            "bazel-graph.yml",
            "build-and-test.yml",
            "secrets-scan.yml",
        ):
            workflow = (
                ROOT / ".github" / "workflows" / workflow_name
            ).read_text(encoding="utf-8")
            self.assertIn("runs-on: tinyland-nix", workflow)
            self.assertNotIn("runs-on: ubuntu-latest", workflow)

    def test_executor_proof_has_a_deterministic_cacheable_action(self) -> None:
        build = (ROOT / "BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn('name = "gf_cache_probe"', build)
        self.assertIn('srcs = ["packaging/manifest.json"]', build)
        self.assertIn('":gf_cache_probe"', build)


if __name__ == "__main__":
    unittest.main()
