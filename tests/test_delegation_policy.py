import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "delegation.json"
DHALL_TYPE_PATH = ROOT / "policy" / "dhall" / "DelegationPolicy.dhall"
DHALL_SOURCE_PATH = ROOT / "policy" / "dhall" / "delegation.dhall"
SKILL_PATH = ROOT / ".agents" / "skills" / "mythos-delegation" / "SKILL.md"

FABLE_FORBIDDEN = {
    "adversarial-analysis",
    "purple-team",
    "red-team",
    "deep-iteration-hammering",
    "bulk-execution",
}


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


class DelegationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()
        cls.personas = {p["id"]: p for p in cls.policy["personas"]}

    def test_schema_version(self):
        self.assertEqual(self.policy["schema_version"], 1)

    def test_owner_linear_issue_shape(self):
        self.assertRegex(self.policy["metadata"]["owner_linear_issue"], r"^[A-Z]+-\d+$")

    def test_founding_docs_exist(self):
        for doc in self.policy["metadata"]["founding_docs"]:
            self.assertTrue((ROOT / doc).is_file(), f"missing founding doc: {doc}")

    def test_dhall_source_of_truth_exists(self):
        self.assertTrue(DHALL_TYPE_PATH.is_file())
        self.assertTrue(DHALL_SOURCE_PATH.is_file())

    def test_json_declares_dhall_regeneration_target(self):
        self.assertIn("dhall-to-json", self.policy["$comment"])

    def test_every_persona_has_purpose_and_doctrine(self):
        for persona in self.policy["personas"]:
            self.assertTrue(persona["purpose"].strip(), persona["id"])
            self.assertTrue(persona["doctrine"].strip(), persona["id"])

    def test_every_lane_has_purpose(self):
        for lane in self.policy["lanes"]:
            self.assertTrue(lane["purpose"].strip(), lane["route"])

    def test_lane_personas_resolve(self):
        for lane in self.policy["lanes"]:
            self.assertIn(lane["persona"], self.personas, lane["route"])

    def test_fable_forbids_adversarial_work(self):
        fable = self.personas["fable"]
        self.assertTrue(FABLE_FORBIDDEN.issubset(set(fable["forbidden_tasks"])))

    def test_adversarial_persona_excludes_fable(self):
        adversarial = self.personas["adversarial"]
        self.assertNotIn("fable", adversarial["model_classes"])

    def test_adversarial_lanes_never_route_to_fable(self):
        for lane in self.policy["lanes"]:
            persona = self.personas[lane["persona"]]
            if lane["persona"] == "adversarial" or "adversarial" in lane["route"]:
                self.assertNotIn("fable", persona["model_classes"], lane["route"])

    def test_error_rules_present(self):
        errors = {r["id"] for r in self.policy["enforcement"] if r["severity"] == "error"}
        self.assertTrue(
            {"no-adversarial-on-fable", "fable-forbidden-tasks", "purpose-required"}.issubset(errors)
        )

    def test_rule_severities_are_known(self):
        for rule in self.policy["enforcement"]:
            self.assertIn(rule["severity"], {"error", "warn"}, rule["id"])

    def test_attribution_values_nonempty(self):
        self.assertTrue(self.policy["attribution"]["values"])
        for value in self.policy["attribution"]["values"]:
            self.assertTrue(value.strip())

    def test_scarce_resources_name_both_realities(self):
        kinds = {r["kind"] for r in self.policy["scarce_resources"]}
        self.assertEqual(kinds, {"human", "model"})

    def test_skill_references_policy_artifact(self):
        self.assertTrue(SKILL_PATH.is_file())
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("policy/delegation.json", skill_text)

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

    def test_dhall_and_json_agree_on_load_bearing_ids(self):
        dhall_text = DHALL_SOURCE_PATH.read_text(encoding="utf-8")
        for persona in self.personas:
            self.assertIn(f'id = "{persona}"', dhall_text, persona)
        for lane in self.policy["lanes"]:
            self.assertIn(f'route = "{lane["route"]}"', dhall_text, lane["route"])
        for rule in self.policy["enforcement"]:
            self.assertIn(f'id = "{rule["id"]}"', dhall_text, rule["id"])
        owner = self.policy["metadata"]["owner_linear_issue"]
        self.assertIn(f'owner_linear_issue = "{owner}"', dhall_text)


if __name__ == "__main__":
    unittest.main()
