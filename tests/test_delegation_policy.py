import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "delegation.json"
SCHEMA_PATH = ROOT / "policy" / "delegation.schema.json"
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

# First-class seat names. Not model_classes strings: adding or renaming a seat
# is a policy change that has to move the Dhall, the JSON, and this set.
SEAT_IDS = {"fable", "opus", "sonnet", "haiku"}

# Harness seats are dispatch identities, not model classes. Same rule: adding
# or renaming one moves the Dhall, the JSON, the schema enum, and this set.
HARNESS_SEAT_IDS = {"pi", "pi-k3"}

# The two legs a harness seat can never own, whatever else it is trusted with.
# Ratification is the operator's WORD; refutation-of-record is the adversarial
# persona's. A harness seat only ever produces evidence for the fable seat.
HARNESS_FORBIDDEN = {
    "word-leg-ratification",
    "adversarial-refutation-of-record",
}

HARNESS_POSTURE = "evidentiary-only"


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def structural_schema_violations(instance, schema, path="$"):
    """Minimal fail-closed subset validator for degraded envs without jsonschema.

    Covers only what the delegation schema uses: type, const, enum, required,
    additionalProperties=false, properties, items, minItems, minLength, and
    local $ref into $defs. Unknown constructs are ignored rather than passed
    silently as valid, which is why jsonschema stays the preferred path.
    """
    defs = schema.get("$defs", {})

    def resolve(node):
        ref = node.get("$ref")
        if not ref:
            return node
        if not ref.startswith("#/$defs/"):
            return node
        merged = dict(defs[ref[len("#/$defs/") :]])
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        return merged

    def walk(value, node, where):
        node = resolve(node)
        errors = []
        expected = node.get("type")
        if expected == "object" and not isinstance(value, dict):
            return [f"{where}: expected object"]
        if expected == "array" and not isinstance(value, list):
            return [f"{where}: expected array"]
        if expected == "string" and not isinstance(value, str):
            return [f"{where}: expected string"]
        if expected == "integer" and not isinstance(value, int):
            return [f"{where}: expected integer"]
        if "const" in node and value != node["const"]:
            errors.append(f"{where}: expected const {node['const']!r}")
        if "enum" in node and value not in node["enum"]:
            errors.append(f"{where}: {value!r} not in {node['enum']}")
        if isinstance(value, str) and len(value) < node.get("minLength", 0):
            errors.append(f"{where}: shorter than minLength")
        if isinstance(value, str) and "pattern" in node:
            if not re.search(node["pattern"], value):
                errors.append(f"{where}: {value!r} fails {node['pattern']}")
        if isinstance(value, dict):
            for key in node.get("required", []):
                if key not in value:
                    errors.append(f"{where}: missing required {key!r}")
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{where}: unexpected property {key!r}")
            for key, subschema in properties.items():
                if key in value:
                    errors.extend(walk(value[key], subschema, f"{where}.{key}"))
        if isinstance(value, list):
            if len(value) < node.get("minItems", 0):
                errors.append(f"{where}: fewer than minItems")
            if node.get("uniqueItems") and len(
                {json.dumps(item, sort_keys=True) for item in value}
            ) != len(value):
                errors.append(f"{where}: duplicate items")
            item_schema = node.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    errors.extend(walk(item, item_schema, f"{where}[{index}]"))
        return errors

    return walk(instance, schema, path)


class DelegationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()
        cls.schema = load_schema()
        cls.personas = {p["id"]: p for p in cls.policy["personas"]}
        cls.seats = {s["id"]: s for s in cls.policy["seats"]}
        cls.harness_seats = {s["id"]: s for s in cls.policy["harness_seats"]}

    def test_schema_version(self):
        self.assertEqual(self.policy["schema_version"], 2)

    def test_owner_linear_issue_shape(self):
        self.assertRegex(self.policy["metadata"]["owner_linear_issue"], r"^[A-Z]+-\d+$")

    def test_founding_docs_exist(self):
        for doc in self.policy["metadata"]["founding_docs"]:
            self.assertTrue((ROOT / doc).is_file(), f"missing founding doc: {doc}")

    def test_dhall_source_of_truth_exists(self):
        self.assertTrue(DHALL_TYPE_PATH.is_file())
        self.assertTrue(DHALL_SOURCE_PATH.is_file())
        type_text = DHALL_TYPE_PATH.read_text(encoding="utf-8")
        self.assertIn("seats : List Seat", type_text)
        self.assertIn("harness_seats : List HarnessSeat", type_text)

    def test_json_declares_dhall_regeneration_target(self):
        self.assertIn("dhall-to-json", self.policy["$comment"])

    def test_json_declares_schema_document(self):
        self.assertTrue(SCHEMA_PATH.is_file())
        self.assertIn("policy/delegation.schema.json", self.policy["$comment"])
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )

    def test_policy_validates_against_schema(self):
        try:
            import jsonschema
        except ImportError:
            violations = structural_schema_violations(self.policy, self.schema)
            self.assertEqual(violations, [], "structural schema violations")
            return
        jsonschema.validate(instance=self.policy, schema=self.schema)

    def test_structural_validator_rejects_a_known_bad_document(self):
        # Guards the degraded-mode path itself: it must fail closed, not
        # silently pass every document handed to it.
        broken = json.loads(json.dumps(self.policy))
        broken["seats"][0]["cost_tier"] = "free"
        broken["seats"][0]["unexpected_key"] = True
        del broken["attribution"]
        self.assertTrue(structural_schema_violations(broken, self.schema))

    def test_seat_ids_are_first_class(self):
        self.assertEqual(set(self.seats), SEAT_IDS)
        for seat in self.policy["seats"]:
            self.assertEqual(seat["id"], seat["model_class"], seat["id"])
            self.assertTrue(seat["notes"].strip(), seat["id"])

    def test_every_routed_model_class_has_a_seat(self):
        routed = {
            model_class
            for persona in self.policy["personas"]
            for model_class in persona["model_classes"]
            if model_class != "operator"
        }
        self.assertTrue(routed.issubset(set(self.seats)), routed - set(self.seats))

    def test_seat_default_personas_accept_that_seat(self):
        for seat in self.policy["seats"]:
            for persona_id in seat["default_personas"]:
                self.assertIn(persona_id, self.personas, seat["id"])
                self.assertIn(
                    seat["model_class"],
                    self.personas[persona_id]["model_classes"],
                    f"{seat['id']} defaults to {persona_id} which excludes it",
                )

    def test_seat_forbidden_personas_are_consistent(self):
        for seat in self.policy["seats"]:
            for persona_id in seat["forbidden_personas"]:
                self.assertIn(persona_id, self.personas, seat["id"])
                self.assertNotIn(
                    seat["model_class"],
                    self.personas[persona_id]["model_classes"],
                    f"{seat['id']} forbids {persona_id} but is listed in its model classes",
                )
                self.assertNotIn(persona_id, seat["default_personas"], seat["id"])

    def test_fable_seat_never_serves_adversarial(self):
        self.assertIn("adversarial", self.seats["fable"]["forbidden_personas"])
        self.assertEqual(self.seats["fable"]["cost_tier"], "scarce")

    def test_harness_seat_ids_are_first_class(self):
        self.assertEqual(set(self.harness_seats), HARNESS_SEAT_IDS)
        for seat in self.policy["harness_seats"]:
            self.assertTrue(seat["purpose"].strip(), seat["id"])
            self.assertTrue(seat["model_lane"].strip(), seat["id"])
            self.assertTrue(seat["dispatch_surface"].strip(), seat["id"])

    def test_harness_seats_are_evidentiary_only(self):
        for seat in self.policy["harness_seats"]:
            self.assertEqual(seat["posture"], HARNESS_POSTURE, seat["id"])

    def test_harness_seats_never_own_the_word_or_refutation_legs(self):
        for seat in self.policy["harness_seats"]:
            forbidden = set(seat["forbidden_tasks"])
            self.assertTrue(
                HARNESS_FORBIDDEN.issubset(forbidden),
                f"{seat['id']} must forbid {sorted(HARNESS_FORBIDDEN - forbidden)}",
            )

    def test_harness_delegable_and_forbidden_are_disjoint(self):
        for seat in self.policy["harness_seats"]:
            delegable = set(seat["delegable_tasks"])
            forbidden = set(seat["forbidden_tasks"])
            self.assertTrue(delegable, seat["id"])
            self.assertEqual(
                delegable & forbidden,
                set(),
                f"{seat['id']} both delegates and forbids "
                f"{sorted(delegable & forbidden)}",
            )

    def test_harness_dispatch_is_bounded_launchers_not_raw_binaries(self):
        for seat in self.policy["harness_seats"]:
            surface = seat["dispatch_surface"]
            self.assertIn("launcher", surface.lower(), seat["id"])
            self.assertIn(f"{seat['harness']}-*", surface, seat["id"])

    def test_kimi_reviewer_rides_the_pi_harness(self):
        # The native kimi CLI has no dispatch envelope, so the Kimi reviewer is
        # a pi-harness seat. If that ever stops being true the seat record has
        # to change, not the doctrine quietly.
        k3 = self.harness_seats["pi-k3"]
        self.assertEqual(k3["harness"], "pi")
        self.assertIn("kimi", k3["model_lane"].lower())
        self.assertEqual(
            set(k3["delegable_tasks"]),
            set(self.harness_seats["pi"]["delegable_tasks"]),
            "pi-k3 must ride the identical technical-review envelope as pi",
        )

    def test_harness_seats_are_not_model_classes(self):
        # A harness seat is a dispatch identity. Leaking one into a persona's
        # model_classes or the model-class seat registry would let it inherit a
        # routing authority it must never have.
        self.assertEqual(HARNESS_SEAT_IDS & set(self.seats), set())
        for persona in self.policy["personas"]:
            self.assertEqual(
                HARNESS_SEAT_IDS & set(persona["model_classes"]),
                set(),
                persona["id"],
            )

    def test_every_persona_has_purpose_and_doctrine(self):
        for persona in self.policy["personas"]:
            self.assertTrue(persona["purpose"].strip(), persona["id"])
            self.assertTrue(persona["doctrine"].strip(), persona["id"])

    def test_every_lane_has_purpose(self):
        for lane in self.policy["lanes"]:
            self.assertTrue(lane["purpose"].strip(), lane["route"])

    def test_lane_personas_resolve(self):
        # A lane routes to a model-class persona or, for mythos.delegate.*, to
        # a harness seat. Nothing else resolves.
        for lane in self.policy["lanes"]:
            self.assertIn(
                lane["persona"],
                set(self.personas) | HARNESS_SEAT_IDS,
                lane["route"],
            )

    def test_harness_lanes_and_harness_personas_imply_each_other(self):
        # The mythos.delegate.* namespace and harness-seat routing are the same
        # fact stated twice; drift either way is a routing lie.
        for lane in self.policy["lanes"]:
            delegate_route = lane["route"].startswith("mythos.delegate.")
            harness_persona = lane["persona"] in self.harness_seats
            self.assertEqual(delegate_route, harness_persona, lane["route"])

    def test_harness_lanes_carry_only_evidentiary_work(self):
        for lane in self.policy["lanes"]:
            if lane["persona"] not in self.harness_seats:
                continue
            seat = self.harness_seats[lane["persona"]]
            self.assertEqual(seat["posture"], HARNESS_POSTURE, lane["route"])
            self.assertTrue(lane["purpose"].strip(), lane["route"])

    def test_fable_forbids_adversarial_work(self):
        fable = self.personas["fable"]
        self.assertTrue(FABLE_FORBIDDEN.issubset(set(fable["forbidden_tasks"])))

    def test_adversarial_persona_excludes_fable(self):
        adversarial = self.personas["adversarial"]
        self.assertNotIn("fable", adversarial["model_classes"])

    def test_adversarial_lanes_never_route_to_fable(self):
        for lane in self.policy["lanes"]:
            if lane["persona"] in self.harness_seats:
                # A harness seat has no model_classes to leak fable through,
                # and is barred from the refutation-of-record leg outright.
                self.assertIn(
                    "adversarial-refutation-of-record",
                    self.harness_seats[lane["persona"]]["forbidden_tasks"],
                    lane["route"],
                )
                continue
            persona = self.personas[lane["persona"]]
            if lane["persona"] == "adversarial" or "adversarial" in lane["route"]:
                self.assertNotIn("fable", persona["model_classes"], lane["route"])

    def test_error_rules_present(self):
        errors = {r["id"] for r in self.policy["enforcement"] if r["severity"] == "error"}
        self.assertTrue(
            {
                "no-adversarial-on-fable",
                "fable-forbidden-tasks",
                "purpose-required",
                "seat-registry-complete",
                "harness-delegation-envelope",
                "harness-dispatch-bounded-only",
            }.issubset(errors)
        )

    def test_warn_rules_present(self):
        warns = {r["id"] for r in self.policy["enforcement"] if r["severity"] == "warn"}
        self.assertTrue(
            {"cheap-lane-escalation", "serial-workflow-on-fan-out"}.issubset(warns)
        )

    def test_fan_out_rule_demands_parallel_lanes_and_names_the_checkpoint(self):
        # The rule exists to stop a serial mega-workflow from swallowing the
        # per-lane operator interview checkpoint, so it has to say both halves.
        rule = next(
            r for r in self.policy["enforcement"] if r["id"] == "serial-workflow-on-fan-out"
        )
        text = rule["rule"].lower()
        self.assertIn("parallel", text)
        self.assertIn("never one serial workflow", text)
        self.assertIn("interview checkpoint", text)

    def test_harness_envelope_rule_names_both_withheld_legs(self):
        rule = next(
            r for r in self.policy["enforcement"] if r["id"] == "harness-delegation-envelope"
        )
        text = rule["rule"].lower()
        self.assertIn("delegable_tasks", text)
        self.assertIn("word", text)
        self.assertIn("adversarial-refutation-of-record", text)

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

    def test_skill_documents_harness_delegation_and_fan_out(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("harness_seats", skill_text)
        self.assertIn("## Harness delegation", skill_text)
        self.assertIn("## Fan-out doctrine", skill_text)
        for seat_id in HARNESS_SEAT_IDS:
            self.assertIn(seat_id, skill_text, seat_id)
        # The cross-repo warning is load-bearing; a rewrite must not drop it.
        self.assertIn("Do not confuse repos", skill_text)

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
        for seat in self.seats:
            self.assertIn(f'model_class = "{seat}"', dhall_text, seat)
        for harness_seat in self.harness_seats:
            self.assertIn(f'id = "{harness_seat}"', dhall_text, harness_seat)
        for lane in self.policy["lanes"]:
            self.assertIn(f'route = "{lane["route"]}"', dhall_text, lane["route"])
        for rule in self.policy["enforcement"]:
            self.assertIn(f'id = "{rule["id"]}"', dhall_text, rule["id"])
        owner = self.policy["metadata"]["owner_linear_issue"]
        self.assertIn(f'owner_linear_issue = "{owner}"', dhall_text)


if __name__ == "__main__":
    unittest.main()
