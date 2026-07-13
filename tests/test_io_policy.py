import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "io.json"
DHALL_TYPE_PATH = ROOT / "policy" / "dhall" / "IoPolicy.dhall"
DHALL_SOURCE_PATH = ROOT / "policy" / "dhall" / "io.dhall"


class IoPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def test_schema_version(self):
        self.assertEqual(self.policy["schema_version"], 2)

    def test_owner_linear_issue_shape(self):
        self.assertRegex(self.policy["metadata"]["owner_linear_issue"], r"^[A-Z]+-\d+$")

    def test_dhall_source_of_truth_exists(self):
        self.assertTrue(DHALL_TYPE_PATH.is_file())
        self.assertTrue(DHALL_SOURCE_PATH.is_file())

    def test_gate_locked_means_every_surface_disabled(self):
        gate = self.policy["enforcement_gate"]
        self.assertIsInstance(gate["unlocked"], bool)
        self.assertTrue(gate["gated_on"], "gate must name its gating tickets")
        if not gate["unlocked"]:
            for surface in self.policy["surfaces"]:
                self.assertFalse(
                    surface["enabled"],
                    f"surface {surface['id']} enabled while enforcement gate is locked",
                )

    def test_redaction_never_fails_open(self):
        self.assertEqual(self.policy["failure_modes"]["redaction"], "fail_closed")

    def test_condensation_fails_open(self):
        self.assertEqual(self.policy["failure_modes"]["condensation"], "fail_open")

    def test_thresholds_sane(self):
        thresholds = self.policy["thresholds"]
        self.assertGreater(thresholds["enforce_threshold_tokens"], 0)
        self.assertGreater(thresholds["max_input_bytes"], 0)
        self.assertGreater(thresholds["wall_clock_budget_ms"], 0)
        self.assertGreater(thresholds["min_savings"], 0.0)
        self.assertLess(thresholds["min_savings"], 1.0)

    def test_resident_service_limits_are_bounded(self):
        limits = self.policy["service_limits"]
        for name, value in limits.items():
            self.assertIsInstance(value, int, name)
            self.assertGreater(value, 0, name)
        self.assertLessEqual(
            limits["transform_workers"], limits["max_concurrent_streams"]
        )
        self.assertLessEqual(
            limits["max_documents_per_request"], limits["max_concurrent_streams"]
        )
        self.assertGreaterEqual(
            limits["pending_queue_depth"], limits["transform_workers"]
        )
        self.assertGreaterEqual(
            limits["max_request_bytes"], self.policy["thresholds"]["max_input_bytes"]
        )
        self.assertGreaterEqual(
            limits["max_inflight_request_bytes"], limits["max_request_bytes"]
        )
        self.assertLessEqual(
            limits["max_inflight_request_bytes"], limits["max_response_bytes"]
        )
        self.assertGreaterEqual(
            limits["max_response_bytes"], limits["max_request_bytes"]
        )
        self.assertEqual(limits["max_cards_per_document"], 24)

    def test_every_surface_has_purpose(self):
        for surface in self.policy["surfaces"]:
            self.assertTrue(surface["purpose"].strip(), surface["id"])

    def test_surface_ids_unique_and_expected(self):
        ids = [s["id"] for s in self.policy["surfaces"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            set(ids), {"subagent", "mcp", "model_gateway", "bash", "read", "webfetch"}
        )

    def test_trust_tiers_cover_core_sources(self):
        sources = {t["source"]: t["tier"] for t in self.policy["trust_tiers"]}
        for required in (
            "Task",
            "Agent",
            "Bash",
            "shell_command",
            "exec_command",
            "Read",
            "WebFetch",
        ):
            self.assertIn(required, sources)
            self.assertTrue(sources[required].strip())
        self.assertNotEqual(sources["WebFetch"], "repo_source")
        self.assertEqual(sources["Task"], sources["Agent"])

    def test_dhall_json_structural_equality(self):
        dhall_to_json = shutil.which("dhall-to-json")
        if dhall_to_json is None:
            self.skipTest("dhall-to-json not on PATH (degraded mode)")
        generated = subprocess.run(
            [dhall_to_json, "--file", str(DHALL_SOURCE_PATH)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(json.loads(generated), self.policy)


if __name__ == "__main__":
    unittest.main()
