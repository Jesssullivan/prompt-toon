"""Codex Responses profile and bounded launcher tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_toon.codex_harness import (
    GATEWAY_TOKEN_ENV,
    PROFILE_NAME,
    PROXY_BYPASS_CONFLICT,
    build_codex_command,
    build_codex_env,
    codex_profile,
    parse_codex_stream,
    render_codex_profile,
    run_codex,
    validate_loopback_gateway,
)


class CodexHarnessTests(unittest.TestCase):
    def test_gateway_url_must_be_loopback_root(self) -> None:
        self.assertEqual(
            validate_loopback_gateway("http://127.0.0.1:8788/"),
            "http://127.0.0.1:8788",
        )
        self.assertEqual(
            validate_loopback_gateway("https://localhost:8788"),
            "https://127.0.0.1:8788",
        )
        self.assertEqual(
            validate_loopback_gateway("http://[::1]/"),
            "http://[::1]:80",
        )
        for invalid in (
            "https://api.openai.com",
            "http://user:pass@127.0.0.1:8788",
            "http://127.0.0.1:8788/base",
            "http://127.0.0.1:8788?x=1",
            "http://127.0.0.1:secret-in-port",
            "http://127.0.0.1:0",
            "not-a-url",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_loopback_gateway(invalid)

    def test_profile_is_responses_only_without_model_pin(self) -> None:
        profile = render_codex_profile("http://127.0.0.1:8788", auth_mode="api-key")
        self.assertIn('model_provider = "prompt-toon-shadow"', profile)
        self.assertIn('base_url = "http://127.0.0.1:8788/v1"', profile)
        self.assertIn('wire_api = "responses"', profile)
        self.assertIn("supports_websockets = false", profile)
        self.assertIn("request_max_retries = 0", profile)
        self.assertIn("stream_max_retries = 0", profile)
        self.assertIn("allow_login_shell = false", profile)
        self.assertIn(f'env_key = "{GATEWAY_TOKEN_ENV}"', profile)
        self.assertIn("requires_openai_auth = false", profile)
        self.assertIn("[shell_environment_policy]", profile)
        self.assertIn('inherit = "core"', profile)
        self.assertIn("ignore_default_excludes = false", profile)
        self.assertIn(f'exclude = ["{GATEWAY_TOKEN_ENV}"]', profile)
        self.assertNotIn("\nmodel =", profile)

    def test_openai_auth_profile_uses_existing_codex_auth(self) -> None:
        profile = render_codex_profile("http://localhost:8788", auth_mode="openai")
        self.assertIn("requires_openai_auth = true", profile)
        self.assertNotIn("env_key", profile)
        with self.assertRaises(ValueError):
            render_codex_profile("http://localhost:8788", auth_mode="unknown")

    def test_structured_profile_reports_names_only_and_direct_rollback(self) -> None:
        shadow = codex_profile(
            "shadow",
            "http://127.0.0.1:8788",
            auth_mode="api-key",
            base_env={
                "OPENAI_BASE_URL": "secret-route-value",
                "CHATGPT_BASE_URL": "other-secret-route",
            },
        )
        self.assertEqual(shadow["profile_name"], PROFILE_NAME)
        self.assertEqual(shadow["select_args"], ["--profile", PROFILE_NAME])
        self.assertEqual(shadow["conflicts"], ["CHATGPT_BASE_URL", "OPENAI_BASE_URL"])
        serialized = json.dumps(shadow)
        self.assertNotIn("secret-route-value", serialized)
        self.assertNotIn("other-secret-route", serialized)

        direct = codex_profile(
            "direct",
            "http://127.0.0.1:8788",
            auth_mode="api-key",
            base_env={},
        )
        self.assertEqual(direct["select_args"], [])
        self.assertIsNone(direct["profile_name"])
        self.assertFalse(direct["user_config_mutated"])

    def test_shadow_profile_rejects_proxy_without_loopback_bypass(self) -> None:
        shadow = codex_profile(
            "shadow",
            "http://127.0.0.1:8788",
            auth_mode="api-key",
            base_env={"HTTPS_PROXY": "http://proxy.invalid"},
        )
        self.assertEqual(shadow["proxy_environment_present"], ["HTTPS_PROXY"])
        self.assertFalse(shadow["loopback_proxy_bypass_configured"])
        self.assertIn(PROXY_BYPASS_CONFLICT, shadow["conflicts"])

        safe = codex_profile(
            "shadow",
            "http://127.0.0.1:8788",
            auth_mode="api-key",
            base_env={
                "HTTPS_PROXY": "http://proxy.invalid",
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
            },
        )
        self.assertEqual(safe["conflicts"], [])
        self.assertTrue(safe["loopback_proxy_bypass_configured"])

    def test_child_environment_is_minimal_and_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = build_codex_env(
                codex_home=Path(tmp) / "codex",
                gateway_token="fixture-token",
                base_env={
                    "PATH": "/bin",
                    "HTTPS_PROXY": "http://proxy.invalid",
                    "OPENAI_API_KEY": "must-not-leak",
                    "GITHUB_TOKEN": "must-not-leak-either",
                },
            )
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env[GATEWAY_TOKEN_ENV], "fixture-token")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertIn("localhost", env["no_proxy"])

    def test_command_is_ephemeral_read_only_and_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.txt"
            command = build_codex_command(
                codex_bin="codex",
                model="gpt-probe",
                fixture=fixture,
            )
        self.assertEqual(command[:4], ["codex", "--ask-for-approval", "never", "exec"])
        self.assertEqual(command[command.index("--profile") + 1], PROFILE_NAME)
        self.assertIn("--strict-config", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("plugins", command)
        self.assertIn("web_search_request", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--ask-for-approval") + 1], "never")
        self.assertIn(
            f'shell_environment_policy.exclude=["{GATEWAY_TOKEN_ENV}"]', command
        )
        self.assertIn("allow_login_shell=false", command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-probe")
        self.assertFalse(any("dangerously" in value for value in command))

        direct = build_codex_command(
            codex_bin="codex",
            model="gpt-probe",
            fixture=fixture,
            profile_name=None,
        )
        self.assertNotIn("--profile", direct)

    def test_stream_parser_requires_one_completion_and_final_message(self) -> None:
        stream = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "thread.started", "thread_id": "thread_fixture"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "PASS"},
                },
                {"type": "turn.completed", "usage": {"input_tokens": 1}},
            )
        )
        parsed = parse_codex_stream(stream)
        self.assertEqual(parsed["result"], "PASS")
        self.assertEqual(parsed["completed_turns"], 1)
        self.assertEqual(parsed["errors"], 0)
        with self.assertRaisesRegex(ValueError, "completed turn"):
            parse_codex_stream(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "no turn"},
                    }
                )
            )
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            parse_codex_stream("not json")

    @mock.patch("prompt_toon.codex_harness.subprocess.run")
    def test_runner_never_surfaces_child_output_on_failure(
        self, run: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["codex"], 7, stdout="secret stdout", stderr="secret stderr"
        )
        with self.assertRaisesRegex(RuntimeError, "status 7") as caught:
            run_codex(
                command=["codex"],
                env={},
                cwd=".",
                timeout_seconds=1,
            )
        self.assertNotIn("secret", str(caught.exception))

        run.side_effect = subprocess.TimeoutExpired(["codex"], 1)
        with self.assertRaisesRegex(RuntimeError, "could not complete"):
            run_codex(
                command=["codex"],
                env={},
                cwd=".",
                timeout_seconds=1,
            )


if __name__ == "__main__":
    unittest.main()
