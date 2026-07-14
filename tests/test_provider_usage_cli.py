from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from prompt_toon import cli
from prompt_toon.provider_usage import ProviderUsageError


class ProviderUsageCliTests(unittest.TestCase):
    @mock.patch("prompt_toon.cli.build_provider_usage_sidecar")
    def test_import_preserves_request_order(self, build: mock.Mock) -> None:
        build.return_value = {"kind": "prompt-toon-provider-usage"}
        output = io.StringIO()
        with redirect_stdout(output):
            status = cli.main(
                [
                    "provider-usage-import",
                    "--ledger",
                    "efficiency.json",
                    "--request",
                    "request-1.json",
                    "--request",
                    "request-2.json",
                    "--usage",
                    "turn.jsonl",
                    "--source",
                    "codex-jsonl",
                    "--variant",
                    "summary_only",
                    "--model-label",
                    "gpt-5.6-sol",
                ]
            )

        self.assertEqual(status, 0)
        build.assert_called_once_with(
            ledger_path="efficiency.json",
            request_paths=["request-1.json", "request-2.json"],
            usage_path="turn.jsonl",
            source="codex-jsonl",
            variant="summary_only",
            model_label="gpt-5.6-sol",
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            {"kind": "prompt-toon-provider-usage"},
        )

    @mock.patch("prompt_toon.cli.build_provider_usage_sidecar")
    def test_import_accepts_responses_input_count(self, build: mock.Mock) -> None:
        build.return_value = {"kind": "prompt-toon-provider-usage"}
        with redirect_stdout(io.StringIO()):
            status = cli.main(
                [
                    "provider-usage-import",
                    "--ledger",
                    "efficiency.json",
                    "--request",
                    "request.json",
                    "--usage",
                    "input-tokens.json",
                    "--source",
                    "responses-input-count",
                    "--variant",
                    "summary_only",
                    "--model-label",
                    "gpt-5.6",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            build.call_args.kwargs["source"],
            "responses-input-count",
        )

    @mock.patch("prompt_toon.cli.build_provider_usage_sidecar")
    def test_import_surfaces_bounded_error_only(self, build: mock.Mock) -> None:
        build.side_effect = ProviderUsageError("usage artifact is invalid")
        with self.assertRaisesRegex(SystemExit, "usage artifact is invalid") as caught:
            cli.main(
                [
                    "provider-usage-import",
                    "--ledger",
                    "efficiency.json",
                    "--request",
                    "request.json",
                    "--usage",
                    "response.json",
                    "--source",
                    "responses-json",
                    "--variant",
                    "raw_input",
                ]
            )
        self.assertNotIn("request body", str(caught.exception))

    @mock.patch("prompt_toon.cli.build_provider_usage_comparison")
    @mock.patch("prompt_toon.cli.load_provider_usage_sidecar")
    def test_compare_loads_both_sidecars(
        self, load: mock.Mock, compare: mock.Mock
    ) -> None:
        baseline = {"observation_id": "baseline"}
        candidate = {"observation_id": "candidate"}
        load.side_effect = [baseline, candidate]
        compare.return_value = {"kind": "prompt-toon-provider-usage-comparison"}
        output = io.StringIO()

        with redirect_stdout(output):
            status = cli.main(
                [
                    "provider-usage-compare",
                    "raw.provider-usage.json",
                    "summary.provider-usage.json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            [call.args[0] for call in load.call_args_list],
            ["raw.provider-usage.json", "summary.provider-usage.json"],
        )
        compare.assert_called_once_with(baseline, candidate)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"kind": "prompt-toon-provider-usage-comparison"},
        )


if __name__ == "__main__":
    unittest.main()
