"""Drift and portability gates for policy/redaction.json (J82(d), PT-1b).

The Dhall source, the rendered JSON, the Python SECRET_PATTERNS and the
Chapel RE2 specs must name the same nine patterns in the same order.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_toon import cli

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "redaction.json"
DHALL_TYPE_PATH = ROOT / "policy" / "dhall" / "RedactionPolicy.dhall"
DHALL_SOURCE_PATH = ROOT / "policy" / "dhall" / "redaction.dhall"
CHAPEL_REDACT_PATH = ROOT / "src" / "ptoon" / "Redact.chpl"

# The literal SECRET_PATTERNS list cli.py carried before the policy file
# existed. It pins behaviour: the policy may only restate these patterns in
# portable form, never change what they match.
HISTORIC_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|ghp|gho|github_pat|xox[baprs])-[-_A-Za-z0-9]{16,}\b"),
    re.compile(r"\b(?:sk|ghp|gho|github_pat)_[-_A-Za-z0-9]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]+?-----END[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
]

# Synthetic, non-secret samples that exercise every pattern and its edges.
SAMPLES = [
    "key sk-" + "A" * 20 + " end",
    "ghp_" + "b" * 24,
    "xoxb-" + "1234567890abcdefgh",
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ASIA" + "ABCDEFGHIJKLMNO",
    "AIza" + "x" * 35,
    "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
    "-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----",
    "mail someone@example.com today",
    "API_KEY = '" + "a" * 13 + "'",
    "Token: " + '"' + "b" * 11 + '"',
    "PASSWORD=" + "c" * 14,
    "secret:   x",
    "card 4111 1111 1111 1111 ok",
    "4111-1111-1111-1111",
    "ésk-" + "A" * 20,
    "sk-" + "A" * 20 + "é",
    "中文ghp_" + "c" * 20,
    "nothing to see here",
]


def portable_violations(source):
    """Return the forbidden constructs a pattern source uses."""

    found = []
    if re.search(r"\(\?(?!:)", source):
        found.append("non-capturing (?: is the only (? form allowed")
    if re.search(r"\\[1-9]|\\k<", source):
        found.append("backreference")
    if re.search(r"[*+?}]\+", source):
        found.append("possessive-quantifier")
    return found


def chapel_gen_sources(text):
    """Extract the RE2 candidate sources from Redact.chpl PatternSpec calls."""

    specs = re.findall(
        r'new PatternSpec\("(pattern-\d+)",\s*"((?:[^"\\]|\\.)*)"', text
    )
    return [(name, re.sub(r"\\(.)", r"\1", raw)) for name, raw in specs]


class RedactionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        cls.patterns = cls.policy["patterns"]

    def test_dhall_source_of_truth_exists(self):
        self.assertTrue(DHALL_TYPE_PATH.is_file())
        self.assertTrue(DHALL_SOURCE_PATH.is_file())
        self.assertIn("dhall-to-json", self.policy["$comment"])

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

    def test_schema_and_ids_are_positional(self):
        self.assertEqual(self.policy["schema_version"], cli.REDACTION_POLICY_SCHEMA_VERSION)
        self.assertRegex(self.policy["metadata"]["owner_linear_issue"], r"^[A-Z]+-\d+$")
        self.assertEqual(
            [p["id"] for p in self.patterns],
            [f"pattern-{i}" for i in range(1, len(self.patterns) + 1)],
        )
        self.assertEqual(len(self.patterns), len(HISTORIC_SECRET_PATTERNS))

    def test_sources_stay_in_portable_subset(self):
        for entry in self.patterns:
            with self.subTest(entry["id"]):
                self.assertEqual(portable_violations(entry["source"]), [])
                self.assertIsInstance(entry["case_insensitive"], bool)

    def test_portable_checker_rejects_lookaround(self):
        for bad in (r"a(?=b)", r"(?<!a)b", r"(?i)x", r"(a)\1", r"a++"):
            with self.subTest(bad):
                self.assertTrue(portable_violations(bad))

    def test_cli_compiles_the_policy_in_order(self):
        self.assertEqual(len(cli.SECRET_PATTERNS), len(self.patterns))
        for compiled, entry in zip(cli.SECRET_PATTERNS, self.patterns):
            with self.subTest(entry["id"]):
                self.assertEqual(compiled.pattern, entry["source"])
                self.assertEqual(
                    bool(compiled.flags & re.IGNORECASE), entry["case_insensitive"]
                )

    def test_policy_restates_historic_patterns(self):
        for historic, entry in zip(HISTORIC_SECRET_PATTERNS, self.patterns):
            with self.subTest(entry["id"]):
                source = historic.pattern
                inline_ci = source.startswith("(?i)")
                if inline_ci:
                    source = source[len("(?i)"):]
                # A raw-string \" inside a class is a plain quote.
                source = source.replace('\\"', '"')
                self.assertEqual(source, entry["source"])
                self.assertEqual(inline_ci, entry["case_insensitive"])

    def test_redaction_behaviour_matches_historic_patterns(self):
        def historic_redact(text):
            redacted = cli.normalize_text(text)
            findings = []
            for index, pattern in enumerate(HISTORIC_SECRET_PATTERNS, start=1):
                if pattern.search(redacted):
                    findings.append(f"pattern-{index}")
                    redacted = pattern.sub(cli._redaction_replacement, redacted)
            return redacted, findings

        for sample in SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(cli.redact_text(sample), historic_redact(sample))
        hits = {f for sample in SAMPLES for f in cli.redact_text(sample)[1]}
        self.assertEqual(hits, {p["id"] for p in self.patterns})

    def test_replacement_token_and_line_breaks(self):
        replacement = self.policy["replacement"]
        self.assertTrue(replacement["preserve_line_breaks"])
        redacted, findings = cli.redact_text(SAMPLES[7])
        self.assertEqual(findings, ["pattern-6"])
        self.assertEqual(redacted, replacement["token"] + "\n\n\n")

    def test_chapel_specs_match_policy(self):
        specs = chapel_gen_sources(CHAPEL_REDACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(specs), len(self.patterns))
        for (name, gen), entry in zip(specs, self.patterns):
            with self.subTest(entry["id"]):
                self.assertEqual(name, entry["id"])
                prefix = "(?i)" if entry["case_insensitive"] else ""
                self.assertEqual(gen, prefix + entry["source"])

    def test_loader_fails_closed(self):
        broken = [
            {"schema_version": 2, "patterns": self.patterns},
            {"schema_version": 1, "patterns": []},
            {"schema_version": 1, "patterns": list(reversed(self.patterns))},
            {"schema_version": 1, "patterns": [dict(self.patterns[0], case_insensitive="no")]},
            # Well-formed but narrower or weaker. Only the pinned digest
            # rejects these.
            {"schema_version": 1, "patterns": self.patterns[:1]},
            {"schema_version": 1, "patterns": self.patterns[:-1]},
            {
                "schema_version": 1,
                "patterns": self.patterns[:7]
                + [dict(self.patterns[7], source="a^")]
                + self.patterns[8:],
            },
            {
                "schema_version": 1,
                "patterns": self.patterns[:7]
                + [dict(self.patterns[7], case_insensitive=False)]
                + self.patterns[8:],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, payload in enumerate(broken):
                path = Path(tmp) / f"broken-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(index=index):
                    with self.assertRaises(RuntimeError):
                        cli.load_secret_patterns(path)

    def test_patterns_digest_is_pinned(self):
        self.assertEqual(
            cli.redaction_patterns_digest(self.patterns), cli.REDACTION_PATTERNS_SHA256
        )
        self.assertEqual(len(cli.load_secret_patterns(POLICY_PATH)), len(self.patterns))

    def test_installed_lookup_prefers_data_files(self):
        site_cli = (
            Path(sys.prefix) / "lib" / "python3" / "site-packages" / "prompt_toon" / "cli.py"
        )
        with mock.patch.object(cli, "__file__", str(site_cli)):
            candidates = cli.redaction_policy_candidates()
        data = Path(sys.prefix) / "share" / "prompt-toon" / "policy" / "redaction.json"
        self.assertEqual(candidates[0], data)
        self.assertEqual(candidates[-1], site_cli.parent.parent / "policy" / "redaction.json")

    def test_bazel_binary_declares_the_policy(self):
        build = (ROOT / "BUILD.bazel").read_text(encoding="utf-8")
        binary = re.search(r'sh_binary\(\s*name = "prompt-toon",.*?\n\)', build, re.S)
        self.assertIsNotNone(binary)
        self.assertIn('"policy/redaction.json"', binary.group(0))

    def test_packaging_ships_the_policy(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"policy/redaction.json"', pyproject)


if __name__ == "__main__":
    unittest.main()
