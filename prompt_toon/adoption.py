"""Deterministic C4d adoption contract data.

This module describes how a fleet manager may consume the local shadow
gateways. It is intentionally declarative: no provider requests, releases, or
host mutations happen here.
"""

from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import secrets
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .codex_harness import (
    GATEWAY_TOKEN_ENV,
    LOOPBACK_NO_PROXY_VALUES,
    PROFILE_FILE,
    PROFILE_NAME,
    PROXY_BYPASS_CONFLICT,
    PROXY_ENV,
    loopback_proxy_bypass_configured,
    render_codex_profile,
    validate_loopback_gateway,
)
from .claude_harness import (
    CLAUDE_PROXY_ENV,
    CLAUDE_ROUTE_CONFLICT_ENV,
    claude_route_conflicts,
)
from .resident import BOUNDED_CHAPEL_RUNTIME_ENV

SCHEMA_VERSION = 2
CONTRACT_PATH = "packaging/home-manager.json"

ANTHROPIC_GATEWAY_URL = "http://127.0.0.1:8787"
OPENAI_GATEWAY_URL = "http://127.0.0.1:8788"

_PTOON_BINARY = "<ptoon-binary>"
_POLICY_FILE = "<prompt-toon-io-policy-json>"
_MAX_DOCTOR_RESPONSE_BYTES = 64 * 1024
_SAFE_METRIC_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)


def _gateway_command(
    *,
    subcommand: str,
    port: int,
    upstream: str,
    client_token_file: str,
    upstream_token_file: str,
) -> list[str]:
    return [
        "prompt-toon",
        subcommand,
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
        "--upstream",
        upstream,
        "--policy",
        _POLICY_FILE,
        "--ptoon",
        _PTOON_BINARY,
        "--client-token-file",
        client_token_file,
        "--upstream-token-file",
        upstream_token_file,
        "--require-ptoon",
        "--require-split-auth",
    ]


def _codex_profile() -> str:
    return render_codex_profile(OPENAI_GATEWAY_URL, auth_mode="api-key")


