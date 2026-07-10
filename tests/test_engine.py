"""tests/test_engine.py -- TIN-2708 C1 Python-lane coverage for the
subprocess wrapper (prompt_toon/engine.py) over the standalone `ptoon`
binary, behind --engine={python,chapel,auto}.

This host is darwin with no chpl toolchain and (by default) no
build/ptoon binary artifact, so this file runs in degraded mode by
design -- the same pattern as test_delegation_policy.py's dhall-to-json
skip: policy/contract behavior that is decidable without the external
tool is asserted unconditionally (forcing a deterministic "binary
absent" state via PROMPT_TOON_PTOON so the assertion doesn't depend on
host state), while behavior that requires the real binary skips cleanly
via self.skipTest() when it isn't present.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from prompt_toon import engine as engine_module
from prompt_toon.cli import main, normalize_text as python_normalize_text, resolve_engine

ROOT = Path(__file__).resolve().parent.parent


class _ForcedBinaryAbsentTestCase(unittest.TestCase):
    """Deterministically forces "no ptoon binary" regardless of host state
    by pointing PROMPT_TOON_PTOON at a path that cannot exist."""

    def setUp(self) -> None:
        self._old_env = os.environ.get("PROMPT_TOON_PTOON")
        os.environ["PROMPT_TOON_PTOON"] = str(ROOT / "no-such-ptoon-for-tests")

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("PROMPT_TOON_PTOON", None)
        else:
            os.environ["PROMPT_TOON_PTOON"] = self._old_env


class ResolveBinaryPathTests(unittest.TestCase):
    def test_env_override_pointing_nowhere_resolves_to_none(self):
        old_env = os.environ.get("PROMPT_TOON_PTOON")
        os.environ["PROMPT_TOON_PTOON"] = str(ROOT / "definitely-does-not-exist")
        try:
            self.assertIsNone(engine_module.resolve_binary_path())
        finally:
            if old_env is None:
                os.environ.pop("PROMPT_TOON_PTOON", None)
            else:
                os.environ["PROMPT_TOON_PTOON"] = old_env

    def test_env_override_pointing_at_real_file_wins(self):
        with tempfile.NamedTemporaryFile() as handle:
            old_env = os.environ.get("PROMPT_TOON_PTOON")
            os.environ["PROMPT_TOON_PTOON"] = handle.name
            try:
                self.assertEqual(engine_module.resolve_binary_path(), Path(handle.name))
            finally:
                if old_env is None:
                    os.environ.pop("PROMPT_TOON_PTOON", None)
                else:
                    os.environ["PROMPT_TOON_PTOON"] = old_env

class DegradedModeEngineResolutionTests(_ForcedBinaryAbsentTestCase):
    def test_chapel_engine_reports_unavailable(self):
        self.assertFalse(engine_module.ChapelEngine().available())

    def test_auto_falls_back_to_python(self):
        ns = resolve_engine("auto")
        sample = "café​ MUST"
        self.assertEqual(ns.normalize_text(sample), python_normalize_text(sample))

    def test_explicit_chapel_engine_exits_nonzero_when_absent(self):
        with self.assertRaises(SystemExit) as ctx:
            resolve_engine("chapel")
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIsNotNone(ctx.exception.code)

    def test_cli_condense_with_engine_chapel_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            input_path.write_text("hello world\n", encoding="utf-8")
            out_dir = Path(tmp) / "out"
            with self.assertRaises(SystemExit):
                main(["condense", str(input_path), "--engine", "chapel", "--output-dir", str(out_dir)])

    def test_cli_condense_with_engine_auto_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            input_path.write_text("You MUST keep provenance.\n", encoding="utf-8")
            out_dir = Path(tmp) / "out"
            code = main(["condense", str(input_path), "--engine", "auto", "--output-dir", str(out_dir)])
            self.assertEqual(code, 0)
            self.assertTrue((out_dir / "summary.md").is_file())

    def test_analyze_and_encode_toon_engine_chapel_also_fail_closed(self):
        payload = '[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]'
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "rows.json"
            input_path.write_text(payload, encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["analyze", str(input_path), "--engine", "chapel"])
            with self.assertRaises(SystemExit):
                main(["encode-toon", str(input_path), "--engine", "chapel"])

    def test_unknown_engine_value_rejected_by_argparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            input_path.write_text("hello\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["condense", str(input_path), "--engine", "bogus"])


class ChapelEngineSubprocessErrorTests(_ForcedBinaryAbsentTestCase):
    """Exercises the subprocess error paths with a fake `ptoon` binary
    (a small shell script), so this coverage does not depend on the real
    Chapel-built binary being present on this host."""

    def _write_fake_binary(self, tmp: str, script: str) -> Path:
        path = Path(tmp) / "ptoon"
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def test_nonzero_exit_raises_engine_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp, "#!/bin/sh\necho 'bad subcommand' 1>&2\nexit 2\n"
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            with self.assertRaises(engine_module.EngineError):
                engine.normalize_text("hello")

    def test_stderr_output_with_zero_exit_raises_engine_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp, "#!/bin/sh\ncat >/dev/null\necho 'unexpected warning' 1>&2\nexit 0\n"
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            with self.assertRaises(engine_module.EngineError):
                engine.defang_text("hello")

    def test_redact_output_missing_findings_header_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(tmp, "#!/bin/sh\ncat >/dev/null\necho 'no header here'\n")
            engine = engine_module.ChapelEngine(binary_path=binary)
            with self.assertRaises(engine_module.EngineError):
                engine.redact_text("hello")

    def test_redact_parses_findings_line_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp,
                "#!/bin/sh\ncat >/dev/null\nprintf 'findings:pattern-1,pattern-2\\nredacted body\\n'\n",
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            redacted, findings = engine.redact_text("irrelevant")
            self.assertEqual(redacted, "redacted body\n")
            self.assertEqual(findings, ["pattern-1", "pattern-2"])

    def test_redact_parses_empty_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp, "#!/bin/sh\ncat >/dev/null\nprintf 'findings:\\nclean body\\n'\n"
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            redacted, findings = engine.redact_text("irrelevant")
            self.assertEqual(redacted, "clean body\n")
            self.assertEqual(findings, [])

    def test_engine_caps_parses_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp, '#!/bin/sh\ncat >/dev/null\nprintf \'{"engine": "chapel"}\'\n'
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            self.assertEqual(engine.engine_caps(), {"engine": "chapel"})

    def test_redact_batch_parser_rejects_truncated_body(self):
        raw = b'1\n{"i":0,"withheld":false,"findings":[]}\n5\nabc'
        with self.assertRaises(engine_module.EngineError):
            engine_module.ChapelEngine._parse_batch(raw)

    def test_redact_batch_parser_rejects_trailing_bytes(self):
        raw = b"0\ntrailing"
        with self.assertRaises(engine_module.EngineError):
            engine_module.ChapelEngine._parse_batch(raw)

    def test_redact_batch_parser_rejects_out_of_order_index(self):
        raw = b'1\n{"i":1,"withheld":false,"findings":[]}\n0\n'
        with self.assertRaises(engine_module.EngineError):
            engine_module.ChapelEngine._parse_batch(raw)

    def test_missing_binary_path_raises_engine_error_not_os_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "does-not-exist"
            engine = engine_module.ChapelEngine(binary_path=binary)
            with self.assertRaises(engine_module.EngineError):
                engine.normalize_text("hello")

    def test_redact_batch_rejects_negative_policy_args_before_spawn(self):
        engine = engine_module.ChapelEngine(binary_path=Path("/does-not-exist"))
        with self.assertRaises(ValueError):
            engine.redact_batch(["x"], max_input_bytes=-1)
        with self.assertRaises(ValueError):
            engine.redact_batch(["x"], max_input_bytes=1, budget_ms=-1)

    def test_redact_batch_budget_requires_input_cap_before_spawn(self):
        engine = engine_module.ChapelEngine(binary_path=Path("/does-not-exist"))
        with self.assertRaises(ValueError):
            engine.redact_batch(["x"], budget_ms=1)

    def test_redact_batch_forwards_policy_args_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._write_fake_binary(
                tmp,
                """#!/bin/sh
