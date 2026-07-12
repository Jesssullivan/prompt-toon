"""Protocol and trust-boundary tests for the Anthropic shadow gateway."""

from __future__ import annotations

import http.client
import http.server
import json
import socket
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from prompt_toon.gateway import (
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ResponseObserver,
    ShadowAnalyzer,
    create_gateway_server,
    parse_message_for_shadow,
)


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "io.json"


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
            response_body = (
                b'{"model":"claude-partial","usage":{"input_tokens":77}}'
            )
            self.close_connection = True
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body) + 10))
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
                    b'data: {"type":"message_delta","usage":{"output_tokens":4}}\n\n',
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
        gateway_headers = {name.lower(): value for name, value in gateway_record["headers"]}
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

    def test_sse_bytes_unknown_events_stream_errors_and_fallback_model_survive(self) -> None:
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
            wait_for(
                lambda: self.metrics.snapshot()["models"]["returned"]
                == {"claude-fallback": 1}
            )
        )
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["models"]["returned"], {"claude-fallback": 1})
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 12)
        self.assertEqual(snapshot["counters"]["provider_output_tokens"], 4)
        self.assertEqual(snapshot["counters"]["sse_error_events"], 1)

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
        metrics = json.loads(payload)
        self.assertEqual(metrics["mode"], "shadow")
        self.assertEqual(metrics["models"]["requested"], {"claude-requested": 1})
        self.assertEqual(metrics["models"]["returned"], {"claude-returned": 1})
        self.assertEqual(metrics["counters"]["provider_input_tokens"], 21)

    def test_readiness_and_connectivity_probe_track_local_and_upstream_state(self) -> None:
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
                        {"type": "tool_use", "id": "m1", "name": "mcp__linear__get_issue"},
                        {"type": "tool_use", "id": "m2", "name": "NotDeclared"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "m1", "content": "linear"},
                        {"type": "tool_result", "tool_use_id": "m2", "content": "unknown"},
                        {"type": "tool_result", "tool_use_id": "missing", "content": "orphan"},
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
                        {"type": "tool_result", "tool_use_id": "same", "content": "ambiguous"}
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

    def test_readiness_reports_transport_failure_without_conflating_local_state(self) -> None:
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
    ) -> Any:
        server = create_gateway_server(
            GatewayConfig(
                "127.0.0.1",
                0,
                f"http://127.0.0.1:{self.upstream.server_port}",
                max_request_bytes,
                max_concurrent_requests,
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
    def send(port: int, body: bytes) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            "/v1/messages",
            body=body,
            headers={"Content-Type": "application/json"},
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

    def test_local_endpoint_closes_instead_of_parsing_unread_body_as_request(self) -> None:
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
        self.assertTrue(
            wait_for(lambda: server.active_connection_count == 3)
        )
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


if __name__ == "__main__":
    unittest.main()