def build_home_manager_contract(
    *,
    version: str,
    policy_digests: Mapping[str, str],
) -> dict[str, Any]:
    """Build the Home Manager consumption contract as deterministic data."""
    policy = {
        "delegation": {
            "file": "policy/delegation.json",
            "sha256": policy_digests["policy/delegation.json"],
        },
        "io": {
            "file": "policy/io.json",
            "sha256": policy_digests["policy/io.json"],
        },
    }
    codex_profile = _codex_profile()
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "prompt-toon-home-manager-consumption",
        "version": version,
        "contract_path": CONTRACT_PATH,
        "activation_default": False,
        "mode": "shadow",
        "policy": policy,
        "activation": {
            "default": False,
            "mode": "shadow",
            "enforcement": {
                "locked": True,
                "source": "policy/io.json",
                "reason": "C4d publishes a consumption contract only; enforcement remains gated by the IO policy.",
            },
        },
        "release_artifact": {
            "required": True,
            "minimum_version": "0.3.0",
            "manifest": "packaging/manifest.json",
            "release_signers": "packaging/release-signers.json",
            "closure_format": "nix-store-export-v1",
            "verify": [
                "openpgp_tag",
                "openpgp_manifest_signature",
                "target_archive_sha256",
                "entrypoint_sha256",
            ],
            "platform_verify": {
                "aarch64-darwin": [
                    "build_provenance.nix.store_path",
                    "build_provenance.nix.entrypoint.store_path",
                ],
            },
            "tagged_flake_rebuild_satisfies_byte_identity": False,
        },
        "services": [
            {
                "id": "anthropic",
                "provider": "anthropic",
                "protocol": "anthropic_messages",
                "auth_mode": "split",
                "listen": {"host": "127.0.0.1", "port": 8787},
                "reviewed_upstream_default": "https://api.anthropic.com",
                "environment": BOUNDED_CHAPEL_RUNTIME_ENV,
                "command": _gateway_command(
                    subcommand="gateway",
                    port=8787,
                    upstream="https://api.anthropic.com",
                    client_token_file="<anthropic-client-token-file>",
                    upstream_token_file="<anthropic-upstream-token-file>",
                ),
            },
            {
                "id": "openai",
                "provider": "openai",
                "protocol": "openai_responses",
                "auth_mode": "split",
                "listen": {"host": "127.0.0.1", "port": 8788},
                "reviewed_upstream_default": "https://api.openai.com/v1",
                "environment": BOUNDED_CHAPEL_RUNTIME_ENV,
                "command": _gateway_command(
                    subcommand="responses-gateway",
                    port=8788,
                    upstream="https://api.openai.com/v1",
                    client_token_file="<openai-client-token-file>",
                    upstream_token_file="<openai-upstream-token-file>",
                ),
            },
        ],
        "client_request_paths": {
            "claude": {
                "mode": "shadow",
                "env": {
                    "ANTHROPIC_API_KEY": {
                        "source": "local_client_token_file",
                        "service": "anthropic",
                    },
                    "ANTHROPIC_BASE_URL": ANTHROPIC_GATEWAY_URL,
                    "NO_PROXY": ",".join(LOOPBACK_NO_PROXY_VALUES),
                    "no_proxy": ",".join(LOOPBACK_NO_PROXY_VALUES),
                },
                "reject_if_set": list(CLAUDE_ROUTE_CONFLICT_ENV),
                "proxy_policy": {
                    "preserve": list(CLAUDE_PROXY_ENV),
                    "require_loopback_bypass": list(LOOPBACK_NO_PROXY_VALUES),
                    "require_both_no_proxy_casings": True,
                },
                "rollback": {
                    "unset": [
                        "ANTHROPIC_API_KEY",
                        "ANTHROPIC_BASE_URL",
                        "PROMPT_TOON_GATEWAY_URL",
                    ],
                    "restore": ["NO_PROXY", "no_proxy"],
                    "provider_auth": "restore from direct-provider secret custody",
                    "description": "Direct rollback removes the managed local token and route before restoring direct-provider auth.",
                },
            },
            "codex": {
                "mode": "shadow",
                "profile_name": PROFILE_NAME,
                "profile_file": "$CODEX_HOME/prompt-toon-shadow.config.toml",
                "profile_toml_sha256": sha256(
                    codex_profile.encode("utf-8")
                ).hexdigest(),
                "profile_toml": codex_profile,
                "local_token_env": GATEWAY_TOKEN_ENV,
                "select_args": ["--profile", PROFILE_NAME],
                "environment": {
                    "NO_PROXY": ",".join(LOOPBACK_NO_PROXY_VALUES),
                    "no_proxy": ",".join(LOOPBACK_NO_PROXY_VALUES),
                },
                "reject_if_set": ["CHATGPT_BASE_URL", "OPENAI_BASE_URL"],
                "proxy_policy": {
                    "preserve": list(PROXY_ENV),
                    "require_loopback_bypass": list(LOOPBACK_NO_PROXY_VALUES),
                    "require_both_no_proxy_casings": True,
                },
                "rollback": {
                    "select_args": [],
                    "unset": [GATEWAY_TOKEN_ENV],
                    "description": "Direct-provider rollback omits --profile and removes the inert local gateway token.",
                },
            },
        },
        "endpoints": {
            "ownership": {
                "path": "/__prompt_toon/ownership",
                "auth": "HMAC-SHA256 over a versioned canonical attestation plus the nonce, keyed by the local client token; the token is never sent to an unverified listener",
                "success": {"status": "owned", "auth_mode": "split"},
                "attests": [
                    "ownership_endpoint",
                    "instance_id",
                    "service_version",
                    "provider",
                    "upstream_url",
                    "policy_sha256",
                    "resident_binary_sha256",
                    "accepting",
                    "engine_available",
                    "upstream",
                ],
                "purpose": "Challenge-response proof that the bound loopback port is the expected prompt-toon instance before clients are routed.",
            },
            "readiness": {
                "path": "/__prompt_toon/ready",
                "semantics": [
                    "HTTP 200 reports local readiness only after authenticated ownership has identified the listener.",
                    "upstream=unknown is valid before a provider request.",
                    "readiness alone is not provider reachability.",
                    "provider reachability is only established after an authenticated request path observes upstream=reachable.",
                ],
            },
            "metrics": {
                "path": "/__prompt_toon/metrics",
                "semantics": [
                    "Aggregate-only counters and process-local HMAC model buckets.",
                    "No request bodies or credential values.",
                    "Use with readiness and ownership proof; metrics alone are not activation proof.",
                ],
            },
        },
        "activation_order": [
            {
                "step": 1,
                "name": "start-bind",
                "requirement": "Start the selected loopback service and bind 127.0.0.1 before client exposure.",
            },
            {
                "step": 2,
                "name": "authenticated-ownership-proof",
                "requirement": "Challenge /__prompt_toon/ownership without sending the token; require a valid HMAC response, status=owned, auth_mode=split, and the reviewed policy/binary/upstream digests.",
            },
            {
                "step": 3,
                "name": "expose-client-request-path",
                "requirement": "Only after ownership proof, expose the Claude env or select the otherwise inert Codex profile.",
            },
        ],
        "host_ledger": {
            "schema_version": 1,
            "write_after_activation": True,
            "claim_boundary": "Each record is a host observation; the source contract contains no fleet health claims.",
            "required_fields": [
                "host",
                "observed_at",
                "service_health",
                "active_binary",
                "request_paths",
                "shadow_decisions",
                "rollback",
                "policy_sha256",
            ],
            "sources": {
                "service_health": "doctor.adoption.gateways.*.readiness and ownership",
                "active_binary": "doctor.adoption.gateways.*.ownership.payload.resident_binary_sha256, correlated with doctor.engines.chapel",
                "request_paths": "doctor.adoption.request_paths plus post-activation request counters; profile presence alone is not traffic proof",
                "shadow_decisions": "doctor.adoption.gateways.*.metrics.payload counters and quality",
                "rollback": "client_request_paths.*.rollback",
                "policy_sha256": "doctor.adoption.policy.sha256",
            },
        },
        "safety": {
            "credentials": "placeholder-paths-only",
            "credential_files": {
                "regular_files": True,
                "owner_only": True,
                "symlinks_allowed": False,
                "client_and_upstream_distinct": True,
                "values_never_enter_contract": True,
            },
            "local_client_token": {
                "classification": "sensitive billed-relay capability",
                "ownership_probe_sends_token": False,
                "rotate_after_ownership_failure": True,
                "residual_trust": "Normal Claude/Codex requests use the token as a bearer credential, so loopback and same-user process isolation remain required.",
            },
            "model_pins": False,
            "provider_calls": False,
            "release": False,
            "host_mutation": False,
            "activation_default": False,
        },
    }


