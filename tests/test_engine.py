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

    def test_missing_binary_path_raises_engine_error_not_os_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "does-not-exist"
            engine = engine_module.ChapelEngine(binary_path=binary)
            with self.assertRaises(engine_module.EngineError):
                engine.normalize_text("hello")


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


if __name__ == "__main__":
    unittest.main()
