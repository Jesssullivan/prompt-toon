from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prompt_toon.cli import main
from prompt_toon.corpus import CorpusLedgerError, build_corpus_report
from prompt_toon.dogfood import (
    COMPACT_HANDOFF_FORMAT,
    DOGFOOD_LEDGER_SCHEMA_VERSION,
    HANDOFF_RECALL_CLAIM_BOUNDARY,
    HANDOFF_RECALL_METHOD,
    LEGACY_DOGFOOD_LEDGER_SCHEMA_VERSION,
    LEXICAL_ESTIMATOR_ID,
    LEXICAL_ESTIMATOR_PATTERN,
    build_corpus_identity,
)


def _ledger(
    index: int,
    *,
    engine: str = "python",
    shape: str = "python-sequential-oracle",
    budget: str = "bounded-input-only-python-oracle",
) -> dict:
    digest = f"{index:064x}"
    input_bytes = 1000 + index
    input_tokens = 100 + index
    summary_tokens = 60 + index
    authoritative_tokens = 140 + index
    summary_bytes = summary_tokens * 2
    authoritative_bytes = authoritative_tokens * 2
    cards_tokens = authoritative_tokens - summary_tokens
    cards_bytes = authoritative_bytes - summary_bytes
    toon_selected = index % 2 == 0
    toon_tokens = 50 if toon_selected else 70
    toon_bytes = 100
    corpus_identity = build_corpus_identity(
        [
            {
                "bytes": input_bytes,
                "sha256": digest,
                "trust_tier": "repo_source",
            }
        ]
    )
    return {
        "schema_version": LEGACY_DOGFOOD_LEDGER_SCHEMA_VERSION,
        "run_id": f"run-{index}",
        "generated_at": "IGNORED",
        "corpus_identity": corpus_identity,
        "claim_boundary": {
            "provider_requests": 0,
            "provider_token_counts": {"exact": False, "status": "not_measured"},
        },
        "estimator": {
            "exact": False,
            "id": LEXICAL_ESTIMATOR_ID,
            "pattern": LEXICAL_ESTIMATOR_PATTERN,
            "unit": "lexical pieces",
        },
        "execution": {
            "budget_enforcement": budget,
            "engine_resolved": engine,
            "engine_wall_ms": index,
            "run_wall_ms": index + 1,
            "shape": shape,
        },
        "spool": {
            "documents": 1,
            "emitted_cards": index,
            "input_bytes": input_bytes,
            "input_tokens_estimate": input_tokens,
            "limits": {
                "aggregate_input_bytes": 16 * 1024 * 1024,
                "cards_per_document": 24,
                "chapel_wall_clock_budget_ms": 2000,
                "documents": 64,
                "input_bytes_per_document": 2_000_000,
            },
            "raw_inputs_copied": False,
            "withheld": [],
            "withheld_documents": 0,
        },
        "artifacts": {
            "summary.md": {
                "bytes": summary_bytes,
                "sha256": f"{index + 2000:064x}",
                "tokens_estimate": summary_tokens,
            },
            "source-cards.jsonl": {
                "bytes": cards_bytes,
                "records": index,
                "sha256": f"{index + 1000:064x}",
                "tokens_estimate": cards_tokens,
            },
            **(
                {
                    "source-cards.toon": {
                        "bytes": toon_bytes,
                        "sha256": f"{index + 3000:064x}",
                        "tokens_estimate": toon_tokens,
                    }
                }
                if toon_selected
                else {}
            ),
        },
        "handoffs": {
            "summary_only": {
                "byte_savings_vs_raw_input": round(
                    (input_bytes - summary_bytes) / input_bytes, 4
                ),
                "bytes": summary_bytes,
                "token_estimate_savings_vs_raw_input": round(
                    (input_tokens - summary_tokens) / input_tokens, 4
                ),
                "tokens_estimate": summary_tokens,
            },
            "summary_plus_authoritative_cards": {
                "byte_savings_vs_raw_input": round(
                    (input_bytes - authoritative_bytes) / input_bytes, 4
                ),
                "bytes": authoritative_bytes,
                "token_estimate_savings_vs_raw_input": round(
                    (input_tokens - authoritative_tokens) / input_tokens, 4
                ),
                "tokens_estimate": authoritative_tokens,
            },
            **(
                {
                    "summary_plus_toon_compact_view": {
                        "byte_savings_vs_raw_input": round(
                            (input_bytes - (summary_bytes + toon_bytes))
                            / input_bytes,
                            4,
                        ),
                        "bytes": summary_bytes + toon_bytes,
                        "token_estimate_savings_vs_raw_input": round(
                            (input_tokens - (summary_tokens + toon_tokens))
                            / input_tokens,
                            4,
                        ),
                        "tokens_estimate": summary_tokens + toon_tokens,
                    }
                }
                if toon_selected
                else {}
            ),
        },
        "handoff_decision": {
            "best_measured_handoff": "summary_only",
            "eligible_handoffs": ["summary_only"],
            "gate": "pass",
            "minimum_token_estimate_savings": 0.2,
            "recommended_handoff": "summary_only",
        },
        "toon": {
            **(
                {
                    "byte_savings_vs_jsonl": round(
                        (cards_bytes - toon_bytes) / cards_bytes, 4
                    )
                }
                if toon_selected
                else {}
            ),
            "eligible": toon_selected,
            "minimum_token_estimate_savings": 0.2,
            "selected": toon_selected,
            "token_estimate_savings_vs_jsonl": round(
                (cards_tokens - toon_tokens) / cards_tokens, 4
            ),
        },
    }


