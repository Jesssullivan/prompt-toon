"""Bounded Codex launcher and user-profile renderer for Responses probes."""

from __future__ import annotations

import ipaddress
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


PROFILE_NAME = "prompt-toon-shadow"
PROFILE_FILE = f"{PROFILE_NAME}.config.toml"
GATEWAY_TOKEN_ENV = "PROMPT_TOON_CODEX_GATEWAY_TOKEN"

_PROVIDER_ROUTE_ENV = (
    "CHATGPT_BASE_URL",
    "OPENAI_BASE_URL",
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


def validate_loopback_gateway(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("gateway URL must be absolute HTTP(S)")
    if parts.username is not None or parts.password is not None:
        raise ValueError("gateway URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("gateway URL must not contain a query or fragment")
    if parts.path not in ("", "/"):
        raise ValueError("gateway URL must not contain a path")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if not loopback:
        raise ValueError("gateway URL must be loopback")
    return value.rstrip("/")


def render_codex_profile(gateway_url: str, *, auth_mode: str) -> str:
    gateway = validate_loopback_gateway(gateway_url)
    if auth_mode == "openai":
        auth_lines = ["requires_openai_auth = true"]
    elif auth_mode == "api-key":
        auth_lines = [
            f'env_key = "{GATEWAY_TOKEN_ENV}"',
            "requires_openai_auth = false",
        ]
    else:
        raise ValueError(f"unknown Codex auth mode {auth_mode!r}")
    lines = [
        f'model_provider = "{PROFILE_NAME}"',
        "allow_login_shell = false",
        "",
        f"[model_providers.{PROFILE_NAME}]",
        'name = "prompt-toon Responses shadow gateway"',
        f"base_url = {json.dumps(gateway + '/v1')}",
        'wire_api = "responses"',
        "supports_websockets = false",
        "request_max_retries = 0",
        "stream_max_retries = 0",
        *auth_lines,
        "",
        "[shell_environment_policy]",
        'inherit = "core"',
        "ignore_default_excludes = false",
        f'exclude = ["{GATEWAY_TOKEN_ENV}"]',
    ]
    return "\n".join(lines) + "\n"


def codex_profile(
    mode: str,
    gateway_url: str,
    *,
    auth_mode: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if base_env is None else base_env
    if mode == "direct":
        return {
            "mode": "direct",
            "profile_name": None,
            "select_args": [],
            "user_config_mutated": False,
        }
    if mode != "shadow":
        raise ValueError(f"unknown Codex profile mode {mode!r}")
    gateway = validate_loopback_gateway(gateway_url)
    conflicts = sorted(key for key in _PROVIDER_ROUTE_ENV if source.get(key))
    return {
        "mode": "shadow",
        "gateway": gateway,
        "profile_name": PROFILE_NAME,
        "profile_file": f"$CODEX_HOME/{PROFILE_FILE}",
        "select_args": ["--profile", PROFILE_NAME],
        "auth_mode": auth_mode,
        "conflicts": conflicts,
        "toml": render_codex_profile(gateway, auth_mode=auth_mode),
        "user_config_mutated": False,
    }


def _with_loopback_no_proxy(env: dict[str, str], key: str) -> None:
    values = [part.strip() for part in env.get(key, "").split(",") if part.strip()]
    for value in ("127.0.0.1", "localhost", "::1"):
        if value not in values:
            values.append(value)
    env[key] = ",".join(values)


def build_codex_env(
    *,
    codex_home: str | Path,
    gateway_token: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if not gateway_token:
        raise ValueError("Codex gateway token must be non-empty")
    source = os.environ if base_env is None else base_env
    env = {key: source[key] for key in _CHILD_ENV_ALLOWLIST if source.get(key)}
    home = Path(codex_home).resolve()
    isolated_root = home.parent
    env.update(
        {
            "CODEX_HOME": str(home),
            GATEWAY_TOKEN_ENV: gateway_token,
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


def build_codex_command(
    *,
    codex_bin: str,
    model: str,
    fixture: str | Path,
    profile_name: str | None = PROFILE_NAME,
) -> list[str]:
    if not codex_bin:
        raise ValueError("Codex binary path must be non-empty")
    if not model:
        raise ValueError("Codex probe model must be non-empty")
    fixture_path = Path(fixture).resolve()
    command = [
        codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "--config",
        'shell_environment_policy.inherit="core"',
        "--config",
        "shell_environment_policy.ignore_default_excludes=false",
        "--config",
        f'shell_environment_policy.exclude=["{GATEWAY_TOKEN_ENV}"]',
        "--config",
        "allow_login_shell=false",
    ]
    if profile_name is not None:
        command.extend(("--profile", profile_name))
    command.extend(
        (
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "web_search_request",
            "--strict-config",
            "--ephemeral",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--model",
            model,
            "-C",
            str(fixture_path.parent),
            (
                "Use shell_command exactly once to run "
                f"/usr/bin/sed -n p {shlex.quote(str(fixture_path))}. "
                "Then follow the fixture's final-answer instruction exactly."
            ),
        )
    )
    return command


def parse_codex_stream(stdout: str) -> dict[str, Any]:
    final_message: str | None = None
    stream_lines = 0
    completed_turns = 0
    errors = 0
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        stream_lines += 1
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex emitted a non-JSON stream line") from exc
        if not isinstance(value, dict):
            continue
        event_type = value.get("type")
        if event_type == "turn.completed":
            completed_turns += 1
        elif event_type in ("error", "turn.failed"):
            errors += 1
        elif event_type == "item.completed":
            item = value.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    final_message = text
    if completed_turns != 1:
        raise ValueError("Codex stream must contain exactly one completed turn")
    if final_message is None:
        raise ValueError("Codex stream is missing its final agent message")
    return {
        "result": final_message,
        "stream_lines": stream_lines,
        "completed_turns": completed_turns,
        "errors": errors,
    }


def run_codex(
    *,
    command: list[str],
    env: Mapping[str, str],
    cwd: str | Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("Codex timeout must be positive")
    child_env = dict(env)
    child_env["PWD"] = str(Path(cwd).resolve())
    try:
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
        raise RuntimeError("Codex probe could not complete") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"Codex probe exited with status {completed.returncode}")
    parsed = parse_codex_stream(completed.stdout)
    if parsed["errors"]:
        raise RuntimeError("Codex probe returned an error event")
    return parsed
