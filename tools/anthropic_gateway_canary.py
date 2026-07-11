#!/usr/bin/env python3
"""Explicitly gated live canary for the local Anthropic shadow gateway."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> tuple[int, dict[str, str], Any]:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SystemExit("gateway URL must be absolute HTTP(S)")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if not loopback:
        raise SystemExit("live canary gateway URL must be loopback")
    connection_type = (
        http.client.HTTPSConnection
        if parts.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(parts.hostname, parts.port, timeout=timeout)
    request_path = f"{parts.path.rstrip('/')}{path}"
    connection.request(method, request_path, body=body, headers=headers or {})
    response = connection.getresponse()
    raw = response.read(2 * 1024 * 1024 + 1)
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    status = response.status
    connection.close()
    if len(raw) > 2 * 1024 * 1024:
        raise SystemExit("canary response exceeded 2 MiB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("canary response was not JSON") from exc
    return status, response_headers, value


def counter(metrics: dict[str, Any], name: str) -> int:
    counters = metrics.get("counters", {})
    if not isinstance(counters, dict):
        return 0
    return int(counters.get(name, 0))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gateway",
        default=os.environ.get(
            "PROMPT_TOON_GATEWAY_URL", "http://127.0.0.1:8787"
        ),
    )
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_CANARY_MODEL"))
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("PROMPT_TOON_LIVE_CANARY") != "1":
        raise SystemExit("set PROMPT_TOON_LIVE_CANARY=1 to authorize a billed API call")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is required")
    if not args.model:
        raise SystemExit("set ANTHROPIC_CANARY_MODEL or pass --model")

    health_status, _, health = request_json(
        args.gateway, "GET", "/__prompt_toon/health", timeout=args.timeout
    )
    if health_status != 200 or not health.get("engine_available"):
        raise SystemExit("gateway is not healthy with the resident engine available")
    _, _, before = request_json(
        args.gateway, "GET", "/__prompt_toon/metrics", timeout=args.timeout
    )
    before_completed = counter(before, "shadow_completed")
    terminal_failures = (
        "shadow_failed",
        "shadow_dropped_capacity",
        "shadow_skipped_engine_unavailable",
    )
    before_failures = {name: counter(before, name) for name in terminal_failures}
    before_withheld = counter(before, "shadow_documents_withheld")

    payload = {
        "model": args.model,
        "max_tokens": 8,
        "tools": [
            {
                "name": "Task",
                "description": "Return already-completed canary research.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "messages": [
            {"role": "user", "content": "Use the completed canary task result."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_prompt_toon_canary",
                        "name": "Task",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_prompt_toon_canary",
                        "content": "Canary result: preserve provenance and report one fact.",
                    }
                ],
            },
        ],
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(raw_payload)),
        "x-api-key": api_key,
        "anthropic-version": os.environ.get(
            "ANTHROPIC_VERSION", "2023-06-01"
        ),
    }
    beta = os.environ.get("ANTHROPIC_BETA")
    if beta:
        headers["anthropic-beta"] = beta
    status, response_headers, response = request_json(
        args.gateway,
        "POST",
        "/v1/messages",
        body=raw_payload,
        headers=headers,
        timeout=args.timeout,
    )
    if status // 100 != 2:
        request_id = response_headers.get("request-id", "unavailable")
        raise SystemExit(f"provider canary failed: status={status} request_id={request_id}")

    deadline = time.monotonic() + 5.0
    after: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, _, after = request_json(
            args.gateway, "GET", "/__prompt_toon/metrics", timeout=args.timeout
        )
        if counter(after, "shadow_completed") > before_completed:
            break
        failed = [
            name
            for name in terminal_failures
            if counter(after, name) > before_failures[name]
        ]
        if failed:
            raise SystemExit(f"shadow canary failed: {','.join(failed)}")
        time.sleep(0.05)
    if counter(after, "shadow_completed") <= before_completed:
        raise SystemExit("provider returned, but shadow analysis did not settle")

    counters = after.get("counters", {})
    result = {
        "status": "PASS",
        "request_id": response_headers.get("request-id", "unavailable"),
        "requested_model": args.model,
        "returned_model": response.get("model", "unavailable"),
        "usage": response.get("usage", {}),
        "shadow_completed_delta": counters.get("shadow_completed", 0)
        - before_completed,
        "shadow_failed_delta": counters.get("shadow_failed", 0)
        - before_failures["shadow_failed"],
        "shadow_documents_withheld_delta": counters.get(
            "shadow_documents_withheld", 0
        )
        - before_withheld,
    }
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
