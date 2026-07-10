"""tests/test_hook_adapter.py -- TIN-2709 C2f coverage for the PostToolUse
condensation hook (hooks/post_tool_condense.py) that is decidable WITHOUT
the ptoon binary: the policy gate, the extraction/threshold bypasses, and
the fail-open contract when the binary is absent. The with-binary legs
(rewrite, never-leak, cache hit, tamper quarantine) live in
tools/hook_canary.py, which runs inside the ptoon-parity derivation where
the binary exists.

Every case runs the hook as a real subprocess -- the same shape Claude Code
invokes it in -- with a scratch PROMPT_TOON_STATE_HOME and an explicit
PROMPT_TOON_IO_POLICY, so no host state can leak in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "post_tool_condense.py"

ENABLED_POLICY = {
    "enforcement_gate": {"unlocked": True},
    "surfaces": [{"id": "subagent", "enabled": True}],
    "thresholds": {
        "enforce_threshold_tokens": 10,
        "max_input_bytes": 2000000,
        "min_savings": 0.25,
        "wall_clock_budget_ms": 60000,
    },
    "trust_tiers": [{"source": "Task", "tier": "subagent_return"}],
}


class HookAdapterDegradedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ptoon-hook-test-")
        self.state_home = Path(self._tmp.name) / "state"
        self.policy_path = Path(self._tmp.name) / "policy.json"
        self.policy_path.write_text(json.dumps(ENABLED_POLICY), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, payload: dict, *, policy: Path | None = None,
             binary: str | None = None) -> tuple[int, bytes]:
        env = dict(os.environ)
        env["PROMPT_TOON_STATE_HOME"] = str(self.state_home)
        env["PROMPT_TOON_IO_POLICY"] = str(policy if policy else self.policy_path)
        # Force a deterministic "binary absent" state unless a test opts in.
        env["PROMPT_TOON_PTOON"] = binary if binary else str(ROOT / "no-such-ptoon")
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            env=env,
            cwd=ROOT,
        )
        return proc.returncode, proc.stdout

    def _log_outcomes(self) -> list[dict]:
        log = self.state_home / "hook-log.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def _task_payload(text: str) -> dict:
        return {"tool_name": "Task",
                "tool_response": {"content": [{"type": "text", "text": text}]}}

    def test_locked_fleet_policy_is_a_silent_noop(self):
        rc, out = self._run(
            self._task_payload("word " * 100),
            policy=ROOT / "policy" / "io.json",
        )
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(self._log_outcomes()[-1]["reason"], "surface-disabled")

    def test_non_task_tool_is_ignored_without_logging(self):
        rc, out = self._run({"tool_name": "Bash", "tool_response": {"stdout": "x"}})
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(self._log_outcomes(), [])

    def test_below_token_threshold_bypasses(self):
        rc, out = self._run(self._task_payload("tiny"))
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(self._log_outcomes()[-1]["reason"], "below-token-threshold")

    def test_no_extractable_text_bypasses(self):
        rc, out = self._run({"tool_name": "Task", "tool_response": {"weird": 7}})
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(self._log_outcomes()[-1]["reason"], "no-extractable-text")

    def test_binary_absent_fails_open_with_no_output(self):
        rc, out = self._run(self._task_payload("word " * 100))
        self.assertEqual((rc, out), (0, b""))
        last = self._log_outcomes()[-1]
        self.assertEqual(last["outcome"], "error")
        self.assertIn("unavailable", last["reason"])

    def test_malformed_payload_is_a_silent_noop(self):
        env = dict(os.environ)
        env["PROMPT_TOON_STATE_HOME"] = str(self.state_home)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=b"not json at all",
            capture_output=True,
            env=env,
            cwd=ROOT,
        )
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main()
