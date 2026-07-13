"""Focused tests for the resident ptoon serve client."""

from __future__ import annotations

import json
import shutil
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from prompt_toon.engine import EngineError
from prompt_toon.resident import (
    BOUNDED_CHAPEL_RUNTIME_ENV,
    DEFAULT_BUDGET_MS,
    DEFAULT_MAX_CARDS,
    DEFAULT_MAX_DOCS,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_LABEL_BYTES,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MAX_STREAMS,
    DEFAULT_QUEUE_DEPTH,
    DEFAULT_WORKERS,
    ResidentEngine,
)


ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = ROOT / "tests" / "fake_ptoon_serve.py"
GENERATED_AT = "2026-07-11T12:00:00Z"
RESULT_TIMEOUT = 30


def doc(body: str = "MUST preserve provenance.\n") -> dict[str, str]:
    return {
        "source": "agent-output.txt",
        "trust_tier": "subagent_return",
        "body": body,
    }


class ResidentEngineTests(unittest.TestCase):
    def make_engine(self, **kwargs: object) -> ResidentEngine:
        engine = ResidentEngine(binary_path=FAKE_SERVER, **kwargs)
        self.addCleanup(engine.close)
        return engine

    def submit(
        self,
        engine: ResidentEngine,
        request_id: str,
        *,
        stream_id: str | None = None,
        docs: list[dict[str, str]] | None = None,
        **kwargs: object,
    ) -> Future:
        return engine.submit_condense_run(
            request_id=request_id,
            stream_id=stream_id or f"stream-{request_id}",
            docs=[doc()] if docs is None else docs,
            run_id=request_id,
            generated_at=GENERATED_AT,
            **kwargs,
        )

    def test_out_of_order_responses_are_demultiplexed(self):
        engine = self.make_engine()
        held = self.submit(engine, "hold-first")
        fast = self.submit(engine, "fast-second")

        fast_results, fast_summary, fast_manifest = fast.result(timeout=RESULT_TIMEOUT)
        self.assertEqual(fast_results[0]["source"], "agent-output.txt")
        self.assertEqual(fast_summary, "summary:fast-second")
        self.assertEqual(fast_manifest["id"], "fast-second")
        self.assertFalse(held.done())

        release = self.submit(engine, "release-held")
        self.assertEqual(release.result(timeout=RESULT_TIMEOUT)[2]["id"], "release-held")
        held_results, held_summary, held_manifest = held.result(timeout=RESULT_TIMEOUT)
        self.assertEqual(held_results[0]["i"], 0)
        self.assertEqual(held_summary, "summary:hold-first")
        self.assertEqual(held_manifest["id"], "hold-first")
        self.assertEqual(engine.pending_count, 0)

    def test_submit_is_safe_from_concurrent_callers(self):
        engine = self.make_engine()
        with ThreadPoolExecutor(max_workers=8) as pool:
            submissions = [
                pool.submit(self.submit, engine, f"thread-{index}")
                for index in range(16)
            ]
            response_futures = [
                submission.result(timeout=RESULT_TIMEOUT) for submission in submissions
            ]
        manifests = [
            future.result(timeout=RESULT_TIMEOUT)[2] for future in response_futures
        ]
        self.assertEqual(
            {manifest["id"] for manifest in manifests},
            {f"thread-{index}" for index in range(16)},
        )
        self.assertEqual(engine.pending_count, 0)

    def test_malformed_frame_fails_every_pending_call_and_engine(self):
        engine = self.make_engine()
        held = self.submit(engine, "hold-before-malformed")
        malformed = self.submit(engine, "malformed-response")

        with self.assertRaises(EngineError):
            malformed.result(timeout=RESULT_TIMEOUT)
        with self.assertRaises(EngineError):
            held.result(timeout=RESULT_TIMEOUT)
        self.assertTrue(engine.closed)
        with self.assertRaises(EngineError):
            self.submit(engine, "after-protocol-failure")

    def test_malformed_ok_body_is_a_connection_failure(self):
        engine = self.make_engine()
        held = self.submit(engine, "hold-before-bad-body")
        bad = self.submit(engine, "bad-body-response")

        with self.assertRaisesRegex(EngineError, "malformed body"):
            bad.result(timeout=RESULT_TIMEOUT)
        with self.assertRaises(EngineError):
            held.result(timeout=RESULT_TIMEOUT)
        self.assertTrue(engine.closed)

    def test_oversized_response_header_fails_before_body_read(self):
        engine = self.make_engine(
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_input_bytes=512,
        )
        oversized = self.submit(engine, "oversized-response")

        with self.assertRaisesRegex(EngineError, "body length exceeds limit"):
            oversized.result(timeout=RESULT_TIMEOUT)
        self.assertTrue(engine.closed)

    def test_error_response_body_has_a_smaller_diagnostic_cap(self):
        engine = self.make_engine()
        oversized = self.submit(engine, "oversized-error")

        with self.assertRaisesRegex(EngineError, "body length exceeds limit"):
            oversized.result(timeout=RESULT_TIMEOUT)
        self.assertTrue(engine.closed)

    def test_error_status_fails_only_its_request(self):
        engine = self.make_engine()
        failed = self.submit(engine, "error-one")
        with self.assertRaisesRegex(EngineError, "forced error"):
            failed.result(timeout=RESULT_TIMEOUT)

        healthy = self.submit(engine, "healthy-after-error")
        self.assertEqual(
            healthy.result(timeout=RESULT_TIMEOUT)[2]["id"], "healthy-after-error"
        )
        self.assertFalse(engine.closed)

    def test_closed_observes_child_exit_before_reader_records_terminal_error(self):
        engine = self.make_engine()
        self.assertIsNone(engine._terminal_error)
        with mock.patch.object(engine._process, "poll", return_value=17):
            self.assertTrue(engine.closed)

    def test_error_diagnostics_escape_control_characters(self):
        engine = self.make_engine()
        failed = self.submit(engine, "error-control")
        with self.assertRaises(EngineError) as raised:
            failed.result(timeout=RESULT_TIMEOUT)
        message = str(raised.exception)
        self.assertIn(r"\x1b", message)
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\n", message)

    def test_duplicate_active_request_and_stream_ids_are_rejected(self):
        engine = self.make_engine()
        held = self.submit(engine, "hold-duplicate", stream_id="shared-stream")

        with self.assertRaisesRegex(ValueError, "duplicate active request_id"):
            self.submit(engine, "hold-duplicate", stream_id="another-stream")
        with self.assertRaisesRegex(ValueError, "duplicate active stream_id"):
            self.submit(engine, "another-request", stream_id="shared-stream")

        release = self.submit(engine, "release-duplicate")
        release.result(timeout=RESULT_TIMEOUT)
        held.result(timeout=RESULT_TIMEOUT)

    def test_client_defaults_match_policy(self):
        engine = self.make_engine()
        policy = json.loads((ROOT / "policy" / "io.json").read_text(encoding="utf-8"))
        limits = policy["service_limits"]
        thresholds = policy["thresholds"]
        self.assertEqual(engine.max_streams, DEFAULT_MAX_STREAMS)
        self.assertEqual(engine.workers, DEFAULT_WORKERS)
        self.assertEqual(engine.queue_depth, DEFAULT_QUEUE_DEPTH)
        self.assertEqual(engine.max_docs, DEFAULT_MAX_DOCS)
        self.assertEqual(engine.max_request_bytes, DEFAULT_MAX_REQUEST_BYTES)
        self.assertEqual(engine.max_response_bytes, DEFAULT_MAX_RESPONSE_BYTES)
        self.assertEqual(engine.max_label_bytes, DEFAULT_MAX_LABEL_BYTES)
        self.assertEqual(engine.max_input_bytes, DEFAULT_MAX_INPUT_BYTES)
        self.assertEqual(engine.budget_ms, DEFAULT_BUDGET_MS)
        self.assertEqual(DEFAULT_MAX_STREAMS, limits["max_concurrent_streams"])
        self.assertEqual(DEFAULT_WORKERS, limits["transform_workers"])
        self.assertEqual(DEFAULT_QUEUE_DEPTH, limits["pending_queue_depth"])
        self.assertEqual(DEFAULT_MAX_DOCS, limits["max_documents_per_request"])
        self.assertEqual(DEFAULT_MAX_REQUEST_BYTES, limits["max_request_bytes"])
        self.assertEqual(DEFAULT_MAX_RESPONSE_BYTES, limits["max_response_bytes"])
        self.assertEqual(DEFAULT_MAX_LABEL_BYTES, limits["max_label_bytes"])
        self.assertEqual(DEFAULT_MAX_INPUT_BYTES, thresholds["max_input_bytes"])
        self.assertEqual(DEFAULT_BUDGET_MS, thresholds["wall_clock_budget_ms"])
        self.assertEqual(DEFAULT_MAX_CARDS, limits["max_cards_per_document"])
        self.assertEqual(
            BOUNDED_CHAPEL_RUNTIME_ENV,
            {
                "CHPL_RT_NUM_THREADS_PER_LOCALE": "2",
                "QT_NUM_SHEPHERDS": "1",
                "QT_NUM_WORKERS_PER_SHEPHERD": "2",
            },
        )
        self.assertEqual(
            engine.argv[1:],
            (
                "serve",
                "16",
                "64",
                "64",
                "16777216",
                "4096",
                "2000000",
                "2000",
                "24",
                "268435456",
            ),
        )

    def test_ids_and_policy_values_are_validated_before_write(self):
        engine = self.make_engine(max_label_bytes=32)
        for request_id, stream_id in (
            ("", "stream"),
            ("request", ""),
            ("bad\nrequest", "stream"),
            ("request", "bad\tstream"),
            ("has space", "stream"),
            ("request", "has space"),
            ("bad\x7fid", "stream"),
            ("r\u00e9quest", "stream"),
            ("x" * 33, "stream"),
        ):
            with self.subTest(request_id=request_id, stream_id=stream_id):
                with self.assertRaises(ValueError):
                    engine.submit_condense_run(
                        request_id=request_id,
                        stream_id=stream_id,
                        docs=[doc()],
                        run_id=request_id,
                        generated_at="timestamp",
                    )

        for policy in (
            {"max_input_bytes": 0},
            {"budget_ms": 0},
            {"max_cards": 0},
            {"max_input_bytes": True},
            {"budget_ms": 2_147_483_648},
            {"max_cards": 2_147_483_648},
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    self.submit(engine, "policy-check", **policy)

        with self.assertRaisesRegex(ValueError, "must match"):
            engine.submit_condense_run(
                request_id="outer-id",
                stream_id="stream",
                docs=[doc()],
                run_id="inner-id",
                generated_at="timestamp",
            )

    def test_payload_labels_remain_utf8(self):
        engine = self.make_engine()
        result = self.submit(
            engine,
            "unicode-labels",
            docs=[
                {
                    "source": "caf\u00e9.txt",
                    "trust_tier": "r\u00e9sum\u00e9_source",
                    "body": "body",
                }
            ],
            default_trust_tier="r\u00e9sum\u00e9_source",
        ).result(timeout=RESULT_TIMEOUT)
        self.assertEqual(result[0][0]["source"], "caf\u00e9.txt")
        self.assertEqual(result[0][0]["trust_tier"], "r\u00e9sum\u00e9_source")

    def test_document_label_and_request_limits_are_enforced(self):
        engine = self.make_engine(
            max_docs=1,
            max_request_bytes=512,
            max_label_bytes=32,
            max_input_bytes=256,
        )
        with self.assertRaisesRegex(ValueError, "document count"):
            self.submit(engine, "too-many", docs=[doc(), doc()])
        with self.assertRaisesRegex(ValueError, "doc 0 source"):
            self.submit(
                engine,
                "long-label",
                docs=[
                    {
                        "source": "s" * 33,
                        "trust_tier": "tier",
                        "body": "body",
                    }
                ],
            )
        with self.assertRaisesRegex(ValueError, "request payload"):
            self.submit(engine, "large-payload", docs=[doc("x" * 600)])

    def test_per_request_policy_cannot_exceed_launch_ceilings(self):
        engine = self.make_engine(
            max_input_bytes=128,
            budget_ms=75,
            max_cards=3,
        )
        boundary = self.submit(
            engine,
            "policy-boundary",
            max_input_bytes=128,
            budget_ms=75,
            max_cards=3,
        )
        self.assertEqual(
            boundary.result(timeout=RESULT_TIMEOUT)[2]["id"], "policy-boundary"
        )

        for policy in (
            {"max_input_bytes": 129},
            {"budget_ms": 76},
            {"max_cards": 4},
        ):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(ValueError, "configured ceiling"):
                    self.submit(engine, "policy-over-ceiling", **policy)

    def test_min_toon_savings_rejects_non_json_constants_before_write(self):
        engine = self.make_engine()
        for index, value in enumerate(("NaN", "Infinity", "-Infinity")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "JSON number"):
                    self.submit(
                        engine,
                        f"bad-savings-{index}",
                        min_toon_savings=value,
                    )
        self.assertEqual(engine.pending_count, 0)
        healthy = self.submit(engine, "after-bad-savings")
        self.assertEqual(
            healthy.result(timeout=RESULT_TIMEOUT)[2]["id"], "after-bad-savings"
        )

    def test_max_streams_rejects_excess_pending_work(self):
        engine = self.make_engine(max_streams=1, workers=1)
        held = self.submit(engine, "hold-only")
        with self.assertRaisesRegex(EngineError, "stream limit"):
            self.submit(engine, "over-limit")
        engine.close()
        with self.assertRaises(EngineError):
            held.result(timeout=RESULT_TIMEOUT)

    def test_close_fails_pending_work_and_is_idempotent(self):
        engine = self.make_engine()
        held = self.submit(engine, "hold-close")
        engine.close()

        with self.assertRaisesRegex(EngineError, "closed"):
            held.result(timeout=RESULT_TIMEOUT)
        self.assertTrue(engine.closed)
        self.assertIsNotNone(engine.returncode)
        with self.assertRaisesRegex(EngineError, "closed"):
            self.submit(engine, "after-close")
        engine.close()

    def test_context_manager_closes_process(self):
        with ResidentEngine(binary_path=FAKE_SERVER) as engine:
            result = self.submit(engine, "context-run").result(timeout=RESULT_TIMEOUT)
            self.assertEqual(result[2]["id"], "context-run")
        self.assertTrue(engine.closed)
        self.assertIsNotNone(engine.returncode)

    def test_invalid_constructor_limits_do_not_spawn(self):
        for kwargs in (
            {"max_streams": 0},
            {"workers": True},
            {"max_streams": 1, "workers": 2},
            {"workers": 2, "queue_depth": 1},
            {"queue_depth": -1},
            {"max_docs": 0},
            {"max_request_bytes": 100, "max_input_bytes": 101},
            {"max_request_bytes": 100, "max_response_bytes": 99},
            {"max_response_bytes": 0},
            {"max_label_bytes": 0},
            {"budget_ms": 0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ResidentEngine(binary_path=FAKE_SERVER, **kwargs)

    def test_binary_without_serve_v1_is_rejected_before_startup(self):
        true_binary = shutil.which("true")
        self.assertIsNotNone(true_binary)
        with self.assertRaisesRegex(EngineError, "caps preflight"):
            ResidentEngine(binary_path=true_binary)


if __name__ == "__main__":
    unittest.main()
