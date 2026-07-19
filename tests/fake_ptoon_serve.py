#!/usr/bin/env python3
"""Deterministic fake for ResidentEngine pipe-protocol tests."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import BinaryIO


EXPECTED_RUNTIME_ENV = {
    "CHPL_RT_NUM_THREADS_PER_LOCALE": "2",
    "QT_NUM_SHEPHERDS": "1",
    "QT_NUM_WORKERS_PER_SHEPHERD": "2",
}


@dataclass(frozen=True)
class Request:
    request_id: str
    stream_id: str
    max_input_bytes: int
    budget_ms: int
    max_cards: int
    payload: bytes


def reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def read_line_int(
    stream: BinaryIO, what: str, maximum: int, *, allow_eof: bool = False
) -> int | None:
    line = stream.readline(22)
    if line == b"":
        if allow_eof:
            return None
        raise ValueError(f"unexpected EOF reading {what}")
    if not line.endswith(b"\n"):
        raise ValueError(f"overlong {what}")
    digits = line[:-1]
    if not digits or len(digits) > 20 or not digits.isdigit():
        raise ValueError(f"invalid {what}")
    value = int(digits)
    if value > maximum:
        raise ValueError(f"{what} exceeds limit")
    return value


def read_exact(stream: BinaryIO, length: int, what: str) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = stream.read(length - len(result))
        if not chunk:
            raise ValueError(f"truncated {what}")
        result += chunk
    return bytes(result)


def decode_id(raw: bytes, what: str) -> str:
    if not raw or any(byte < 0x21 or byte > 0x7E for byte in raw):
        raise ValueError(f"invalid {what}")
    return raw.decode("ascii")


def read_request(
    stream: BinaryIO,
    max_request_bytes: int,
    max_label_bytes: int,
    max_input_bytes_limit: int,
    max_budget_ms: int,
    max_cards_limit: int,
) -> Request | None:
    request_len = read_line_int(
        stream, "request id length", max_label_bytes, allow_eof=True
    )
    if request_len is None:
        return None
    request_id = decode_id(read_exact(stream, request_len, "request id"), "request id")
    stream_len = read_line_int(stream, "stream id length", max_label_bytes)
    assert stream_len is not None
    stream_id = decode_id(read_exact(stream, stream_len, "stream id"), "stream id")
    max_input_bytes = read_line_int(stream, "maxInputBytes", max_input_bytes_limit)
    budget_ms = read_line_int(stream, "budgetMs", max_budget_ms)
    max_cards = read_line_int(stream, "maxCards", max_cards_limit)
    payload_len = read_line_int(stream, "payload length", max_request_bytes)
    assert None not in (max_input_bytes, budget_ms, max_cards, payload_len)
    if max_input_bytes <= 0 or budget_ms <= 0 or max_cards <= 0:
        raise ValueError("policy values must be positive")
    payload = read_exact(stream, payload_len, "payload")
    return Request(
        request_id,
        stream_id,
        max_input_bytes,
        budget_ms,
        max_cards,
        payload,
    )


def parse_payload(
    raw: bytes, max_docs: int, max_request_bytes: int, max_label_bytes: int
) -> tuple[dict[str, object], list[tuple[str, str, bytes]]]:
    if len(raw) > max_request_bytes:
        raise ValueError("payload exceeds request limit")
    pos = 0

    def field(what: str, *, label: bool = True) -> bytes:
        nonlocal pos
        newline = raw.find(b"\n", pos)
        if newline < 0:
            raise ValueError(f"missing {what} length")
        digits = raw[pos:newline]
        if not digits or not digits.isdigit():
            raise ValueError(f"invalid {what} length")
        length = int(digits)
        pos = newline + 1
        if label and length > max_label_bytes:
            raise ValueError(f"{what} exceeds label limit")
        if pos + length > len(raw):
            raise ValueError(f"truncated {what}")
        value = raw[pos : pos + length]
        pos += length
        return value

    run_id = field("run id").decode("utf-8")
    generated_at = field("generated at").decode("utf-8")
    savings_text = field("min toon savings").decode("utf-8")
    default_tier = field("default tier").decode("utf-8")
    overrides_text = field("tier overrides").decode("utf-8")
    savings = json.loads(savings_text, parse_constant=reject_json_constant)
    if isinstance(savings, bool) or not isinstance(savings, (int, float)):
        raise ValueError("min toon savings is not a number")
    overrides = json.loads(overrides_text)
    if not isinstance(overrides, dict):
        raise ValueError("tier overrides is not an object")

    newline = raw.find(b"\n", pos)
    if newline < 0 or not raw[pos:newline].isdigit():
        raise ValueError("invalid document count")
    count = int(raw[pos:newline])
    pos = newline + 1
    if count > max_docs:
        raise ValueError("document count exceeds limit")
    docs: list[tuple[str, str, bytes]] = []
    for index in range(count):
        source = field(f"doc {index} source").decode("utf-8")
        tier = field(f"doc {index} trust tier").decode("utf-8")
        body = field(f"doc {index} body", label=False)
        docs.append((source, tier, body))
    if pos != len(raw):
        raise ValueError("trailing payload bytes")
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "min_toon_savings": savings,
        "default_tier": default_tier,
        "overrides": overrides,
    }, docs


def json_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def condense_body(request: Request, max_docs: int, max_request: int, max_label: int) -> bytes:
    header, docs = parse_payload(request.payload, max_docs, max_request, max_label)
    if header["run_id"] != request.request_id:
        raise ValueError("request id does not match condense run id")

    output = bytearray()
    inputs: list[dict[str, object]] = []
    withheld_count = 0
    for index, (source, trust_tier, raw_body) in enumerate(docs):
        digest = hashlib.sha256(raw_body).hexdigest()
        withheld = len(raw_body) > request.max_input_bytes
        event: dict[str, object] = {
            "event": "doc",
            "i": index,
            "source": source,
            "trust_tier": trust_tier,
            "bytes": len(raw_body),
            "sha256": digest,
            "withheld": withheld,
            "findings": [],
        }
        if withheld:
            event["reason"] = "input-cap"
            withheld_count += 1
        output += json_line(event)
        output += json_line({"event": "end", "i": index, "cards": 0})
        inputs.append(
            {
                "bytes": len(raw_body),
                "sha256": digest,
                "source": source,
                "trust_tier": trust_tier,
            }
        )

    output += json_line({"event": "summary", "text": f"summary:{request.request_id}"})
    output += json_line(
        {
            "event": "manifest",
            "manifest": {
                "id": request.request_id,
                "generated_at": header["generated_at"],
                "inputs": inputs,
                "mixed_trust_tiers": len({tier for _, tier, _ in docs}) > 1,
                "settings": {
                    "format": "jsonl",
                    "handoff_format": "compact-source-index-v1",
                    "max_cards_per_input": request.max_cards,
                    "min_toon_savings": header["min_toon_savings"],
                    "trust_tier": header["default_tier"],
                    "input_tier_overrides": header["overrides"],
                    "store_raw": False,
                },
            },
        }
    )
    output += json_line(
        {"event": "batch", "docs": len(docs), "cards": 0, "withheld": withheld_count}
    )
    return bytes(output)


def frame(request_id: str, stream_id: str, status: str, body: bytes) -> bytes:
    fields = (
        request_id.encode("utf-8"),
        stream_id.encode("utf-8"),
        status.encode("ascii"),
        body,
    )
    return b"".join(str(len(value)).encode("ascii") + b"\n" + value for value in fields)


def response_for(
    request: Request,
    max_docs: int,
    max_request: int,
    max_label: int,
    max_response: int,
) -> bytes:
    if request.request_id.startswith("oversized-response"):
        fields = (
            request.request_id.encode("ascii"),
            request.stream_id.encode("ascii"),
            b"ok",
        )
        prefix = b"".join(
            str(len(value)).encode("ascii") + b"\n" + value for value in fields
        )
        return prefix + f"{max_response + 1}\n".encode("ascii")
    if request.request_id.startswith("oversized-error"):
        fields = (
            request.request_id.encode("ascii"),
            request.stream_id.encode("ascii"),
            b"error",
        )
        prefix = b"".join(
            str(len(value)).encode("ascii") + b"\n" + value for value in fields
        )
        return prefix + b"65537\n"
    if request.request_id.startswith("bad-body"):
        return frame(request.request_id, request.stream_id, "ok", b"not-jsonl\n")
    if request.request_id.startswith("bad-status"):
        return frame(request.request_id, request.stream_id, "bogus", b"")
    if request.request_id.startswith("wrong-stream"):
        return frame(request.request_id, "wrong", "error", b"wrong stream")
    if request.request_id.startswith("error-control"):
        return frame(
            request.request_id, request.stream_id, "error", b"bad\x1b[31m\n"
        )
    if request.request_id.startswith("error"):
        return frame(request.request_id, request.stream_id, "error", b"forced error")
    try:
        body = condense_body(request, max_docs, max_request, max_label)
    except Exception as exc:
        return frame(request.request_id, request.stream_id, "error", str(exc).encode("utf-8"))
    return frame(request.request_id, request.stream_id, "ok", body)


def main() -> int:
    if any(os.environ.get(key) != value for key, value in EXPECTED_RUNTIME_ENV.items()):
        print("bounded Chapel runtime environment is missing", file=sys.stderr)
        return 3
    if sys.argv[1:] == ["caps"]:
        sys.stdout.write(
            '{"engine":"chapel","serve_protocol":1,"features":["serve"]}'
        )
        return 0
    if len(sys.argv) != 11 or sys.argv[1] != "serve":
        print(
            "want: serve workers queueDepth maxDocs maxRequestBytes "
            "maxLabelBytes maxInputBytes maxBudgetMs maxCards maxResponseBytes",
            file=sys.stderr,
        )
        return 2
    try:
        (
            workers,
            queue_depth,
            max_docs,
            max_request,
            max_label,
            max_input,
            max_budget,
            max_cards,
            max_response,
        ) = map(int, sys.argv[2:])
    except ValueError:
        print("serve args must be integers", file=sys.stderr)
        return 2
    if min(
        workers,
        queue_depth,
        max_docs,
        max_request,
        max_label,
        max_input,
        max_budget,
        max_cards,
        max_response,
    ) <= 0:
        print("serve args must be positive", file=sys.stderr)
        return 2
    if max_input > max_request:
        print("maxInputBytes cannot exceed maxRequestBytes", file=sys.stderr)
        return 2
    if max_response < max_request:
        print("maxResponseBytes cannot be smaller than maxRequestBytes", file=sys.stderr)
        return 2

    held: list[Request] = []
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    try:
        while True:
            request = read_request(
                stdin,
                max_request,
                max_label,
                max_input,
                max_budget,
                max_cards,
            )
            if request is None:
                return 0
            if request.request_id.startswith("malformed"):
                stdout.write(b"not-a-length\n")
                stdout.flush()
                continue
            if request.request_id.startswith("hold"):
                held.append(request)
                continue
            stdout.write(
                response_for(
                    request, max_docs, max_request, max_label, max_response
                )
            )
            if request.request_id.startswith("release"):
                for blocked in held:
                    stdout.write(
                        response_for(
                            blocked, max_docs, max_request, max_label, max_response
                        )
                    )
                held.clear()
            stdout.flush()
    except (BrokenPipeError, ValueError) as exc:
        if not isinstance(exc, BrokenPipeError):
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