class CorpusReportTests(unittest.TestCase):
    def _write_ledgers(self, root: Path, ledgers: list[dict]) -> list[str]:
        paths = []
        for index, ledger in enumerate(ledgers):
            path = root / f"ledger-{index:02}.json"
            path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
            paths.append(str(path))
        return paths

    def test_report_is_deterministic_cohort_separated_and_nearest_ranked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledgers = [_ledger(index) for index in range(1, 11)]
            ledgers.extend(
                _ledger(
                    index,
                    engine="chapel",
                    shape="chapel-one-shot-coforall-batch",
                    budget="chapel-wall-clock-withholding",
                )
                for index in range(11, 21)
            )
            paths = self._write_ledgers(root, ledgers)

            report = build_corpus_report(paths)
            reverse_report = build_corpus_report(list(reversed(paths)))
            self.assertEqual(report, reverse_report)
            self.assertEqual(report["corpus_gate"]["status"], "pass")
            self.assertEqual(report["corpus_gate"]["unique_spools"], 20)
            self.assertEqual(report["percentile_method"], "nearest-rank")
            self.assertEqual(len(report["cohorts"]), 2)
            by_engine = {
                cohort["key"]["engine_resolved"]: cohort for cohort in report["cohorts"]
            }
            self.assertEqual(
                by_engine["python"]["statistics"]["engine_wall_ms"]["p50"], 5
            )
            self.assertEqual(
                by_engine["python"]["statistics"]["engine_wall_ms"]["p90"], 9
            )
            self.assertEqual(
                by_engine["chapel"]["statistics"]["engine_wall_ms"]["p50"], 15
            )
            self.assertEqual(
                by_engine["chapel"]["statistics"]["engine_wall_ms"]["p90"], 19
            )
            self.assertEqual(
                by_engine["python"]["rates"]["handoff_pass"],
                {"denominator": 10, "numerator": 0, "rate": 0.0},
            )
            self.assertEqual(
                by_engine["python"]["rates"]["legacy_unmeasured"],
                {"denominator": 10, "numerator": 10, "rate": 1.0},
            )
            self.assertEqual(
                by_engine["python"]["economics"]["statistics"][
                    "summary_only_bytes"
                ]["count"],
                10,
            )
            self.assertEqual(
                report["quality_gate"],
                {
                    "promotion": "blocked",
                    "status": "not-evaluated",
                    "required_next": (
                        "reviewed implementation/fixture/corpus binding plus "
                        "separately authorized provider evidence"
                    ),
                },
            )
            self.assertFalse(report["claim_boundary"]["raw_sources_read"])
            self.assertEqual(report["claim_boundary"]["provider_requests"], 0)
            self.assertNotIn(str(root), json.dumps(report, sort_keys=True))

    def test_actual_dogfood_ledger_round_trips_through_reporter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.md"
            source.write_text("- Provenance MUST remain attached.\n", encoding="utf-8")
            run = root / "run"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "dogfood",
                            str(source),
                            "--engine",
                            "python",
                            "--output-dir",
                            str(run),
                        ]
                    ),
                    0,
                )

            report = build_corpus_report([str(run / "efficiency.json")])
            self.assertEqual(report["corpus_gate"]["status"], "insufficient-corpus")
            self.assertEqual(report["cohorts"][0]["runs"], 1)
            self.assertEqual(
                report["cohorts"][0]["percentile_gate"]["status"],
                "insufficient-cohort",
            )

    def test_zero_token_dogfood_ledgers_are_operational_not_economic_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in (("empty", ""), ("whitespace", " \t\n")):
                with self.subTest(name=name):
                    source = root / f"{name}.txt"
                    source.write_text(body, encoding="utf-8")
                    run = root / f"run-{name}"
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(
                            main(
                                [
                                    "dogfood",
                                    str(source),
                                    "--engine",
                                    "python",
                                    "--output-dir",
                                    str(run),
                                ]
                            ),
                            0,
                        )

                    cohort = build_corpus_report(
                        [str(run / "efficiency.json")]
                    )["cohorts"][0]
                    self.assertEqual(
                        cohort["statistics"]["input_tokens_estimate"]["values"],
                        [0],
                    )
                    self.assertEqual(cohort["economics"]["runs"], 0)
                    self.assertEqual(
                        cohort["economics"]["excluded_runs"]["zero_token_baseline"],
                        1,
                    )

    def test_small_corpus_is_diagnostic_but_not_acceptance_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._write_ledgers(Path(tmp), [_ledger(1)])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["corpus-report", *paths]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["corpus_gate"]["status"], "insufficient-corpus")
            self.assertEqual(report["quality_gate"]["status"], "not-evaluated")

    def test_withheld_runs_stay_in_rates_but_not_economics(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledgers = [_ledger(index) for index in range(1, 6)]
            withheld = ledgers[-1]
            withheld["spool"]["emitted_cards"] = 0
            withheld["spool"]["withheld_documents"] = 1
            withheld["spool"]["withheld"] = [
                {"reason": "budget", "source": "withheld.md"}
            ]
            withheld["artifacts"]["source-cards.jsonl"]["records"] = 0
            withheld["artifacts"]["source-cards.jsonl"]["bytes"] = 0
            withheld["artifacts"]["source-cards.jsonl"]["tokens_estimate"] = 0
            summary = withheld["handoffs"]["summary_only"]
            withheld["handoffs"]["summary_plus_authoritative_cards"] = {
                **summary,
                "bytes": summary["bytes"],
                "tokens_estimate": summary["tokens_estimate"],
            }
            withheld["handoff_decision"]["best_measured_handoff"] = None
            withheld["handoff_decision"]["eligible_handoffs"] = []
            withheld["handoff_decision"]["gate"] = "withheld"
            withheld["handoff_decision"]["recommended_handoff"] = None
            withheld["toon"]["eligible"] = False
            withheld["toon"]["selected"] = False
            withheld["toon"]["token_estimate_savings_vs_jsonl"] = None
            paths = self._write_ledgers(Path(tmp), ledgers)

            cohort = build_corpus_report(paths)["cohorts"][0]
            self.assertEqual(
                cohort["rates"]["withheld"],
                {"denominator": 5, "numerator": 1, "rate": 0.2},
            )
            self.assertEqual(cohort["economics"]["runs"], 4)
            self.assertEqual(cohort["economics"]["excluded_runs"]["withheld"], 1)
            self.assertEqual(
                cohort["economics"]["percentile_gate"]["status"],
                "insufficient-cohort",
            )

    def test_duplicate_corpus_identity_is_rejected_even_if_run_id_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = _ledger(1)
            second = json.loads(json.dumps(first))
            second["run_id"] = "replayed-under-a-new-id"
            paths = self._write_ledgers(Path(tmp), [first, second])
            with self.assertRaisesRegex(CorpusLedgerError, "duplicate corpus diversity"):
                build_corpus_report(paths)

    def test_same_spool_is_allowed_once_per_execution_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = _ledger(1)
            second = json.loads(json.dumps(first))
            second["run_id"] = "same-spool-chapel"
            second["execution"].update(
                {
                    "budget_enforcement": "chapel-wall-clock-withholding",
                    "engine_resolved": "chapel",
                    "shape": "chapel-one-shot-coforall-batch",
                }
            )
            paths = self._write_ledgers(Path(tmp), [first, second])
            report = build_corpus_report(paths)
            self.assertEqual(report["corpus_gate"]["ledgers"], 2)
            self.assertEqual(report["corpus_gate"]["unique_spools"], 1)
            self.assertEqual(len(report["cohorts"]), 2)

    def test_compact_handoff_is_isolated_from_legacy_summary_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = _ledger(1)
            compact = json.loads(json.dumps(legacy))
            compact["run_id"] = "same-spool-compact"
            compact["execution"]["handoff_format"] = COMPACT_HANDOFF_FORMAT
            report = build_corpus_report(
                self._write_ledgers(Path(tmp), [legacy, compact])
            )
            self.assertEqual(len(report["cohorts"]), 2)
            self.assertEqual(
                {cohort["key"]["handoff_format"] for cohort in report["cohorts"]},
                {"legacy-duplicated-summary-v1", COMPACT_HANDOFF_FORMAT},
            )

            compact["execution"]["handoff_format"] = "unknown"
            path = self._write_ledgers(Path(tmp), [compact])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "handoff_format"):
                build_corpus_report([path])

            compact["execution"]["handoff_format"] = []
            path = self._write_ledgers(Path(tmp), [compact])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "handoff_format"):
                build_corpus_report([path])

    def test_measured_recall_is_validated_and_isolated_from_legacy_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = _ledger(1)
            measured = json.loads(json.dumps(legacy))
            measured["run_id"] = "measured-recall"
            measured["schema_version"] = DOGFOOD_LEDGER_SCHEMA_VERSION
            measured["handoff_recall"] = {
                "claim_boundary": HANDOFF_RECALL_CLAIM_BOUNDARY,
                "handoffs": {
                    "summary_only": {
                        "retained": {
                            "critical_constraints": 0,
                            "open_questions": 0,
                        },
                        "retained_total": 0,
                        "status": "recall-loss",
                    },
                    "summary_plus_authoritative_cards": {
                        "retained": {
                            "critical_constraints": 1,
                            "open_questions": 0,
                        },
                        "retained_total": 1,
                        "status": "pass",
                    },
                },
                "method": HANDOFF_RECALL_METHOD,
                "recognized": {
                    "critical_constraints": 1,
                    "open_questions": 0,
                    "total": 1,
                },
                "source_text_recorded": False,
            }
            measured["handoff_decision"].update(
                {
                    "eligible_handoffs": [],
                    "gate": "recall-loss",
                    "recommended_handoff": None,
                    "savings_eligible_handoffs": ["summary_only"],
                }
            )
            paths = self._write_ledgers(root, [legacy, measured])

            report = build_corpus_report(paths)
            self.assertEqual(report["corpus_gate"]["unique_spools"], 1)
            self.assertEqual(len(report["cohorts"]), 2)
            self.assertEqual(
                report["handoff_recall_gate"],
                {
                    "legacy_unmeasured_ledgers": 1,
                    "measured_ledgers": 1,
                    "recall_loss_ledgers": 1,
                    "scope": (
                        "recognized critical-constraint and open-question line "
                        "recall; not semantic-equivalence or task-quality proof"
                    ),
                },
            )
            self.assertEqual(
                {cohort["key"]["handoff_recall"] for cohort in report["cohorts"]},
                {HANDOFF_RECALL_METHOD, "legacy-unmeasured-v2"},
            )

            missing_recall = json.loads(json.dumps(legacy))
            missing_recall["schema_version"] = DOGFOOD_LEDGER_SCHEMA_VERSION
            missing_path = self._write_ledgers(root, [missing_recall])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "requires handoff_recall"):
                build_corpus_report([missing_path])

            hybrid = json.loads(json.dumps(measured))
            hybrid["schema_version"] = LEGACY_DOGFOOD_LEDGER_SCHEMA_VERSION
            hybrid_path = self._write_ledgers(root, [hybrid])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "legacy-unmeasured"):
                build_corpus_report([hybrid_path])

            null_hybrid = json.loads(json.dumps(legacy))
            null_hybrid["handoff_recall"] = None
            null_hybrid_path = self._write_ledgers(root, [null_hybrid])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "legacy-unmeasured"):
                build_corpus_report([null_hybrid_path])

            extra_field = json.loads(json.dumps(measured))
            extra_field["handoff_recall"]["source_text"] = "must not survive"
            extra_field_path = self._write_ledgers(root, [extra_field])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "unexpected keys"):
                build_corpus_report([extra_field_path])

            changed_boundary = json.loads(json.dumps(measured))
            changed_boundary["handoff_recall"]["claim_boundary"] = (
                "semantic equivalence proven"
            )
            changed_boundary_path = self._write_ledgers(root, [changed_boundary])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "claim_boundary"):
                build_corpus_report([changed_boundary_path])

            measured["handoff_recall"]["handoffs"][
                "summary_plus_authoritative_cards"
            ]["retained"]["critical_constraints"] = 2
            tampered_path = self._write_ledgers(root, [measured])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "exceeds recognized"):
                build_corpus_report([tampered_path])

    def test_document_permutations_do_not_inflate_unique_spools(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = _ledger(1)
            fingerprints = [
                {
                    "bytes": 400,
                    "sha256": f"{9001:064x}",
                    "trust_tier": "repo_source",
                },
                {
                    "bytes": 601,
                    "sha256": f"{9002:064x}",
                    "trust_tier": "untrusted_tool_output",
                },
            ]
            first["spool"]["documents"] = 2
            first["corpus_identity"] = build_corpus_identity(fingerprints)
            second = json.loads(json.dumps(first))
            second["run_id"] = "permuted"
            second["corpus_identity"] = build_corpus_identity(
                list(reversed(fingerprints))
            )
            paths = self._write_ledgers(Path(tmp), [first, second])
            with self.assertRaisesRegex(CorpusLedgerError, "duplicate corpus diversity"):
                build_corpus_report(paths)

    def test_mixed_estimators_and_resident_shapes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _ledger(1)
            second = _ledger(2)
            second["estimator"]["pattern"] = "drifted"
            paths = self._write_ledgers(root, [first, second])
            with self.assertRaisesRegex(CorpusLedgerError, "mixed estimator"):
                build_corpus_report(paths)

            resident = _ledger(3, shape="resident-64-stream")
            path = self._write_ledgers(root, [resident])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "resident evidence"):
                build_corpus_report([path])

            mismatch = _ledger(
                4,
                engine="chapel",
                shape="python-sequential-oracle",
                budget="bounded-input-only-python-oracle",
            )
            mismatch_path = self._write_ledgers(root, [mismatch])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "supported one-shot"):
                build_corpus_report([mismatch_path])

    def test_identity_tampering_old_schema_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tampered = _ledger(1)
            tampered["corpus_identity"]["ordered_key"]["sha256"] = "0" * 64
            path = self._write_ledgers(root, [tampered])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "does not match"):
                build_corpus_report([path])

            old = _ledger(2)
            old["schema_version"] = 1
            old_path = self._write_ledgers(root, [old])[0]
            with self.assertRaisesRegex(
                CorpusLedgerError, "regenerate older"
            ) as raised:
                build_corpus_report([old_path])
            self.assertIn(old_path, str(raised.exception))

            link = root / "linked.json"
            link.symlink_to(old_path)
            with self.assertRaisesRegex(CorpusLedgerError, "must not be a symlink"):
                build_corpus_report([str(link)])

    def test_provider_and_auto_format_claims_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = _ledger(1)
            provider["claim_boundary"]["provider_token_counts"]["exact"] = True
            path = self._write_ledgers(root, [provider])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "exact must remain false"):
                build_corpus_report([path])

            boolean_count = _ledger(2)
            boolean_count["claim_boundary"]["provider_requests"] = False
            path = self._write_ledgers(root, [boolean_count])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "must be an integer"):
                build_corpus_report([path])

            format_drift = _ledger(3)
            format_drift["toon"]["selected"] = True
            format_drift["toon"]["eligible"] = False
            path = self._write_ledgers(root, [format_drift])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "selected must equal"):
                build_corpus_report([path])

            impossible_savings = _ledger(5)
            impossible_savings["toon"]["token_estimate_savings_vs_jsonl"] = 5.0
            path = self._write_ledgers(root, [impossible_savings])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "outside its accepted range"):
                build_corpus_report([path])

            unselected_drift = _ledger(7)
            unselected_drift["toon"]["token_estimate_savings_vs_jsonl"] = 0.5
            path = self._write_ledgers(root, [unselected_drift])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "unselected TOON"):
                build_corpus_report([path])

            threshold_drift = _ledger(6)
            threshold_drift["toon"]["minimum_token_estimate_savings"] = 0.9
            path = self._write_ledgers(root, [threshold_drift])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "exact savings threshold"):
                build_corpus_report([path])

            withheld_drift = _ledger(8)
            withheld_drift["spool"]["withheld_documents"] = 1
            path = self._write_ledgers(root, [withheld_drift])[0]
            with self.assertRaisesRegex(CorpusLedgerError, "one entry per withheld"):
                build_corpus_report([path])

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text('{"schema_version":2,"schema_version":2}\n')
            with self.assertRaisesRegex(CorpusLedgerError, "duplicate JSON key"):
                build_corpus_report([str(path)])

            path.write_text('{"schema_version":2,"unused":NaN}\n')
            with self.assertRaisesRegex(CorpusLedgerError, "non-finite JSON number"):
                build_corpus_report([str(path)])

    def test_pathological_json_parse_failures_are_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pathological.json"
            path.write_text('{"schema_version":' + ("9" * 5000) + "}\n")
            with self.assertRaises(CorpusLedgerError):
                build_corpus_report([str(path)])

            path.write_text(("[" * 2000) + "0" + ("]" * 2000))
            with self.assertRaises(CorpusLedgerError):
                build_corpus_report([str(path)])

    def test_more_than_fifty_input_paths_are_rejected_before_reading(self):
        with self.assertRaisesRegex(CorpusLedgerError, "at most 50"):
            build_corpus_report([f"missing-{index}.json" for index in range(51)])


if __name__ == "__main__":
    unittest.main()
