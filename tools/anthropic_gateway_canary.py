#!/usr/bin/env python3
"""Explicitly gated live Claude Code canary for a dedicated shadow gateway."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from prompt_toon.claude_harness import (
    build_claude_command,
    build_claude_env,
    run_claude,
    validate_loopback_url,
)
from prompt_toon.cli import default_io_policy_path
from prompt_toon.engine import EngineError
from prompt_toon.gateway import (
    DEFAULT_UPSTREAM,
    GatewayConfig,
    GatewayMetrics,
    GatewayPolicy,
    ShadowAnalyzer,
    create_gateway_server,
)
from prompt_toon.resident import ResidentEngine


_MARKER = "PROMPT_TOON_CLAUDE_CANARY_OK"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_FAILURE_COUNTERS = (
    "downstream_disconnects",
    "response_telemetry_unavailable",
    "shadow_dropped_capacity",
    "shadow_failed",
    "shadow_skipped_engine_unavailable",
    "shadow_timeouts",
    "sse_error_events",
    "upstream_framing_rejected",
    "upstream_response_read_failures",
    "upstream_transport_failures",
)


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    timeout: float,
) -> tuple[int, Any]:
    try:
        validated = validate_loopback_url(base_url)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    parts = urlsplit(validated)
    connection_type = (
        http.client.HTTPSConnection
        if parts.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(parts.hostname, parts.port, timeout=timeout)
    request_path = f"{parts.path.rstrip('/')}{path}"
    connection.request(method, request_path)
    response = connection.getresponse()
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    status = response.status
    connection.close()
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("canary response exceeded 2 MiB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("canary response was not JSON") from exc
    return status, value


def _mapping(value: object, key: str) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    selected = value.get(key, {})
    if not isinstance(selected, dict):
        return {}
    return {
        str(name): int(amount)
        for name, amount in selected.items()
        if isinstance(amount, int) and not isinstance(amount, bool)
    }


def counter(metrics: dict[str, Any], name: str) -> int:
    return _mapping(metrics, "counters").get(name, 0)


def _model_mapping(metrics: dict[str, Any], kind: str) -> dict[str, int]:
    models = metrics.get("models", {})
    return _mapping(models, kind)


def _stop_gateway(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=10)
    server.server_close()
    if thread.is_alive():
        raise RuntimeError("dedicated canary gateway did not stop")


def _wait_for_shadow(
    metrics: GatewayMetrics,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + min(timeout, 10.0)
    waiter = threading.Event()
    while time.monotonic() < deadline:
        snapshot = metrics.snapshot()
        if any(counter(snapshot, name) for name in _FAILURE_COUNTERS):
            failures = [name for name in _FAILURE_COUNTERS if counter(snapshot, name)]
            raise RuntimeError(f"shadow canary failed: {','.join(failures)}")
        if counter(snapshot, "shadow_completed") == 1:
            return snapshot
        waiter.wait(0.05)
    raise RuntimeError("Claude Code returned, but shadow analysis did not settle")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_CANARY_MODEL"))
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=os.environ.get("ANTHROPIC_CANARY_MAX_BUDGET_USD"),
    )
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--policy", default=default_io_policy_path())
    parser.add_argument("--ptoon")
    parser.add_argument(
        "--upstream",
        default=os.environ.get("PROMPT_TOON_ANTHROPIC_UPSTREAM", DEFAULT_UPSTREAM),
    )
    return parser


def _fixture_text() -> str:
    padding = "".join(
        f"Reference line {index:03d}: deterministic gateway canary context.\n"
        for index in range(1, 101)
    )
    return (
        "This is a bounded prompt-toon gateway canary.\n"
        "Preserve provenance and use no additional tools.\n"
        f"Your final answer must be exactly {_MARKER}.\n"
        + padding
    )


def _run_dedicated_canary(
    args: argparse.Namespace, api_key: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = GatewayPolicy.load(args.policy)
    try:
        engine = ResidentEngine(
            binary_path=args.ptoon,
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
    except EngineError as exc:
        raise RuntimeError(str(exc)) from exc
    metrics = GatewayMetrics()
    analyzer = ShadowAnalyzer(policy, metrics, engine)
    config = GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        upstream=args.upstream,
        max_request_bytes=policy.max_request_bytes,
        max_concurrent_requests=policy.max_concurrent_streams,
        upstream_timeout_seconds=args.timeout,
    )
    try:
        server = create_gateway_server(config, analyzer, metrics)
    except Exception:
        analyzer.close()
        raise
    thread = threading.Thread(
        target=server.serve_forever,
        name="prompt-toon-dedicated-live-canary",
        daemon=True,
    )
    thread.start()
    gateway_url = f"http://127.0.0.1:{server.server_port}"
    try:
        ready_status, readiness = request_json(
            gateway_url, "GET", "/__prompt_toon/ready", timeout=args.timeout
        )
        if ready_status != 200 or readiness.get("status") != "ready":
            raise RuntimeError("dedicated gateway did not become locally ready")
        with tempfile.TemporaryDirectory(prefix="prompt-toon-live-canary-") as tmp:
            root = Path(tmp)
            fixture = root / "canary.txt"
            fixture.write_text(_fixture_text(), encoding="utf-8")
            command = build_claude_command(
                claude_bin=args.claude_bin,
                model=args.model,
                fixture=fixture,
                max_budget_usd=args.max_budget_usd,
                session_id=str(uuid.uuid4()),
            )
            env = build_claude_env(
                gateway_url=gateway_url,
                api_key=api_key,
                config_dir=root / "claude-config",
            )
            harness = run_claude(
                command=command,
                env=env,
                cwd=root,
                timeout_seconds=args.timeout,
            )
        snapshot = _wait_for_shadow(metrics, timeout=args.timeout)
        ready_status, readiness = request_json(
            gateway_url, "GET", "/__prompt_toon/ready", timeout=args.timeout
        )
        if ready_status != 200 or readiness.get("upstream") != "reachable":
            raise RuntimeError("dedicated gateway did not observe upstream reachability")
        return harness, snapshot
    finally:
        _stop_gateway(server, thread)


def build_report(
    *,
    harness: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    if harness.get("result", "").strip() != _MARKER:
        raise RuntimeError("Claude Code canary did not return the expected marker")
    if harness.get("api_retries"):
        raise RuntimeError("Claude Code retried a provider request")
    expected = {
        "messages_requests": 2,
        "typed_documents_selected": 1,
        "shadow_completed": 1,
        "shadow_documents_completed": 1,
        "upstream_responses": 2,
    }
    for name, value in expected.items():
        if counter(metrics, name) != value:
            raise RuntimeError(
                f"dedicated canary counter {name}={counter(metrics, name)}, expected {value}"
            )
    failures = {name: counter(metrics, name) for name in _FAILURE_COUNTERS}
    failures = {name: value for name, value in failures.items() if value}
    if failures:
        raise RuntimeError("dedicated canary recorded a transport or shadow failure")
    if counter(metrics, "shadow_documents_withheld") != 0:
        raise RuntimeError("dedicated canary transform was withheld")
    raw_tokens = counter(metrics, "estimated_raw_tokens")
    condensed_tokens = counter(metrics, "estimated_condensed_tokens")
    estimated_saved = counter(metrics, "estimated_tokens_saved")
    if raw_tokens <= condensed_tokens or estimated_saved <= 0:
        raise RuntimeError("dedicated canary transform did not reduce estimated tokens")
    quality = _mapping(metrics, "quality")
    if quality != {"eligible": 1}:
        raise RuntimeError("dedicated canary transform was not eligible")
    requested = _model_mapping(metrics, "requested")
    returned = _model_mapping(metrics, "returned")
    if sum(requested.values()) != 2 or sum(returned.values()) != 2:
        raise RuntimeError("dedicated canary model telemetry was incomplete")
    provider_usage = {
        field: counter(metrics, f"provider_{field}")
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }
    if provider_usage["input_tokens"] <= 0 or provider_usage["output_tokens"] <= 0:
        raise RuntimeError("dedicated canary provider usage was incomplete")
    return {
        "status": "PASS",
        "harness": {
            "status": "PASS",
            "client": "claude-code",
            "quality_check": "exact-marker",
            "stream_lines": harness["stream_lines"],
            "api_retries": 0,
        },
        "transport": {
            "status": "PASS",
            "dedicated_gateway": True,
            "messages_requests": 2,
            "sse_error_events": 0,
            "requested_model_count": sum(requested.values()),
            "returned_model_count": sum(returned.values()),
            "provider_usage": provider_usage,
        },
        "transform": {
            "status": "PASS",
            "typed_documents_selected": 1,
            "completed": 1,
            "withheld": 0,
            "estimated_raw_tokens": raw_tokens,
            "estimated_condensed_tokens": condensed_tokens,
            "estimated_transform_tokens_saved": estimated_saved,
            "quality": quality,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("PROMPT_TOON_LIVE_CANARY") != "1":
        raise SystemExit("set PROMPT_TOON_LIVE_CANARY=1 to authorize billed calls")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is required")
    if not args.model:
        raise SystemExit("set ANTHROPIC_CANARY_MODEL or pass --model")
    if args.max_budget_usd is None:
        raise SystemExit(
            "set ANTHROPIC_CANARY_MAX_BUDGET_USD or pass --max-budget-usd"
        )
    if not 0 < args.max_budget_usd <= 1:
        raise SystemExit("Claude canary budget must be in (0, 1]")
    try:
        harness, metrics = _run_dedicated_canary(args, api_key)
        result = build_report(
            harness=harness,
            metrics=metrics,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
