"""C4d Home Manager adoption contract coverage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

import prompt_toon
from prompt_toon.adoption import (
    CONTRACT_PATH,
    gateway_doctor_status,
    policy_doctor_status,
    request_path_doctor_status,
)
from prompt_toon.codex_harness import (
    GATEWAY_TOKEN_ENV,
    PROFILE_NAME,
    render_codex_profile,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / CONTRACT_PATH


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


class HomeManagerAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_schema_version_policy_and_activation_boundary(self) -> None:
        self.assertEqual(self.contract["schema_version"], 2)
        self.assertEqual(self.contract["version"], prompt_toon.__version__)
        self.assertEqual(self.contract["contract_path"], CONTRACT_PATH)
        self.assertIs(self.contract["activation_default"], False)
        self.assertEqual(self.contract["mode"], "shadow")
        self.assertEqual(
            self.contract["policy"]["io"]["sha256"],
            sha256((ROOT / "policy/io.json").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            self.contract["policy"]["delegation"]["sha256"],
            sha256((ROOT / "policy/delegation.json").read_bytes()).hexdigest(),
        )
        activation = self.contract["activation"]
        self.assertIs(activation["default"], False)
        self.assertEqual(activation["mode"], "shadow")
        self.assertIs(activation["enforcement"]["locked"], True)

    def test_release_artifact_requires_authenticated_nix_closure(self) -> None:
        artifact = self.contract["release_artifact"]
        self.assertEqual(
            artifact,
            {
                "required": True,
                "minimum_version": "0.3.0",
                "source_manifest": "packaging/manifest.json",
                "manifest_asset_pattern": "manifest-v{version}.json",
                "manifest_signature_asset_pattern": "manifest-v{version}.json.asc",
                "release_signers": "packaging/release-signers.json",
                "closure_format": "nix-store-export-v1",
                "closure_assets": {
                    "aarch64-darwin": "ptoon-aarch64-darwin.nar",
                    "x86_64-linux": "ptoon-x86_64-linux.nar",
                },
                "verify": [
                    "openpgp_tag",
                    "openpgp_manifest_signature",
                    "target_archive_sha256",
                    "entrypoint_sha256",
                ],
                "platform_verify": {
                    "aarch64-darwin": [
                        "build_provenance.nix.store_path",
                        "build_provenance.nix.entrypoint.store_path",
                    ],
                },
                "tagged_flake_rebuild_satisfies_byte_identity": False,
            },
        )

    def test_services_pin_loopback_ports_and_gateway_flags(self) -> None:
        services = {service["id"]: service for service in self.contract["services"]}
        self.assertEqual(set(services), {"anthropic", "openai"})
        expected = {
            "anthropic": ("gateway", 8787, "https://api.anthropic.com"),
            "openai": ("responses-gateway", 8788, "https://api.openai.com/v1"),
        }
        for service_id, (subcommand, port, upstream) in expected.items():
            with self.subTest(service_id=service_id):
                service = services[service_id]
                command = service["command"]
                self.assertEqual(service["auth_mode"], "split")
                self.assertEqual(service["listen"], {"host": "127.0.0.1", "port": port})
                self.assertEqual(service["reviewed_upstream_default"], upstream)
                self.assertEqual(
                    service["environment"],
                    {
                        "CHPL_RT_NUM_THREADS_PER_LOCALE": "2",
                        "QT_NUM_SHEPHERDS": "1",
                        "QT_NUM_WORKERS_PER_SHEPHERD": "2",
                    },
                )
                self.assertIn(subcommand, command)
                self.assertEqual(command[command.index("--listen") + 1], "127.0.0.1")
                self.assertEqual(command[command.index("--port") + 1], str(port))
                self.assertEqual(command[command.index("--upstream") + 1], upstream)
                for flag in (
                    "--require-ptoon",
                    "--require-split-auth",
                    "--ptoon",
                    "--policy",
                    "--client-token-file",
                    "--upstream-token-file",
                ):
                    self.assertIn(flag, command)
                self.assertRegex(command[command.index("--ptoon") + 1], r"^<.+>$")
                self.assertRegex(command[command.index("--policy") + 1], r"^<.+>$")
                self.assertRegex(
                    command[command.index("--client-token-file") + 1], r"^<.+>$"
                )
                self.assertRegex(
                    command[command.index("--upstream-token-file") + 1], r"^<.+>$"
                )
                self.assertNotIn("--model", command)

    def test_client_paths_and_rollbacks_are_local_only(self) -> None:
        clients = self.contract["client_request_paths"]
        claude = clients["claude"]
        self.assertEqual(
            claude["env"]["ANTHROPIC_API_KEY"],
            {"source": "local_client_token_file", "service": "anthropic"},
        )
        self.assertEqual(claude["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8787")
        self.assertEqual(
            claude["env"]["NO_PROXY"], "127.0.0.1,localhost,::1"
        )
        self.assertIn("ANTHROPIC_AUTH_TOKEN", claude["reject_if_set"])
        self.assertIn("CLAUDE_CODE_USE_BEDROCK", claude["reject_if_set"])
        self.assertEqual(
            claude["rollback"]["unset"],
            [
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "PROMPT_TOON_GATEWAY_URL",
            ],
        )
        self.assertIn(
            "direct-provider secret custody", claude["rollback"]["provider_auth"]
        )

        codex = clients["codex"]
        expected_profile = render_codex_profile(
            "http://127.0.0.1:8788", auth_mode="api-key"
        )
        self.assertEqual(codex["profile_name"], PROFILE_NAME)
        self.assertEqual(codex["profile_toml"], expected_profile)
        self.assertEqual(
            codex["profile_toml_sha256"],
            sha256(expected_profile.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(codex["local_token_env"], GATEWAY_TOKEN_ENV)
        self.assertEqual(codex["select_args"], ["--profile", PROFILE_NAME])
        self.assertEqual(codex["environment"]["NO_PROXY"], "127.0.0.1,localhost,::1")
        self.assertTrue(codex["proxy_policy"]["require_both_no_proxy_casings"])
        self.assertEqual(codex["rollback"]["select_args"], [])
        self.assertEqual(codex["rollback"]["unset"], [GATEWAY_TOKEN_ENV])

    def test_endpoints_and_activation_order_capture_safety_semantics(self) -> None:
        endpoints = self.contract["endpoints"]
        self.assertEqual(endpoints["ownership"]["path"], "/__prompt_toon/ownership")
        self.assertIn("versioned canonical attestation", endpoints["ownership"]["auth"])
        self.assertIn("ownership_endpoint", endpoints["ownership"]["attests"])
        self.assertIn("policy_sha256", endpoints["ownership"]["attests"])
        self.assertIn("resident_binary_sha256", endpoints["ownership"]["attests"])
        self.assertEqual(endpoints["readiness"]["path"], "/__prompt_toon/ready")
        self.assertIn(
            "readiness alone is not provider reachability.",
            endpoints["readiness"]["semantics"],
        )
        self.assertEqual(endpoints["metrics"]["path"], "/__prompt_toon/metrics")
        self.assertIn(
            "No request bodies or credential values.", endpoints["metrics"]["semantics"]
        )

        order = self.contract["activation_order"]
        self.assertEqual(
            [step["name"] for step in order],
            [
                "start-bind",
                "authenticated-ownership-proof",
                "expose-client-request-path",
            ],
        )

        ledger = self.contract["host_ledger"]
        self.assertTrue(ledger["write_after_activation"])
        self.assertEqual(
            set(ledger["required_fields"]),
            {
                "host",
                "observed_at",
                "service_health",
                "active_binary",
                "request_paths",
                "shadow_decisions",
                "rollback",
                "policy_sha256",
            },
        )
        self.assertIn(
            "ownership.payload.resident_binary_sha256",
            ledger["sources"]["active_binary"],
        )
        self.assertIn(
            "doctor.adoption.policy.sha256", ledger["sources"]["policy_sha256"]
        )

    def test_no_secret_like_values_or_live_actions(self) -> None:
        safety = self.contract["safety"]
        self.assertIs(safety["activation_default"], False)
        self.assertEqual(safety["credentials"], "placeholder-paths-only")
        self.assertIs(safety["host_mutation"], False)
        self.assertIs(safety["model_pins"], False)
        self.assertIs(safety["provider_calls"], False)
        self.assertIs(safety["release"], False)
        self.assertEqual(
            safety["credential_files"],
            {
                "regular_files": True,
                "owner_only": True,
                "symlinks_allowed": False,
                "client_and_upstream_distinct": True,
                "values_never_enter_contract": True,
            },
        )
        self.assertEqual(
            safety["local_client_token"]["classification"],
            "sensitive billed-relay capability",
        )
        self.assertFalse(safety["local_client_token"]["ownership_probe_sends_token"])
        secret_patterns = [
            re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
            re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
            re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),
            re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[^<\s][^\s]+"),
        ]
        for value in _walk_values(self.contract):
            if not isinstance(value, str):
                continue
            with self.subTest(value=value):
                for pattern in secret_patterns:
                    self.assertIsNone(pattern.search(value))

    def test_policy_doctor_reports_digest_and_locked_model_gateway(self) -> None:
        status = policy_doctor_status(ROOT / "policy/io.json")
        self.assertTrue(status["available"])
        self.assertEqual(
            status["sha256"], sha256((ROOT / "policy/io.json").read_bytes()).hexdigest()
        )
        self.assertFalse(status["enforcement_unlocked"])
        self.assertFalse(status["model_gateway_enabled"])

    def test_request_path_doctor_distinguishes_observed_and_unobservable_state(
        self,
    ) -> None:
        anthropic_token = "anthropic-local-" + "a" * 32
        openai_token = "openai-local-" + "o" * 32
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            anthropic_file = root / "anthropic-client"
            openai_file = root / "openai-client"
            profile_file = root / "prompt-toon-shadow.config.toml"
            anthropic_file.write_text(anthropic_token, encoding="ascii")
            openai_file.write_text(openai_token, encoding="ascii")
            profile_file.write_text(
                render_codex_profile("http://127.0.0.1:8788", auth_mode="api-key"),
                encoding="utf-8",
            )
            os.chmod(anthropic_file, 0o600)
            os.chmod(openai_file, 0o600)
            status = request_path_doctor_status(
                anthropic_gateway="http://127.0.0.1:8787",
                anthropic_client_token_file=anthropic_file,
                openai_gateway="http://127.0.0.1:8788",
                openai_client_token_file=openai_file,
                codex_profile_file=profile_file,
                environ={
                    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
                    "ANTHROPIC_API_KEY": anthropic_token,
                    "NO_PROXY": "127.0.0.1,localhost,::1",
                    "no_proxy": "127.0.0.1,localhost,::1",
                    GATEWAY_TOKEN_ENV: openai_token,
                },
            )

        self.assertEqual(status["claude"]["state"], "active_in_doctor_environment")
        self.assertTrue(status["claude"]["client_token_matches"])
        self.assertTrue(status["codex"]["profile_matches"])
        self.assertTrue(status["codex"]["local_token_matches"])
        self.assertTrue(status["codex"]["loopback_proxy_bypass_configured"])
        self.assertFalse(status["codex"]["selection_observable"])
        self.assertEqual(status["codex"]["selection_state"], "unknown")
        self.assertNotIn(anthropic_token, repr(status))
        self.assertNotIn(openai_token, repr(status))

    def test_request_path_doctor_rejects_competing_claude_routes(self) -> None:
        token = "anthropic-local-" + "a" * 32
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "anthropic-client"
            token_file.write_text(token, encoding="ascii")
            os.chmod(token_file, 0o600)
            status = request_path_doctor_status(
                anthropic_gateway="http://127.0.0.1:8787",
                anthropic_client_token_file=token_file,
                openai_gateway="http://127.0.0.1:8788",
                openai_client_token_file=None,
                codex_profile_file=Path(tmp) / "missing-profile",
                environ={
                    "ANTHROPIC_BASE_URL": "http://localhost:8787",
                    "ANTHROPIC_API_KEY": token,
                    "ANTHROPIC_AUTH_TOKEN": "must-not-echo",
                    "HTTPS_PROXY": "http://proxy.example",
                    "NO_PROXY": "127.0.0.1,localhost,::1",
                    "no_proxy": "127.0.0.1,localhost,::1",
                },
            )
        claude = status["claude"]
        self.assertEqual(claude["state"], "conflict")
        self.assertEqual(claude["route_conflicts"], ["ANTHROPIC_AUTH_TOKEN"])
        self.assertTrue(claude["base_url_matches"])
        self.assertTrue(claude["client_token_matches"])
        self.assertTrue(claude["loopback_proxy_bypass_configured"])
        self.assertNotIn("must-not-echo", repr(status))

    def test_request_path_doctor_rejects_codex_proxy_without_bypass(self) -> None:
        status = request_path_doctor_status(
            anthropic_gateway="http://127.0.0.1:8787",
            anthropic_client_token_file=None,
            openai_gateway="http://127.0.0.1:8788",
            openai_client_token_file=None,
            codex_profile_file=None,
            environ={"HTTPS_PROXY": "http://proxy.invalid"},
        )
        self.assertEqual(status["codex"]["proxy_environment_present"], ["HTTPS_PROXY"])
        self.assertFalse(status["codex"]["loopback_proxy_bypass_configured"])
        self.assertIn("proxy_without_loopback_bypass", status["codex"]["route_conflicts"])

    def test_invalid_gateway_url_is_never_echoed_by_doctor(self) -> None:
        secret_url = "http://token-value@127.0.0.1:8787?secret=value"
        status = gateway_doctor_status(
            secret_url,
            provider="anthropic",
            client_token_file=None,
            timeout=0.1,
        )
        self.assertFalse(status["gateway_valid"])
        self.assertNotIn("token-value", repr(status))
        self.assertNotIn("secret=value", repr(status))

        malformed_port = "http://127.0.0.1:secret-in-port"
        status = gateway_doctor_status(
            malformed_port,
            provider="anthropic",
            client_token_file=None,
            timeout=0.1,
        )
        self.assertFalse(status["gateway_valid"])
        self.assertNotIn("secret-in-port", repr(status))


if __name__ == "__main__":
    unittest.main()
