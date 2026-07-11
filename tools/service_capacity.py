#!/usr/bin/env python3
"""TIN-2792 capacity gate for one bounded resident ptoon process."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from service_parity import (
    BUDGET_MS,
    MAX_CARDS,
    MAX_DOCS,
    MAX_INPUT_BYTES,
    MAX_LABEL_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    _binary,
    condense_payload,
    parse_responses,
    request_frame,
)

STREAMS = 64
WORKERS = 16
QUEUE_DEPTH = 64
BODY_BYTES = 128 * 1024
DEFAULT_MAX_RSS_KIB = 256 * 1024


def _body(index: int) -> bytes:
    prefix = f"# capacity-{index}\n".encode()
    line = b"plain provenance-bearing research datum\n"
    repeats = (BODY_BYTES - len(prefix) + len(line) - 1) // len(line)
    return (prefix + line * repeats)[:BODY_BYTES]


def _sample_rss(pid: int, stop: threading.Event, samples: list[int]) -> None:
    status = Path(f"/proc/{pid}/status")
    while not stop.is_set():
        try:
            fields = {}
            for line in status.read_text(encoding="ascii").splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    name, value, _unit = line.split()
                    fields[name.rstrip(":")] = int(value)
            if fields:
                samples.append(max(fields.values()))
        except FileNotFoundError:
            return
        stop.wait(0.01)


def main() -> int:
    binary = _binary()
    max_rss_kib = int(
        os.environ.get("PROMPT_TOON_SERVICE_MAX_RSS_KIB", DEFAULT_MAX_RSS_KIB)
    )
    if max_rss_kib <= 0:
        raise AssertionError("PROMPT_TOON_SERVICE_MAX_RSS_KIB must be positive")

    frames = []
    for index in range(STREAMS):
        request_id = f"capacity-{index:02d}"
        payload = condense_payload(
            request_id,
            [(f"source-{index:02d}.txt", "untrusted_tool_output", _body(index))],
        )
        frames.append(request_frame(request_id, f"stream-{index:02d}", payload))

    proc = subprocess.Popen(
        [
            binary,
            "serve",
            str(WORKERS),
            str(QUEUE_DEPTH),
            str(MAX_DOCS),
            str(MAX_REQUEST_BYTES),
            str(MAX_LABEL_BYTES),
            str(MAX_INPUT_BYTES),
            str(BUDGET_MS),
            str(MAX_CARDS),
            str(MAX_RESPONSE_BYTES),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stop = threading.Event()
    samples: list[int] = []
    sampler = threading.Thread(
        target=_sample_rss, args=(proc.pid, stop, samples), daemon=True
    )
    sampler.start()
    started = time.monotonic()
    try:
        stdout, stderr = proc.communicate(input=b"".join(frames), timeout=120)
    finally:
        stop.set()
        sampler.join(timeout=2)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    elapsed = time.monotonic() - started

    if proc.returncode != 0 or stderr:
        raise AssertionError(
            f"capacity service failed rc={proc.returncode}: {stderr!r}"
        )
    responses = parse_responses(stdout)
    if len(responses) != STREAMS:
        raise AssertionError(f"want {STREAMS} responses, got {len(responses)}")
    for index in range(STREAMS):
        key = (f"capacity-{index:02d}", f"stream-{index:02d}")
        status, body = responses[key]
        if status != "ok":
            raise AssertionError(f"{key[0]} returned {status}: {body!r}")
        events = [json.loads(line) for line in body.splitlines()]
        if not events:
            raise AssertionError(f"{key[0]} returned no JSONL events")
        batch = events[-1]
        cards = batch.get("cards")
        if (
            set(batch) != {"event", "docs", "cards", "withheld"}
            or batch.get("event") != "batch"
            or batch.get("docs") != 1
            or isinstance(cards, bool)
            or not isinstance(cards, int)
            or cards < 0
            or batch.get("withheld") != 0
        ):
            raise AssertionError(f"{key[0]} was withheld or has a bad batch event")

    if not samples:
        raise AssertionError("capacity gate could not sample resident RSS from /proc")
    peak_rss_kib = max(samples)
    if peak_rss_kib > max_rss_kib:
        raise AssertionError(
            f"resident peak RSS {peak_rss_kib} KiB exceeds {max_rss_kib} KiB"
        )
    print(
        f"SERVICE CAPACITY: PASS ({STREAMS} streams; {BODY_BYTES} bytes each; "
        f"peak RSS {peak_rss_kib} KiB <= {max_rss_kib} KiB; {elapsed:.3f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
