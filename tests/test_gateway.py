"""Protocol and trust-boundary tests for the Anthropic shadow gateway."""

from __future__ import annotations

import http.client
import http.server
import hmac
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from prompt_toon.adoption import gateway_doctor_status
from prompt_toon.engine import EngineError
from prompt_toon.gateway import (
    GatewayAuth,
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ResponseObserver,
    ShadowAnalyzer,
    create_gateway_server,
    ownership_attestation_message,
    parse_message_for_shadow,
)


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "io.json"
REFUSAL_RESPONSE_BODY = (
    b'{"id":"msg_refusal","type":"message","role":"assistant",'
    b'"model":"claude-fable-fixture","content":[],"stop_reason":"refusal",'
    b'"stop_details":{"type":"refusal","category":"cyber",'
    b'"explanation":"fixture explanation must not enter metrics"},'
    b'"usage":{"input_tokens":34,"output_tokens":0}}'
)
FALLBACK_RESPONSE_BODY = (
    b'{"id":"msg_fallback","type":"message","role":"assistant",'
    b'"model":"claude-fallback-fixture","content":['
    b'{"type":"fallback","from":{"model":"claude-fable-fixture"},'
    b'"to":{"model":"claude-fallback-fixture"}},'
    b'{"type":"text","text":"fixture answer"}],"stop_reason":"end_turn",'
    b'"stop_details":null,"usage":{"input_tokens":34,"output_tokens":3,'
    b'"iterations":[{"type":"message","model":"claude-fable-fixture"},'
    b'{"type":"fallback_message","model":"claude-fallback-fixture"}]}}'
)


