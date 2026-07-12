#!/usr/bin/env python3
"""Unbilled Claude Code -> gateway -> scripted SSE upstream probe."""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from prompt_toon.claude_harness import (
    build_claude_command,
    build_claude_env,
    claude_profile,
    run_claude,
)
from prompt_toon.gateway import (
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ShadowAnalyzer,
    create_gateway_server,
)


_MARKER = "PROMPT_TOON_CLAUDE_HARNESS_OK"


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for name, data in events
    ).encode("utf-8")


class _ProbeEngine:
    def __init__(self) -> None:
        self.closed = False

    def submit_condense_run(self, *, docs: list[dict[str, str]], **_: Any) -> Future:
        future: Future = Future()
        future.set_result(
            (
                [
                    {
                        "withheld": False,
                        "cards": [{"claim": "bounded harness probe"}],
                    }
                    for _doc in docs
                ],
                "probe",
                {"documents": len(docs)},
            )
        )
        return future

    def close(self) -> None:
        self.closed = True


class _UpstreamState:
    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def append(self, item: dict[str, Any]) -> int:
        with self.lock:
            self.requests.append(item)
            return len(self.requests)

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in self.requests]


class _ScriptedUpstream(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: _UpstreamState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), _ScriptedHandler)


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _ScriptedUpstream

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_HEAD(self) -> None:
        self.send_response_only(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            self.send_error(400)
            return
        value = json.loads(self.rfile.read(int(lengths[0])))
        messages = value.get("messages", []) if isinstance(value, dict) else []
        has_tool_result = any(
            isinstance(message, dict)
            and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in (
                    message.get("content")
                    if isinstance(message.get("content"), list)
                    else []
                )
            )
            for message in messages
        )
        sequence = self.server.state.append(
            {
                "path": self.path,
                "stream": value.get("stream") if isinstance(value, dict) else None,
                "model": value.get("model") if isinstance(value, dict) else None,
                "has_tool_result": has_tool_result,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
            }
        )
        model = value.get("model", "claude-sonnet-4-6")
        if has_tool_result:
            events = self._final_events(model)
        else:
            events = self._tool_events(model)
        raw = _sse(events)
        self.send_response_only(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("request-id", f"req_prompt_toon_probe_{sequence}")
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()

    def _tool_events(self, model: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_prompt_toon_probe_1",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 100, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_prompt_toon_probe",
                        "name": "Read",
                        "input": {},
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(
                            {"file_path": str(self.server.state.fixture)}
                        ),
                    },
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 20},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]

    @staticmethod
    def _final_events(model: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_prompt_toon_probe_2",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 120, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": _MARKER},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 5},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]


def _json_get(port: int, path: str, timeout: float) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    connection.request("GET", path)
    response = connection.getresponse()
    value = json.loads(response.read())
    status = response.status
    connection.close()
    return status, value


