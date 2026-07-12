"""Claude Code gateway profile and bounded launcher tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_toon.claude_harness import (
    build_claude_command,
    build_claude_env,
    claude_profile,
    parse_claude_stream,
    run_claude,
    validate_loopback_url,
)


class ClaudeHarnessTests(unittest.TestCase):
    def test_loopback_validation_rejects_remote_credentials_and_query(self) -> None:
        self.assertEqual(
            validate_loopback_url("http://127.0.0.1:8787/"),
            "http://127.0.0.1:8787",
        )
        self.assertEqual(validate_loopback_url("http://[::1]:8787"), "http://[::1]:8787")
        for value in (
            "https://api.anthropic.com",
            "http://user:secret@127.0.0.1:8787",
            "http://127.0.0.1:8787?x=1",
            "not-a-url",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_loopback_url(value)

    def test_profiles_are_structured_process_scoped_and_detect_route_conflicts(self) -> None:
        shadow = claude_profile(
            "shadow",
            "http://127.0.0.1:8787",
            base_env={"CLAUDE_CODE_USE_BEDROCK": "1"},
        )
        self.assertEqual(
            shadow,
            {
                "mode": "shadow",
                "process_scope": True,
                "gateway": "http://127.0.0.1:8787",
                "conflicts": ["CLAUDE_CODE_USE_BEDROCK"],
            },
        )
        direct = claude_profile("direct", "http://example.invalid", base_env={})
        self.assertEqual(
            direct,
            {
                "mode": "direct",
                "process_scope": True,
                "unset": ["ANTHROPIC_BASE_URL", "PROMPT_TOON_GATEWAY_URL"],
            },
        )
        rendered = json.dumps((shadow, direct))
        self.assertNotIn("API_KEY", rendered)
        self.assertNotIn("model", rendered.lower())

    def test_canary_environment_is_isolated_to_loopback_api_key_auth(self) -> None:
        env = build_claude_env(
            gateway_url="http://localhost:8787",
            api_key="unit-test-key",
            config_dir="/tmp/unit-claude",
            base_env={
                "PATH": "/bin",
                "ANTHROPIC_AUTH_TOKEN": "remove",
                "ANTHROPIC_CUSTOM_HEADERS": "X-Remove: value",
                "CLAUDE_CODE_OAUTH_TOKEN": "remove",
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "NO_PROXY": "example.test",
                "UNRELATED_SECRET": "remove",
            },
        )
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "unit-test-key")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:8787")
        expected_config = str(Path("/tmp/unit-claude").resolve())
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], expected_config)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
        self.assertNotIn("UNRELATED_SECRET", env)
        self.assertEqual(env["HOME"], str(Path("/tmp").resolve()))
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertIn("::1", env["no_proxy"])

    def test_command_is_bare_streaming_read_only_and_budget_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.txt"
            fixture.write_text("fixture", encoding="utf-8")
            command = build_claude_command(
                claude_bin="/usr/bin/claude",
                model="claude-test",
                fixture=fixture,
                max_budget_usd=0.25,
                session_id="11111111-1111-4111-8111-111111111111",
            )
        self.assertEqual(command[0], "/usr/bin/claude")
        for value in (
            "--bare",
            "--print",
            "stream-json",
            "--include-partial-messages",
            "--no-session-persistence",
            "--max-budget-usd",
            "0.25",
            "--session-id",
            "11111111-1111-4111-8111-111111111111",
        ):
            self.assertIn(value, command)
        self.assertEqual(command[command.index("--tools") + 1], "Read")
        self.assertEqual(
            command[command.index("--allowedTools") + 1],
            f"Read(/{fixture.resolve()})",
        )
        self.assertIn(str(fixture.resolve()), command[-1])
        for budget in (0, -1, 1.01):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    build_claude_command(
                        claude_bin="claude",
                        model="claude-test",
                        fixture=fixture,
                        max_budget_usd=budget,
                        session_id="11111111-1111-4111-8111-111111111111",
                    )

    def test_stream_parser_requires_a_non_error_result(self) -> None:
        stdout = "\n".join(
            (
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps({"type": "system", "subtype": "api_retry"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "PROMPT_TOON_OK",
                    }
                ),
            )
        )
        parsed = parse_claude_stream(stdout)
        self.assertEqual(parsed["result"], "PROMPT_TOON_OK")
        self.assertEqual(parsed["stream_lines"], 3)
        self.assertEqual(parsed["api_retries"], 1)
        with self.assertRaises(ValueError):
            parse_claude_stream("not-json\n")
        with self.assertRaises(ValueError):
            parse_claude_stream(json.dumps({"type": "assistant"}))

    @mock.patch("prompt_toon.claude_harness.subprocess.run")
    def test_runner_does_not_surface_captured_prompt_or_provider_error(
        self, run: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["claude"], 1, stdout="sensitive prompt", stderr="provider body"
        )
        with self.assertRaisesRegex(RuntimeError, "status 1") as raised:
            run_claude(
                command=["claude"],
                env={},
                cwd="/tmp",
                timeout_seconds=1,
            )
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("provider body", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
