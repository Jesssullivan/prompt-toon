#!/usr/bin/env python3
"""TIN-2792 resident-service concurrency and resource capacity gate."""
from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toon.engine import EngineError
from prompt_toon.resident import ResidentEngine
from service_parity import (
    BUDGET_MS,
    MAX_CARDS,
    MAX_DOCS,
    MAX_INPUT_BYTES,
    MAX_LABEL_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    _binary,
)

CONCURRENCY_LEVELS = (1, 2, 5, 16, 64)
MAX_STREAMS = 64
WORKERS = 16
QUEUE_DEPTH = 64
BODY_BYTES = 128 * 1024
SLOW_BODY_BYTES = 1_900_000
DEFAULT_MAX_RSS_KIB = 256 * 1024
RESULT_TIMEOUT_SECONDS = 120
SLOW_FIRST_FAST_MAX_SECONDS = 5
GENERATED_AT = "GENERATED_AT"


@dataclass(frozen=True)
class Sample:
    at: float
    rss_kib: int
    pending: int


@dataclass(frozen=True)
class BenchmarkRow:
    streams: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    cpu_seconds: float
    peak_rss_kib: int
    peak_pending: int
    peak_queue_depth: int


def _body(index: int, size: int = BODY_BYTES) -> str:
    prefix = f"# capacity-{index}\n"
    line = "plain provenance-bearing research datum\n"
    repeats = (size - len(prefix) + len(line) - 1) // len(line)
    return (prefix + line * repeats)[:size]


def _proc_rss_kib(pid: int) -> int:
    status = Path(f"/proc/{pid}/status")
    for line in status.read_text(encoding="ascii").splitlines():
        if line.startswith("VmRSS:"):
            _name, value, unit = line.split()
            if unit != "kB":
                raise AssertionError(f"unexpected resident RSS unit {unit!r}")
            return int(value)
    raise AssertionError("resident /proc status has no VmRSS field")


def _proc_cpu_seconds(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    if len(fields) <= 14:
        raise AssertionError("resident /proc stat is truncated")
    ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[13]) + int(fields[14])) / ticks


def _sample_process(
    engine: ResidentEngine,
    stop: threading.Event,
    samples: list[Sample],
    samples_lock: threading.Lock,
) -> None:
    while not stop.is_set():
        try:
            sample = Sample(time.monotonic(), _proc_rss_kib(engine.pid), engine.pending_count)
        except FileNotFoundError:
            return
        with samples_lock:
            samples.append(sample)
        stop.wait(0.005)


def _percentile_ms(latencies: list[float], percentile: int) -> float:
    ordered = sorted(latencies)
    rank = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[rank] * 1000


def _submit(
    engine: ResidentEngine,
    request_id: str,
    stream_id: str,
    body: str,
) -> Future[tuple[list[dict[str, Any]], str, dict[str, Any]]]:
    return engine.submit_condense_run(
        request_id=request_id,
        stream_id=stream_id,
        docs=[
            {
                "source": f"{request_id}.txt",
                "trust_tier": "untrusted_tool_output",
                "body": body,
            }
        ],
        run_id=request_id,
        generated_at=GENERATED_AT,
    )


def _validate_result(
    request_id: str,
    result: tuple[list[dict[str, Any]], str, dict[str, Any]],
) -> None:
    docs, _summary, manifest = result
    if len(docs) != 1 or docs[0].get("withheld") is not False:
        raise AssertionError(f"{request_id} was withheld or returned bad document output")
    if manifest.get("id") != request_id:
        raise AssertionError(f"{request_id} returned a mismatched manifest")


def _benchmark_round(
    engine: ResidentEngine,
    streams: int,
    samples: list[Sample],
    samples_lock: threading.Lock,
) -> BenchmarkRow:
    cpu_start = _proc_cpu_seconds(engine.pid)
    round_start = time.monotonic()
    started: dict[Future[Any], float] = {}
    ids: dict[Future[Any], str] = {}
    barrier = threading.Barrier(streams)

    def submit_one(index: int) -> tuple[Future[Any], float, str]:
        request_id = f"bench-{streams:02d}-{index:02d}"
        barrier.wait(timeout=30)
        submitted_at = time.monotonic()
        return (
            _submit(
                engine,
                request_id,
                f"stream-{streams:02d}-{index:02d}",
                _body(index),
            ),
            submitted_at,
            request_id,
        )

    with ThreadPoolExecutor(max_workers=streams) as pool:
        submissions = [pool.submit(submit_one, index) for index in range(streams)]
        for submission in submissions:
            future, submitted_at, request_id = submission.result(timeout=60)
            started[future] = submitted_at
            ids[future] = request_id

    with samples_lock:
        samples.append(
            Sample(time.monotonic(), _proc_rss_kib(engine.pid), engine.pending_count)
        )

    latencies: list[float] = []
    for future in as_completed(started, timeout=RESULT_TIMEOUT_SECONDS):
        completed_at = time.monotonic()
        request_id = ids[future]
        _validate_result(request_id, future.result())
        latencies.append(completed_at - started[future])
    round_end = time.monotonic()
    cpu_seconds = _proc_cpu_seconds(engine.pid) - cpu_start

    with samples_lock:
        round_samples = [
            sample for sample in samples if round_start <= sample.at <= round_end
        ]
    if not round_samples:
        raise AssertionError(f"{streams}-stream round produced no resource samples")
    peak_rss_kib = max(sample.rss_kib for sample in round_samples)
    peak_pending = max(sample.pending for sample in round_samples)
    # Pending includes requests executing in the fixed worker set. Anything
    # above that set is the gateway-observable queue high-water mark.
    return BenchmarkRow(
        streams=streams,
        p50_ms=_percentile_ms(latencies, 50),
        p95_ms=_percentile_ms(latencies, 95),
        p99_ms=_percentile_ms(latencies, 99),
        cpu_seconds=cpu_seconds,
        peak_rss_kib=peak_rss_kib,
        peak_pending=peak_pending,
        peak_queue_depth=max(0, peak_pending - WORKERS),
    )


