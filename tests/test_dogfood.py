from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from prompt_toon.cli import main, rough_token_count
from prompt_toon.dogfood import (
    MAX_DOGFOOD_BUDGET_MS,
    MAX_DOGFOOD_DOCUMENTS,
    MAX_DOGFOOD_INPUT_BYTES,
    LEXICAL_ESTIMATOR_ID,
    LEXICAL_ESTIMATOR_PATTERN,
    build_efficiency_ledger,
    expand_spool_inputs,
)
from prompt_toon.engine import ChapelEngine


class DogfoodTests(unittest.TestCase):
    def test_published_estimator_pattern_matches_the_counter(self):
        samples = [
            "plain words and_underscores",
            "punctuation: [x] / y?",
            "Unicode cafe\N{COMBINING ACUTE ACCENT} and \N{SNOWMAN}",
            "tabs\tnewlines\nand\rcontrols",
            "",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    rough_token_count(sample),
                    len(re.findall(LEXICAL_ESTIMATOR_PATTERN, sample)),
                )

    def test_offline_spool_writes_claim_bounded_efficiency_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            nested = spool / "nested"
            nested.mkdir(parents=True)
            first = spool / "a.md"
            second = nested / "b.md"
            first.write_text(
                "- Deployment MUST preserve provenance.\n"
                "- token=ghp_abcdefghijklmnopqrstuvwxyz must remain private.\n",
                encoding="utf-8",
            )
            second.write_text(
                "- Open question: which source should the operator reopen?\n",
                encoding="utf-8",
            )
            out_dir = root / "run"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "dogfood",
                        str(spool),
                        str(first),
                        "--id",
                        "dogfood-test",
                        "--output-dir",
                        str(out_dir),
                        "--engine",
                        "python",
                        "--input-tier",
                        f"{spool}=repo_source",
                        "--mythos-route",
                        "mythos.synthesis",
                        "--model-label",
                        "gpt-5.6-sol",
                    ]
                )

            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["provider_requests"], 0)
            self.assertEqual(result["engine"], "python")
            self.assertIn(
                result["handoff_gate"], {"pass", "below-threshold", "withheld"}
            )

            ledger = json.loads((out_dir / "efficiency.json").read_text())
            manifest = json.loads((out_dir / "manifest.json").read_text())
            self.assertEqual(ledger["schema_version"], 1)
            self.assertEqual(ledger["claim_boundary"]["provider_requests"], 0)
            self.assertFalse(ledger["claim_boundary"]["provider_token_counts"]["exact"])
            self.assertEqual(ledger["estimator"]["id"], LEXICAL_ESTIMATOR_ID)
            self.assertEqual(ledger["spool"]["documents"], 2)
            self.assertFalse(ledger["spool"]["raw_inputs_copied"])
            self.assertEqual(ledger["execution"]["engine_requested"], "python")
            self.assertEqual(ledger["execution"]["engine_resolved"], "python")
            self.assertEqual(ledger["execution"]["shape"], "python-sequential-oracle")
            self.assertGreaterEqual(ledger["execution"]["run_wall_ms"], 0)
            self.assertGreaterEqual(
                ledger["execution"]["run_wall_ms"],
                ledger["execution"]["engine_wall_ms"],
            )
            self.assertEqual(
                ledger["execution"]["postprocessing"],
                "python-ledger-and-optional-toon-view",
            )
            self.assertEqual(ledger["attribution"]["mythos_route"], "mythos.synthesis")
            self.assertEqual(ledger["attribution"]["model_label"], "gpt-5.6-sol")
            self.assertIn("not delegation-policy", ledger["attribution"]["binding"])
            self.assertEqual(
                manifest["format_analysis"]["token_estimator"], LEXICAL_ESTIMATOR_ID
            )
            self.assertEqual(manifest["outputs"]["efficiency"], "efficiency.json")
            self.assertEqual(
                [item["source"] for item in manifest["inputs"]],
                [str(first), str(second)],
            )
            self.assertTrue(
                all(item["trust_tier"] == "repo_source" for item in manifest["inputs"])
            )
            self.assertNotIn("ghp_", (out_dir / "source-cards.jsonl").read_text())
            self.assertFalse((out_dir / "inputs").exists())

            summary_bytes = (out_dir / "summary.md").read_bytes()
            self.assertEqual(
                ledger["artifacts"]["summary.md"]["sha256"],
                hashlib.sha256(summary_bytes).hexdigest(),
            )
            self.assertEqual(
                ledger["toon"]["selected"], (out_dir / "source-cards.toon").is_file()
            )
            self.assertIn("summary_only", ledger["handoffs"])
            self.assertIn("summary_plus_authoritative_cards", ledger["handoffs"])
            decision = ledger["handoff_decision"]
            self.assertEqual(decision["minimum_token_estimate_savings"], 0.2)
            self.assertIn(decision["gate"], {"pass", "below-threshold", "withheld"})
            if decision["gate"] == "below-threshold":
                self.assertIsNone(decision["recommended_handoff"])

    def test_chapel_engine_uses_full_batch_condense_surface(self):
        class FakeChapel:
            def __init__(self) -> None:
                self.docs = None
                self.kwargs = None

            def condense_run(self, docs, run_id, generated_at, **kwargs):
                self.docs = docs
                self.kwargs = kwargs
                card = {
                    "id": "card-0",
                    "source": docs[0]["source"],
                    "trust_tier": docs[0]["trust_tier"],
                    "sha256": hashlib.sha256(docs[0]["body"].encode()).hexdigest(),
                    "line_start": 1,
                    "line_end": 1,
                    "claim": "Provenance MUST remain attached.",
                    "evidence": "Provenance MUST remain attached.",
                    "confidence": "high",
                    "flags": [],
                }
                manifest = {
                    "id": run_id,
                    "generated_at": generated_at,
                    "inputs": [],
                    "mixed_trust_tiers": False,
                    "settings": {
                        "format": "jsonl",
                        "max_cards_per_input": kwargs["max_cards"],
                        "min_toon_savings": json.loads(kwargs["min_toon_savings"]),
                        "trust_tier": kwargs["default_trust_tier"],
                        "input_tier_overrides": kwargs["tier_overrides"],
                        "store_raw": False,
                    },
                    "outputs": {
                        "summary": "summary.md",
                        "source_cards_jsonl": "source-cards.jsonl",
                        "primary_source_cards": "source-cards.jsonl",
                        "manifest": "manifest.json",
                    },
                }
                return ([{"withheld": False, "cards": [card]}], "# summary\n", manifest)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.md"
            source.write_text("Provenance MUST remain attached.\n", encoding="utf-8")
            backend = FakeChapel()
            engine = SimpleNamespace(name="chapel", backend=backend)
            with mock.patch("prompt_toon.cli.resolve_engine", return_value=engine):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "dogfood",
                                str(source),
                                "--id",
                                "chapel-test",
                                "--output-dir",
                                str(root / "out"),
                                "--engine",
                                "chapel",
                            ]
                        ),
                        0,
                    )
            self.assertEqual(len(backend.docs), 1)
            self.assertEqual(backend.kwargs["max_input_bytes"], MAX_DOGFOOD_INPUT_BYTES)
            self.assertEqual(backend.kwargs["budget_ms"], MAX_DOGFOOD_BUDGET_MS)
            ledger = json.loads((root / "out" / "efficiency.json").read_text())
            self.assertEqual(
                ledger["execution"]["shape"], "chapel-one-shot-coforall-batch"
            )

    def test_withholding_never_passes_the_handoff_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            (output / "summary.md").write_text("# withheld\n", encoding="utf-8")
            (output / "source-cards.jsonl").write_text("", encoding="utf-8")
            (output / "manifest.json").write_text("{}\n", encoding="utf-8")

            ledger = build_efficiency_ledger(
                run_id="withheld",
                generated_at="GENERATED_AT",
                output_dir=output,
                documents=1,
                max_cards_per_document=24,
                input_bytes=1000,
                input_tokens_estimate=1000,
                withheld_documents=1,
                withheld_details=[{"source": "large.md", "reason": "budget"}],
                engine_requested="chapel",
                engine_resolved="chapel",
                execution_shape="chapel-one-shot-coforall-batch",
                engine_wall_ms=1,
                run_wall_ms=2,
                min_toon_savings=0.2,
                min_handoff_savings=0.2,
                format_analysis={"jsonl_tokens": 0, "format": "jsonl"},
                mythos_route=None,
                model_label=None,
                token_counter=rough_token_count,
            )
            self.assertEqual(ledger["handoff_decision"]["gate"], "withheld")
            self.assertEqual(ledger["handoff_decision"]["eligible_handoffs"], [])
            self.assertIsNone(ledger["handoff_decision"]["recommended_handoff"])
            self.assertEqual(
                ledger["spool"]["withheld"],
                [{"source": "large.md", "reason": "budget"}],
            )

    def test_handoff_gate_uses_unrounded_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "summary.md").write_text("x" * 80004, encoding="utf-8")
            (output / "source-cards.jsonl").write_text("", encoding="utf-8")
            (output / "manifest.json").write_text("{}\n", encoding="utf-8")

            ledger = build_efficiency_ledger(
                run_id="rounding",
                generated_at="GENERATED_AT",
                output_dir=output,
                documents=1,
                max_cards_per_document=24,
                input_bytes=100000,
                input_tokens_estimate=100000,
                withheld_documents=0,
                withheld_details=[],
                engine_requested="python",
                engine_resolved="python",
                execution_shape="python-sequential-oracle",
                engine_wall_ms=1,
                run_wall_ms=2,
                min_toon_savings=0.2,
                min_handoff_savings=0.2,
                format_analysis={"jsonl_tokens": 0, "format": "jsonl"},
                mythos_route=None,
                model_label=None,
                token_counter=len,
            )
            self.assertEqual(
                ledger["handoffs"]["summary_only"][
                    "token_estimate_savings_vs_raw_input"
                ],
                0.2,
            )
            self.assertEqual(ledger["handoff_decision"]["gate"], "below-threshold")
            self.assertEqual(ledger["handoff_decision"]["eligible_handoffs"], [])

    @unittest.skipUnless(
        ChapelEngine().available(), "ptoon binary unavailable for dogfood integration"
    )
    def test_real_chapel_dogfood_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.md"
            second = root / "b.md"
            first.write_text("- Provenance MUST remain attached.\n", encoding="utf-8")
            second.write_text("- Open question: owner?\n", encoding="utf-8")
            out_dir = root / "out"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "dogfood",
                            str(first),
                            str(second),
                            "--id",
                            "real-chapel-dogfood",
                            "--output-dir",
                            str(out_dir),
                            "--engine",
                            "chapel",
                        ]
                    ),
                    0,
                )
            ledger = json.loads((out_dir / "efficiency.json").read_text())
            self.assertEqual(ledger["execution"]["engine_resolved"], "chapel")
            self.assertEqual(
                ledger["execution"]["shape"], "chapel-one-shot-coforall-batch"
            )
            self.assertEqual(ledger["spool"]["documents"], 2)
            self.assertEqual(ledger["claim_boundary"]["provider_requests"], 0)

    def test_metadata_cannot_inject_summary_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forged_path = root / "safe\n- forged.md"
            forged_path.write_text("content\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "line separators"):
                main(["dogfood", str(forged_path), "--engine", "python"])

            safe_path = root / "safe.md"
            safe_path.write_text("content\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "visible ASCII label"):
                main(
                    [
                        "dogfood",
                        str(safe_path),
                        "--engine",
                        "python",
                        "--trust-tier",
                        "repo_source]\n- forged",
                    ]
                )

    def test_aggregate_input_limit_is_enforced_before_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.md"
            second = root / "b.md"
            first.write_text("123456", encoding="utf-8")
            second.write_text("abcdef", encoding="utf-8")
            with mock.patch("prompt_toon.cli.MAX_DOGFOOD_REQUEST_BYTES", 10):
                with self.assertRaisesRegex(SystemExit, "aggregate input limit"):
                    main(
                        [
                            "dogfood",
                            str(first),
                            str(second),
                            "--engine",
                            "python",
                            "--output-dir",
                            str(root / "out"),
                        ]
                    )
            self.assertFalse((root / "out").exists())

    def test_failed_transform_cleans_staging_and_allows_retry(self):
        class FailingChapel:
            def condense_run(self, *args, **kwargs):
                raise RuntimeError("injected transform failure")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.md"
            source.write_text("content\n", encoding="utf-8")
            out_dir = root / "out"
            engine = SimpleNamespace(name="chapel", backend=FailingChapel())
            with mock.patch("prompt_toon.cli.resolve_engine", return_value=engine):
                with self.assertRaisesRegex(RuntimeError, "injected transform failure"):
                    main(
                        [
                            "dogfood",
                            str(source),
                            "--engine",
                            "chapel",
                            "--output-dir",
                            str(out_dir),
                        ]
                    )
            self.assertFalse(out_dir.exists())
            self.assertEqual(list(root.glob(".prompt-toon-dogfood-*")), [])

    def test_spool_expansion_is_stable_bounded_and_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            spool.mkdir()
            with self.assertRaisesRegex(ValueError, "contains no regular files"):
                expand_spool_inputs([str(spool)])

            files = []
            for index in range(MAX_DOGFOOD_DOCUMENTS + 1):
                path = spool / f"{index:03}.md"
                path.write_text(f"document {index}\n", encoding="utf-8")
                files.append(path)
            with self.assertRaisesRegex(ValueError, "limit is 64"):
                expand_spool_inputs([str(spool)])

            files[-1].unlink()
            expanded = expand_spool_inputs([str(spool), str(files[0])])
            self.assertEqual(expanded, [str(path) for path in files[:-1]])

            link = root / "linked.md"
            link.symlink_to(files[0])
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                expand_spool_inputs([str(link)])

    def test_dogfood_rejects_invalid_utf8_and_nonempty_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "invalid.bin"
            source.write_bytes(b"\xff")
            with self.assertRaisesRegex(SystemExit, "not valid UTF-8"):
                main(["dogfood", str(source), "--engine", "python"])

            source.write_text("valid\n", encoding="utf-8")
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing").write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "already exists"):
                main(
                    [
                        "dogfood",
                        str(source),
                        "--output-dir",
                        str(out_dir),
                        "--engine",
                        "python",
                    ]
                )

    def test_explicit_chapel_fails_closed_and_run_ids_are_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.md"
            source.write_text("bounded\n", encoding="utf-8")

            with mock.patch(
                "prompt_toon.engine.ChapelEngine.available", return_value=False
            ):
                with self.assertRaisesRegex(
                    SystemExit, "ptoon binary is not available"
                ):
                    main(
                        [
                            "dogfood",
                            str(source),
                            "--engine",
                            "chapel",
                            "--output-dir",
                            str(root / "absent"),
                        ]
                    )

            with mock.patch("prompt_toon.cli.fcntl.flock", side_effect=BlockingIOError):
                with self.assertRaisesRegex(SystemExit, "run is already active"):
                    main(
                        [
                            "dogfood",
                            str(source),
                            "--engine",
                            "python",
                            "--output-dir",
                            str(root / "locked"),
                        ]
                    )
            self.assertFalse((root / "locked").exists())

    def test_dogfood_enforces_resident_input_and_label_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "large.md"
            source.write_bytes(b"x" * (MAX_DOGFOOD_INPUT_BYTES + 1))
            with self.assertRaisesRegex(SystemExit, "per-document limit"):
                main(["dogfood", str(source), "--engine", "python"])

            source.write_text("bounded\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "visible ASCII"):
                main(
                    [
                        "dogfood",
                        str(source),
                        "--engine",
                        "python",
                        "--model-label",
                        "line\nbreak",
                    ]
                )
            with self.assertRaisesRegex(SystemExit, "between 1 and 24"):
                main(
                    [
                        "dogfood",
                        str(source),
                        "--engine",
                        "python",
                        "--max-cards",
                        "25",
                    ]
                )
            with self.assertRaisesRegex(SystemExit, "--id must be"):
                main(
                    [
                        "dogfood",
                        str(source),
                        "--engine",
                        "python",
                        "--id",
                        "bad/id",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
