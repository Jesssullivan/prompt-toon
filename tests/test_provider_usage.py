from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from prompt_toon.dogfood import build_efficiency_ledger
from prompt_toon.provider_usage import (
    MAX_REQUEST_BYTES,
    MAX_REQUESTS,
    ProviderUsageError,
    _read_bounded,
    _request_sequence,
    build_provider_usage_comparison,
    build_provider_usage_sidecar,
    load_provider_usage_sidecar,
)


def _write(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    return path


def _ledger_directory(root: Path) -> Path:
    output = root / "run"
    output.mkdir()
    summary = _write(output / "summary.md", b"summary\n")
    cards = _write(output / "source-cards.jsonl", b'{"id":"card-1"}\n')
    manifest = _write(output / "manifest.json", b'{"manifest":"private-manifest"}\n')
    source = b"source bytes for corpus"
    ledger = build_efficiency_ledger(
        run_id="provider-usage-run",
        generated_at="2026-07-13T00:00:00Z",
        output_dir=output,
        documents=1,
        input_metadata=[
            {
                "bytes": len(source),
                "sha256": hashlib.sha256(source).hexdigest(),
                "trust_tier": "repo_source",
            }
        ],
        max_cards_per_document=1,
        input_bytes=len(source),
        input_tokens_estimate=100,
        withheld_documents=0,
        withheld_details=[],
        engine_requested="python",
        engine_resolved="python",
        execution_shape="python-sequential-oracle",
        engine_wall_ms=1,
        run_wall_ms=2,
        min_toon_savings=0.2,
        min_handoff_savings=0.2,
        format_analysis={"toon_savings": 0.0, "toon_eligible": False},
        mythos_route=None,
        model_label=None,
        token_counter=lambda text: len(text),
    )
    _write(output / "efficiency.json", json.dumps(ledger).encode("utf-8"))
    assert summary.exists() and cards.exists() and manifest.exists()
    return output


def _responses_json(
    *,
    input_tokens: int = 100,
    output_tokens: int | None = 20,
    cached_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    total_tokens: int | None = 120,
    model: str = "gpt-returned",
) -> bytes:
    usage: dict[str, object] = {"input_tokens": input_tokens}
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    if cached_tokens is not None or cache_write_tokens is not None:
        details: dict[str, int] = {}
        if cached_tokens is not None:
            details["cached_tokens"] = cached_tokens
        if cache_write_tokens is not None:
            details["cache_write_tokens"] = cache_write_tokens
        usage["input_tokens_details"] = details
    if reasoning_tokens is not None:
        usage["output_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return json.dumps(
        {
            "object": "response",
            "model": model,
            "status": "completed",
            "usage": usage,
        }
    ).encode("utf-8")


class ProviderUsageTests(unittest.TestCase):
    def _sidecar(
        self,
        root: Path,
        *,
        source: str = "responses-json",
        variant: str = "raw_input",
        usage: bytes | None = None,
        model_label: str | None = None,
        request: bytes | None = None,
    ) -> dict:
        run = _ledger_directory(root)
        if request is None:
            request = b'{"model":"gpt-requested","secret":"request-secret"}'
        request_path = _write(root / "request.json", request)
        if usage is None:
            usage = _responses_json(cached_tokens=15, cache_write_tokens=4)
        usage_path = _write(root / "usage.txt", usage)
        return build_provider_usage_sidecar(
            ledger_path=run / "efficiency.json",
            request_paths=[request_path],
            usage_path=usage_path,
            source=source,
            variant=variant,
            model_label=model_label,
        )

    def test_responses_json_binds_only_descriptors_and_canonical_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = self._sidecar(root)
            self.assertEqual(sidecar["schema_version"], 1)
            self.assertEqual(sidecar["kind"], "prompt-toon-provider-usage")
            self.assertEqual(sidecar["variant"], "raw_input")
            self.assertEqual(sidecar["handoff_artifacts"], [])
            self.assertEqual(
                sidecar["model"],
                {"label": "gpt-returned", "provenance": "response_returned"},
            )
            self.assertEqual(sidecar["usage"]["input_tokens"], 100)
            self.assertEqual(sidecar["usage"]["cached_input_tokens"], 15)
            self.assertEqual(sidecar["usage"]["cache_write_tokens"], 4)
            self.assertEqual(sidecar["usage"]["total_tokens"], 120)
            self.assertEqual(
                sidecar["requests"]["ordered"][0]["sha256"],
                hashlib.sha256(
                    b'{"model":"gpt-requested","secret":"request-secret"}'
                ).hexdigest(),
            )
            self.assertEqual(
                sidecar["usage_artifact"]["sha256"],
                hashlib.sha256(
                    _responses_json(cached_tokens=15, cache_write_tokens=4)
                ).hexdigest(),
            )
            rendered = json.dumps(sidecar, sort_keys=True)
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("request-secret", rendered)
            self.assertNotIn("private-manifest", rendered)
            self.assertNotIn("provider-usage-run", rendered)
            self.assertEqual(
                sidecar["claim_boundary"]["provider_requests_made_by_importer"], 0
            )
            self.assertEqual(sidecar["claim_boundary"]["cost_claim"], "none")
            self.assertEqual(sidecar["claim_boundary"]["quality_claim"], "none")

    def test_responses_input_count_binds_one_complete_request(self) -> None:
        request = b'{"model":"gpt-5.6","input":"condensed context"}'
        usage = b'{"object":"response.input_tokens","input_tokens":42}'
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = self._sidecar(
                Path(tmp),
                source="responses-input-count",
                request=request,
                usage=usage,
                model_label="gpt-5.6",
            )

        self.assertEqual(
            sidecar["model"],
            {"label": "gpt-5.6", "provenance": "request_declared"},
        )
        self.assertEqual(sidecar["usage"]["input_tokens"], 42)
        self.assertEqual(
            sidecar["usage"]["omitted_fields"],
            [
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_tokens",
            ],
        )
        self.assertEqual(
            sidecar["requests"]["ordered"][0]["sha256"],
            hashlib.sha256(request).hexdigest(),
        )

    def test_responses_input_count_rejects_ambiguous_artifacts(self) -> None:
        cases = (
            (
                b'{"input":"missing model"}',
                b'{"object":"response.input_tokens","input_tokens":1}',
                None,
                "request artifact 1.model",
            ),
            (
                b'{"model":"m","model":"m","input":"duplicate"}',
                b'{"object":"response.input_tokens","input_tokens":1}',
                None,
                "duplicate JSON key",
            ),
            (
                b'{"model":"model\\nsecret","input":"unsafe"}',
                b'{"object":"response.input_tokens","input_tokens":1}',
                None,
                "unsupported characters",
            ),
            (
                b'{"model":"m","input":NaN}',
                b'{"object":"response.input_tokens","input_tokens":1}',
                None,
                "non-finite JSON number",
            ),
            (
                b'{"model":"m","input":"x"}',
                b'{"object":"response.input_tokens","input_tokens":1,"extra":0}',
                None,
                "fields do not match",
            ),
            (
                b'{"model":"m","input":"x"}',
                b'{"object":"wrong","input_tokens":1}',
                None,
                "not a Responses input-token count",
            ),
            (
                b'{"model":"m","input":"x"}',
                b'{"object":"response.input_tokens","input_tokens":1}',
                "different-model",
                "does not match",
            ),
        )
        for request, usage, model_label, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ProviderUsageError, message):
                    self._sidecar(
                        Path(tmp),
                        source="responses-input-count",
                        request=request,
                        usage=usage,
                        model_label=model_label,
                    )

        utf16_request = json.dumps({"model": "m", "input": "x"}).encode("utf-16")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "valid UTF-8 JSON"):
                self._sidecar(
                    Path(tmp),
                    source="responses-input-count",
                    request=utf16_request,
                    usage=b'{"object":"response.input_tokens","input_tokens":1}',
                )

    def test_request_count_guard_allows_long_turns_but_remains_bounded(self) -> None:
        self.assertGreaterEqual(MAX_REQUESTS, 256)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            request = _write(root / "request", b'{"model":"gpt-5.6-sol","input":"x"}')
            usage = _write(
                root / "usage",
                b"\n".join(
                    (
                        b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
                        b'{"type":"turn.completed","usage":{"input_tokens":100}}',
                    )
                ),
            )
            sidecar = build_provider_usage_sidecar(
                ledger_path=run / "efficiency.json",
                request_paths=[request] * MAX_REQUESTS,
                usage_path=usage,
                source="codex-jsonl",
                variant="raw_input",
                model_label="gpt-5.6-sol",
            )
            self.assertEqual(len(sidecar["requests"]["ordered"]), MAX_REQUESTS)
            with self.assertRaisesRegex(ProviderUsageError, "count exceeds"):
                build_provider_usage_sidecar(
                    ledger_path=run / "efficiency.json",
                    request_paths=[request] * (MAX_REQUESTS + 1),
                    usage_path=usage,
                    source="codex-jsonl",
                    variant="raw_input",
                    model_label="gpt-5.6-sol",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            request = _write(root / "request", b'{"model":"m","input":"x"}')
            usage = _write(
                root / "usage",
                b'{"object":"response.input_tokens","input_tokens":1}',
            )
            with self.assertRaisesRegex(ProviderUsageError, "exactly one"):
                build_provider_usage_sidecar(
                    ledger_path=run / "efficiency.json",
                    request_paths=[request, request],
                    usage_path=usage,
                    source="responses-input-count",
                    variant="raw_input",
                )

    def test_handoff_binds_ledger_artifact_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = self._sidecar(root, variant="summary_plus_authoritative_cards")
            self.assertEqual(
                [item["name"] for item in sidecar["handoff_artifacts"]],
                ["summary.md", "source-cards.jsonl"],
            )
            self.assertTrue(
                all(item["sha256"] for item in sidecar["handoff_artifacts"])
            )

    def test_import_rejects_changed_or_symlinked_ledger_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            request = _write(root / "request", b'{"model":"m","input":"x"}')
            usage = _write(root / "usage", _responses_json())
            _write(run / "manifest.json", b"changed manifest")
            with self.assertRaisesRegex(ProviderUsageError, "manifest.json"):
                build_provider_usage_sidecar(
                    ledger_path=run / "efficiency.json",
                    request_paths=[request],
                    usage_path=usage,
                    source="responses-json",
                    variant="raw_input",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            request = _write(root / "request", b'{"model":"m","input":"x"}')
            usage = _write(root / "usage", _responses_json())
            summary = run / "summary.md"
            target = _write(root / "replacement-summary", summary.read_bytes())
            summary.unlink()
            summary.symlink_to(target)
            with self.assertRaisesRegex(ProviderUsageError, "summary.md"):
                build_provider_usage_sidecar(
                    ledger_path=run / "efficiency.json",
                    request_paths=[request],
                    usage_path=usage,
                    source="responses-json",
                    variant="summary_only",
                )

    def test_import_rejects_handoff_artifact_list_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            ledger_path = run / "efficiency.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["handoffs"]["summary_only"]["artifacts"] = ["source-cards.jsonl"]
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            with self.assertRaisesRegex(ProviderUsageError, "handoff artifacts"):
                build_provider_usage_sidecar(
                    ledger_path=ledger_path,
                    request_paths=[
                        _write(root / "request", b'{"model":"m","input":"x"}')
                    ],
                    usage_path=_write(root / "usage", _responses_json()),
                    source="responses-json",
                    variant="summary_only",
                )

    def test_sse_requires_one_pure_terminal_completion(self) -> None:
        sse = b"\n".join(
            (
                b"event: response.created",
                b'data: {"type":"response.created","response":{"id":"r"}}',
                b"",
                b"event: response.completed",
                b'data: {"type":"response.completed","response":{"object":"response","model":"gpt-returned","status":"completed","usage":{"input_tokens":30,"output_tokens":5,"total_tokens":35}}}',
                b"",
                b"",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = self._sidecar(
                Path(tmp), source="responses-sse", usage=sse, model_label="gpt-returned"
            )
        self.assertEqual(sidecar["usage"]["input_tokens"], 30)
        self.assertIsNone(sidecar["usage"]["cached_input_tokens"])
        self.assertEqual(
            sidecar["usage"]["omitted_fields"],
            ["cached_input_tokens", "cache_write_tokens", "reasoning_output_tokens"],
        )

    def test_sse_rejects_duplicate_or_nonterminal_terminal_events(self) -> None:
        duplicate = b"\n".join(
            (
                b"event: response.completed",
                b'data: {"type":"response.completed","response":{"object":"response","model":"m","status":"completed","usage":{"input_tokens":1}}}',
                b"",
                b"event: response.completed",
                b'data: {"type":"response.completed","response":{"object":"response","model":"m","status":"completed","usage":{"input_tokens":1}}}',
                b"",
            )
        )
        failed = b"\n".join(
            (
                b"event: response.failed",
                b'data: {"type":"response.failed"}',
                b"",
            )
        )
        for value in (duplicate, failed):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ProviderUsageError, "SSE"):
                    self._sidecar(Path(tmp), source="responses-sse", usage=value)

        unicode_separator = (
            "event: response.completed\u2028data: "
            '{"type":"response.completed","response":{"object":"response",'
            '"model":"m","status":"completed","usage":{"input_tokens":1}}}'
            "\u2028\u2028"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "line separator"):
                self._sidecar(
                    Path(tmp), source="responses-sse", usage=unicode_separator
                )

        invalid_type = b"\n".join(
            (
                b"event: response.completed",
                b'data: {"type":[],"response":{"model":"m","usage":{"input_tokens":1}}}',
                b"",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "type must be a string"):
                self._sidecar(Path(tmp), source="responses-sse", usage=invalid_type)

    def test_codex_requires_matching_request_label_and_strict_stream(self) -> None:
        stream = b"\n".join(
            (
                b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
                b'{"type":"turn.completed","usage":{"input_tokens":9,"cache_write_tokens":2}}',
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ProviderUsageError, "model_label"):
                self._sidecar(root, source="codex-jsonl", usage=stream)
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = self._sidecar(
                Path(tmp),
                source="codex-jsonl",
                usage=stream,
                model_label="caller-model",
                request=b'{"model":"caller-model","input":"x","stream":true}',
            )
        self.assertEqual(
            sidecar["model"],
            {"label": "caller-model", "provenance": "request_declared"},
        )
        self.assertEqual(sidecar["usage"]["cache_write_tokens"], 2)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "every exact Codex"):
                self._sidecar(
                    Path(tmp),
                    source="codex-jsonl",
                    usage=stream,
                    model_label="caller-model",
                    request=b'{"model":"different","input":"x","stream":true}',
                )

    def test_rejects_duplicate_keys_nonfinite_and_usage_invariants(self) -> None:
        cases = (
            b"not JSON",
            b'{"type":[],"model":"m","status":"completed","usage":{"input_tokens":1}}',
            b'{"model":"m","model":"m","usage":{"input_tokens":1}}',
            b'{"model":"m","usage":{"input_tokens":NaN}}',
            _responses_json(cached_tokens=101),
            _responses_json(output_tokens=2, reasoning_tokens=3, total_tokens=102),
            _responses_json(output_tokens=2, total_tokens=999),
            _responses_json(output_tokens=None, total_tokens=99),
        )
        for value in cases:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ProviderUsageError):
                    self._sidecar(Path(tmp), usage=value)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "valid UTF-8 JSON"):
                self._sidecar(Path(tmp), request=b"not a JSON request")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "must be an object"):
                self._sidecar(Path(tmp), request=b"[]")

    def test_single_response_sources_reject_multiple_requests(self) -> None:
        for source, usage in (
            ("responses-json", _responses_json()),
            (
                "responses-sse",
                b"\n".join(
                    (
                        b"event: response.completed",
                        b'data: {"type":"response.completed","response":{"object":"response","model":"m","status":"completed","usage":{"input_tokens":1}}}',
                        b"",
                    )
                ),
            ),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run = _ledger_directory(root)
                request = _write(root / "request", b'{"model":"m","input":"x"}')
                usage_path = _write(root / "usage", usage)
                with self.assertRaisesRegex(ProviderUsageError, "exactly one"):
                    build_provider_usage_sidecar(
                        ledger_path=run / "efficiency.json",
                        request_paths=[request, request],
                        usage_path=usage_path,
                        source=source,
                        variant="raw_input",
                    )

    def test_response_model_label_must_match_and_variant_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "does not match"):
                self._sidecar(Path(tmp), model_label="different")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "variant"):
                self._sidecar(Path(tmp), variant="not-a-handoff")
        with tempfile.TemporaryDirectory() as tmp:
            incomplete = json.dumps(
                {
                    "object": "response",
                    "model": "gpt-returned",
                    "status": "incomplete",
                    "usage": {"input_tokens": 1},
                }
            ).encode("utf-8")
            with self.assertRaisesRegex(ProviderUsageError, "status"):
                self._sidecar(Path(tmp), usage=incomplete)
        with tempfile.TemporaryDirectory() as tmp:
            missing_object = json.dumps(
                {
                    "model": "gpt-returned",
                    "status": "completed",
                    "usage": {"input_tokens": 1},
                }
            ).encode("utf-8")
            with self.assertRaisesRegex(ProviderUsageError, "response.object"):
                self._sidecar(Path(tmp), usage=missing_object)
        with tempfile.TemporaryDirectory() as tmp:
            unsafe_model = _responses_json(model="model\nsecret")
            with self.assertRaisesRegex(ProviderUsageError, "unsupported characters"):
                self._sidecar(Path(tmp), usage=unsafe_model)

    def test_completed_event_rejects_contradictory_response_status(self) -> None:
        event = json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "object": "response",
                    "model": "gpt-returned",
                    "status": "incomplete",
                    "usage": {"input_tokens": 1},
                },
            }
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProviderUsageError, "contradicts"):
                self._sidecar(Path(tmp), usage=event)

    def test_readers_reject_symlink_oversize_and_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write(root / "target", b"{}")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(ProviderUsageError, "non-symlink"):
                _read_bounded(link, limit=10, label="test")
            oversized = _write(root / "oversized", b"x" * (MAX_REQUEST_BYTES + 1))
            with self.assertRaisesRegex(ProviderUsageError, "exceeds"):
                _read_bounded(oversized, limit=MAX_REQUEST_BYTES, label="test")
            regular = _write(root / "regular", b"stable")
            metadata = os.stat(regular)
            changed = SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_size=metadata.st_size,
                st_mtime_ns=metadata.st_mtime_ns + 1,
                st_ctime_ns=metadata.st_ctime_ns,
            )
            with mock.patch(
                "prompt_toon.provider_usage.os.fstat", side_effect=[metadata, changed]
            ):
                with self.assertRaisesRegex(ProviderUsageError, "changed"):
                    _read_bounded(regular, limit=10, label="test")

    def test_loader_strictly_validates_schema_and_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = self._sidecar(root)
            path = _write(root / "sidecar.json", json.dumps(sidecar).encode("utf-8"))
            self.assertEqual(load_provider_usage_sidecar(path), sidecar)
            invalid = dict(sidecar)
            invalid["usage"] = dict(sidecar["usage"])
            invalid["usage"]["omitted_fields"] = []
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "omitted_fields"):
                load_provider_usage_sidecar(path)
            invalid = dict(sidecar)
            invalid["variant"] = "unrecognized"
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "variant"):
                load_provider_usage_sidecar(path)
            _write(path, b'{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(ProviderUsageError, "duplicate"):
                load_provider_usage_sidecar(path)

            invalid = dict(sidecar)
            invalid["schema_version"] = True
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "schema_version"):
                load_provider_usage_sidecar(path)

            invalid = dict(sidecar)
            invalid["source"] = []
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "source"):
                load_provider_usage_sidecar(path)

            invalid = dict(sidecar)
            invalid["model"] = dict(sidecar["model"])
            invalid["model"]["provenance"] = []
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "provenance"):
                load_provider_usage_sidecar(path)

    def test_loader_rechecks_input_count_source_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = self._sidecar(
                root,
                source="responses-input-count",
                request=b'{"model":"m","input":"x"}',
                usage=b'{"object":"response.input_tokens","input_tokens":1}',
            )
            path = root / "sidecar.json"

            invalid = json.loads(json.dumps(sidecar))
            invalid["requests"]["ordered"].append(
                dict(invalid["requests"]["ordered"][0])
            )
            invalid["requests"]["sequence_sha256"] = _request_sequence(
                invalid["requests"]["ordered"]
            )
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "exactly one"):
                load_provider_usage_sidecar(path)

            invalid = json.loads(json.dumps(sidecar))
            invalid["usage"]["output_tokens"] = 1
            invalid["usage"]["omitted_fields"].remove("output_tokens")
            _write(path, json.dumps(invalid).encode("utf-8"))
            with self.assertRaisesRegex(ProviderUsageError, "only input_tokens"):
                load_provider_usage_sidecar(path)

    def test_comparison_requires_same_binding_and_reports_cache_separately(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _ledger_directory(root)
            baseline_request = _write(
                root / "baseline-request", b'{"model":"m","input":"raw"}'
            )
            candidate_request = _write(
                root / "candidate-request", b'{"model":"m","input":"condensed"}'
            )
            baseline_usage = _write(
                root / "baseline",
                _responses_json(
                    input_tokens=100, cached_tokens=20, cache_write_tokens=5
                ),
            )
            candidate_usage = _write(
                root / "candidate",
                _responses_json(
                    input_tokens=60,
                    cached_tokens=10,
                    cache_write_tokens=2,
                    total_tokens=80,
                ),
            )
            baseline = build_provider_usage_sidecar(
                ledger_path=run / "efficiency.json",
                request_paths=[baseline_request],
                usage_path=baseline_usage,
                source="responses-json",
                variant="raw_input",
            )
            candidate = build_provider_usage_sidecar(
                ledger_path=run / "efficiency.json",
                request_paths=[candidate_request],
                usage_path=candidate_usage,
                source="responses-json",
                variant="summary_only",
            )
            report = build_provider_usage_comparison(baseline, candidate)
            self.assertEqual(
                report["input_tokens"],
                {"baseline": 100, "candidate": 60, "savings": 40},
            )
            self.assertEqual(
                report["cache_tokens"]["candidate"], {"read": 10, "write": 2}
            )
            self.assertEqual(
                report["observations"]["baseline"]["usage_artifact_sha256"],
                baseline["usage_artifact"]["sha256"],
            )
            self.assertEqual(
                report["observations"]["candidate"]["request_sequence_sha256"],
                candidate["requests"]["sequence_sha256"],
            )
            self.assertEqual(report["claim_boundary"]["cost_claim"], "none")
            self.assertIn(
                "not provider-authenticated",
                report["claim_boundary"]["caller_supplied_external_artifacts"],
            )
            mismatched = json.loads(json.dumps(candidate))
            mismatched["model"]["label"] = "another-model"
            with self.assertRaisesRegex(ProviderUsageError, "same model"):
                build_provider_usage_comparison(baseline, mismatched)
            mismatched = json.loads(json.dumps(candidate))
            mismatched["manifest"]["sha256"] = "f" * 64
            with self.assertRaisesRegex(ProviderUsageError, "same manifest"):
                build_provider_usage_comparison(baseline, mismatched)
            reused = json.loads(json.dumps(candidate))
            reused["requests"] = baseline["requests"]
            with self.assertRaisesRegex(ProviderUsageError, "different request"):
                build_provider_usage_comparison(baseline, reused)
            reused = json.loads(json.dumps(candidate))
            reused["usage_artifact"] = baseline["usage_artifact"]
            with self.assertRaisesRegex(ProviderUsageError, "different usage"):
                build_provider_usage_comparison(baseline, reused)
            with self.assertRaisesRegex(ProviderUsageError, "baseline"):
                build_provider_usage_comparison(candidate, baseline)


if __name__ == "__main__":
    unittest.main()
