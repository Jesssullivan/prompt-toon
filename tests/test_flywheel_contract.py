from __future__ import annotations

import json
import subprocess
import tempfile
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
            "REQUIRE_CACHE_REUSE",
            "github.event_name == 'push'",
            'counts["remote_processes"] <= 0',
            'require_cache_reuse and counts["remote_cache_hits"] <= 0',
            "name: Compile ptoon through the GF Chapel toolchain",
            "nix develop --command just flywheel-chapel-proof",
            "CHAPEL_EXECUTION_LOG:",
            "tools/bazel/assert_execution_log.py",
            "steps.chapel.outputs.execution_log",
            "CHAPEL_LOG:",
            'chapel_counts["remote_processes"] <= 0',
            "Chapel build did not record remote execution",
            "name: Upload executor proof log",
            "github.event_name != 'pull_request'",
            "steps.proof.outputs.log != '' || steps.chapel.outputs.log != ''",
            "uses: actions/upload-artifact@v7",
        ):
            self.assertIn(required, workflow)
        for required in (
            "flywheel-executor-proof",
            "--remote_accept_cached=false",
            "--nocache_test_results",
            "--test_output=errors",
            "--execution_log_json_file=",
            "mktemp -d",
            "forced-output",
            "warm-output",
            "measured-output",
            "flywheel-chapel-proof",
            "chapel-output",
            "--spawn_strategy=remote",
            "--remote_local_fallback=false",
            "--target //src/ptoon:ptoon",
            "--mnemonic ChapelCompile",
            "gf.platform=gloriousflywheel-rbe-linux-x86_64",
        ):
            self.assertIn(required, recipes)
        for forbidden in (
            "continue-on-error",
            "ubuntu-latest",
            "cache-attachment-contract.sh",
            "mint-gf-reapi-token-from-exchange.sh",
            "workflow_dispatch",
        ):
            self.assertNotIn(forbidden, workflow)

    def test_executor_profile_pins_the_linux_target_and_execution_platform(self) -> None:
        config = (ROOT / ".bazelrc.flywheel").read_text(encoding="utf-8")
        platform = "//tools/bazel/platforms:linux_x86_64"
        self.assertIn(f"build:executor-backed --host_platform={platform}", config)
        self.assertIn(f"build:executor-backed --platforms={platform}", config)
        self.assertIn(
            f"build:executor-backed --extra_execution_platforms={platform}",
            config,
        )
        self.assertIn("build:executor-backed --remote_local_fallback=false", config)

    def test_chapel_action_is_bound_and_non_cacheable_until_keyed(self) -> None:
        rule = (ROOT / "tools" / "bazel" / "chapel" / "defs.bzl").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'execution_requirements = {} if toolchain.cacheable else {"no-cache": "1"}',
            rule,
        )
        self.assertIn("toolchain = _CHAPEL_TOOLCHAIN_TYPE", rule)

    def test_darwin_toolchain_is_worker_closure_managed(self) -> None:
        module = (ROOT / "MODULE.bazel").read_text(encoding="utf-8")
        wrapper = (
            ROOT
            / "tools"
            / "bazel"
            / "chapel"
            / "chpl_from_worker_closure.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("darwin_worker_closure_toolchain", module)
        self.assertIn("gf-chapel-prompt-toon-ab552d8/bin/chpl", wrapper)
        self.assertIn(
            "ac1a9d5a93cb85b2aec2b74bf2af21c8cfb0ce01ced7dc3222a9cf5532f12e09",
            wrapper,
        )
        recipes = (ROOT / "Justfile").read_text(encoding="utf-8")
        self.assertIn('"gf.toolchain-policy": "chapel-ab552d8"', recipes)
        self.assertIn(
            "'mnemonic(PtoonNativeSmoke, //src/ptoon:ptoon)'",
            recipes,
        )

    def test_darwin_toolchain_metadata_is_bound_byte_for_byte(self) -> None:
        wrapper = (
            ROOT
            / "tools"
            / "bazel"
            / "chapel"
            / "chpl_from_worker_closure.sh"
        )
        canonical = (
            '{"compiler_build_profile":"OPTIMIZE=0 DEBUG=0",'
            '"compiler_reported_version":"2.8.0 pre-release",'
            '"consumer_issue":"TIN-2949",'
            '"eligibility":"worker-image-candidate-not-broad-rbe",'
            '"nix_package_version":"2.7.0","schema_version":1,'
            '"source":"github:Jesssullivan/chapel",'
            '"source_revision":"ab552d88630823a961cca5db5f693b3511234e6c",'
            '"system":"aarch64-darwin"}\n'
        )
        decoy_claims = {
            "schema_version": 1,
            "system": "aarch64-darwin",
            "compiler_build_profile": "OPTIMIZE=0 DEBUG=0",
            "compiler_reported_version": "2.8.0 pre-release",
            "nix_package_version": "2.7.0",
            "source": "github:Jesssullivan/chapel",
            "source_revision": "ab552d88630823a961cca5db5f693b3511234e6c",
        }
        rejected = (
            json.dumps(
                {"schema_version": 2, "claims": decoy_claims},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            canonical.replace('"schema_version":1', '"schema_version":1,"extra":0'),
            canonical.replace(
                '"schema_version":1',
                '"schema_version":1,"schema_version":1',
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "chapel-toolchain.json"
            metadata.write_text(canonical, encoding="utf-8")
            accepted = subprocess.run(
                ["/bin/sh", str(wrapper), "--validate-metadata-only", str(metadata)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            for value in rejected:
                with self.subTest(metadata=value):
                    metadata.write_text(value, encoding="utf-8")
                    result = subprocess.run(
                        [
                            "/bin/sh",
                            str(wrapper),
                            "--validate-metadata-only",
                            str(metadata),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 125)
                    self.assertIn("metadata digest mismatch", result.stderr)

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

    def test_darwin_pr_lane_is_definition_only_until_native_rbe_exists(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "native-darwin.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: Darwin Definition", workflow)
        self.assertIn("runs-on: tinyland-nix", workflow)
        self.assertIn(
            "nix eval --raw .#packages.aarch64-darwin.ptoon.drvPath", workflow
        )
        for forbidden in (
            "macos-15",
            "nix build",
            "Require native Apple Silicon",
            "Build and run native smoke gates",
        ):
            self.assertNotIn(forbidden, workflow)

    def test_executor_proof_has_a_deterministic_cacheable_action(self) -> None:
        build = (ROOT / "BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn('name = "gf_cache_probe"', build)
        self.assertIn('srcs = ["packaging/manifest.json"]', build)
        self.assertIn('":gf_cache_probe"', build)

    def test_remote_test_runfiles_include_subprocess_and_manifest_imports(self) -> None:
        build = (ROOT / "BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn('"flake.nix"', build)
        self.assertIn('"hooks/post_tool_condense.py"', build)
        self.assertIn('"//tools/bazel/chapel:defs.bzl"', build)
        manifest_start = build.index('name = "manifest_srcs"')
        manifest_end = build.index("test_suite(", manifest_start)
        manifest_block = build[manifest_start:manifest_end]
        self.assertIn('glob(["prompt_toon/**/*.py"])', manifest_block)


if __name__ == "__main__":
    unittest.main()