def _exercise_slow_first(engine: ResidentEngine) -> tuple[str, float]:
    started = time.monotonic()
    slow = _submit(engine, "slow-first", "stream-slow", _body(999, SLOW_BODY_BYTES))
    fast = _submit(engine, "fast-after-slow", "stream-fast", "ok\n")
    names = {slow: "slow", fast: "fast"}
    order: list[str] = []
    fast_latency = 0.0
    for future in as_completed((slow, fast), timeout=RESULT_TIMEOUT_SECONDS):
        name = names[future]
        order.append(name)
        if name == "fast":
            fast_latency = time.monotonic() - started
            _validate_result("fast-after-slow", future.result())
        else:
            _validate_result("slow-first", future.result())
    if fast_latency > SLOW_FIRST_FAST_MAX_SECONDS:
        raise AssertionError(
            f"fast follower took {fast_latency:.3f}s after a slow-first request"
        )
    return "-then-".join(order), fast_latency * 1000


def _assert_overload_rejected(binary: str) -> None:
    with ResidentEngine(
        binary_path=binary,
        max_streams=1,
        workers=1,
        queue_depth=1,
        max_docs=MAX_DOCS,
        max_request_bytes=MAX_REQUEST_BYTES,
        max_response_bytes=MAX_RESPONSE_BYTES,
        max_label_bytes=MAX_LABEL_BYTES,
        max_input_bytes=MAX_INPUT_BYTES,
        budget_ms=BUDGET_MS,
        max_cards=MAX_CARDS,
    ) as engine:
        active = _submit(engine, "overload-active", "stream-active", _body(1000, SLOW_BODY_BYTES))
        try:
            _submit(engine, "overload-extra", "stream-extra", "ok\n")
        except EngineError as exc:
            if "stream limit" not in str(exc):
                raise AssertionError(f"unexpected overload error: {exc}") from exc
        else:
            raise AssertionError("65th-style pending request was not rejected")
        _validate_result(
            "overload-active", active.result(timeout=RESULT_TIMEOUT_SECONDS)
        )


def main() -> int:
    binary = _binary()
    max_rss_kib = int(
        os.environ.get("PROMPT_TOON_SERVICE_MAX_RSS_KIB", DEFAULT_MAX_RSS_KIB)
    )
    if max_rss_kib <= 0:
        raise AssertionError("PROMPT_TOON_SERVICE_MAX_RSS_KIB must be positive")

    rows: list[BenchmarkRow] = []
    samples: list[Sample] = []
    samples_lock = threading.Lock()
    stop = threading.Event()
    with ResidentEngine(
        binary_path=binary,
        max_streams=MAX_STREAMS,
        workers=WORKERS,
        queue_depth=QUEUE_DEPTH,
        max_docs=MAX_DOCS,
        max_request_bytes=MAX_REQUEST_BYTES,
        max_response_bytes=MAX_RESPONSE_BYTES,
        max_label_bytes=MAX_LABEL_BYTES,
        max_input_bytes=MAX_INPUT_BYTES,
        budget_ms=BUDGET_MS,
        max_cards=MAX_CARDS,
    ) as engine:
        sampler = threading.Thread(
            target=_sample_process,
            args=(engine, stop, samples, samples_lock),
            daemon=True,
        )
        sampler.start()
        try:
            for streams in CONCURRENCY_LEVELS:
                rows.append(
                    _benchmark_round(engine, streams, samples, samples_lock)
                )
            slow_first_order, slow_first_fast_ms = _exercise_slow_first(engine)
        finally:
            stop.set()
            sampler.join(timeout=2)

    _assert_overload_rejected(binary)
    peak_rss_kib = max(row.peak_rss_kib for row in rows)
    if peak_rss_kib > max_rss_kib:
        raise AssertionError(
            f"resident peak RSS {peak_rss_kib} KiB exceeds {max_rss_kib} KiB"
        )

    print(
        "| streams | p50 ms | p95 ms | p99 ms | CPU s | peak RSS KiB | "
        "peak pending | peak queue depth |"
    )
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row.streams} | {row.p50_ms:.3f} | {row.p95_ms:.3f} | "
            f"{row.p99_ms:.3f} | {row.cpu_seconds:.3f} | {row.peak_rss_kib} | "
            f"{row.peak_pending} | {row.peak_queue_depth} |"
        )
    print(
        "SERVICE CAPACITY: PASS "
        f"(levels=1/2/5/16/64; {BODY_BYTES} bytes each; "
        f"peak RSS {peak_rss_kib} KiB <= {max_rss_kib} KiB; "
        f"slow-first=PASS(order={slow_first_order},fast={slow_first_fast_ms:.3f}ms); "
        "overload=PASS)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
