#!/usr/bin/env python3
"""C4d pre-rollout gate for bounded HTTP/SSE fan-in over real ``ptoon serve``."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import http.server
import json
import os
import resource
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toon.gateway import (
    ANTHROPIC_PROTOCOL,
    OPENAI_PROTOCOL,
    GatewayAuth,
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ShadowAnalyzer,
    create_gateway_server,
    ownership_attestation_message,
)
from prompt_toon.resident import ResidentEngine
from service_parity import _binary


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "io.json"
STREAMS = int(os.environ.get("PROMPT_TOON_GATEWAY_CAPACITY_STREAMS", "64"))
if not 1 <= STREAMS <= 64:
    raise ValueError("PROMPT_TOON_GATEWAY_CAPACITY_STREAMS must be in [1, 64]")
RESULT_TIMEOUT_SECONDS = 120
DEFAULT_GATEWAY_MAX_RSS_KIB = 512 * 1024
DEFAULT_RESIDENT_MAX_RSS_KIB = 256 * 1024
# The gate intentionally keeps its scripted clients and upstream in-process:
# one client, gateway-handler, header-deadline, and upstream-handler thread per
# stream, plus bounded transform and control threads. Keep that complete test
# process bounded without pretending it is the gateway's production footprint.
DEFAULT_MAX_THREADS = 4 * STREAMS + 32
CAPACITY_PADDING_BYTES = int(
    os.environ.get(
        "PROMPT_TOON_GATEWAY_CAPACITY_PADDING_BYTES", str(2 * 1024 * 1024 - 4096)
    )
)
if not 1 <= CAPACITY_PADDING_BYTES <= 2 * 1024 * 1024:
    raise ValueError(
        "PROMPT_TOON_GATEWAY_CAPACITY_PADDING_BYTES must be in [1, 2097152]"
    )
LOCAL_TOKEN = "capacity-local-" + "l" * 32
UPSTREAM_TOKEN = "capacity-provider-token"


@dataclass
class Sample:
    gateway_rss_kib: int
    resident_rss_kib: int
    threads: int
    active_connections: int
    pending_transforms: int
    resident_pending: int


@dataclass
class ProtocolResult:
    protocol: str
    peak_gateway_rss_kib: int
    peak_resident_rss_kib: int
    peak_threads: int
    peak_connections: int
    peak_pending_transforms: int
    peak_resident_pending: int


class UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.all_active = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.peak_active = 0
        self.headers: list[list[tuple[str, str]]] = []

    def enter(self, headers: list[tuple[str, str]]) -> None:
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.headers.append(headers)
            if self.active == STREAMS:
                self.all_active.set()

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class CapacityUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Any

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        state: UpstreamState = self.server.state
        state.enter(list(self.headers.raw_items()))
        try:
            if not state.release.wait(RESULT_TIMEOUT_SECONDS):
                self.send_error(504)
                return
            body = _sse_body(self.server.protocol)
            self.send_response_only(200, "OK")
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            split = max(1, len(body) // 3)
            for chunk in (body[:split], body[split : split * 2], body[split * 2 :]):
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            state.leave()


class CapacityUpstreamServer(http.server.ThreadingHTTPServer):
    request_queue_size = 128


def _sse_body(protocol: str) -> bytes:
    if protocol == OPENAI_PROTOCOL:
        return b"".join(
            (
                b"event: response.created\n",
                b'data: {"type":"response.created","response":'
                b'{"id":"capacity","model":"capacity-returned"}}\n\n',
                b"event: response.completed\n",
                b'data: {"type":"response.completed","response":'
                b'{"id":"capacity","model":"capacity-returned","usage":'
                b'{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n',
            )
        )
    return b"".join(
        (
            b"event: message_start\n",
            b'data: {"type":"message_start","message":'
            b'{"model":"capacity-returned","usage":{"input_tokens":2}}}\n\n',
            b"event: message_delta\n",
            b'data: {"type":"message_delta","usage":{"output_tokens":1}}\n\n',
            b"event: message_stop\n",
            b'data: {"type":"message_stop"}\n\n',
        )
    )


def _request_body(protocol: str, index: int) -> bytes:
    call_id = f"capacity-{index:02d}"
    output = f"durable stream {index} MUST preserve provenance"
    if protocol == OPENAI_PROTOCOL:
        value = {
            "model": "capacity-requested",
            "input": [
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": "exec_command",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            ],
        }
    else:
        value = {
            "model": "capacity-requested",
            "max_tokens": 8,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": "exec_command",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": output,
                        }
                    ],
                },
            ],
        }
    # JSON permits trailing whitespace. It is forwarded byte-for-byte and
    # exercises HTTP-body residency without manufacturing a giant parsed field
    # or increasing the Chapel transform input.
    return (
        json.dumps(value, separators=(",", ":")).encode("utf-8")
        + b" " * CAPACITY_PADDING_BYTES
    )


def _headers(protocol: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if protocol == OPENAI_PROTOCOL:
        headers["Authorization"] = f"Bearer {LOCAL_TOKEN}"
    else:
        headers["x-api-key"] = LOCAL_TOKEN
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _send(
    port: int, protocol: str, index: int, *, body: bytes | None = None
) -> tuple[int, bytes]:
    path = "/v1/responses" if protocol == OPENAI_PROTOCOL else "/v1/messages"
    connection = http.client.HTTPConnection(
        "127.0.0.1", port, timeout=RESULT_TIMEOUT_SECONDS
    )
    connection.request(
        "POST",
        path,
        body=_request_body(protocol, index) if body is None else body,
        headers=_headers(protocol),
    )
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def _status_value(pid: int, key: str) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
        if line.startswith(f"{key}:"):
            value = line.split()[1]
            return int(value)
    raise AssertionError(f"/proc/{pid}/status has no {key}")


def _sample(
    gateway: Any,
    engine: ResidentEngine,
    stop: threading.Event,
    samples: list[Sample],
) -> None:
    proc_available = Path(f"/proc/{os.getpid()}/status").is_file()
    while not stop.is_set():
        try:
            if proc_available:
                gateway_rss = _status_value(os.getpid(), "VmRSS")
                resident_rss = _status_value(engine.pid, "VmRSS")
                threads = _status_value(os.getpid(), "Threads")
            else:
                gateway_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                if sys.platform == "darwin":
                    gateway_rss //= 1024
                resident_rss = 0
                threads = threading.active_count()
            samples.append(
                Sample(
                    gateway_rss_kib=gateway_rss,
                    resident_rss_kib=resident_rss,
                    threads=threads,
                    active_connections=gateway.active_connection_count,
                    pending_transforms=gateway.analyzer.pending_jobs,
                    resident_pending=engine.pending_count,
                )
            )
        except FileNotFoundError:
            return
        stop.wait(0.005)


def _wait_for_counter(metrics: GatewayMetrics, name: str, expected: int) -> None:
    deadline = time.monotonic() + RESULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if metrics.snapshot()["counters"].get(name, 0) == expected:
            return
        threading.Event().wait(0.01)
    actual = metrics.snapshot()["counters"].get(name, 0)
    raise AssertionError(f"{name} did not reach {expected}; got {actual}")


def _assert_upstream_auth(state: UpstreamState, protocol: str) -> None:
    if len(state.headers) != STREAMS:
        raise AssertionError(
            f"upstream saw {len(state.headers)} requests, want {STREAMS}"
        )
    for raw_headers in state.headers:
        headers = {name.lower(): value for name, value in raw_headers}
        serialized = repr(raw_headers)
        if LOCAL_TOKEN in serialized:
            raise AssertionError("local gateway token reached scripted upstream")
        if protocol == OPENAI_PROTOCOL:
            if headers.get("authorization") != f"Bearer {UPSTREAM_TOKEN}":
                raise AssertionError("OpenAI upstream token was not injected exactly")
            if "x-api-key" in headers:
                raise AssertionError("unexpected x-api-key reached OpenAI upstream")
        else:
            if headers.get("x-api-key") != UPSTREAM_TOKEN:
                raise AssertionError(
                    "Anthropic upstream token was not injected exactly"
                )
            if "authorization" in headers:
                raise AssertionError(
                    "unexpected Authorization reached Anthropic upstream"
                )


def _run_protocol(binary: str, protocol: str) -> ProtocolResult:
    policy = GatewayPolicy.load(POLICY_PATH)
    bodies = [_request_body(protocol, index) for index in range(STREAMS)]
    ingress_budget = sum(len(body) for body in bodies)
    if STREAMS == policy.max_concurrent_streams and not (
        policy.max_inflight_request_bytes * 0.95
        <= ingress_budget
        <= policy.max_inflight_request_bytes
    ):
        raise AssertionError(
            f"{protocol}: capacity corpus {ingress_budget} bytes does not exercise "
            f"at least 95% of production ingress cap "
            f"{policy.max_inflight_request_bytes}"
        )
    state = UpstreamState()
    upstream = CapacityUpstreamServer(("127.0.0.1", 0), CapacityUpstream)
    upstream.state = state  # type: ignore[attr-defined]
    upstream.protocol = protocol  # type: ignore[attr-defined]
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    metrics = GatewayMetrics(model_hash_key=b"capacity-model-hash-key-32bytes")
    engine = ResidentEngine(
        binary_path=binary,
        max_streams=policy.max_concurrent_streams,
        workers=policy.transform_workers,
        queue_depth=policy.pending_queue_depth,
        max_docs=policy.max_documents_per_request,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        max_label_bytes=policy.max_label_bytes,
        max_input_bytes=policy.max_input_bytes,
        budget_ms=policy.wall_clock_budget_ms,
        max_cards=policy.max_cards_per_document,
    )
    analyzer = ShadowAnalyzer(policy, metrics, engine, protocol=protocol)
    upstream_base = f"http://127.0.0.1:{upstream.server_port}"
    if protocol == OPENAI_PROTOCOL:
        upstream_base += "/v1"
    gateway = create_gateway_server(
        GatewayConfig(
            listen_host="127.0.0.1",
            listen_port=0,
            upstream=upstream_base,
            max_request_bytes=policy.max_request_bytes,
            max_concurrent_requests=STREAMS + 1,
            max_inflight_request_bytes=ingress_budget,
            protocol=protocol,
            auth=GatewayAuth(LOCAL_TOKEN, UPSTREAM_TOKEN),
            policy_sha256=hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
            resident_binary_sha256=hashlib.sha256(
                Path(binary).read_bytes()
            ).hexdigest(),
        ),
        analyzer,
        metrics,
    )
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()

    samples: list[Sample] = []
    stop = threading.Event()
    sampler = threading.Thread(target=_sample, args=(gateway, engine, stop, samples))
    sampler.start()
    pool = ThreadPoolExecutor(max_workers=STREAMS)
    futures: list[Future[tuple[int, bytes]]] = []
    try:
        barrier = threading.Barrier(STREAMS)

        def send_one(index: int) -> tuple[int, bytes]:
            barrier.wait(timeout=30)
            return _send(
                gateway.server_port, protocol, index, body=bodies[index]
            )

        futures = [pool.submit(send_one, index) for index in range(STREAMS)]
        if not state.all_active.wait(30):
            completed = [future for future in futures if future.done()]
            failures = [
                repr(future.exception())
                for future in completed
                if future.exception() is not None
            ]
            raise AssertionError(
                f"{protocol}: upstream did not hold all {STREAMS} concurrent requests; "
                f"active={state.active}, peak={state.peak_active}, "
                f"gateway_connections={gateway.active_connection_count}, "
                f"completed_clients={len(completed)}, failures={failures[:4]}, "
                f"counters={metrics.snapshot()['counters']}"
            )
        _wait_for_counter(metrics, "shadow_admitted", STREAMS)
        if gateway.inflight_request_bytes != ingress_budget:
            raise AssertionError(
                f"{protocol}: ingress residency "
                f"{gateway.inflight_request_bytes} != near-cap {ingress_budget}"
            )

        owner = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
        challenge = "e" * 64
        owner.request(
            "GET",
            "/__prompt_toon/ownership",
            headers={"X-Prompt-Toon-Challenge": challenge},
        )
        ownership_response = owner.getresponse()
        ownership = json.loads(ownership_response.read())
        owner.close()
        attestation = dict(ownership)
        challenge_response = attestation.pop("challenge_response", "")
        expected_owner = hmac.new(
            LOCAL_TOKEN.encode(),
            ownership_attestation_message(challenge, attestation),
            "sha256",
        ).hexdigest()
        if (
            ownership_response.status != 200
            or ownership.get("status") != "owned"
            or not hmac.compare_digest(
                challenge_response, expected_owner
            )
            or ownership.get("policy_sha256")
            != hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
            or ownership.get("resident_binary_sha256")
            != hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        ):
            raise AssertionError(f"{protocol}: ownership failed under saturation")

        overload_status, overload_body = _send(
            gateway.server_port, protocol, STREAMS, body=b"{}"
        )
        if overload_status != 503 or b"overloaded_error" not in overload_body:
            raise AssertionError(
                f"{protocol}: over-budget request was not rejected deterministically"
            )
        if metrics.snapshot()["counters"].get("ingress_byte_budget_rejected") != 1:
            raise AssertionError(f"{protocol}: ingress byte rejection was not counted")

        state.release.set()
        expected_sse = _sse_body(protocol)
        for future in as_completed(futures, timeout=RESULT_TIMEOUT_SECONDS):
            status, body = future.result()
            if status != 200 or body != expected_sse:
                raise AssertionError(f"{protocol}: under-limit SSE bytes changed")
        _wait_for_counter(metrics, "shadow_completed", STREAMS)
        _wait_for_counter(metrics, "upstream_responses", STREAMS)
        deadline = time.monotonic() + 5
        while gateway.inflight_request_bytes and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        if gateway.inflight_request_bytes:
            raise AssertionError(f"{protocol}: ingress reservations did not drain")

        counters = metrics.snapshot()["counters"]
        request_counter = (
            "responses_requests" if protocol == OPENAI_PROTOCOL else "messages_requests"
        )
        for name, expected in (
            (request_counter, STREAMS),
            ("typed_documents_selected", STREAMS),
            ("shadow_admitted", STREAMS),
            ("shadow_completed", STREAMS),
            ("upstream_responses", STREAMS),
        ):
            if counters.get(name, 0) != expected:
                raise AssertionError(
                    f"{protocol}: {name}={counters.get(name, 0)}, want {expected}"
                )
        for name in (
            "client_auth_rejected",
            "shadow_dropped_capacity",
            "shadow_failed",
            "shadow_timeouts",
            "upstream_transport_failures",
        ):
            if counters.get(name, 0) != 0:
                raise AssertionError(f"{protocol}: unexpected {name}={counters[name]}")
        _assert_upstream_auth(state, protocol)
    finally:
        state.release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        stop.set()
        sampler.join(timeout=2)
        gateway.shutdown()
        gateway_thread.join(timeout=5)
        gateway.server_close()
        analyzer.close()
        upstream.shutdown()
        upstream_thread.join(timeout=5)
        upstream.server_close()

    if not samples:
        raise AssertionError(f"{protocol}: no capacity samples recorded")
    return ProtocolResult(
        protocol=protocol,
        peak_gateway_rss_kib=max(sample.gateway_rss_kib for sample in samples),
        peak_resident_rss_kib=max(sample.resident_rss_kib for sample in samples),
        peak_threads=max(sample.threads for sample in samples),
        peak_connections=max(sample.active_connections for sample in samples),
        peak_pending_transforms=max(sample.pending_transforms for sample in samples),
        peak_resident_pending=max(sample.resident_pending for sample in samples),
    )


def main() -> int:
    binary = _binary()
    max_gateway_rss = int(
        os.environ.get("PROMPT_TOON_GATEWAY_MAX_RSS_KIB", DEFAULT_GATEWAY_MAX_RSS_KIB)
    )
    max_resident_rss = int(
        os.environ.get("PROMPT_TOON_SERVICE_MAX_RSS_KIB", DEFAULT_RESIDENT_MAX_RSS_KIB)
    )
    max_threads = int(
        os.environ.get("PROMPT_TOON_GATEWAY_MAX_THREADS", DEFAULT_MAX_THREADS)
    )
    if min(max_gateway_rss, max_resident_rss, max_threads) <= 0:
        raise AssertionError("gateway capacity ceilings must be positive")

    results = [
        _run_protocol(binary, ANTHROPIC_PROTOCOL),
        _run_protocol(binary, OPENAI_PROTOCOL),
    ]
    for result in results:
        if result.peak_gateway_rss_kib > max_gateway_rss:
            raise AssertionError(
                f"{result.protocol}: gateway RSS {result.peak_gateway_rss_kib} KiB "
                f"exceeds {max_gateway_rss} KiB"
            )
        if result.peak_resident_rss_kib > max_resident_rss:
            raise AssertionError(
                f"{result.protocol}: resident RSS {result.peak_resident_rss_kib} KiB "
                f"exceeds {max_resident_rss} KiB"
            )
        if result.peak_threads > max_threads:
            raise AssertionError(
                f"{result.protocol}: {result.peak_threads} threads exceeds {max_threads}"
            )
        if result.peak_connections > STREAMS + 2:
            raise AssertionError(f"{result.protocol}: connection bound was exceeded")
        if result.peak_pending_transforms > STREAMS:
            raise AssertionError(
                f"{result.protocol}: shadow pending bound was exceeded"
            )
        if result.peak_resident_pending > STREAMS:
            raise AssertionError(
                f"{result.protocol}: resident pending bound was exceeded"
            )

    print(
        "| protocol | streams | gateway RSS KiB | resident RSS KiB | threads | "
        "connections | shadow pending | resident pending |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for result in results:
        print(
            f"| {result.protocol} | {STREAMS} | {result.peak_gateway_rss_kib} | "
            f"{result.peak_resident_rss_kib} | {result.peak_threads} | "
            f"{result.peak_connections} | {result.peak_pending_transforms} | "
            f"{result.peak_resident_pending} |"
        )
    ingress_proof = (
        "near-production ingress cap"
        if STREAMS == GatewayPolicy.load(POLICY_PATH).max_concurrent_streams
        else "scaled ingress cap"
    )
    print(
        "GATEWAY CAPACITY: PASS "
        f"(anthropic={STREAMS}/{STREAMS}; openai={STREAMS}/{STREAMS}; "
        "real ResidentEngine; exact SSE; "
        f"split auth; ownership under saturation; {ingress_proof}; "
        "overload=PASS; external_requests=0)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
