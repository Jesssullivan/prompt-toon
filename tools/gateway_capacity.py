#!/usr/bin/env python3
"""C4d pre-rollout gate for bounded HTTP/SSE fan-in over real ``ptoon serve``."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import http.server
import json
import multiprocessing
import os
import resource
import signal
import sys
import threading
import time
import traceback
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
WORKER_STOP_TIMEOUT_SECONDS = 15
PROCESS_EXIT_TIMEOUT_SECONDS = 10
DEFAULT_GATEWAY_MAX_RSS_KIB = 512 * 1024
DEFAULT_RESIDENT_MAX_RSS_KIB = 256 * 1024
# This ceiling applies to the isolated gateway process: one gateway-handler and
# header-deadline thread per stream, plus bounded transform and control threads.
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


@dataclass
class GatewayWorker:
    process: Any
    control: Any
    port: int
    pid: int
    resident_pid: int


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


def _worker_snapshot(
    gateway: Any, engine: ResidentEngine, metrics: GatewayMetrics
) -> dict[str, Any]:
    return {
        "kind": "snapshot",
        "active_connections": gateway.active_connection_count,
        "inflight_request_bytes": gateway.inflight_request_bytes,
        "pending_transforms": gateway.analyzer.pending_jobs,
        "resident_pending": engine.pending_count,
        "metrics": metrics.snapshot(),
    }


def _gateway_worker_main(
    control: Any,
    binary: str,
    protocol: str,
    upstream_base: str,
    ingress_budget: int,
) -> None:
    """Own the real gateway and resident in a process isolated from load drivers."""

    gateway: Any | None = None
    engine: ResidentEngine | None = None
    analyzer: ShadowAnalyzer | None = None
    gateway_thread: threading.Thread | None = None
    sampler: threading.Thread | None = None
    samples: list[Sample] = []
    stop = threading.Event()
    shutdown_requested = threading.Event()
    failure: dict[str, str] | None = None

    def request_shutdown(_signum: int, _frame: Any) -> None:
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        policy = GatewayPolicy.load(POLICY_PATH)
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
        gateway_thread = threading.Thread(
            target=gateway.serve_forever,
            name=f"capacity-{protocol}-gateway",
            daemon=True,
        )
        gateway_thread.start()
        sampler = threading.Thread(
            target=_sample,
            args=(gateway, engine, stop, samples),
            name=f"capacity-{protocol}-sampler",
            daemon=True,
        )
        sampler.start()
        control.send(
            {
                "kind": "ready",
                "port": gateway.server_port,
                "pid": os.getpid(),
                "resident_pid": engine.pid,
            }
        )
        while not shutdown_requested.is_set():
            if not control.poll(0.1):
                continue
            command = control.recv()
            if not isinstance(command, dict):
                raise ValueError("gateway worker command must be an object")
            action = command.get("action")
            if action == "snapshot":
                control.send(_worker_snapshot(gateway, engine, metrics))
            elif action == "stop":
                break
            else:
                raise ValueError(f"unknown gateway worker action {action!r}")
    except EOFError:
        pass
    except BaseException as exc:
        failure = {
            "kind": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    finally:
        stop.set()
        if sampler is not None:
            sampler.join(timeout=2)
        if (
            gateway is not None
            and gateway_thread is not None
            and gateway_thread.is_alive()
        ):
            gateway.shutdown()
        if gateway_thread is not None:
            gateway_thread.join(timeout=5)
        try:
            if gateway is not None:
                gateway.server_close()
        finally:
            if analyzer is not None:
                analyzer.close()
            elif engine is not None:
                engine.close()

    try:
        if failure is not None:
            control.send(failure)
        elif not samples:
            control.send(
                {"kind": "error", "error": "gateway worker recorded no samples"}
            )
        else:
            control.send(
                {
                    "kind": "stopped",
                    "result": {
                        "peak_gateway_rss_kib": max(
                            sample.gateway_rss_kib for sample in samples
                        ),
                        "peak_resident_rss_kib": max(
                            sample.resident_rss_kib for sample in samples
                        ),
                        "peak_threads": max(sample.threads for sample in samples),
                        "peak_connections": max(
                            sample.active_connections for sample in samples
                        ),
                        "peak_pending_transforms": max(
                            sample.pending_transforms for sample in samples
                        ),
                        "peak_resident_pending": max(
                            sample.resident_pending for sample in samples
                        ),
                    },
                }
            )
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        control.close()


def _recv_worker(
    control: Any,
    process: Any,
    *,
    timeout: float,
) -> dict[str, Any]:
    if not control.poll(timeout):
        state = "running" if process.is_alive() else f"exited {process.exitcode}"
        raise AssertionError(f"gateway worker did not respond within {timeout}s ({state})")
    try:
        message = control.recv()
    except EOFError as exc:
        raise AssertionError(
            f"gateway worker closed its control pipe (exit={process.exitcode})"
        ) from exc
    if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
        raise AssertionError(f"gateway worker returned invalid control data: {message!r}")
    if message["kind"] == "error":
        detail = message.get("traceback") or message.get("error") or "unknown error"
        raise AssertionError(f"gateway worker failed:\n{detail}")
    return message


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        threading.Event().wait(0.01)
    return not _pid_exists(pid)


def _terminate_process(process: Any) -> None:
    if not process.is_alive():
        process.join(timeout=0)
        return
    process.terminate()
    process.join(timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
    if process.is_alive():
        raise AssertionError(f"gateway worker {process.pid} could not be terminated")


def _ensure_resident_exit(pid: int) -> None:
    if _wait_for_pid_exit(pid, PROCESS_EXIT_TIMEOUT_SECONDS):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if _wait_for_pid_exit(pid, PROCESS_EXIT_TIMEOUT_SECONDS):
        raise AssertionError(f"resident {pid} required forced SIGTERM during cleanup")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not _wait_for_pid_exit(pid, PROCESS_EXIT_TIMEOUT_SECONDS):
        raise AssertionError(f"resident {pid} survived forced cleanup")
    raise AssertionError(f"resident {pid} required forced SIGKILL during cleanup")


def _start_gateway_worker(
    binary: str, protocol: str, upstream_base: str, ingress_budget: int
) -> GatewayWorker:
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe()
    process = context.Process(
        target=_gateway_worker_main,
        args=(child_control, binary, protocol, upstream_base, ingress_budget),
        name=f"prompt-toon-capacity-{protocol}",
    )
    process.start()
    child_control.close()
    try:
        ready = _recv_worker(parent_control, process, timeout=30)
        if ready.get("kind") != "ready":
            raise AssertionError(f"gateway worker did not send ready: {ready!r}")
        pid = ready.get("pid")
        resident_pid = ready.get("resident_pid")
        port = ready.get("port")
        if (
            not isinstance(pid, int)
            or pid == os.getpid()
            or not isinstance(resident_pid, int)
            or resident_pid in (pid, os.getpid())
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise AssertionError(f"gateway worker did not isolate its processes: {ready!r}")
        return GatewayWorker(process, parent_control, port, pid, resident_pid)
    except BaseException as exc:
        parent_control.close()
        try:
            _terminate_process(process)
        except BaseException as cleanup_exc:
            exc.add_note(f"gateway worker startup cleanup failed: {cleanup_exc!r}")
        raise


def _worker_command(
    worker: GatewayWorker, action: str, *, timeout: float = RESULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    if not worker.process.is_alive():
        raise AssertionError(
            f"gateway worker exited unexpectedly with {worker.process.exitcode}"
        )
    worker.control.send({"action": action})
    return _recv_worker(worker.control, worker.process, timeout=timeout)


def _stop_gateway_worker(worker: GatewayWorker) -> dict[str, int]:
    result: dict[str, Any] | None = None
    stop_error: BaseException | None = None
    try:
        stopped = _worker_command(
            worker, "stop", timeout=WORKER_STOP_TIMEOUT_SECONDS
        )
        if stopped.get("kind") != "stopped" or not isinstance(
            stopped.get("result"), dict
        ):
            raise AssertionError(f"gateway worker did not stop cleanly: {stopped!r}")
        result = stopped["result"]
    except BaseException as exc:
        stop_error = exc
    finally:
        worker.control.close()
        worker.process.join(timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
        if worker.process.is_alive():
            try:
                _terminate_process(worker.process)
            except BaseException as exc:
                if stop_error is None:
                    stop_error = exc
                else:
                    stop_error.add_note(f"worker termination also failed: {exc!r}")
        try:
            _ensure_resident_exit(worker.resident_pid)
        except BaseException as exc:
            if stop_error is None:
                stop_error = exc
            else:
                stop_error.add_note(f"resident cleanup also failed: {exc!r}")
    if stop_error is not None:
        raise stop_error.with_traceback(stop_error.__traceback__)
    if worker.process.exitcode != 0:
        raise AssertionError(
            f"gateway worker exited with status {worker.process.exitcode}"
        )
    if result is None:
        raise AssertionError("gateway worker returned no capacity result")
    if not all(isinstance(value, int) for value in result.values()):
        raise AssertionError(f"gateway worker returned invalid peaks: {result!r}")
    return {name: int(value) for name, value in result.items()}


def _snapshot(worker: GatewayWorker) -> dict[str, Any]:
    snapshot = _worker_command(worker, "snapshot")
    if snapshot.get("kind") != "snapshot":
        raise AssertionError(f"gateway worker did not return a snapshot: {snapshot!r}")
    return snapshot


def _wait_for_counter(worker: GatewayWorker, name: str, expected: int) -> None:
    deadline = time.monotonic() + RESULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = _snapshot(worker)
        if snapshot["metrics"]["counters"].get(name, 0) == expected:
            return
        threading.Event().wait(0.01)
    actual = _snapshot(worker)["metrics"]["counters"].get(name, 0)
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
    upstream_base = f"http://127.0.0.1:{upstream.server_port}"
    if protocol == OPENAI_PROTOCOL:
        upstream_base += "/v1"
    worker: GatewayWorker | None = None
    worker_peaks: dict[str, int] | None = None
    pool: ThreadPoolExecutor | None = None
    futures: list[Future[tuple[int, bytes]]] = []
    primary_error: BaseException | None = None
    primary_traceback: Any = None
    cleanup_errors: list[BaseException] = []
    try:
        worker = _start_gateway_worker(
            binary, protocol, upstream_base, ingress_budget
        )
        pool = ThreadPoolExecutor(max_workers=STREAMS)
        barrier = threading.Barrier(STREAMS)

        def send_one(index: int) -> tuple[int, bytes]:
            barrier.wait(timeout=30)
            return _send(
                worker.port, protocol, index, body=bodies[index]
            )

        futures = [pool.submit(send_one, index) for index in range(STREAMS)]
        if not state.all_active.wait(30):
            completed = [future for future in futures if future.done()]
            failures = [
                repr(future.exception())
                for future in completed
                if future.exception() is not None
            ]
            snapshot = _snapshot(worker)
            raise AssertionError(
                f"{protocol}: upstream did not hold all {STREAMS} concurrent requests; "
                f"active={state.active}, peak={state.peak_active}, "
                f"gateway_connections={snapshot['active_connections']}, "
                f"completed_clients={len(completed)}, failures={failures[:4]}, "
                f"counters={snapshot['metrics']['counters']}"
            )
        _wait_for_counter(worker, "shadow_admitted", STREAMS)
        snapshot = _snapshot(worker)
        if snapshot["inflight_request_bytes"] != ingress_budget:
            raise AssertionError(
                f"{protocol}: ingress residency "
                f"{snapshot['inflight_request_bytes']} != near-cap {ingress_budget}"
            )

        owner = http.client.HTTPConnection("127.0.0.1", worker.port, timeout=5)
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
            worker.port, protocol, STREAMS, body=b"{}"
        )
        if overload_status != 503 or b"overloaded_error" not in overload_body:
            raise AssertionError(
                f"{protocol}: over-budget request was not rejected deterministically"
            )
        snapshot = _snapshot(worker)
        if (
            snapshot["metrics"]["counters"].get("ingress_byte_budget_rejected")
            != 1
        ):
            raise AssertionError(f"{protocol}: ingress byte rejection was not counted")

        state.release.set()
        expected_sse = _sse_body(protocol)
        for future in as_completed(futures, timeout=RESULT_TIMEOUT_SECONDS):
            status, body = future.result()
            if status != 200 or body != expected_sse:
                raise AssertionError(f"{protocol}: under-limit SSE bytes changed")
        _wait_for_counter(worker, "shadow_completed", STREAMS)
        _wait_for_counter(worker, "upstream_responses", STREAMS)
        deadline = time.monotonic() + 5
        snapshot = _snapshot(worker)
        while snapshot["inflight_request_bytes"] and time.monotonic() < deadline:
            threading.Event().wait(0.01)
            snapshot = _snapshot(worker)
        if snapshot["inflight_request_bytes"]:
            raise AssertionError(f"{protocol}: ingress reservations did not drain")

        counters = snapshot["metrics"]["counters"]
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
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    finally:
        state.release.set()
        try:
            if worker is not None:
                worker_peaks = _stop_gateway_worker(worker)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            upstream.shutdown()
            upstream_thread.join(timeout=5)
            upstream.server_close()
        except BaseException as exc:
            cleanup_errors.append(exc)

    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            primary_error.add_note(f"capacity cleanup failure: {cleanup_error!r}")
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_errors:
        first_cleanup_error = cleanup_errors[0]
        for cleanup_error in cleanup_errors[1:]:
            first_cleanup_error.add_note(
                f"additional capacity cleanup failure: {cleanup_error!r}"
            )
        raise first_cleanup_error.with_traceback(first_cleanup_error.__traceback__)

    if worker_peaks is None:
        raise AssertionError(f"{protocol}: gateway worker returned no capacity peaks")
    return ProtocolResult(
        protocol=protocol,
        peak_gateway_rss_kib=worker_peaks["peak_gateway_rss_kib"],
        peak_resident_rss_kib=worker_peaks["peak_resident_rss_kib"],
        peak_threads=worker_peaks["peak_threads"],
        peak_connections=worker_peaks["peak_connections"],
        peak_pending_transforms=worker_peaks["peak_pending_transforms"],
        peak_resident_pending=worker_peaks["peak_resident_pending"],
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
        "isolated gateway process; real ResidentEngine; exact SSE; "
        f"split auth; ownership under saturation; {ingress_proof}; "
        "overload=PASS; external_requests=0)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