cat >/dev/null
if [ "$1" != "redact-batch" ] || [ "$2" != "1234" ] || [ "$3" != "60000" ]; then
  echo "bad args: $*" 1>&2
  exit 7
fi
printf '0\\n'
""",
            )
            engine = engine_module.ChapelEngine(binary_path=binary)
            self.assertEqual(
                engine.redact_batch([], max_input_bytes=1234, budget_ms=60000),
                [],
            )

    # --- condense-batch (C2d) stream-grammar and pre-spawn validation ---

    @staticmethod
    def _stream(*events: dict) -> bytes:
        return b"".join(json.dumps(e).encode() + b"\n" for e in events)

    @staticmethod
    def _doc_event(i: int, withheld: bool = False, **extra) -> dict:
        event = {
            "event": "doc", "i": i, "source": "s", "trust_tier": "t",
            "bytes": 1, "sha256": "0" * 64, "withheld": withheld,
            "findings": [],
        }
        event.update(extra)
        return event

    @staticmethod
    def _card(**extra) -> dict:
        card = {
            "claim": "claim",
            "confidence": "low",
            "evidence": "evidence",
            "flags": [],
            "id": "src-001",
            "line_end": 1,
            "line_start": 1,
            "sha256": "0" * 64,
            "source": "s",
            "trust_tier": "t",
        }
        card.update(extra)
        return card

    @staticmethod
    def _meta(**extra) -> dict:
        meta = {
            "source": "s",
            "trust_tier": "t",
            "bytes": 1,
            "sha256": "0" * 64,
        }
        meta.update(extra)
        return meta

    def _parse(
        self,
        raw: bytes,
        expected_docs: int,
        *,
        metas: list[dict] | None = None,
        max_cards: int = 24,
    ) -> list[dict]:
        if metas is None:
            metas = [self._meta() for _ in range(expected_docs)]
        return engine_module.ChapelEngine._parse_stream(
            raw, expected_docs, expected_meta=metas, max_cards=max_cards
        )

    def test_condense_batch_rejects_bad_policy_args_before_spawn(self):
        engine = engine_module.ChapelEngine(binary_path=Path("/nonexistent"))
        doc = [{"source": "s", "trust_tier": "t", "body": "x"}]
        for kwargs in (
            {"max_input_bytes": -1},
            {"budget_ms": -1},
            {"budget_ms": 5},              # budget without cap
            {"max_cards": 0},
            {"max_cards": -3},
            {"max_input_bytes": True},     # bool is not an int here
        ):
            with self.assertRaises(ValueError):
                engine.condense_batch(doc, **kwargs)

    def test_condense_batch_rejects_bad_doc_shape_before_spawn(self):
        engine = engine_module.ChapelEngine(binary_path=Path("/nonexistent"))
        cases = [
            ([None], "doc 0 must be a dict"),
            ([{"source": "s", "trust_tier": "t"}], "missing required field 'body'"),
            ([{"source": "s", "body": "x"}], "missing required field 'trust_tier'"),
            ([{"source": 1, "trust_tier": "t", "body": "x"}], "must be strings"),
            ([{"source": "s", "trust_tier": "t", "body": b"x"}], "must be strings"),
        ]
        for docs, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValueError, pattern):
                    engine.condense_batch(docs)

    def test_parse_stream_accepts_wellformed_events(self):
        card = self._card()
        raw = self._stream(
            self._doc_event(0, findings=["pattern-1"]),
            {"event": "card", "i": 0, "card": card},
            {"event": "end", "i": 0, "cards": 1},
            self._doc_event(1, withheld=True, reason="input-cap"),
            {"event": "end", "i": 1, "cards": 0},
            {"event": "batch", "docs": 2, "cards": 1, "withheld": 1},
        )
        results = self._parse(raw, 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["cards"], [card])
        self.assertTrue(results[1]["withheld"])
        self.assertEqual(results[1]["reason"], "input-cap")
        self.assertNotIn("cards", results[1])

    def test_parse_stream_rejects_out_of_order_doc_index(self):
        raw = self._stream(
            self._doc_event(1),
            {"event": "end", "i": 1, "cards": 0},
            {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(raw, 1)

    def test_parse_stream_rejects_card_from_withheld_doc(self):
        raw = self._stream(
            self._doc_event(0, withheld=True, reason="budget"),
            {"event": "card", "i": 0, "card": self._card()},
            {"event": "end", "i": 0, "cards": 1},
            {"event": "batch", "docs": 1, "cards": 0, "withheld": 1},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(raw, 1)

    def test_parse_stream_rejects_spoofed_doc_event_types(self):
        cases = {
            "withheld-string": self._stream(
                self._doc_event(0, withheld="false"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "bad-sha": self._stream(
                self._doc_event(0, sha256="not-a-sha256"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "bad-findings": self._stream(
                self._doc_event(0, findings=["ok", 7]),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "negative-bytes": self._stream(
                self._doc_event(0, bytes=-1),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "non-string-source": self._stream(
                self._doc_event(0, source=12),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "withheld-missing-reason": self._stream(
                self._doc_event(0, withheld=True),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 1},
            ),
            "withheld-empty-reason": self._stream(
                self._doc_event(0, withheld=True, reason=""),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 1},
            ),
            "non-withheld-reason": self._stream(
                self._doc_event(0, reason="budget"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "withheld-findings": self._stream(
                self._doc_event(0, withheld=True, reason="budget", findings=["secret"]),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 1},
            ),
            "withheld-bad-reason": self._stream(
                self._doc_event(0, withheld=True, reason="leaked detail"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 1},
            ),
            "extra-doc-key": self._stream(
                self._doc_event(0, raw="secret"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(engine_module.EngineError):
                    self._parse(raw, 1)

    def test_parse_stream_rejects_doc_metadata_mismatch(self):
        cases = {
            "source": self._stream(
                self._doc_event(0, source="spoofed"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "trust-tier": self._stream(
                self._doc_event(0, trust_tier="operator"),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "bytes": self._stream(
                self._doc_event(0, bytes=2),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "sha256": self._stream(
                self._doc_event(0, sha256="1" * 64),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(engine_module.EngineError):
                    self._parse(raw, 1)

    def test_parse_stream_rejects_spoofed_card_end_and_batch_types(self):
        cases = {
            "bad-card-payload": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": "not an object"},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "bad-card-index": self._stream(
                self._doc_event(0),
                {"event": "card", "i": True, "card": self._card()},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "extra-card-event-key": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(), "raw": "secret"},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "extra-card-object-key": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(raw="secret")},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "bad-card-flags": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(flags=["ok", 7])},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "bad-card-line-span": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(line_start=3, line_end=2)},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "card-source-mismatch": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(source="spoofed")},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "card-trust-tier-mismatch": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(trust_tier="operator")},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "card-sha-mismatch": self._stream(
                self._doc_event(0),
                {"event": "card", "i": 0, "card": self._card(sha256="1" * 64)},
                {"event": "end", "i": 0, "cards": 1},
                {"event": "batch", "docs": 1, "cards": 1, "withheld": 0},
            ),
            "bad-end-count": self._stream(
                self._doc_event(0),
                {"event": "end", "i": 0, "cards": True},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "extra-end-key": self._stream(
                self._doc_event(0),
                {"event": "end", "i": 0, "cards": 0, "raw": "secret"},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            ),
            "bad-batch-docs": self._stream(
                self._doc_event(0),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": True, "cards": 0, "withheld": 0},
            ),
            "extra-batch-key": self._stream(
                self._doc_event(0),
                {"event": "end", "i": 0, "cards": 0},
                {"event": "batch", "docs": 1, "cards": 0, "withheld": 0, "raw": "secret"},
            ),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(engine_module.EngineError):
                    self._parse(raw, 1)

    def test_parse_stream_rejects_more_cards_than_policy_cap(self):
        raw = self._stream(
            self._doc_event(0),
            {"event": "card", "i": 0, "card": self._card(id="src-001")},
            {"event": "card", "i": 0, "card": self._card(id="src-002")},
            {"event": "end", "i": 0, "cards": 2},
            {"event": "batch", "docs": 1, "cards": 2, "withheld": 0},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(raw, 1, max_cards=1)

    def test_parse_stream_rejects_end_count_mismatch(self):
        raw = self._stream(
            self._doc_event(0),
            {"event": "end", "i": 0, "cards": 3},
            {"event": "batch", "docs": 1, "cards": 3, "withheld": 0},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(raw, 1)

    def test_parse_stream_rejects_missing_or_mismatched_batch_summary(self):
        no_batch = self._stream(
            self._doc_event(0),
            {"event": "end", "i": 0, "cards": 0},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(no_batch, 1)
        bad_tally = self._stream(
            self._doc_event(0),
            {"event": "end", "i": 0, "cards": 0},
            {"event": "batch", "docs": 1, "cards": 5, "withheld": 0},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(bad_tally, 1)

    def test_parse_stream_rejects_trailing_or_unterminated_output(self):
        trailing = self._stream(
            self._doc_event(0),
            {"event": "end", "i": 0, "cards": 0},
            {"event": "batch", "docs": 1, "cards": 0, "withheld": 0},
            {"event": "doc", "i": 9},
        )
        with self.assertRaises(engine_module.EngineError):
            self._parse(trailing, 1)
        with self.assertRaises(engine_module.EngineError):
            self._parse(b'{"event":"batch"}', 0)


class ChapelEngineBinaryDependentTests(unittest.TestCase):
    """Only runs meaningfully when a real ptoon binary build artifact is
    discoverable on this host; skips cleanly otherwise (no chpl toolchain
    on darwin, per AGENTS.md remote-only doctrine)."""

    def setUp(self) -> None:
        self.engine = engine_module.ChapelEngine()
        if not self.engine.available():
            self.skipTest("ptoon binary not built on this host (degraded mode; remote-only per AGENTS.md)")

    def test_normalize_matches_python_reference(self):
        sample = "café​ MUST\r\nnever\r"
        self.assertEqual(self.engine.normalize_text(sample), python_normalize_text(sample))

    def test_engine_caps_reports_a_dict(self):
        caps = self.engine.engine_caps()
        self.assertIsInstance(caps, dict)

    def test_redact_returns_text_and_findings_list(self):
        redacted, findings = self.engine.redact_text("token=ghp_abcdefghijklmnopqrstuvwxyz")
        self.assertIsInstance(redacted, str)
        self.assertIsInstance(findings, list)

    def test_redact_batch_matches_per_document_redact(self):
        # The coforall fan-in must be byte-identical to redacting each document
        # on its own, in input order. Includes a PEM that spans newlines (the
        # length-prefix framing must carry it) and the F1 injected-literal case.
        docs = [
            "token=ghp_abcdefghijklmnopqrstuvwxyz",
            "no secrets here, just prose.",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAA\n-----END RSA PRIVATE KEY-----",
            "token=a@b.co",
        ]
        results = self.engine.redact_batch(docs)
        self.assertEqual([r["i"] for r in results], list(range(len(docs))))
        for i, doc in enumerate(docs):
            exp_redacted, exp_findings = self.engine.redact_text(doc)
            self.assertFalse(results[i].get("withheld", False))
            self.assertEqual(results[i]["findings"], exp_findings)
            self.assertEqual(results[i]["redacted"], exp_redacted)

    def test_redact_batch_empty_input(self):
        self.assertEqual(self.engine.redact_batch([]), [])

    def test_redact_batch_input_cap_withholds_oversized_doc(self):
        # A doc over max_input_bytes is withheld fail-closed (reason input-cap),
        # with NO redacted text; a doc under the cap still passes normally.
        small = "token=ghp_abcdefghijklmnopqrstuvwxyz"
        big = "x" * 5000
        results = self.engine.redact_batch([small, big], max_input_bytes=1000)
        self.assertFalse(results[0].get("withheld", False))
        self.assertIn("redacted", results[0])
        self.assertTrue(results[1]["withheld"])
        self.assertEqual(results[1]["reason"], "input-cap")
        self.assertNotIn("redacted", results[1])

    def test_redact_batch_generous_budget_withholds_nothing(self):
        # A generous budget must not perturb results: identical to no budget.
        docs = ["token=ghp_abcdefghijklmnopqrstuvwxyz", "plain text"]
        baseline = self.engine.redact_batch(docs)
        budgeted = self.engine.redact_batch(docs, max_input_bytes=10000, budget_ms=60000)
        self.assertEqual(budgeted, baseline)

    def test_condense_batch_matches_python_oracle_cards(self):
        # C2d: sha256 of the RAW bytes, findings, and every card object must
        # equal the Python oracle's cards_from_text for the same input.
        import hashlib

        from prompt_toon.cli import cards_from_text, redact_text as py_redact

        body = (
            "# Findings\n"
            "- deploy MUST be approved by the owner\n"
            "- see https://example.com/spec for details\n"
            "token=ghp_abcdefghijklmnopqrstuvwxyz\n"
            "plain closing line\n"
        )
        results = self.engine.condense_batch(
            [{"source": "note.md", "trust_tier": "repo_source", "body": body}]
        )
        self.assertEqual(len(results), 1)
        got = results[0]
        raw = body.encode("utf-8")
        self.assertFalse(got["withheld"])
        self.assertEqual(got["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(got["bytes"], len(raw))
        self.assertEqual(got["findings"], py_redact(body)[1])
        expected = cards_from_text(
            "note.md", body, got["sha256"], "repo_source", 24, resolve_engine("python")
        )
        self.assertEqual(got["cards"], [card.as_dict() for card in expected])

    def test_condense_batch_input_cap_withholds_without_cards(self):
        # Fail-closed: an over-cap doc emits provenance (sha256/bytes) but
        # nothing derived from the body -- no cards key at all.
        small = {"source": "a", "trust_tier": "t", "body": "- fine MUST line"}
        big = {"source": "b", "trust_tier": "t", "body": "x" * 5000}
        results = self.engine.condense_batch([small, big], max_input_bytes=1000)
        self.assertFalse(results[0]["withheld"])
        self.assertIn("cards", results[0])
        self.assertTrue(results[1]["withheld"])
        self.assertEqual(results[1]["reason"], "input-cap")
        self.assertNotIn("cards", results[1])
        self.assertEqual(results[1]["findings"], [])

    def test_condense_batch_generous_budget_matches_baseline(self):
        docs = [
            {"source": "a", "trust_tier": "t", "body": "- deploy MUST be approved"},
            {"source": "b", "trust_tier": "t", "body": "plain text"},
        ]
        baseline = self.engine.condense_batch(docs)
        budgeted = self.engine.condense_batch(
            docs, max_input_bytes=10000, budget_ms=60000
        )
        self.assertEqual(budgeted, baseline)


if __name__ == "__main__":
    unittest.main()