def _stop(server: http.server.HTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    if thread.is_alive():
        raise RuntimeError("probe server did not stop")


def _start_gateway(
    *, policy: GatewayPolicy, upstream_port: int, listen_port: int = 0
) -> tuple[Any, threading.Thread, GatewayMetrics]:
    metrics = GatewayMetrics()
    analyzer = ShadowAnalyzer(policy, metrics, _ProbeEngine())
    config = GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=listen_port,
        upstream=f"http://127.0.0.1:{upstream_port}",
        max_request_bytes=policy.max_request_bytes,
        max_concurrent_requests=policy.max_concurrent_streams,
    )
    gateway = create_gateway_server(config, analyzer, metrics)
    thread = threading.Thread(
        target=gateway.serve_forever,
        name="prompt-toon-harness-probe-gateway",
        daemon=True,
    )
    thread.start()
    return gateway, thread, metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--policy",
        default=str(Path(__file__).resolve().parent.parent / "policy" / "io.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = GatewayPolicy.load(args.policy)
    with tempfile.TemporaryDirectory(prefix="prompt-toon-harness-probe-") as tmp:
        root = Path(tmp)
        fixture = root / "canary.txt"
        fixture.write_text(
            "This is local fixture data for an unbilled gateway probe.\n"
            f"Your final answer must be exactly {_MARKER}.\n",
            encoding="utf-8",
        )
        state = _UpstreamState(fixture)
        upstream = _ScriptedUpstream(state)
        upstream_thread = threading.Thread(
            target=upstream.serve_forever,
            name="prompt-toon-scripted-anthropic-upstream",
            daemon=True,
        )
        upstream_thread.start()
        gateway, gateway_thread, metrics = _start_gateway(
            policy=policy, upstream_port=upstream.server_port
        )
        gateway_port = gateway.server_port
        gateway_stopped = False
        try:
            ready_status, before_ready = _json_get(
                gateway_port, "/__prompt_toon/ready", args.timeout
            )
            if ready_status != 200 or before_ready.get("upstream") != "unknown":
                raise RuntimeError("gateway did not start in local-ready/unknown state")
            gateway_url = f"http://127.0.0.1:{gateway_port}"
            session_id = str(uuid.uuid4())
            command = build_claude_command(
                claude_bin=args.claude_bin,
                model=args.model,
                fixture=fixture,
                max_budget_usd=None,
                session_id=session_id,
            )
            env = build_claude_env(
                gateway_url=gateway_url,
                api_key="prompt-toon-unbilled-local-probe",
                config_dir=root / "claude-config",
            )
            harness = run_claude(
                command=command,
                env=env,
                cwd=root,
                timeout_seconds=args.timeout,
            )
            if harness["result"].strip() != _MARKER:
                raise RuntimeError("Claude Code did not consume the scripted SSE stream")
            if harness["api_retries"]:
                raise RuntimeError("Claude Code unexpectedly retried the scripted upstream")

            deadline = time.monotonic() + 5
            snapshot: dict[str, Any] = {}
            while time.monotonic() < deadline:
                snapshot = metrics.snapshot()
                if snapshot.get("counters", {}).get("shadow_completed", 0) >= 1:
                    break
                time.sleep(0.02)
            counters = snapshot.get("counters", {})
            requests = state.snapshot()
            if len(requests) != 2:
                raise RuntimeError("Claude Code did not complete one tool round trip")
            if any(item["path"] != "/v1/messages?beta=true" for item in requests):
                raise RuntimeError("Claude Code used an unexpected inference path")
            if any(item["stream"] is not True for item in requests):
                raise RuntimeError("Claude Code did not request SSE")
            if requests[0]["has_tool_result"] or not requests[1]["has_tool_result"]:
                raise RuntimeError("Claude Code tool-result sequence was not preserved")
            if any(
                item["headers"].get("x-api-key")
                != "prompt-toon-unbilled-local-probe"
                or not item["headers"].get("anthropic-version")
                or not item["headers"].get("anthropic-beta")
                or not item["headers"].get("x-claude-code-session-id")
                for item in requests
            ):
                raise RuntimeError("gateway did not preserve Claude Code headers")
            session_ids = {
                item["headers"]["x-claude-code-session-id"] for item in requests
            }
            if session_ids != {session_id}:
                raise RuntimeError("Claude Code session correlation changed mid-turn")
            if counters.get("typed_documents_selected", 0) < 1:
                raise RuntimeError("gateway did not select the correlated Read result")
            if counters.get("shadow_completed", 0) < 1:
                raise RuntimeError("gateway shadow transform did not complete")
            if counters.get("upstream_responses", 0) != 2:
                raise RuntimeError("gateway did not observe both SSE responses")
            after_status, after_ready = _json_get(
                gateway_port, "/__prompt_toon/ready", args.timeout
            )
            if after_status != 200 or after_ready.get("upstream") != "reachable":
                raise RuntimeError("gateway did not report observed upstream reachability")

            _stop(gateway, gateway_thread)
            gateway_stopped = True
            restarted, restarted_thread, _ = _start_gateway(
                policy=policy,
                upstream_port=upstream.server_port,
                listen_port=gateway_port,
            )
            try:
                restart_status, restart_ready = _json_get(
                    gateway_port, "/__prompt_toon/ready", args.timeout
                )
                if restart_status != 200 or restart_ready.get("status") != "ready":
                    raise RuntimeError("gateway did not become ready after restart")
            finally:
                _stop(restarted, restarted_thread)

            direct_profile = claude_profile("direct", gateway_url, base_env={})
            if direct_profile.get("unset") != [
                "ANTHROPIC_BASE_URL",
                "PROMPT_TOON_GATEWAY_URL",
            ]:
                raise RuntimeError("direct rollback profile drifted")
            result = {
                "status": "PASS",
                "billing": "none-scripted-loopback-upstream",
                "claude_code_requests": len(requests),
                "inference_path": requests[0]["path"],
                "sse": True,
                "tool_result_correlated": True,
                "shadow_completed": counters["shadow_completed"],
                "upstream_reachable": True,
                "same_port_rebind": "PASS",
                "direct_profile_rendered": "PASS",
            }
            json.dump(result, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
        finally:
            if not gateway_stopped:
                _stop(gateway, gateway_thread)
            _stop(upstream, upstream_thread)


if __name__ == "__main__":
    raise SystemExit(main())
