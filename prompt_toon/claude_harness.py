"""Bounded Claude Code launcher for gateway probes and canaries."""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


_PROVIDER_ROUTE_ENV = (
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_AWS_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
)

_CHILD_ENV_ALLOWLIST = (
    "ALL_PROXY",
    "CURL_CA_BUNDLE",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "LOGNAME",
    "NIX_SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TZ",
    "USER",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def validate_loopback_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("gateway URL must be absolute HTTP(S)")
    if parts.username is not None or parts.password is not None:
        raise ValueError("gateway URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("gateway URL must not contain a query or fragment")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if not loopback:
        raise ValueError("gateway URL must be loopback")
    return value.rstrip("/")


def claude_profile(
    mode: str,
    gateway_url: str,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if base_env is None else base_env
    if mode == "direct":
        return {
            "mode": "direct",
            "process_scope": True,
            "unset": ["ANTHROPIC_BASE_URL", "PROMPT_TOON_GATEWAY_URL"],
        }
    if mode != "shadow":
        raise ValueError(f"unknown Claude profile mode {mode!r}")
    gateway = validate_loopback_url(gateway_url)
    conflicts = sorted(key for key in _PROVIDER_ROUTE_ENV if source.get(key))
    return {
        "mode": "shadow",
        "process_scope": True,
        "gateway": gateway,
        "conflicts": conflicts,
    }


def _with_loopback_no_proxy(env: dict[str, str], key: str) -> None:
    values = [part.strip() for part in env.get(key, "").split(",") if part.strip()]
    for value in ("127.0.0.1", "localhost", "::1"):
        if value not in values:
            values.append(value)
    env[key] = ",".join(values)


def build_claude_env(
    *,
    gateway_url: str,
    api_key: str,
    config_dir: str | Path,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if not api_key:
        raise ValueError("Claude gateway API key must be non-empty")
    gateway = validate_loopback_url(gateway_url)
    source = os.environ if base_env is None else base_env
    env = {key: source[key] for key in _CHILD_ENV_ALLOWLIST if source.get(key)}
    isolated_root = Path(config_dir).resolve().parent
    env.update(
        {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": gateway,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CONFIG_DIR": str(Path(config_dir).resolve()),
            "HOME": str(isolated_root),
            "TMPDIR": str(isolated_root),
            "XDG_CACHE_HOME": str(isolated_root / "cache"),
            "XDG_CONFIG_HOME": str(isolated_root / "config"),
            "XDG_STATE_HOME": str(isolated_root / "state"),
        }
    )
    _with_loopback_no_proxy(env, "NO_PROXY")
    _with_loopback_no_proxy(env, "no_proxy")
    return env


def build_claude_command(
    *,
    claude_bin: str,
    model: str,
    fixture: str | Path,
    max_budget_usd: float | None,
    session_id: str,
) -> list[str]:
    if not claude_bin:
        raise ValueError("Claude Code binary path must be non-empty")
    if not model:
        raise ValueError("Claude model must be non-empty")
    if max_budget_usd is not None and not 0 < max_budget_usd <= 1:
        raise ValueError("Claude canary budget must be in (0, 1]")
    try:
        normalized_session_id = str(uuid.UUID(session_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Claude canary session ID must be a UUID") from exc
    fixture_path = str(Path(fixture).resolve())
    read_rule = f"Read(/{fixture_path})"
    command = [
        claude_bin,
        "--bare",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--no-session-persistence",
        "--no-chrome",
        "--session-id",
        normalized_session_id,
        "--model",
        model,
        "--tools",
        "Read",
        "--allowedTools",
        read_rule,
        "--permission-mode",
        "dontAsk",
        "--system-prompt",
        (
            "Use the Read tool exactly once on the file named by the user. "
            "Then follow the file's final-answer instruction exactly."
        ),
    ]
    if max_budget_usd is not None:
        command.extend(("--max-budget-usd", format(max_budget_usd, ".6g")))
    command.append(
        f"Read {fixture_path} and follow its final-answer instruction exactly."
    )
    return command


def parse_claude_stream(stdout: str) -> dict[str, Any]:
    final: dict[str, Any] | None = None
    lines = 0
    api_retries = 0
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        lines += 1
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError("Claude Code emitted a non-JSON stream line") from exc
        if isinstance(value, dict):
            if value.get("type") == "result":
                final = value
            elif value.get("type") == "system" and value.get("subtype") == "api_retry":
                api_retries += 1
    if final is None:
        raise ValueError("Claude Code stream is missing its result event")
    result = final.get("result")
    if not isinstance(result, str):
        raise ValueError("Claude Code result event is missing text")
    return {
        "result": result,
        "stream_lines": lines,
        "is_error": bool(final.get("is_error")),
        "subtype": final.get("subtype"),
        "api_retries": api_retries,
    }


def run_claude(
    *,
    command: list[str],
    env: Mapping[str, str],
    cwd: str | Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("Claude Code timeout must be positive")
    try:
        child_env = dict(env)
        child_env["PWD"] = str(Path(cwd).resolve())
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Claude Code canary could not complete") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            f"Claude Code canary exited with status {completed.returncode}"
        )
    parsed = parse_claude_stream(completed.stdout)
    if parsed["is_error"]:
        raise RuntimeError("Claude Code canary returned an error result")
    return parsed
