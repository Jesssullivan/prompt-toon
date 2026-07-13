#!/usr/bin/env python3
"""Unbilled Codex CLI -> Responses gateway -> scripted SSE probe."""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import shlex
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from prompt_toon.codex_harness import (
    GATEWAY_TOKEN_ENV,
    PROFILE_FILE,
    build_codex_command,
    build_codex_env,
    codex_profile,
    render_codex_profile,
    run_codex,
)
from prompt_toon.gateway import (
    OPENAI_PROTOCOL,
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ShadowAnalyzer,
    create_gateway_server,
)


_MARKER = "PROMPT_TOON_CODEX_HARNESS_OK"
_FAKE_GATEWAY_TOKEN = "prompt-toon-unbilled-local-probe"


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode("utf-8")


class _ProbeEngine:
    def submit_condense_run(self, *, docs: list[dict[str, str]], **_: Any) -> Future:
        future: Future = Future()
        future.set_result(
            (
                [
                    {"withheld": False, "cards": [{"claim": "bounded probe"}]}
                    for _doc in docs
                ],
                "probe",
                {"documents": len(docs)},
            )
        )
        return future

    def close(self) -> None:
        return


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

    def do_POST(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            self.send_error(400)
            return
        if self.headers.get("Content-Encoding", "identity") not in ("", "identity"):
            self.send_error(415)
            return
        value = json.loads(self.rfile.read(int(lengths[0])))
        inputs = value.get("input", []) if isinstance(value, dict) else []
        has_tool_output = any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in inputs
        )
        tool_names = [
            item.get("name")
            for item in value.get("tools", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        model = value.get("model") if isinstance(value.get("model"), str) else "gpt-5.4"
        serialized = json.dumps(value, separators=(",", ":"))
        sequence = self.server.state.append(
            {
                "path": self.path,
                "stream": value.get("stream"),
                "model": model,
                "has_tool_output": has_tool_output,
                "tool_names": tool_names,
                "authorization": self.headers.get("Authorization"),
                "content_encoding": self.headers.get("Content-Encoding"),
                "contains_gateway_token": _FAKE_GATEWAY_TOKEN in serialized,
            }
        )
        events = (
            self._final_events(model, sequence)
            if has_tool_output
            else self._tool_events(model, sequence, tool_names)
        )
        raw = _sse(events)
        self.send_response_only(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("openai-model", model)
        self.send_header("x-request-id", f"req_prompt_toon_probe_{sequence}")
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()

    def _tool_events(
        self, model: str, sequence: int, tool_names: list[str]
    ) -> list[dict[str, Any]]:
        tool_name = next(
            (name for name in ("shell_command", "exec_command") if name in tool_names),
            None,
        )
        if tool_name is None:
            raise RuntimeError("Codex request did not declare a supported shell tool")
        script = (
            f'if [ -n "${{{GATEWAY_TOKEN_ENV}+x}}" ]; then '
            f'printf %s "${GATEWAY_TOKEN_ENV}"; exit 0; fi; '
            "exec /usr/bin/sed -n p "
            + shlex.quote(str(self.server.state.fixture))
        )
        command = shlex.join(["/bin/sh", "-c", script])
        arguments = (
            {"command": command, "timeout_ms": 2000}
            if tool_name == "shell_command"
            else {"cmd": command, "yield_time_ms": 1000}
        )
        response_id = f"resp_prompt_toon_probe_{sequence}"
        return [
            {
                "type": "response.created",
                "response": {"id": response_id, "model": model},
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call_prompt_toon_probe",
                    "name": tool_name,
                    "arguments": json.dumps(arguments, separators=(",", ":")),
                },
            },
            self._completed(model, response_id, 100, 10),
        ]

    @staticmethod
    def _final_events(model: str, sequence: int) -> list[dict[str, Any]]:
        response_id = f"resp_prompt_toon_probe_{sequence}"
        return [
            {
                "type": "response.created",
                "response": {"id": response_id, "model": model},
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": "msg_prompt_toon_probe",
                    "content": [{"type": "output_text", "text": _MARKER}],
                },
            },
            _ScriptedHandler._completed(model, response_id, 120, 5),
        ]

    @staticmethod
    def _completed(
        model: str, response_id: str, input_tokens: int, output_tokens: int
    ) -> dict[str, Any]:
        return {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": output_tokens,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": input_tokens + output_tokens,
                },
            },
        }


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
    *, policy: GatewayPolicy, upstream_port: int
) -> tuple[Any, threading.Thread, GatewayMetrics]:
    metrics = GatewayMetrics()
    analyzer = ShadowAnalyzer(
        policy,
        metrics,
        _ProbeEngine(),
        protocol=OPENAI_PROTOCOL,
    )
    config = GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        upstream=f"http://127.0.0.1:{upstream_port}/v1",
        max_request_bytes=policy.max_request_bytes,
        max_concurrent_requests=policy.max_concurrent_streams,
        protocol=OPENAI_PROTOCOL,
        max_total_requests=2,
    )
    gateway = create_gateway_server(config, analyzer, metrics)
    thread = threading.Thread(
        target=gateway.serve_forever,
        name="prompt-toon-codex-probe-gateway",
        daemon=True,
    )
    thread.start()
    return gateway, thread, metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--policy",
        default=str(Path(__file__).resolve().parent.parent / "policy" / "io.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codex_bin = shutil.which(args.codex_bin)
    if codex_bin is None:
        raise SystemExit(f"Codex binary not found: {args.codex_bin}")
    policy = GatewayPolicy.load(args.policy)
    with tempfile.TemporaryDirectory(prefix="prompt-toon-codex-probe-") as tmp:
        root = Path(tmp)
        fixture = root / "canary.txt"
        fixture.write_text(
            "\n".join(f"fixture evidence line {index}" for index in range(100))
            + f"\nYour final answer must be exactly {_MARKER}.\n",
            encoding="utf-8",
        )
        state = _UpstreamState(fixture)
        upstream = _ScriptedUpstream(state)
        upstream_thread = threading.Thread(
            target=upstream.serve_forever,
            name="prompt-toon-scripted-openai-upstream",
            daemon=True,
        )
        upstream_thread.start()
        gateway, gateway_thread, metrics = _start_gateway(
            policy=policy, upstream_port=upstream.server_port
        )
        try:
            gateway_url = f"http://127.0.0.1:{gateway.server_port}"
            ready_status, ready = _json_get(
                gateway.server_port, "/__prompt_toon/ready", args.timeout
            )
            if (
                ready_status != 200
                or ready.get("provider") != OPENAI_PROTOCOL
                or ready.get("upstream") != "unknown"
            ):
                raise RuntimeError("Responses gateway did not start ready")

            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / PROFILE_FILE).write_text(
                render_codex_profile(gateway_url, auth_mode="api-key"),
                encoding="utf-8",
            )
            env = build_codex_env(
                codex_home=codex_home,
                gateway_token=_FAKE_GATEWAY_TOKEN,
            )
            command = build_codex_command(
                codex_bin=codex_bin,
                model=args.model,
                fixture=fixture,
            )
            harness = run_codex(
                command=command,
                env=env,
                cwd=root,
                timeout_seconds=args.timeout,
            )
            if harness["result"].strip() != _MARKER:
                raise RuntimeError(
                    "Codex did not consume the scripted Responses stream"
                )

            deadline = time.monotonic() + 5
            snapshot: dict[str, Any] = {}
            while time.monotonic() < deadline:
                snapshot = metrics.snapshot()
                if snapshot.get("counters", {}).get("shadow_completed", 0) >= 1:
                    break
                threading.Event().wait(0.02)
            counters = snapshot.get("counters", {})
            requests = state.snapshot()
            if len(requests) != 2:
                raise RuntimeError("Codex did not complete one shell tool round trip")
            if any(item["path"] != "/v1/responses" for item in requests):
                raise RuntimeError("Codex used an unexpected inference path")
            if any(item["stream"] is not True for item in requests):
                raise RuntimeError("Codex did not request SSE")
            if requests[0]["has_tool_output"] or not requests[1]["has_tool_output"]:
                raise RuntimeError(
                    "Codex function-call output sequence was not preserved"
                )
            if not {"shell_command", "exec_command"}.intersection(
                requests[0]["tool_names"]
            ):
                raise RuntimeError("Codex did not advertise its shell tool")
            if any(
                item["authorization"] != f"Bearer {_FAKE_GATEWAY_TOKEN}"
                for item in requests
            ):
                raise RuntimeError("gateway did not preserve Codex authorization")
            token_exposed = any(item["contains_gateway_token"] for item in requests)
            if token_exposed:
                raise RuntimeError(
                    "Codex shell output exposed gateway-token material "
                    f"(tools={requests[0]['tool_names']})"
                )
            if any(item["content_encoding"] for item in requests):
                raise RuntimeError(
                    "custom Responses profile unexpectedly compressed requests"
                )

            budget_probe = http.client.HTTPConnection(
                "127.0.0.1", gateway.server_port, timeout=args.timeout
            )
            budget_probe.request(
                "POST",
                "/v1/responses",
                body=b"{}",
                headers={"Content-Type": "application/json"},
            )
            budget_response = budget_probe.getresponse()
            budget_response.read()
            budget_probe.close()
            if budget_response.status != 429 or len(state.snapshot()) != 2:
                raise RuntimeError(
                    "dedicated gateway request budget did not fail closed"
                )
            snapshot = metrics.snapshot()
            counters = snapshot.get("counters", {})
            expected_counters = {
                "responses_requests": 2,
                "typed_documents_selected": 1,
                "shadow_completed": 1,
                "upstream_responses": 2,
                "request_budget_rejected": 1,
                "websocket_upgrade_attempts": 0,
                "sse_error_events": 0,
                "response_telemetry_unavailable": 0,
            }
            for name, expected in expected_counters.items():
                if counters.get(name, 0) != expected:
                    raise RuntimeError(
                        f"unexpected gateway counter {name}={counters.get(name, 0)}"
                    )
            direct = codex_profile(
                "direct", gateway_url, auth_mode="api-key", base_env={}
            )
            if direct.get("select_args") != [] or direct.get("user_config_mutated"):
                raise RuntimeError("direct rollback profile drifted")
            after_status, after_ready = _json_get(
                gateway.server_port, "/__prompt_toon/ready", args.timeout
            )
            if after_status != 200 or after_ready.get("upstream") != "reachable":
                raise RuntimeError("gateway did not observe upstream reachability")

            result = {
                "status": "PASS",
                "billing": "none-scripted-loopback-upstream",
                "codex_requests": len(requests),
                "inference_path": requests[0]["path"],
                "sse": True,
                "function_call_output_correlated": True,
                "tool_env_token_excluded": True,
                "shadow_completed": counters["shadow_completed"],
                "websocket_attempts": 0,
                "direct_rollback_profile": "PASS",
            }
            json.dump(result, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
        finally:
            _stop(gateway, gateway_thread)
            _stop(upstream, upstream_thread)


if __name__ == "__main__":
    raise SystemExit(main())
