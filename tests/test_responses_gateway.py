"""OpenAI Responses shadow-gateway protocol and trust-boundary tests."""

from __future__ import annotations

import http.client
import http.server
import json
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from prompt_toon.gateway import (
    OPENAI_PROTOCOL,
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ResponseObserver,
    ShadowAnalyzer,
    create_gateway_server,
    parse_response_for_shadow,
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

    def submit_condense_run(self, **kwargs: Any) -> Future:
        self.submissions.append(kwargs)
        future: Future = Future()
        future.set_result(
            (
                [
                    {"withheld": False, "cards": [{"claim": "condensed"}]}
                    for _ in kwargs["docs"]
                ],
                "condensed",
                {"id": kwargs["request_id"]},
            )
        )
        self.submitted.set()
        return future

    def close(self) -> None:
        return


class UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def append(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(value)


class ResponsesUpstreamHandler(http.server.BaseHTTPRequestHandler):
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
        if self.path.startswith("/v1/responses/compact"):
            compact = (
                b'{"id":"resp_compact","object":"response.compaction",'
                b'"x-future":{"preserved":true}}'
            )
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(compact)))
            self.send_header("x-upstream-unknown", "preserved")
            self.end_headers()
            self.wfile.write(compact)
            return
        events = b"".join(
            (
                b"event: response.created\n",
                b'data: {"type":"response.created","response":'
                b'{"id":"resp_fixture","model":"gpt-created"}}\n\n',
                b"event: response.output_text.delta\n",
                b'data: {"type":"response.output_text.delta",'
                b'"delta":"provider bytes"}\n\n',
                b"event: response.completed\n",
                b'data: {"type":"response.completed","response":'
                b'{"id":"resp_fixture","model":"gpt-returned","usage":'
                b'{"input_tokens":31,"output_tokens":7,"total_tokens":38,'
                b'"input_tokens_details":{"cached_tokens":11},'
                b'"output_tokens_details":{"reasoning_tokens":3}}}}\n\n',
            )
        )
        self.send_response_only(200, "OK")
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(events)))
        self.send_header("openai-model", "gpt-header")
        self.send_header("x-upstream-unknown", "preserved")
        self.end_headers()
        self.wfile.write(events)


def responses_body() -> bytes:
    return b"""{
  "model": "gpt-requested",
  "instructions": "AUTHORITY_MARKER",
  "tools": [{"type":"function","name":"shell_command","parameters":{"type":"object","x-future":true}}],
  "input": [
    {"type":"message","role":"user","content":[{"type":"input_text","text":"USER_MARKER"}]},
    {"type":"function_call","call_id":"call_known","name":"shell_command","arguments":"{}"},
    {"type":"function_call","call_id":"call_unknown","name":"UnknownTool","arguments":"{}"},
    {"type":"function_call_output","call_id":"call_known","output":"first typed result"},
    {"type":"function_call_output","call_id":"call_known","output":[
      {"type":"input_text","text":"second typed result"},
      {"type":"input_image","image_url":"IMAGE_MARKER"}
    ]},
    {"type":"function_call_output","call_id":"call_unknown","output":"UNKNOWN_MARKER"},
    {"type":"function_call_output","call_id":"call_orphan","output":"ORPHAN_MARKER"},
    {"type":"custom_tool_call_output","call_id":"call_custom","output":"CUSTOM_MARKER"}
  ],
  "stream": true,
  "store": false,
  "x-future-provider-field": {"nested":[1,2,3]}
}
"""


class ResponsesParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GatewayPolicy.load(POLICY_PATH)

    def test_selects_only_correlated_policy_known_text(self) -> None:
        parsed = parse_response_for_shadow(responses_body(), self.policy)
        self.assertEqual(parsed.provider, OPENAI_PROTOCOL)
        self.assertEqual(parsed.requested_model, "gpt-requested")
        self.assertEqual(
            [doc["body"] for doc in parsed.docs],
            ["first typed result", "second typed result"],
        )
        self.assertEqual(
            {doc["trust_tier"] for doc in parsed.docs},
            {"untrusted_tool_output"},
        )
        captured = json.dumps(parsed.docs)
        for marker in (
            "AUTHORITY_MARKER",
            "USER_MARKER",
            "IMAGE_MARKER",
            "UNKNOWN_MARKER",
            "ORPHAN_MARKER",
            "CUSTOM_MARKER",
        ):
            self.assertNotIn(marker, captured)

    def test_conflicting_duplicate_call_ids_fail_closed(self) -> None:
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "same",
                        "name": "shell_command",
                    },
                    {
                        "type": "function_call",
                        "call_id": "same",
                        "name": "exec_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "same",
                        "output": "must not be selected",
                    },
                ]
            }
        ).encode()
        self.assertEqual(parse_response_for_shadow(body, self.policy).docs, [])

    def test_non_unique_or_invalid_call_ids_fail_closed(self) -> None:
        for call_id, calls in (
            (
                "same",
                [
                    {"type": "function_call", "call_id": "same", "name": "shell_command"},
                    {"type": "function_call", "call_id": "same", "name": "shell_command"},
                ],
            ),
            ("", [{"type": "function_call", "call_id": "", "name": "shell_command"}]),
            (
                "x" * 1025,
                [
                    {
                        "type": "function_call",
                        "call_id": "x" * 1025,
                        "name": "shell_command",
                    }
                ],
            ),
        ):
            with self.subTest(call_id_length=len(call_id), declarations=len(calls)):
                body = json.dumps(
                    {
                        "input": calls
                        + [
                            {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": "must not be selected",
                            }
                        ]
                    }
                ).encode()
                self.assertEqual(parse_response_for_shadow(body, self.policy).docs, [])

    def test_duplicate_declared_after_output_still_fails_closed(self) -> None:
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "late-duplicate",
                        "name": "shell_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "late-duplicate",
                        "output": "must not be selected",
                    },
                    {
                        "type": "function_call",
                        "call_id": "late-duplicate",
                        "name": "shell_command",
                    },
                ]
            }
        ).encode()
        self.assertEqual(parse_response_for_shadow(body, self.policy).docs, [])


class ResponsesGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GatewayPolicy.load(POLICY_PATH)
        self.state = UpstreamState()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), ResponsesUpstreamHandler
        )
        self.upstream.state = self.state  # type: ignore[attr-defined]
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()

        self.metrics = GatewayMetrics()
        self.engine = FakeEngine()
        self.analyzer = ShadowAnalyzer(
            self.policy,
            self.metrics,
            self.engine,
            protocol=OPENAI_PROTOCOL,
        )
        self.gateway = create_gateway_server(
            GatewayConfig(
                listen_host="127.0.0.1",
                listen_port=0,
                upstream=f"http://127.0.0.1:{self.upstream.server_port}/v1",
                max_request_bytes=self.policy.max_request_bytes,
                max_concurrent_requests=self.policy.max_concurrent_streams,
                protocol=OPENAI_PROTOCOL,
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
    def _headers() -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer unit-test",
            "OpenAI-Organization": "org_fixture",
            "OpenAI-Project": "proj_fixture",
            "x-codex-turn-metadata": "preserve-me",
            "x-future-feature": "preserve-me-too",
        }

    @staticmethod
    def _send(
        port: int,
        body: bytes,
        *,
        path: str = "/v1/responses?fixture=true",
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST", path, body=body, headers=ResponsesGatewayTests._headers()
        )
        response = connection.getresponse()
        result = response.status, response.getheaders(), response.read()
        connection.close()
        return result

    def test_direct_vs_gateway_preserves_request_and_response_bytes(self) -> None:
        body = responses_body()
        direct = self._send(self.upstream.server_port, body)
        proxied = self._send(self.gateway.server_port, body)

        self.assertEqual(proxied, direct)
        direct_record, gateway_record = self.state.requests
        self.assertEqual(gateway_record["path"], "/v1/responses?fixture=true")
        self.assertEqual(gateway_record["body"], direct_record["body"])
        self.assertEqual(gateway_record["body"], body)
        forwarded = {name.lower(): value for name, value in gateway_record["headers"]}
        for name, value in self._headers().items():
            self.assertEqual(forwarded[name.lower()], value)

        self.assertTrue(self.engine.submitted.wait(timeout=5))
        self.assertEqual(
            [doc["body"] for doc in self.engine.submissions[0]["docs"]],
            ["first typed result", "second typed result"],
        )
        self.assertTrue(
            wait_for(lambda: bool(self.metrics.snapshot()["models"]["returned"]))
        )
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["counters"]["responses_requests"], 1)
        self.assertEqual(list(snapshot["models"]["requested"].values()), [1])
        self.assertEqual(list(snapshot["models"]["returned"].values()), [1])
        serialized = json.dumps(snapshot)
        self.assertNotIn("gpt-requested", serialized)
        self.assertNotIn("gpt-returned", serialized)
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 31)
        self.assertEqual(snapshot["counters"]["provider_output_tokens"], 7)
        self.assertEqual(snapshot["counters"]["provider_total_tokens"], 38)
        self.assertEqual(snapshot["counters"]["provider_cached_input_tokens"], 11)
        self.assertEqual(snapshot["counters"]["provider_reasoning_output_tokens"], 3)

    def test_compaction_is_opaque_and_never_enters_transform_lane(self) -> None:
        body = b'{"model":"gpt-requested","input":[{"type":"message"}]}'
        direct = self._send(
            self.upstream.server_port,
            body,
            path="/v1/responses/compact?fixture=true",
        )
        proxied = self._send(
            self.gateway.server_port,
            body,
            path="/v1/responses/compact?fixture=true",
        )
        self.assertEqual(proxied, direct)
        direct_record, gateway_record = self.state.requests
        self.assertEqual(gateway_record["body"], direct_record["body"])
        self.assertEqual(gateway_record["body"], body)
        self.assertEqual(gateway_record["path"], "/v1/responses/compact?fixture=true")
        self.assertFalse(self.engine.submitted.is_set())
        counters = self.metrics.snapshot()["counters"]
        self.assertEqual(counters["responses_compact_requests"], 1)
        self.assertEqual(counters.get("responses_requests", 0), 0)
        self.assertEqual(counters.get("upstream_responses", 0), 0)
        self.assertEqual(counters.get("response_telemetry_unavailable", 0), 0)

    def test_unsupported_path_uses_openai_error_shape(self) -> None:
        status, _headers, body = self._send(
            self.gateway.server_port, b"{}", path="/v1/files"
        )
        self.assertEqual(status, 404)
        value = json.loads(body)
        self.assertNotIn("type", value)
        self.assertEqual(value["error"]["type"], "not_found_error")
        self.assertIsNone(value["error"]["param"])
        self.assertIsNone(value["error"]["code"])
        self.assertEqual(self.state.requests, [])

    def test_readiness_identifies_openai_provider(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request("GET", "/__prompt_toon/ready")
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(value["provider"], OPENAI_PROTOCOL)

    def test_websocket_upgrade_fails_explicitly_and_is_counted(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5
        )
        connection.request(
            "GET",
            "/v1/responses",
            headers={"Connection": "Upgrade", "Upgrade": "websocket"},
        )
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 426)
        self.assertEqual(value["error"]["type"], "unsupported_transport_error")
        self.assertEqual(
            self.metrics.snapshot()["counters"]["websocket_upgrade_attempts"], 1
        )

    def test_incomplete_sse_is_terminal_error_without_complete_telemetry(self) -> None:
        metrics = GatewayMetrics()
        observer = ResponseObserver(
            metrics,
            200,
            [("Content-Type", "text/event-stream")],
            protocol=OPENAI_PROTOCOL,
        )
        observer.feed(
            b'data: {"type":"response.incomplete","response":'
            b'{"model":"private-model","usage":{"input_tokens":99}}}\n\n'
        )
        observer.finish()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["counters"]["sse_error_events"], 1)
        self.assertEqual(snapshot["counters"]["response_telemetry_unavailable"], 1)
        self.assertEqual(snapshot["counters"]["provider_input_tokens"], 0)
        self.assertEqual(snapshot["models"]["returned"], {})


if __name__ == "__main__":
    unittest.main()