def render_contract(contract: Mapping[str, Any]) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


def _bounded_metric_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, amount in sorted(value.items())[:256]:
        if (
            isinstance(key, str)
            and 1 <= len(key) <= 64
            and all(char in _SAFE_METRIC_NAME for char in key)
            and isinstance(amount, int)
            and not isinstance(amount, bool)
            and 0 <= amount <= 2**63 - 1
        ):
            result[key] = amount
    return result


def _safe_gateway_payload(kind: str, value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if kind == "metrics":
        return {
            "schema_version": value.get("schema_version")
            if isinstance(value.get("schema_version"), int)
            else None,
            "mode": value.get("mode") if value.get("mode") == "shadow" else None,
            "counters": _bounded_metric_map(value.get("counters")),
            "quality": _bounded_metric_map(value.get("quality")),
        }

    allowed: dict[str, tuple[type, frozenset[Any] | None]] = {
        "schema_version": (int, None),
        "status": (str, frozenset({"owned", "ready", "not_ready"})),
        "mode": (str, frozenset({"shadow"})),
        "provider": (str, frozenset({"anthropic", "openai"})),
        "auth_mode": (str, frozenset({"split"})),
        "accepting": (bool, None),
        "engine_available": (bool, None),
        "upstream": (str, frozenset({"unknown", "reachable", "failed"})),
    }
    result: dict[str, Any] = {}
    for key, (expected_type, choices) in allowed.items():
        item = value.get(key)
        if isinstance(item, expected_type) and (choices is None or item in choices):
            result[key] = item
    if kind == "ownership":
        for key, pattern in (
            ("instance_id", r"[0-9a-f]{32}"),
            ("service_version", r"[A-Za-z0-9._+-]{1,64}"),
            ("challenge_response", r"[0-9a-f]{64}"),
        ):
            item = value.get(key)
            if isinstance(item, str) and re.fullmatch(pattern, item):
                result[key] = item
        for key in ("policy_sha256", "resident_binary_sha256"):
            item = value.get(key)
            if item is None or (
                isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
            ):
                result[key] = item
        ownership_endpoint = value.get("ownership_endpoint")
        if isinstance(ownership_endpoint, str) and len(ownership_endpoint) <= 2048:
            try:
                endpoint_parts = urlsplit(ownership_endpoint)
                endpoint_base = validate_loopback_gateway(
                    f"{endpoint_parts.scheme}://{endpoint_parts.netloc}"
                )
                if (
                    endpoint_parts.path == "/__prompt_toon/ownership"
                    and not endpoint_parts.query
                    and not endpoint_parts.fragment
                    and ownership_endpoint == endpoint_base + endpoint_parts.path
                ):
                    result["ownership_endpoint"] = ownership_endpoint
            except ValueError:
                pass
        upstream_url = value.get("upstream_url")
        if isinstance(upstream_url, str) and len(upstream_url) <= 2048:
            parts = urlsplit(upstream_url)
            try:
                port_valid = parts.port is None or 1 <= parts.port <= 65535
            except ValueError:
                port_valid = False
            if port_valid:
                if (
                    parts.scheme in ("http", "https")
                    and parts.hostname
                    and parts.username is None
                    and parts.password is None
                    and not parts.query
                    and not parts.fragment
                    and all(ord(char) >= 0x20 for char in upstream_url)
                ):
                    result["upstream_url"] = upstream_url
    return result


def _loopback_json_get(
    gateway_url: str,
    path: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    kind: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {"reachable": False, "http_status": None}
    try:
        gateway = validate_loopback_gateway(gateway_url)
        parts = urlsplit(gateway)
        if timeout <= 0 or timeout > 30:
            raise ValueError("doctor timeout must be in (0, 30]")
        connection_type = (
            http.client.HTTPSConnection
            if parts.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(parts.hostname, parts.port, timeout=timeout)
        try:
            connection.request("GET", path, headers=dict(headers or {}))
            response = connection.getresponse()
            body = response.read(_MAX_DOCTOR_RESPONSE_BYTES + 1)
        finally:
            connection.close()
        result["reachable"] = True
        result["http_status"] = response.status
        if len(body) > _MAX_DOCTOR_RESPONSE_BYTES:
            result["error"] = "response_too_large"
            return result
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["error"] = "invalid_json"
            return result
        payload = _safe_gateway_payload(kind, parsed)
        if payload is None:
            result["error"] = "invalid_schema"
        else:
            result["payload"] = payload
    except Exception as exc:  # noqa: BLE001 - doctor is informational
        result["error"] = type(exc).__name__
    return result


def gateway_doctor_status(
    gateway_url: str,
    *,
    provider: str,
    client_token_file: str | Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Probe one managed loopback gateway without exposing credentials."""

    from .gateway import (
        METRICS_PATH,
        OWNERSHIP_PATH,
        READINESS_PATH,
        ownership_attestation_message,
        read_gateway_client_token,
    )

    result: dict[str, Any] = {
        "provider": provider,
        "client_token_file_configured": client_token_file is not None,
        "gateway_valid": False,
    }
    try:
        validated_gateway = validate_loopback_gateway(gateway_url)
    except Exception as exc:  # noqa: BLE001 - never echo an invalid URL
        result["configuration_error"] = type(exc).__name__
        result["readiness"] = {"reachable": False, "reason": "invalid_gateway"}
        result["metrics"] = {"reachable": False, "reason": "invalid_gateway"}
        result["ownership"] = {
            "authenticated": False,
            "reason": "invalid_gateway",
        }
        return result
    result["gateway"] = validated_gateway
    result["gateway_valid"] = True
    token: str | None = None
    if client_token_file is not None:
        try:
            token = read_gateway_client_token(client_token_file)
            result["client_token_file_valid"] = True
        except Exception as exc:  # noqa: BLE001 - never retain token/path details
            result["client_token_file_valid"] = False
            result["client_token_file_error"] = type(exc).__name__

    result["readiness"] = _loopback_json_get(
        validated_gateway,
        READINESS_PATH,
        timeout=timeout,
        kind="readiness",
    )
    result["metrics"] = _loopback_json_get(
        validated_gateway,
        METRICS_PATH,
        timeout=timeout,
        kind="metrics",
    )
    if token is None:
        result["ownership"] = {
            "authenticated": False,
            "reason": "client_token_unavailable",
        }
    else:
        challenge = secrets.token_hex(32)
        ownership = _loopback_json_get(
            validated_gateway,
            OWNERSHIP_PATH,
            timeout=timeout,
            headers={"X-Prompt-Toon-Challenge": challenge},
            kind="ownership",
        )
        payload = ownership.get("payload")
        response = (
            payload.get("challenge_response") if isinstance(payload, dict) else None
        )
        authenticated = False
        if isinstance(payload, dict) and isinstance(response, str):
            attestation = dict(payload)
            attestation.pop("challenge_response", None)
            try:
                expected = hmac.new(
                    token.encode("ascii"),
                    ownership_attestation_message(challenge, attestation),
                    "sha256",
                ).hexdigest()
            except (TypeError, ValueError):
                expected = ""
            expected_endpoint = validated_gateway + OWNERSHIP_PATH
            authenticated = bool(
                payload.get("ownership_endpoint") == expected_endpoint
                and hmac.compare_digest(response, expected)
            )
        ownership["authenticated"] = bool(
            ownership.get("http_status") == 200
            and isinstance(payload, dict)
            and payload.get("schema_version") == 2
            and payload.get("status") == "owned"
            and payload.get("provider") == provider
            and payload.get("auth_mode") == "split"
            and authenticated
        )
        if isinstance(payload, dict):
            payload.pop("challenge_response", None)
        result["ownership"] = ownership
    return result


def policy_doctor_status(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path).expanduser()
    result: dict[str, Any] = {"path": str(policy_path), "available": False}
    try:
        data = policy_path.read_bytes()
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("policy must be an object")
        enforcement = value.get("enforcement_gate")
        surfaces = value.get("surfaces")
        if not isinstance(enforcement, dict) or not isinstance(surfaces, list):
            raise ValueError("policy is missing enforcement or surfaces")
        model_gateway = next(
            (
                surface
                for surface in surfaces
                if isinstance(surface, dict) and surface.get("id") == "model_gateway"
            ),
            None,
        )
        result.update(
            {
                "available": True,
                "sha256": sha256(data).hexdigest(),
                "schema_version": value.get("schema_version"),
                "enforcement_unlocked": enforcement.get("unlocked") is True,
                "model_gateway_enabled": bool(
                    isinstance(model_gateway, dict)
                    and model_gateway.get("enabled") is True
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - doctor is informational
        result["error"] = type(exc).__name__
    return result


def request_path_doctor_status(
    *,
    anthropic_gateway: str,
    anthropic_client_token_file: str | Path | None,
    openai_gateway: str,
    openai_client_token_file: str | Path | None,
    codex_profile_file: str | Path | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Report only process-observable routing state; never infer active clients."""

    from .gateway import read_gateway_client_token

    source = os.environ if environ is None else environ
    try:
        expected_anthropic_gateway = validate_loopback_gateway(anthropic_gateway)
    except ValueError:
        expected_anthropic_gateway = ""
    claude_base = source.get("ANTHROPIC_BASE_URL")
    try:
        normalized_claude_base = (
            validate_loopback_gateway(claude_base) if claude_base else None
        )
    except ValueError:
        normalized_claude_base = None
    base_url_matches = bool(
        normalized_claude_base
        and normalized_claude_base == expected_anthropic_gateway
    )
    route_conflicts = claude_route_conflicts(source)
    proxy_bypass = loopback_proxy_bypass_configured(source)
    claude: dict[str, Any] = {
        "base_url_matches": base_url_matches,
        "route_conflicts": route_conflicts,
        "proxy_environment_present": sorted(
            key for key in CLAUDE_PROXY_ENV if source.get(key)
        ),
        "loopback_proxy_bypass_configured": proxy_bypass,
        "selection_observable": True,
        "observation_scope": "doctor_process_environment_only",
        "traffic_observable": False,
        "traffic_state": "unknown_without_gateway_request_counters",
        "client_token_present": bool(source.get("ANTHROPIC_API_KEY")),
    }
    if anthropic_client_token_file is not None:
        try:
            expected = read_gateway_client_token(anthropic_client_token_file)
            claude["client_token_matches"] = hmac.compare_digest(
                source.get("ANTHROPIC_API_KEY", ""), expected
            )
        except Exception as exc:  # noqa: BLE001 - secret-safe status only
            claude["client_token_matches"] = False
            claude["client_token_error"] = type(exc).__name__
    token_matches = claude.get("client_token_matches") is True
    if route_conflicts or (
        claude_base
        and (not base_url_matches or not token_matches)
    ):
        claude_state = "conflict"
    elif base_url_matches and token_matches and proxy_bypass:
        claude_state = "active_in_doctor_environment"
    elif not claude_base:
        claude_state = "direct_or_unset"
    else:
        claude_state = "incomplete"
    claude["state"] = claude_state

    profile_path = (
        Path(codex_profile_file).expanduser()
        if codex_profile_file is not None
        else Path(source.get("CODEX_HOME", str(Path.home() / ".codex"))) / PROFILE_FILE
    )
    codex_proxy_environment = sorted(key for key in PROXY_ENV if source.get(key))
    codex_proxy_bypass = loopback_proxy_bypass_configured(source)
    codex_route_conflicts = sorted(
        key for key in ("CHATGPT_BASE_URL", "OPENAI_BASE_URL") if source.get(key)
    )
    if codex_proxy_environment and not codex_proxy_bypass:
        codex_route_conflicts.append(PROXY_BYPASS_CONFLICT)
    codex: dict[str, Any] = {
        "profile_name": PROFILE_NAME,
        "profile_file": str(profile_path),
        "profile_installed": profile_path.is_file(),
        "profile_matches": False,
        "selection_observable": False,
        "selection_state": "unknown",
        "observation_scope": "profile_content_only",
        "traffic_observable": False,
        "traffic_state": "unknown_without_gateway_request_counters",
        "select_args": ["--profile", PROFILE_NAME],
        "local_token_present": bool(source.get(GATEWAY_TOKEN_ENV)),
        "route_conflicts": codex_route_conflicts,
        "proxy_environment_present": codex_proxy_environment,
        "loopback_proxy_bypass_configured": codex_proxy_bypass,
    }
    try:
        if profile_path.is_file():
            codex["profile_matches"] = hmac.compare_digest(
                profile_path.read_text(encoding="utf-8"),
                render_codex_profile(openai_gateway, auth_mode="api-key"),
            )
    except Exception as exc:  # noqa: BLE001 - doctor is informational
        codex["profile_error"] = type(exc).__name__
    if openai_client_token_file is not None:
        try:
            expected = read_gateway_client_token(openai_client_token_file)
            codex["local_token_matches"] = hmac.compare_digest(
                source.get(GATEWAY_TOKEN_ENV, ""), expected
            )
        except Exception as exc:  # noqa: BLE001 - secret-safe status only
            codex["local_token_matches"] = False
            codex["local_token_error"] = type(exc).__name__
    return {"claude": claude, "codex": codex}


def adoption_doctor_status(
    *,
    policy_path: str | Path,
    anthropic_gateway: str = ANTHROPIC_GATEWAY_URL,
    openai_gateway: str = OPENAI_GATEWAY_URL,
    anthropic_client_token_file: str | Path | None = None,
    openai_client_token_file: str | Path | None = None,
    codex_profile_file: str | Path | None = None,
    timeout: float = 0.5,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "claim_boundary": "local-observation-only; no provider request is made",
        "policy": policy_doctor_status(policy_path),
        "gateways": {
            "anthropic": gateway_doctor_status(
                anthropic_gateway,
                provider="anthropic",
                client_token_file=anthropic_client_token_file,
                timeout=timeout,
            ),
            "openai": gateway_doctor_status(
                openai_gateway,
                provider="openai",
                client_token_file=openai_client_token_file,
                timeout=timeout,
            ),
        },
        "request_paths": request_path_doctor_status(
            anthropic_gateway=anthropic_gateway,
            anthropic_client_token_file=anthropic_client_token_file,
            openai_gateway=openai_gateway,
            openai_client_token_file=openai_client_token_file,
            codex_profile_file=codex_profile_file,
            environ=environ,
        ),
    }