def wait_for(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return bool(predicate())


class FakeEngine:
    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.submitted = threading.Event()
        self.closed = False

    def submit_condense_run(self, **kwargs: Any) -> Future:
        self.submissions.append(kwargs)
        future: Future = Future()
        future.set_result(
            (
                [
                    {
                        "withheld": False,
                        "cards": [{"claim": "condensed"}],
                    }
                    for _ in kwargs["docs"]
                ],
                "condensed",
                {"id": kwargs["request_id"]},
            )
        )
        self.submitted.set()
        return future

    def close(self) -> None:
        self.closed = True


class NeverCompletesEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.closed_event = threading.Event()

    def submit_condense_run(self, **kwargs: Any) -> Future:
        self.submissions.append(kwargs)
        self.submitted.set()
        return Future()

    def close(self) -> None:
        super().close()
        self.closed_event.set()


class TerminalFailureEngine(FakeEngine):
    def submit_condense_run(self, **kwargs: Any) -> Future:
        self.submissions.append(kwargs)
        self.closed = True
        self.submitted.set()
        future: Future = Future()
        future.set_exception(EngineError("fixture resident protocol failure"))
        return future


class UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def append(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(value)


class FakeUpstreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Any

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.state.append(
            {
                "path": self.path,
                "headers": list(self.headers.raw_items()),
                "body": body,
            }
        )
        response_mode = self.headers.get("X-Test-Response", "json")
        if response_mode == "unsupported-transfer":
            self.close_connection = True
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "gzip, chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"0\r\n\r\n")
            return
        if response_mode == "truncated":
            response_body = b'{"model":"claude-partial","usage":{"input_tokens":77}}'
            self.close_connection = True
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body) + 10))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
            return
        if response_mode in (
            "duplicate-length-short-first",
            "duplicate-length-long-first",
        ):
            response_body = b"123456789"
            lengths = (
                ("3", "9")
                if response_mode == "duplicate-length-short-first"
                else ("9", "3")
            )
            self.close_connection = True
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            for value in lengths:
                self.send_header("Content-Length", value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
            return
        if response_mode == "error":
            response_body = (
                b'{"type":"error","error":{"type":"rate_limit_error",'
                b'"message":"fixture limit"},"request_id":"req_fixture"}'
            )
            self.send_response_only(429, "Too Many Requests")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("request-id", "req_fixture")
            self.send_header("x-upstream-unknown", "preserved")
            self.end_headers()
            self.wfile.write(response_body)
            return
        if response_mode in ("refusal", "fallback"):
            response_body = (
                REFUSAL_RESPONSE_BODY
                if response_mode == "refusal"
                else FALLBACK_RESPONSE_BODY
            )
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("x-upstream-unknown", "preserved")
            self.end_headers()
            self.wfile.write(response_body)
            return
        if response_mode == "sse":
            events = b"".join(
                (
                    b"event: message_start\n",
                    b'data: {"type":"message_start","message":{"model":'
                    b'"claude-returned","usage":{"input_tokens":12,'
                    b'"output_tokens":1}}}\n\n',
                    b"event: future_event\n",
                    b'data: {"type":"future_event","new_field":{"x":1}}\n\n',
                    b"event: content_block_start\n",
                    b'data: {"type":"content_block_start","content_block":'
                    b'{"type":"fallback","to":{"model":"claude-fallback"}}}\n\n',
                    b"event: message_delta\n",
                    b'data: {"type":"message_delta","delta":{"stop_reason":'
                    b'"end_turn","stop_details":null},"usage":{"output_tokens":4,'
                    b'"iterations":[{"type":"message"},{"type":'
                    b'"fallback_message","model":"claude-fallback"}]}}\n\n',
                    b"event: error\n",
                    b'data: {"type":"error","error":{"type":'
                    b'"overloaded_error","message":"Overloaded"}}\n\n',
                    b"event: message_stop\n",
                    b'data: {"type":"message_stop"}\n\n',
                )
            )
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("x-upstream-unknown", "preserved")
            self.end_headers()
            for chunk in (events[:37], events[37:111], events[111:]):
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        response_body = (
            b'{"id":"msg_fixture","type":"message","role":"assistant",'
            b'"model":"claude-returned","content":[],"stop_reason":"end_turn",'
            b'"usage":{"input_tokens":21,"output_tokens":3,'
            b'"cache_creation_input_tokens":5,"cache_read_input_tokens":8}}'
        )
        self.send_response_only(200, "OK")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("request-id", "req_fixture")
        self.send_header("x-upstream-unknown", "preserved")
        self.end_headers()
        self.wfile.write(response_body)


def request_body() -> bytes:
    # Deliberate whitespace and key order make reserialization observable.
    return b"""{
  "model": "claude-requested",
  "max_tokens": 7,
  "system": [{"type":"text","text":"AUTHORITY_MARKER","cache_control":{"type":"ephemeral"}}],
  "tools": [{"name":"Task","description":"SCHEMA_MARKER","input_schema":{"type":"object","x-unknown":true}}],
  "messages": [
    {"role":"user","content":"USER_MARKER"},
    {"role":"assistant","content":[
      {"type":"tool_use","id":"toolu_task","name":"Task","input":{"prompt":"do work"}},
      {"type":"tool_use","id":"toolu_unknown","name":"UnknownTool","input":{}}
    ]},
    {"role":"user","content":[
      {"type":"tool_result","tool_use_id":"toolu_task","content":[
        {"type":"text","text":"first typed result"},
        {"type":"image","source":{"type":"base64","data":"IMAGE_MARKER"}},
        {"type":"text","text":"second typed result","cache_control":{"type":"ephemeral"}}
      ]},
      {"type":"tool_result","tool_use_id":"toolu_unknown","content":"UNKNOWN_MARKER"},
      {"type":"text","text":"USER_TEXT_MARKER"}
    ]}
  ],
  "x-future-provider-field": {"nested": [1,2,3]}
}
"""


class GatewayProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GatewayPolicy.load(POLICY_PATH)
        self.upstream_state = UpstreamState()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), FakeUpstreamHandler
        )
        self.upstream.state = self.upstream_state  # type: ignore[attr-defined]
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()

        self.metrics = GatewayMetrics()
        self.engine = FakeEngine()
        self.analyzer = ShadowAnalyzer(self.policy, self.metrics, self.engine)
        self.gateway = create_gateway_server(
            GatewayConfig(
                listen_host="127.0.0.1",
                listen_port=0,
                upstream=f"http://127.0.0.1:{self.upstream.server_port}",
                max_request_bytes=self.policy.max_request_bytes,
                max_concurrent_requests=self.policy.max_concurrent_streams,
            ),
            self.analyzer,
            self.metrics,
        )
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        )
        self.gateway_thread.start()

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway_thread.join(timeout=5)
        self.gateway.server_close()
        self.upstream.shutdown()
        self.upstream_thread.join(timeout=5)
        self.upstream.server_close()

    @staticmethod
    def _headers(mode: str = "json") -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": "unit-test-credential",
            "Authorization": "Bearer unit-test",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "fixture-beta",
            "x-future-feature": "preserve-me",
            "X-Test-Response": mode,
        }

    @staticmethod
    def _send(
        port: int,
        body: bytes,
        headers: dict[str, str],
        *,
        chunked: bool = False,
        path: str = "/v1/messages",
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        if chunked:
            connection.request(
                "POST",
                path,
                body=[body[:13], body[13:]],
                headers=headers,
                encode_chunked=True,
            )
        else:
            connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.getheaders(), response.read()
        connection.close()
        return result

    def test_direct_vs_gateway_preserves_authority_bytes_and_headers(self) -> None:
        body = request_body()
        headers = self._headers()
        direct = self._send(self.upstream.server_port, body, headers)
        proxied = self._send(self.gateway.server_port, body, headers)

        self.assertEqual(proxied[0], direct[0])
        self.assertEqual(proxied[2], direct[2])
        self.assertEqual(dict(proxied[1])["x-upstream-unknown"], "preserved")
        direct_record, gateway_record = self.upstream_state.requests
        self.assertEqual(gateway_record["body"], direct_record["body"])
        self.assertEqual(gateway_record["body"], body)
        gateway_headers = {
            name.lower(): value for name, value in gateway_record["headers"]
        }
        expected_headers = {name.lower(): value for name, value in headers.items()}
        for name in (
            "x-api-key",
            "authorization",
            "anthropic-version",
            "anthropic-beta",
            "x-future-feature",
        ):
            self.assertEqual(gateway_headers[name], expected_headers[name])

        self.assertTrue(self.engine.submitted.wait(timeout=5))
        docs = self.engine.submissions[0]["docs"]
        self.assertEqual(
            [item["body"] for item in docs],
            ["first typed result", "second typed result"],
        )
        self.assertEqual({item["trust_tier"] for item in docs}, {"subagent_return"})
        captured = json.dumps(docs)
        for marker in (
            "AUTHORITY_MARKER",
            "SCHEMA_MARKER",
            "USER_MARKER",
            "IMAGE_MARKER",
            "UNKNOWN_MARKER",
            "USER_TEXT_MARKER",
        ):
            self.assertNotIn(marker, captured)

    def test_chunked_request_is_dechunked_without_changing_entity_bytes(self) -> None:
        body = request_body()
        response = self._send(
            self.gateway.server_port,
            body,
            self._headers(),
            chunked=True,
        )
        self.assertEqual(response[0], 200)
        self.assertEqual(self.upstream_state.requests[-1]["body"], body)

    def test_claude_code_beta_query_is_preserved(self) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("sse"),
            path="/v1/messages?beta=true",
        )
        self.assertEqual(response[0], 200)
        self.assertEqual(
            self.upstream_state.requests[-1]["path"], "/v1/messages?beta=true"
        )

    def test_non_2xx_status_body_request_id_and_unknown_headers_are_exact(self) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("error"),
        )
        self.assertEqual(response[0], 429)
        self.assertEqual(
            response[2],
            b'{"type":"error","error":{"type":"rate_limit_error",'
            b'"message":"fixture limit"},"request_id":"req_fixture"}',
        )
        response_headers = dict(response[1])
        self.assertEqual(response_headers["request-id"], "req_fixture")
        self.assertEqual(response_headers["x-upstream-unknown"], "preserved")

    def test_http_200_refusal_bytes_and_fixed_category_metrics_are_exact(
        self,
    ) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("refusal"),
        )
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], REFUSAL_RESPONSE_BODY)
        self.assertTrue(
            wait_for(
                lambda: self.metrics.snapshot()["counters"][
                    "provider_refusal_responses"
                ]
                == 1
            )
        )
        snapshot = self.metrics.snapshot()
        counters = snapshot["counters"]
        self.assertEqual(counters["provider_refusal_responses"], 1)
        self.assertEqual(counters["provider_refusal_category_cyber"], 1)
        self.assertEqual(counters["provider_fallback_transitions"], 0)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)
        serialized = json.dumps(snapshot)
        self.assertNotIn("fixture explanation", serialized)
        self.assertNotIn("claude-fable-fixture", serialized)

    def test_http_200_fallback_bytes_transition_and_served_metrics_are_exact(
        self,
    ) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("fallback"),
        )
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], FALLBACK_RESPONSE_BODY)
        self.assertTrue(
            wait_for(
                lambda: self.metrics.snapshot()["counters"][
                    "provider_fallback_served_responses"
                ]
                == 1
            )
        )
        snapshot = self.metrics.snapshot()
        counters = snapshot["counters"]
        self.assertEqual(counters["provider_refusal_responses"], 0)
        self.assertEqual(counters["provider_fallback_transitions"], 1)
        self.assertEqual(counters["provider_fallback_served_responses"], 1)
        self.assertNotIn("claude-fallback-fixture", json.dumps(snapshot))

    def test_sse_bytes_unknown_events_stream_errors_and_fallback_model_survive(
        self,
    ) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("sse"),
        )
        self.assertEqual(response[0], 200)
        self.assertIn(b"event: future_event\n", response[2])
        self.assertIn(b'"type":"error"', response[2])
        self.assertTrue(response[2].endswith(b'data: {"type":"message_stop"}\n\n'))
        self.assertTrue(
            wait_for(lambda: bool(self.metrics.snapshot()["models"]["returned"]))
        )
        snapshot = self.metrics.snapshot()
        self.assertEqual(list(snapshot["models"]["returned"].values()), [1])
        self.assertNotIn("claude-fallback", json.dumps(snapshot))
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 12)
        self.assertEqual(snapshot["counters"]["provider_output_tokens"], 4)
        self.assertEqual(snapshot["counters"]["sse_error_events"], 1)
        self.assertEqual(snapshot["counters"]["provider_fallback_transitions"], 1)
        self.assertEqual(
            snapshot["counters"]["provider_fallback_served_responses"], 1
        )

    def test_unsupported_transfer_coding_is_rejected_not_corrupted(self) -> None:
        response = self._send(
            self.gateway.server_port,
            request_body(),
            self._headers("unsupported-transfer"),
        )
        self.assertEqual(response[0], 502)
        self.assertIn(b"unsupported upstream response framing", response[2])
        self.assertEqual(
            self.metrics.snapshot()["counters"]["upstream_framing_rejected"], 1
        )

    def test_duplicate_content_lengths_are_rejected_in_both_orders(self) -> None:
        for mode in ("duplicate-length-short-first", "duplicate-length-long-first"):
            with self.subTest(mode=mode):
                response = self._send(
                    self.gateway.server_port,
                    request_body(),
                    self._headers(mode),
                )
                self.assertEqual(response[0], 502)
                self.assertIn(b"unsupported upstream response framing", response[2])
        self.assertEqual(
            self.metrics.snapshot()["counters"]["upstream_framing_rejected"], 2
        )

    def test_truncated_upstream_body_does_not_record_partial_telemetry(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request(
            "POST",
            "/v1/messages",
            body=request_body(),
            headers=self._headers("truncated"),
        )
        response = connection.getresponse()
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        connection.close()
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["models"]["returned"], {})
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 0)
        self.assertEqual(snapshot["counters"]["response_telemetry_unavailable"], 1)
        self.assertEqual(snapshot["counters"]["upstream_response_read_failures"], 1)

    def test_metrics_are_aggregate_only(self) -> None:
        self._send(self.gateway.server_port, request_body(), self._headers())
        self.assertTrue(self.engine.submitted.wait(timeout=5))
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request("GET", "/__prompt_toon/metrics")
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertNotIn(b"unit-test-credential", payload)
        self.assertNotIn(b"first typed result", payload)
        self.assertNotIn(b"AUTHORITY_MARKER", payload)
        self.assertNotIn(b"claude-requested", payload)
        self.assertNotIn(b"claude-returned", payload)
        metrics = json.loads(payload)
        self.assertEqual(metrics["mode"], "shadow")
        self.assertEqual(list(metrics["models"]["requested"].values()), [1])
        self.assertEqual(list(metrics["models"]["returned"].values()), [1])
        self.assertEqual(metrics["counters"]["provider_input_tokens"], 21)

    def test_readiness_and_connectivity_probe_track_local_and_upstream_state(
        self,
    ) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request("HEAD", "/")
        head = connection.getresponse()
        self.assertEqual(head.status, 204)
        self.assertEqual(head.read(), b"")
        connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request("GET", "/__prompt_toon/ready")
        ready = connection.getresponse()
        before = json.loads(ready.read())
        connection.close()
        self.assertEqual(ready.status, 200)
        self.assertEqual(before["status"], "ready")
        self.assertTrue(before["accepting"])
        self.assertTrue(before["engine_available"])
        self.assertEqual(before["upstream"], "unknown")

        response = self._send(
            self.gateway.server_port, request_body(), self._headers("sse")
        )
        self.assertEqual(response[0], 200)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request("GET", "/__prompt_toon/ready")
        observed = connection.getresponse()
        after = json.loads(observed.read())
        connection.close()
        self.assertEqual(observed.status, 200)
        self.assertEqual(after["upstream"], "reachable")


class GatewayBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = GatewayPolicy.load(POLICY_PATH)

    def test_only_policy_known_correlated_results_are_selected(self) -> None:
        value = {
            "model": "claude-requested",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "m1",
                            "name": "mcp__linear__get_issue",
                        },
                        {"type": "tool_use", "id": "m2", "name": "NotDeclared"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "m1",
                            "content": "linear",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "m2",
                            "content": "unknown",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "missing",
                            "content": "orphan",
                        },
                    ],
                },
            ],
        }
        parsed = parse_message_for_shadow(
            json.dumps(value).encode("utf-8"), self.policy
        )
        self.assertEqual([doc["body"] for doc in parsed.docs], ["linear"])
        self.assertEqual(parsed.docs[0]["trust_tier"], "semi_trusted_service")
        self.assertIn("anthropic:mcp:linear:", parsed.docs[0]["source"])

    def test_conflicting_duplicate_tool_ids_are_ignored_fail_closed(self) -> None:
        value = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "same", "name": "Task"},
                        {"type": "tool_use", "id": "same", "name": "Read"},
                        {"type": "tool_use", "id": "same", "name": "Task"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "same",
                            "content": "ambiguous",
                        }
                    ],
                },
            ]
        }
        parsed = parse_message_for_shadow(
            json.dumps(value).encode("utf-8"), self.policy
        )
        self.assertEqual(parsed.docs, [])

    def test_non_loopback_bind_and_cleartext_remote_upstream_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            GatewayConfig("0.0.0.0", 8787, "https://api.anthropic.com", 10, 1)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            GatewayConfig("127.0.0.1", 8787, "http://example.com", 10, 1)
        with self.assertRaisesRegex(ValueError, "invalid port"):
            GatewayConfig(
                "127.0.0.1", 8787, "https://api.anthropic.com:secret", 10, 1
            )
        with self.assertRaisesRegex(ValueError, "aggregate ingress"):
            GatewayConfig(
                "127.0.0.1",
                8787,
                "https://api.anthropic.com",
                10,
                1,
                max_inflight_request_bytes=0,
            )

    def test_model_telemetry_cardinality_is_bounded(self) -> None:
        metrics = GatewayMetrics()
        for index in range(50):
            metrics.record_request(f"model-{index}", 0)
            metrics.record_provider_response(
                status=200,
                returned_model=f"returned-{index}",
                usage={},
                stream_errors=0,
                telemetry_available=True,
            )
        snapshot = metrics.snapshot()
        self.assertEqual(len(snapshot["models"]["requested"]), 33)
        self.assertEqual(len(snapshot["models"]["returned"]), 33)
        self.assertEqual(snapshot["models"]["requested"]["__other__"], 18)
        self.assertEqual(snapshot["models"]["returned"]["__other__"], 18)

    def test_localhost_alias_creates_a_loopback_server(self) -> None:
        metrics = GatewayMetrics()
        engine = FakeEngine()
        analyzer = ShadowAnalyzer(self.policy, metrics, engine)
        server = create_gateway_server(
            GatewayConfig(
                "localhost",
                0,
                "https://api.anthropic.com",
                self.policy.max_request_bytes,
                1,
            ),
            analyzer,
            metrics,
        )
        server.server_close()
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_gateway_can_restart_on_the_same_loopback_port(self) -> None:
        def create(port: int) -> tuple[Any, threading.Thread]:
            metrics = GatewayMetrics()
            analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
            server = create_gateway_server(
                GatewayConfig(
                    "127.0.0.1",
                    port,
                    "https://api.anthropic.com",
                    self.policy.max_request_bytes,
                    1,
                ),
                analyzer,
                metrics,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return server, thread

        first, first_thread = create(0)
        port = first.server_port
        first.shutdown()
        first_thread.join(timeout=5)
        first.server_close()
        self.assertFalse(first_thread.is_alive())

        second, second_thread = create(port)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/__prompt_toon/ready")
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["status"], "ready")
        finally:
            second.shutdown()
            second_thread.join(timeout=5)
            second.server_close()

    def test_readiness_reports_transport_failure_without_conflating_local_state(
        self,
    ) -> None:
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        upstream_port = unused.getsockname()[1]
        unused.close()

        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        server = create_gateway_server(
            GatewayConfig(
                "127.0.0.1",
                0,
                f"http://127.0.0.1:{upstream_port}",
                self.policy.max_request_bytes,
                1,
            ),
            analyzer,
            metrics,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connection.request(
                "POST",
                "/v1/messages",
                body=request_body(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 502)

            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connection.request("GET", "/__prompt_toon/ready")
            readiness_response = connection.getresponse()
            readiness = json.loads(readiness_response.read())
            connection.close()
            self.assertEqual(readiness_response.status, 200)
            self.assertEqual(readiness["status"], "ready")
            self.assertEqual(readiness["upstream"], "failed")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_sse_multiline_event_observation_is_bounded(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed((b"data: " + b"x" * 58 + b"\n") * 20_000)
        self.assertFalse(observer._enabled)
        self.assertEqual(observer._event_data, [])
        self.assertEqual(observer._event_bytes, 0)

    def test_sse_without_message_stop_does_not_record_partial_usage(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'event: message_start\ndata: {"type":"message_start","message":'
            b'{"model":"claude-partial","usage":{"input_tokens":99}}}\n\n'
        )
        observer.finish()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["models"]["returned"], {})
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 0)
        self.assertEqual(snapshot["counters"]["response_telemetry_unavailable"], 1)

    def test_sse_unterminated_message_stop_is_discarded_at_eof(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'event: message_start\ndata: {"type":"message_start","message":'
            b'{"model":"claude-partial","usage":{"input_tokens":99}}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}'
        )
        observer.finish()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["models"]["returned"], {})
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 0)
        self.assertEqual(snapshot["counters"]["response_telemetry_unavailable"], 1)

    def test_sse_message_delta_refusal_is_recorded_only_after_message_stop(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'event: message_start\ndata: {"type":"message_start","message":'
            b'{"model":"claude-stream-fixture","usage":{"input_tokens":55}}}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","delta":'
            b'{"stop_reason":"refusal","stop_details":{"type":"refusal",'
            b'"category":"reasoning_extraction","explanation":'
            b'"unstable provider text"}},"usage":{"output_tokens":2}}\n\n'
        )
        self.assertEqual(
            metrics.snapshot()["counters"]["provider_refusal_responses"], 0
        )
        observer.feed(
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        observer.finish()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["counters"]["provider_refusal_responses"], 1)
        self.assertEqual(
            snapshot["counters"][
                "provider_refusal_category_reasoning_extraction"
            ],
            1,
        )
        self.assertNotIn("unstable provider text", json.dumps(snapshot))
        self.assertNotIn("claude-stream-fixture", json.dumps(snapshot))

    def test_malformed_json_fallback_envelope_contributes_no_telemetry(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "application/json")]
        )
        observer.feed(b'{"content":[{"type":"fallback"}]}')
        observer.finish()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["provider_fallback_transitions"], 0)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)
        self.assertEqual(counters["response_telemetry_unavailable"], 1)

    def test_sse_without_final_delta_contributes_no_fallback_telemetry(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'data: {"type":"message_start","message":{"model":'
            b'"claude-stream-fixture","usage":{"input_tokens":1}}}\n\n'
            b'data: {"type":"content_block_start","content_block":'
            b'{"type":"fallback","to":{"model":"claude-fallback"}}}\n\n'
            b'data: {"type":"message_stop"}\n\n'
        )
        observer.finish()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["provider_fallback_transitions"], 0)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)
        self.assertEqual(counters["response_telemetry_unavailable"], 1)

    def test_unknown_sse_event_cannot_inject_fallback_fields(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'data: {"type":"message_start","message":{"model":'
            b'"claude-stream-fixture","usage":{"input_tokens":1}}}\n\n'
            b'data: {"type":"future_event","content_block":{"type":'
            b'"fallback"},"usage":{"iterations":[{"type":'
            b'"fallback_message"}]}}\n\n'
            b'data: {"type":"message_delta","delta":{"stop_reason":'
            b'"end_turn","stop_details":null},"usage":{"output_tokens":1}}\n\n'
            b'data: {"type":"message_stop"}\n\n'
        )
        observer.finish()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["provider_fallback_transitions"], 0)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)
        self.assertEqual(counters.get("response_telemetry_unavailable", 0), 0)

    def test_post_stop_sse_event_invalidates_safety_telemetry(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "text/event-stream")]
        )
        observer.feed(
            b'data: {"type":"message_start","message":{"model":'
            b'"claude-stream-fixture","usage":{"input_tokens":1}}}\n\n'
            b'data: {"type":"message_delta","delta":{"stop_reason":'
            b'"refusal","stop_details":{"category":"cyber"}},"usage":{}}\n\n'
            b'data: {"type":"message_stop"}\n\n'
            b'data: {"type":"message_delta","delta":{"stop_reason":'
            b'"end_turn","stop_details":null},"usage":{"iterations":'
            b'[{"type":"fallback_message"}]}}\n\n'
        )
        observer.finish()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["provider_refusal_responses"], 0)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)
        self.assertEqual(counters["response_telemetry_unavailable"], 1)

    def test_refusal_categories_are_fixed_and_unknown_values_map_to_other(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        for category in (
            "cyber",
            "bio",
            "frontier_llm",
            "reasoning_extraction",
            "new",
        ):
            observer = ResponseObserver(
                metrics, 200, [("Content-Type", "application/json")]
            )
            observer.feed(
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-fixture",
                        "content": [],
                        "stop_reason": "refusal",
                        "stop_details": {"type": "refusal", "category": category},
                        "usage": {},
                    }
                ).encode("utf-8")
            )
            observer.finish()
        counters = metrics.snapshot()["counters"]
        category_counters = {
            name: value
            for name, value in counters.items()
            if name.startswith("provider_refusal_category_")
        }
        self.assertEqual(
            category_counters,
            {
                "provider_refusal_category_bio": 1,
                "provider_refusal_category_cyber": 1,
                "provider_refusal_category_frontier_llm": 1,
                "provider_refusal_category_other": 1,
                "provider_refusal_category_reasoning_extraction": 1,
            },
        )

    def test_final_refusal_after_fallback_is_not_counted_as_fallback_served(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics, 200, [("Content-Type", "application/json")]
        )
        observer.feed(
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-final-fixture",
                    "content": [
                        {
                            "type": "fallback",
                            "from": {"model": "claude-first-fixture"},
                            "to": {"model": "claude-final-fixture"},
                        }
                    ],
                    "stop_reason": "refusal",
                    "stop_details": None,
                    "usage": {"iterations": [{"type": "fallback_message"}]},
                }
            ).encode("utf-8")
        )
        observer.finish()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["provider_refusal_responses"], 1)
        self.assertEqual(counters["provider_refusal_category_other"], 1)
        self.assertEqual(counters["provider_fallback_transitions"], 1)
        self.assertEqual(counters["provider_fallback_served_responses"], 0)

    def test_stuck_shadow_future_times_out_and_quarantines_engine(self) -> None:
        metrics = GatewayMetrics()
        engine = NeverCompletesEngine()
        analyzer = ShadowAnalyzer(
            self.policy,
            metrics,
            engine,
            completion_timeout_seconds=0.05,
        )
        self.addCleanup(analyzer.close)
        analyzer.observe_request(request_body())
        self.assertTrue(engine.submitted.wait(timeout=1))
        self.assertTrue(engine.closed_event.wait(timeout=1))
        self.assertFalse(analyzer.engine_available)
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["shadow_timeouts"], 1)
        self.assertEqual(counters["shadow_failed"], 1)

    def test_terminal_engine_failure_quarantines_readiness_and_ownership(self) -> None:
        local_token = "terminal-local-" + "t" * 32
        upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), FakeUpstreamHandler
        )
        upstream.state = UpstreamState()  # type: ignore[attr-defined]
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        metrics = GatewayMetrics()
        engine = TerminalFailureEngine()
        analyzer = ShadowAnalyzer(self.policy, metrics, engine)
        server = create_gateway_server(
            GatewayConfig(
                "127.0.0.1",
                0,
                f"http://127.0.0.1:{upstream.server_port}",
                self.policy.max_request_bytes,
                1,
                auth=GatewayAuth(local_token, "provider-fixture-secret"),
                policy_sha256="a" * 64,
                resident_binary_sha256="b" * 64,
            ),
            analyzer,
            metrics,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connection.request(
                "POST",
                "/v1/messages",
                body=request_body(),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": local_token,
                },
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(wait_for(lambda: not analyzer.engine_available))

            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connection.request("GET", "/__prompt_toon/ready")
            response = connection.getresponse()
            readiness = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 503)
            self.assertFalse(readiness["engine_available"])

            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connection.request(
                "GET",
                "/__prompt_toon/ownership",
                headers={"X-Prompt-Toon-Challenge": "e" * 64},
            )
            response = connection.getresponse()
            ownership = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertFalse(ownership["engine_available"])
            self.assertEqual(
                metrics.snapshot()["counters"]["shadow_engine_terminal_failures"],
                1,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            upstream.shutdown()
            upstream_thread.join(timeout=5)
            upstream.server_close()

    def test_aggregate_ingress_budget_rejects_before_second_upstream(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        upstream_requests = 0
        upstream_lock = threading.Lock()

        class HoldingUpstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                nonlocal upstream_requests
                self.rfile.read(int(self.headers["Content-Length"]))
                with upstream_lock:
                    upstream_requests += 1
                entered.set()
                release.wait(5)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HoldingUpstream)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        gateway = create_gateway_server(
            GatewayConfig(
                "127.0.0.1",
                0,
                f"http://127.0.0.1:{upstream.server_port}",
                64,
                2,
                max_inflight_request_bytes=64,
            ),
            analyzer,
            metrics,
        )
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        first_result: list[int] = []

        def send_first() -> None:
            connection = http.client.HTTPConnection(
                "127.0.0.1", gateway.server_port, timeout=5
            )
            connection.request("POST", "/v1/messages", body=b"x" * 48)
            response = connection.getresponse()
            response.read()
            first_result.append(response.status)
            connection.close()

        first_thread = threading.Thread(target=send_first)
        first_thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(gateway.inflight_request_bytes, 48)
            connection = http.client.HTTPConnection(
                "127.0.0.1", gateway.server_port, timeout=5
            )
            connection.request("POST", "/v1/messages", body=b"y" * 17)
            response = connection.getresponse()
            body = response.read()
            connection.close()
            self.assertEqual(response.status, 503)
            self.assertIn(b"overloaded_error", body)
            with upstream_lock:
                self.assertEqual(upstream_requests, 1)
            self.assertEqual(
                metrics.snapshot()["counters"]["ingress_byte_budget_rejected"],
                1,
            )
        finally:
            release.set()
            first_thread.join(timeout=5)
            gateway.shutdown()
            gateway_thread.join(timeout=5)
            gateway.server_close()
            upstream.shutdown()
            upstream_thread.join(timeout=5)
            upstream.server_close()
        self.assertEqual(first_result, [200])
        self.assertEqual(gateway.inflight_request_bytes, 0)


class GatewayFailureIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GatewayPolicy.load(POLICY_PATH)
        self.upstream_state = UpstreamState()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), FakeUpstreamHandler
        )
        self.upstream.state = self.upstream_state  # type: ignore[attr-defined]
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()
        self.servers: list[tuple[Any, threading.Thread]] = []

    def tearDown(self) -> None:
        for server, thread in self.servers:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.upstream.shutdown()
        self.upstream_thread.join(timeout=5)
        self.upstream.server_close()

    def start_gateway(
        self,
        analyzer: ShadowAnalyzer,
        metrics: GatewayMetrics,
        *,
        max_request_bytes: int,
        max_concurrent_requests: int = 2,
        ingress_header_timeout_seconds: float = 10.0,
        ingress_body_timeout_seconds: float = 30.0,
        auth: GatewayAuth | None = None,
        policy_sha256: str | None = None,
        resident_binary_sha256: str | None = None,
    ) -> Any:
        server = create_gateway_server(
            GatewayConfig(
                "127.0.0.1",
                0,
                f"http://127.0.0.1:{self.upstream.server_port}",
                max_request_bytes,
                max_concurrent_requests,
                auth=auth,
                policy_sha256=policy_sha256,
                resident_binary_sha256=resident_binary_sha256,
                ingress_header_timeout_seconds=ingress_header_timeout_seconds,
                ingress_body_timeout_seconds=ingress_body_timeout_seconds,
            ),
            analyzer,
            metrics,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    @staticmethod
    def send(
        port: int,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        connection.request(
            "POST",
            "/v1/messages",
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_missing_engine_fails_open_for_shadow_only(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer, metrics, max_request_bytes=self.policy.max_request_bytes
        )
        status, _ = self.send(server.server_port, request_body())
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream_state.requests[-1]["body"], request_body())
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["shadow_skipped_engine_unavailable"], 1)

        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request("GET", "/__prompt_toon/ready")
        response = connection.getresponse()
        readiness = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 503)
        self.assertEqual(readiness["status"], "not_ready")
        self.assertFalse(readiness["engine_available"])
        self.assertEqual(readiness["upstream"], "reachable")

    def test_oversize_request_is_rejected_before_upstream(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        server = self.start_gateway(analyzer, metrics, max_request_bytes=32)
        status, response = self.send(server.server_port, b"x" * 33)
        self.assertEqual(status, 413)
        self.assertIn(b"request_too_large", response)
        self.assertEqual(self.upstream_state.requests, [])

    def test_invalid_json_still_reaches_provider_unchanged(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        server = self.start_gateway(
            analyzer, metrics, max_request_bytes=self.policy.max_request_bytes
        )
        body = b'{"model":"claude-requested","messages":['
        status, _ = self.send(server.server_port, body)
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream_state.requests[-1]["body"], body)
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["shadow_request_parse_errors"], 1)

    def test_local_endpoint_closes_instead_of_parsing_unread_body_as_request(
        self,
    ) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer, metrics, max_request_bytes=self.policy.max_request_bytes
        )
        client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        client.sendall(
            b"GET /__prompt_toon/health HTTP/1.1\r\n"
            b"Host: localhost\r\nContent-Length: 4\r\n\r\nJUNK"
            b"GET /__prompt_toon/metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        chunks = bytearray()
        while True:
            part = client.recv(4096)
            if not part:
                break
            chunks += part
        client.close()
        self.assertEqual(chunks.count(b"HTTP/1.1"), 1)
        self.assertIn(b"Connection: close", chunks)

    def test_request_body_deadline_returns_408(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            ingress_body_timeout_seconds=0.05,
        )
        client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        client.sendall(
            b"POST /v1/messages HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: 10\r\n\r\nx"
        )
        response = bytearray()
        while True:
            part = client.recv(4096)
            if not part:
                break
            response += part
        client.close()
        self.assertIn(b"HTTP/1.1 408", response)
        self.assertIn(b"request body deadline exceeded", response)

    def test_connection_admission_is_bounded_before_threads(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            max_concurrent_requests=1,
            ingress_header_timeout_seconds=2.0,
        )
        held = []
        for _ in range(3):
            client = socket.create_connection(
                ("127.0.0.1", server.server_port), timeout=2
            )
            client.sendall(b"G")
            held.append(client)
        self.assertTrue(wait_for(lambda: server.active_connection_count == 3))
        rejected = socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=2
        )
        response = rejected.recv(4096)
        for client in held:
            client.close()
        rejected.close()
        self.assertIn(b"503 Service Unavailable", response)
        self.assertEqual(
            metrics.snapshot()["counters"]["ingress_connections_rejected"], 1
        )

    def test_incomplete_headers_are_closed_at_absolute_deadline(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            ingress_header_timeout_seconds=0.05,
        )
        client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        client.sendall(b"GET /v1/messages HTTP/1.1\r\nHost:")
        self.assertEqual(client.recv(4096), b"")
        client.close()
        self.assertEqual(metrics.snapshot()["counters"]["ingress_header_timeouts"], 1)

    def test_rejected_post_closes_with_unread_body(self) -> None:
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, None)
        server = self.start_gateway(
            analyzer, metrics, max_request_bytes=self.policy.max_request_bytes
        )
        client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        client.sendall(
            b"POST /v1/other HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: 4\r\n\r\nJUNK"
            b"GET /__prompt_toon/metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        response = bytearray()
        while True:
            part = client.recv(4096)
            if not part:
                break
            response += part
        client.close()
        self.assertEqual(response.count(b"HTTP/1.1"), 1)
        self.assertIn(b"Connection: close", response)

    def test_managed_auth_terminates_local_token_and_proves_port_ownership(
        self,
    ) -> None:
        local_token = "local-" + "l" * 40
        upstream_token = "provider-fixture-secret"
        auth = GatewayAuth(local_token, upstream_token)
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        server = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            auth=auth,
            policy_sha256="a" * 64,
            resident_binary_sha256="b" * 64,
        )

        for supplied in (None, "wrong-local-token"):
            headers = {} if supplied is None else {"x-api-key": supplied}
            status, response = self.send(server.server_port, request_body(), headers)
            self.assertEqual(status, 401)
            self.assertIn(b"authentication_error", response)
        self.assertEqual(self.upstream_state.requests, [])

        status, _ = self.send(
            server.server_port,
            request_body(),
            {
                "x-api-key": local_token,
                "Authorization": "Bearer must-not-leak",
                "x-future-feature": "preserved",
            },
        )
        self.assertEqual(status, 200)
        forwarded = {
            name.lower(): value
            for name, value in self.upstream_state.requests[-1]["headers"]
        }
        self.assertEqual(forwarded["x-api-key"], upstream_token)
        self.assertNotIn("authorization", forwarded)
        self.assertEqual(forwarded["x-future-feature"], "preserved")
        self.assertNotIn(local_token, repr(self.upstream_state.requests[-1]))

        owner = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        challenge = "c" * 64
        owner.request(
            "GET",
            "/__prompt_toon/ownership",
            headers={"X-Prompt-Toon-Challenge": challenge},
        )
        response = owner.getresponse()
        ownership = json.loads(response.read())
        owner.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(ownership["status"], "owned")
        self.assertEqual(ownership["provider"], "anthropic")
        self.assertEqual(ownership["auth_mode"], "split")
        self.assertEqual(ownership["policy_sha256"], "a" * 64)
        self.assertEqual(ownership["resident_binary_sha256"], "b" * 64)
        self.assertEqual(
            ownership["ownership_endpoint"],
            f"http://127.0.0.1:{server.server_port}/__prompt_toon/ownership",
        )
        attestation = dict(ownership)
        challenge_response = attestation.pop("challenge_response")
        self.assertEqual(
            challenge_response,
            hmac.new(
                local_token.encode(),
                ownership_attestation_message(challenge, attestation),
                "sha256",
            ).hexdigest(),
        )
        self.assertEqual(metrics.snapshot()["counters"]["client_auth_rejected"], 2)

    def test_doctor_authenticates_real_managed_gateway_without_echoing_token(
        self,
    ) -> None:
        local_token = "doctor-local-" + "l" * 32
        auth = GatewayAuth(local_token, "provider-fixture-secret")
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        server = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            auth=auth,
            policy_sha256="a" * 64,
            resident_binary_sha256="b" * 64,
        )

        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "client-token"
            token_file.write_text(local_token + "\n", encoding="ascii")
            os.chmod(token_file, 0o600)
            status = gateway_doctor_status(
                f"http://127.0.0.1:{server.server_port}",
                provider="anthropic",
                client_token_file=token_file,
                timeout=2,
            )

        self.assertTrue(status["ownership"]["authenticated"])
        self.assertEqual(status["ownership"]["payload"]["status"], "owned")
        self.assertEqual(status["ownership"]["payload"]["policy_sha256"], "a" * 64)
        self.assertEqual(
            status["ownership"]["payload"]["resident_binary_sha256"], "b" * 64
        )
        self.assertNotIn("challenge_response", status["ownership"]["payload"])
        self.assertEqual(status["readiness"]["payload"]["status"], "ready")
        self.assertEqual(status["metrics"]["payload"]["mode"], "shadow")
        self.assertNotIn(local_token, repr(status))

    def test_doctor_rejects_relayed_mac_with_forged_attestation(self) -> None:
        local_token = "relay-local-" + "r" * 32
        metrics = GatewayMetrics()
        analyzer = ShadowAnalyzer(self.policy, metrics, FakeEngine())
        real = self.start_gateway(
            analyzer,
            metrics,
            max_request_bytes=self.policy.max_request_bytes,
            auth=GatewayAuth(local_token, "provider-fixture-secret"),
            policy_sha256="a" * 64,
            resident_binary_sha256="b" * 64,
        )

        class RelayHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                if self.path == "/__prompt_toon/ownership":
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", real.server_port, timeout=2
                    )
                    connection.request(
                        "GET",
                        self.path,
                        headers={
                            "X-Prompt-Toon-Challenge": self.headers[
                                "X-Prompt-Toon-Challenge"
                            ]
                        },
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    connection.close()
                    payload["policy_sha256"] = "f" * 64
                    payload["ownership_endpoint"] = (
                        f"http://127.0.0.1:{self.server.server_port}{self.path}"
                    )
                elif self.path == "/__prompt_toon/ready":
                    payload = {
                        "status": "ready",
                        "mode": "shadow",
                        "provider": "anthropic",
                        "accepting": True,
                        "engine_available": True,
                        "upstream": "unknown",
                    }
                else:
                    payload = {
                        "schema_version": 1,
                        "mode": "shadow",
                        "counters": {},
                        "quality": {},
                    }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        relay = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                token_file = Path(tmp) / "client-token"
                token_file.write_text(local_token, encoding="ascii")
                os.chmod(token_file, 0o600)
                status = gateway_doctor_status(
                    f"http://127.0.0.1:{relay.server_port}",
                    provider="anthropic",
                    client_token_file=token_file,
                    timeout=2,
                )
            self.assertFalse(status["ownership"]["authenticated"])
            self.assertEqual(
                status["ownership"]["payload"]["policy_sha256"], "f" * 64
            )
            self.assertNotIn(local_token, repr(status))
        finally:
            relay.shutdown()
            relay_thread.join(timeout=5)
            relay.server_close()


class GatewayAuthFileTests(unittest.TestCase):
    def test_token_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "client-token"
            upstream = Path(tmp) / "upstream-token"
            os.mkfifo(client, 0o600)
            upstream.write_text("provider-secret", encoding="ascii")
            os.chmod(upstream, 0o600)
            with self.assertRaisesRegex(ValueError, "regular file"):
                GatewayAuth.from_files(client, upstream)

    def test_owner_only_distinct_files_load_without_secret_repr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "client-token"
            upstream = Path(tmp) / "upstream-token"
            client.write_text("c" * 40 + "\n", encoding="ascii")
            upstream.write_text("provider-secret\n", encoding="ascii")
            os.chmod(client, 0o600)
            os.chmod(upstream, 0o600)
            auth = GatewayAuth.from_files(client, upstream)
            self.assertIsNotNone(auth)
            self.assertNotIn("provider-secret", repr(auth))

    def test_split_auth_requires_both_secure_distinct_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "client-token"
            upstream = Path(tmp) / "upstream-token"
            client.write_text("c" * 40, encoding="ascii")
            upstream.write_text("provider-secret", encoding="ascii")
            os.chmod(client, 0o600)
            os.chmod(upstream, 0o600)
            with self.assertRaisesRegex(ValueError, "requires both"):
                GatewayAuth.from_files(client, None)
            os.chmod(upstream, 0o644)
            with self.assertRaisesRegex(ValueError, "group or other"):
                GatewayAuth.from_files(client, upstream)
            os.chmod(upstream, 0o600)
            upstream.write_text("c" * 40, encoding="ascii")
            with self.assertRaisesRegex(ValueError, "must be distinct"):
                GatewayAuth.from_files(client, upstream)

    def test_token_file_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real-token"
            link = Path(tmp) / "client-token"
            upstream = Path(tmp) / "upstream-token"
            real.write_text("c" * 40, encoding="ascii")
            upstream.write_text("provider-secret", encoding="ascii")
            os.chmod(real, 0o600)
            os.chmod(upstream, 0o600)
            link.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "opened safely"):
                GatewayAuth.from_files(link, upstream)


if __name__ == "__main__":
    unittest.main()
