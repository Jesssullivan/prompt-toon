"""Live canary gating and aggregate-report tests without provider calls."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tools import anthropic_gateway_canary as canary


class GatewayCanaryTests(unittest.TestCase):
    def test_billing_gate_precedes_credentials_and_network(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(canary, "_run_dedicated_canary") as run:
                with self.assertRaisesRegex(SystemExit, "PROMPT_TOON_LIVE_CANARY"):
                    canary.main([])
        run.assert_not_called()

    @mock.patch.object(canary, "_run_dedicated_canary")
    def test_canary_reports_harness_models_usage_and_transform_quality(
        self, run: mock.Mock
    ) -> None:
        metrics = {
            "counters": {
                "messages_requests": 2,
                "typed_documents_selected": 1,
                "shadow_completed": 1,
                "shadow_documents_completed": 1,
                "shadow_failed": 0,
                "shadow_documents_withheld": 0,
                "estimated_raw_tokens": 120,
                "estimated_condensed_tokens": 30,
                "estimated_tokens_saved": 90,
                "upstream_responses": 2,
                "provider_input_tokens": 220,
                "provider_output_tokens": 25,
                "provider_cache_creation_input_tokens": 0,
                "provider_cache_read_input_tokens": 40,
                "sse_error_events": 0,
                "response_telemetry_unavailable": 0,
            },
            "models": {
                "requested": {"model:opaque-requested": 2},
                "returned": {"model:opaque-returned": 2},
            },
            "quality": {"eligible": 1},
        }
        run.return_value = (
            {
                "result": canary._MARKER,
                "stream_lines": 12,
                "is_error": False,
                "subtype": "success",
                "api_retries": 0,
            },
            metrics,
        )
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {
                "PROMPT_TOON_LIVE_CANARY": "1",
                "ANTHROPIC_API_KEY": "unit-test-key",
            },
            clear=True,
        ):
            with redirect_stdout(output):
                code = canary.main(
                    [
                        "--model",
                        "claude-test",
                        "--max-budget-usd",
                        "0.25",
                    ]
                )
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["harness"]["client"], "claude-code")
        self.assertTrue(result["transport"]["dedicated_gateway"])
        self.assertEqual(result["transport"]["requested_model_count"], 2)
        self.assertEqual(result["transport"]["returned_model_count"], 2)
        self.assertEqual(result["transport"]["provider_usage"]["input_tokens"], 220)
        self.assertEqual(result["transform"]["estimated_transform_tokens_saved"], 90)
        self.assertEqual(result["transform"]["quality"], {"eligible": 1})
        rendered = output.getvalue()
        self.assertNotIn("unit-test-key", rendered)
        self.assertNotIn(canary._MARKER, rendered)
        self.assertNotIn("claude-test", rendered)

    def test_report_rejects_retries_and_nonexclusive_metrics(self) -> None:
        harness = {
            "result": canary._MARKER,
            "stream_lines": 3,
            "api_retries": 1,
        }
        with self.assertRaisesRegex(RuntimeError, "retried"):
            canary.build_report(harness=harness, metrics={})
        harness["api_retries"] = 0
        metrics = {
            "counters": {
                "messages_requests": 3,
                "typed_documents_selected": 1,
                "shadow_completed": 1,
                "shadow_documents_completed": 1,
                "upstream_responses": 3,
            },
            "models": {"requested": {}, "returned": {}},
            "quality": {"eligible": 1},
        }
        with self.assertRaisesRegex(RuntimeError, "messages_requests=3"):
            canary.build_report(harness=harness, metrics=metrics)


if __name__ == "__main__":
    unittest.main()
