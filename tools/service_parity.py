#!/usr/bin/env python3
"""TIN-2792 C4a resident-service parity and bounded-fan-in smoke gate."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_INPUT_BYTES = 2_000_000
BUDGET_MS = 60_000
MAX_CARDS = 24
WORKERS = 2
QUEUE_DEPTH = 8
MAX_DOCS = 64
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_LABEL_BYTES = 4096
MAX_RESPONSE_BYTES = 256 * 1024 * 1024


def _binary() -> str:
    binary = os.environ.get("PROMPT_TOON_PTOON")
    if not binary:
        print("SKIP: PROMPT_TOON_PTOON not set; resident service not executable here.")
        raise SystemExit(0)
    return binary


def _field(value: str | bytes) -> bytes:
    encoded = value.encode() if isinstance(value, str) else value
    return str(len(encoded)).encode() + b"\n" + encoded


def condense_payload(request_id: str, docs: list[tuple[str, str, bytes]]) -> bytes:
    parts = [
        _field(request_id),
        _field("GENERATED_AT"),
        _field("0.2"),
        _field("untrusted_tool_output"),
        _field("{}"),
        str(len(docs)).encode() + b"\n",
    ]
    for source, tier, body in docs:
        parts.extend((_field(source), _field(tier), _field(body)))
    return b"".join(parts)


def request_frame(request_id: str, stream_id: str, payload: bytes) -> bytes:
    return b"".join(
        (
            _field(request_id),
            _field(stream_id),
            f"{MAX_INPUT_BYTES}\n{BUDGET_MS}\n{MAX_CARDS}\n".encode(),
            _field(payload),
        )
    )


def _read_line(raw: bytes, pos: int) -> tuple[bytes, int]:
    end = raw.find(b"\n", pos)
    if end < 0:
        raise AssertionError("resident response has an unterminated length line")
    return raw[pos:end], end + 1


def _read_field(raw: bytes, pos: int, what: str) -> tuple[bytes, int]:
    line, pos = _read_line(raw, pos)
    if not line.isdigit():
        raise AssertionError(f"resident response has invalid {what} length: {line!r}")
    size = int(line)
    end = pos + size
    if end > len(raw):
        raise AssertionError(f"resident response truncates {what}")
    return raw[pos:end], end


def parse_responses(raw: bytes) -> dict[tuple[str, str], tuple[str, bytes]]:
    responses: dict[tuple[str, str], tuple[str, bytes]] = {}
    pos = 0
    while pos < len(raw):
        request_id, pos = _read_field(raw, pos, "request id")
        stream_id, pos = _read_field(raw, pos, "stream id")
        status, pos = _read_field(raw, pos, "status")
        body, pos = _read_field(raw, pos, "body")
        key = (request_id.decode(), stream_id.decode())
        if key in responses:
            raise AssertionError(f"duplicate resident response for {key!r}")
        responses[key] = (status.decode(), body)
    return responses


def run_one_shot(binary: str, payload: bytes) -> bytes:
    proc = subprocess.run(
        [binary, "condense", str(MAX_INPUT_BYTES), str(BUDGET_MS), str(MAX_CARDS)],
        input=payload,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or proc.stderr:
        raise AssertionError(
            f"one-shot condense failed rc={proc.returncode}: {proc.stderr!r}"
        )
    return proc.stdout


def run_service(binary: str, framed: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
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
        input=framed,
        capture_output=True,
        timeout=60,
        check=False,
    )


def main() -> int:
    binary = _binary()
    caps_proc = subprocess.run(
        [binary, "caps"], capture_output=True, timeout=10, check=False
    )
    if caps_proc.returncode != 0 or caps_proc.stderr:
        raise AssertionError(f"ptoon caps failed: {caps_proc.stderr!r}")
    caps = json.loads(caps_proc.stdout)
    if caps.get("serve_protocol") != 1 or "serve" not in caps.get("features", []):
        raise AssertionError(f"ptoon caps does not advertise serve v1: {caps!r}")

    requests: list[tuple[str, str, bytes, bytes]] = []
    for index in range(64):
        request_id = f"service-{index:02d}"
        stream_id = f"stream-{index % 8:02d}"
        body = (
            f"# Source {index}\n"
            f"- request {index} MUST preserve provenance.\n"
            f"- token=ghp_{'x' * 40}\n"
        ).encode()
        payload = condense_payload(
            request_id,
            [(f"source-{index}.md", "untrusted_tool_output", body)],
        )
        requests.append((request_id, stream_id, payload, run_one_shot(binary, payload)))

    started = time.monotonic()
    proc = run_service(
        binary,
        b"".join(request_frame(request_id, stream_id, payload)
                 for request_id, stream_id, payload, _ in requests),
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0 or proc.stderr:
        raise AssertionError(f"resident service failed rc={proc.returncode}: {proc.stderr!r}")
    responses = parse_responses(proc.stdout)
    if len(responses) != len(requests):
        raise AssertionError(f"want {len(requests)} responses, got {len(responses)}")
    for request_id, stream_id, _, expected in requests:
        status, body = responses[(request_id, stream_id)]
        if status != "ok":
            raise AssertionError(f"{request_id}: resident status {status!r}: {body!r}")
        if body != expected:
            raise AssertionError(f"{request_id}: resident output differs from one-shot")

    # A valid outer frame with a bad inner payload is request-scoped: it returns
    # an error and the following request still succeeds in the same process.
    good_id, good_stream, good_payload, good_expected = requests[0]
    recovery = run_service(
        binary,
        request_frame("bad-inner", "stream-bad", b"not-a-condense-frame")
        + request_frame(good_id, good_stream, good_payload),
    )
    if recovery.returncode != 0 or recovery.stderr:
        raise AssertionError(f"request-scoped recovery failed: {recovery.stderr!r}")
    recovered = parse_responses(recovery.stdout)
    if recovered[("bad-inner", "stream-bad")][0] != "error":
        raise AssertionError("malformed inner payload did not return error status")
    if recovered[(good_id, good_stream)] != ("ok", good_expected):
        raise AssertionError("request after malformed inner payload was poisoned")

    # Semantic/parser policy failures are request-scoped. None may poison the
    # shared runtime or the healthy request that follows them.
    over_budget = request_frame("over-budget", "stream-over", condense_payload(
        "over-budget", [("source.md", "untrusted_tool_output", b"bounded")]
    )).replace(
        f"{BUDGET_MS}\n".encode(), f"{BUDGET_MS + 1}\n".encode(), 1
    )
    too_many_docs = request_frame(
        "too-many-docs",
        "stream-docs",
        condense_payload(
            "too-many-docs",
            [(f"source-{index}", "untrusted_tool_output", b"")
             for index in range(MAX_DOCS + 1)],
        ),
    )
    long_label = request_frame(
        "long-label",
        "stream-label",
        condense_payload(
            "long-label", [("s" * (MAX_LABEL_BYTES + 1), "repo_source", b"")]
        ),
    )
    mismatched_id = request_frame(
        "outer-id",
        "stream-mismatch",
        condense_payload("inner-id", [("source.md", "repo_source", b"")]),
    )
    rejected = run_service(
        binary,
        over_budget
        + too_many_docs
        + long_label
        + mismatched_id
        + request_frame(good_id, good_stream, good_payload),
    )
    if rejected.returncode != 0 or rejected.stderr:
        raise AssertionError(f"policy-scoped recovery failed: {rejected.stderr!r}")
    policy_responses = parse_responses(rejected.stdout)
    for key in (
        ("over-budget", "stream-over"),
        ("too-many-docs", "stream-docs"),
        ("long-label", "stream-label"),
        ("outer-id", "stream-mismatch"),
    ):
        if policy_responses[key][0] != "error":
            raise AssertionError(f"resident policy violation {key!r} was not rejected")
    if policy_responses[(good_id, good_stream)] != ("ok", good_expected):
        raise AssertionError("policy violation poisoned the following request")

    # Malformed outer framing cannot be resynchronized, so the process fails
    # closed after EOF and must not hang.
    truncated = run_service(binary, b"5\nabc")
    if truncated.returncode == 0:
        raise AssertionError("truncated outer frame unexpectedly succeeded")
    if truncated.stdout:
        raise AssertionError("truncated outer frame emitted a response")

    print(
        f"SERVICE PARITY: PASS ({len(requests)}/{len(requests)}; "
        f"{elapsed:.3f}s resident wall time)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
