"""Offline import of hash-bound provider usage artifacts.

This module deliberately makes no provider calls and writes no files.  It turns
caller-supplied request and response artifacts into a bounded, schema-checked
sidecar suitable for exact token-count comparisons.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable

from .codex_harness import parse_codex_stream
from .corpus import CorpusLedgerError, _read_ledger, _validate_ledger


PROVIDER_USAGE_SCHEMA_VERSION = 1
PROVIDER_USAGE_KIND = "prompt-toon-provider-usage"
MAX_LEDGER_BYTES = 1_000_000
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES_TOTAL = 64 * 1024 * 1024
MAX_USAGE_BYTES = 64 * 1024 * 1024
MAX_SIDECAR_BYTES = 8 * 1024 * 1024
MAX_LEDGER_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_REQUESTS = 256
_SOURCES = frozenset(
    {"responses-json", "responses-sse", "responses-input-count", "codex-jsonl"}
)
_MODEL_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_HANDOFF_ARTIFACTS = {
    "summary_only": ("summary.md",),
    "summary_plus_authoritative_cards": ("summary.md", "source-cards.jsonl"),
    "summary_plus_toon_compact_view": ("summary.md", "source-cards.toon"),
}


class ProviderUsageError(ValueError):
    """A provider usage artifact cannot support an exact, bounded claim."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderUsageError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite_constant(_: str) -> None:
    raise ProviderUsageError("non-finite JSON number")


def _load_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except ProviderUsageError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ProviderUsageError(f"{label} is not valid UTF-8 JSON") from exc


def _read_bounded(path: str | Path, *, limit: int, label: str) -> bytes:
    """Read a stable regular file through a no-follow descriptor."""
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            raise ProviderUsageError(f"{label} must be a regular non-symlink file")
    except OSError as exc:
        raise ProviderUsageError(f"{label} could not be opened safely") from exc
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ProviderUsageError(f"{label} could not be opened safely") from exc
    try:
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ProviderUsageError(f"{label} must be a regular non-symlink file")
            if before.st_size > limit:
                raise ProviderUsageError(f"{label} exceeds its byte limit")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ProviderUsageError(f"{label} exceeds its byte limit")
            after = os.fstat(descriptor)
        except OSError as exc:
            raise ProviderUsageError(f"{label} could not be read safely") from exc
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or len(data) != before.st_size
        ):
            raise ProviderUsageError(f"{label} changed while being read")
        return data
    finally:
        os.close(descriptor)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderUsageError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise ProviderUsageError(f"{label} fields do not match schema v1")


def _string(value: Any, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderUsageError(f"{label} must be a non-empty string")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise ProviderUsageError(f"{label} must be a bounded non-empty string")
    except UnicodeEncodeError as exc:
        raise ProviderUsageError(f"{label} must be valid Unicode") from exc
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderUsageError(f"{label} must be a nonnegative integer")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProviderUsageError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _safe_label(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    value = _string(value, label)
    if pattern.fullmatch(value) is None:
        raise ProviderUsageError(f"{label} contains unsupported characters")
    return value


def _descriptor(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _validate_descriptor(value: Any, label: str) -> dict[str, Any]:
    value = _mapping(value, label)
    _exact_keys(value, {"bytes", "sha256"}, label)
    return {
        "bytes": _integer(value.get("bytes"), f"{label}.bytes"),
        "sha256": _sha256(value.get("sha256"), f"{label}.sha256"),
    }


def _request_sequence(requests: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        requests,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _canonical_usage(usage: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, int | None] = {}
    omitted: list[str] = []
    for field in _USAGE_FIELDS:
        if field not in usage:
            result[field] = None
            omitted.append(field)
        else:
            result[field] = _integer(usage[field], f"usage.{field}")
    if result["input_tokens"] is None:
        raise ProviderUsageError("usage.input_tokens is required")
    input_tokens = result["input_tokens"]
    assert isinstance(input_tokens, int)
    for field in ("cached_input_tokens", "cache_write_tokens"):
        amount = result[field]
        if amount is not None and amount > input_tokens:
            raise ProviderUsageError(f"usage.{field} must not exceed input_tokens")
    reasoning = result["reasoning_output_tokens"]
    output = result["output_tokens"]
    if reasoning is not None and output is not None and reasoning > output:
        raise ProviderUsageError(
            "usage.reasoning_output_tokens must not exceed output_tokens"
        )
    total = result["total_tokens"]
    if total is not None and total < input_tokens:
        raise ProviderUsageError("usage.total_tokens must not be below input_tokens")
    if total is not None and output is not None and total != input_tokens + output:
        raise ProviderUsageError(
            "usage.total_tokens must equal input_tokens + output_tokens"
        )
    return {**result, "omitted_fields": omitted}


def _responses_usage(response: Any) -> tuple[str, dict[str, Any]]:
    response = _mapping(response, "terminal response")
    if response.get("object") != "response":
        raise ProviderUsageError("terminal response.object must be response")
    if response.get("status") != "completed":
        raise ProviderUsageError("terminal response status contradicts completion")
    model = _safe_label(
        response.get("model"), "terminal response.model", _MODEL_LABEL_RE
    )
    raw_usage = _mapping(response.get("usage"), "terminal response.usage")
    usage: dict[str, Any] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if field in raw_usage:
            usage[field] = raw_usage[field]
    input_details = raw_usage.get("input_tokens_details")
    if input_details is not None:
        input_details = _mapping(
            input_details, "terminal response.input_tokens_details"
        )
        if "cached_tokens" in input_details:
            usage["cached_input_tokens"] = input_details["cached_tokens"]
        if "cache_write_tokens" in input_details:
            usage["cache_write_tokens"] = input_details["cache_write_tokens"]
    output_details = raw_usage.get("output_tokens_details")
    if output_details is not None:
        output_details = _mapping(
            output_details, "terminal response.output_tokens_details"
        )
        if "reasoning_tokens" in output_details:
            usage["reasoning_output_tokens"] = output_details["reasoning_tokens"]
    return model, _canonical_usage(usage)


def _parse_responses_json(data: bytes) -> tuple[str, dict[str, Any]]:
    value = _mapping(_load_json(data, "usage artifact"), "usage artifact")
    event_type = value.get("type")
    if event_type is not None and not isinstance(event_type, str):
        raise ProviderUsageError("usage artifact type must be a string")
    if event_type in {"response.failed", "response.incomplete", "error"}:
        raise ProviderUsageError("usage artifact is not a completed response")
    if event_type is None:
        if value.get("status") != "completed":
            raise ProviderUsageError(
                "usage artifact direct response status must be completed"
            )
        response = value
    elif event_type == "response.completed":
        response = value.get("response")
    else:
        raise ProviderUsageError("usage artifact is not a terminal completed response")
    return _responses_usage(response)


def _parse_responses_input_count(data: bytes) -> dict[str, Any]:
    value = _mapping(_load_json(data, "usage artifact"), "usage artifact")
    _exact_keys(value, {"object", "input_tokens"}, "input-token count artifact")
    if value.get("object") != "response.input_tokens":
        raise ProviderUsageError("usage artifact is not a Responses input-token count")
    return _canonical_usage({"input_tokens": value.get("input_tokens")})


def _dispatch_sse_event(
    event_name: str | None, data_lines: list[str]
) -> tuple[str | None, dict[str, Any] | None]:
    if not data_lines:
        raise ProviderUsageError("SSE event is missing data")
    value = _mapping(
        _load_json("\n".join(data_lines).encode("utf-8"), "SSE event"),
        "SSE event",
    )
    event_type = value.get("type")
    if event_type is not None and not isinstance(event_type, str):
        raise ProviderUsageError("SSE event type must be a string")
    terminal_types = {
        "response.completed",
        "response.failed",
        "response.incomplete",
        "error",
    }
    if event_name in terminal_types or event_type in terminal_types:
        if event_name != event_type:
            raise ProviderUsageError("SSE terminal event name and type must match")
        if event_type != "response.completed":
            raise ProviderUsageError("SSE artifact is not a completed response")
        return "completed", _mapping(value.get("response"), "SSE terminal response")
    return None, None


def _parse_responses_sse(data: bytes) -> tuple[str, dict[str, Any]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderUsageError("usage artifact is not valid UTF-8") from exc
    if any(separator in text for separator in ("\x00", "\x85", "\u2028", "\u2029")):
        raise ProviderUsageError("SSE artifact contains an invalid line separator")
    event_name: str | None = None
    data_lines: list[str] = []
    completed_response: dict[str, Any] | None = None
    seen_event = False

    def dispatch() -> None:
        nonlocal event_name, data_lines, completed_response, seen_event
        if event_name is None and not data_lines:
            return
        if completed_response is not None:
            raise ProviderUsageError(
                "SSE artifact contains data after terminal completion"
            )
        state, response = _dispatch_sse_event(event_name, data_lines)
        seen_event = True
        if state == "completed":
            completed_response = response
        event_name = None
        data_lines = []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            dispatch()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            if event_name is not None:
                raise ProviderUsageError("SSE event has repeated event fields")
            event_name = line[6:].lstrip(" ")
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        else:
            raise ProviderUsageError("SSE artifact contains an invalid field")
    if event_name is not None or data_lines:
        raise ProviderUsageError("SSE artifact has an unterminated event")
    if not seen_event or completed_response is None:
        raise ProviderUsageError(
            "SSE artifact must contain exactly one terminal completion"
        )
    return _responses_usage(completed_response)


def _parse_codex_jsonl(
    data: bytes, model_label: str | None
) -> tuple[str, dict[str, Any]]:
    if model_label is None:
        raise ProviderUsageError("codex-jsonl requires model_label")
    label = _safe_label(model_label, "model_label", _MODEL_LABEL_RE)
    try:
        stdout = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderUsageError("usage artifact is not valid UTF-8") from exc
    for line in stdout.splitlines():
        if not line.strip():
            continue
        _mapping(
            _load_json(line.encode("utf-8"), "Codex stream line"), "Codex stream line"
        )
    try:
        parsed = parse_codex_stream(stdout)
    except ValueError as exc:
        raise ProviderUsageError(
            "Codex usage artifact is not one completed turn"
        ) from exc
    if parsed["errors"]:
        raise ProviderUsageError("Codex usage artifact contains an error event")
    usage = parsed["usage"]
    if usage is None:
        raise ProviderUsageError("Codex completed turn is missing usage")
    return label, _canonical_usage(_mapping(usage, "Codex completed turn usage"))


def _load_ledger(path: str | Path) -> tuple[dict[str, Any], str]:
    # corpus owns supported dogfood-ledger integrity; reuse it rather than copy it.
    try:
        data = _read_bounded(path, limit=MAX_LEDGER_BYTES, label="ledger")
        ledger = _mapping(_load_json(data, "ledger"), "ledger")
        validated_ledger, ledger_sha256 = _read_ledger(Path(path))
        if (
            hashlib.sha256(data).hexdigest() != ledger_sha256
            or ledger != validated_ledger
        ):
            raise ProviderUsageError("ledger changed while being validated")
        _validate_ledger(validated_ledger)
        return validated_ledger, ledger_sha256
    except (CorpusLedgerError, ProviderUsageError) as exc:
        if isinstance(exc, ProviderUsageError):
            raise
        raise ProviderUsageError("ledger does not satisfy dogfood schema integrity") from exc


def _verified_ledger_artifact(
    ledger: dict[str, Any], ledger_dir: Path, name: str
) -> dict[str, Any]:
    artifacts = _mapping(ledger.get("artifacts"), "ledger artifacts")
    measurement = _mapping(artifacts.get(name), f"ledger artifacts.{name}")
    expected = {
        "bytes": _integer(measurement.get("bytes"), f"ledger artifacts.{name}.bytes"),
        "sha256": _sha256(measurement.get("sha256"), f"ledger artifacts.{name}.sha256"),
    }
    actual = _descriptor(
        _read_bounded(
            ledger_dir / name,
            limit=MAX_LEDGER_ARTIFACT_BYTES,
            label=f"ledger artifact {name}",
        )
    )
    if actual != expected:
        raise ProviderUsageError(f"ledger artifact {name} does not match its ledger")
    return {"name": name, **actual}


def _handoff_artifacts(
    ledger: dict[str, Any], ledger_dir: Path, variant: str
) -> list[dict[str, Any]]:
    if variant == "raw_input":
        return []
    if variant not in _HANDOFF_ARTIFACTS or variant not in ledger.get("handoffs", {}):
        raise ProviderUsageError("variant must be raw_input or a ledger handoff key")
    handoffs = _mapping(ledger.get("handoffs"), "ledger handoffs")
    handoff = _mapping(handoffs.get(variant), f"ledger handoffs.{variant}")
    expected_names = list(_HANDOFF_ARTIFACTS[variant])
    if handoff.get("artifacts") != expected_names:
        raise ProviderUsageError(
            "selected ledger handoff artifacts do not match the handoff contract"
        )
    return [
        _verified_ledger_artifact(ledger, ledger_dir, name) for name in expected_names
    ]


def build_provider_usage_sidecar(
    *,
    ledger_path: str | Path,
    request_paths: list[str] | tuple[str, ...],
    usage_path: str | Path,
    source: str,
    variant: str,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Build a no-write, no-provider-call exact-usage sidecar."""
    if not isinstance(source, str) or source not in _SOURCES:
        raise ProviderUsageError("source is not supported")
    if not isinstance(request_paths, (list, tuple)) or not request_paths:
        raise ProviderUsageError("at least one request artifact is required")
    if len(request_paths) > MAX_REQUESTS:
        raise ProviderUsageError("request artifact count exceeds the limit")
    if source != "codex-jsonl" and len(request_paths) != 1:
        raise ProviderUsageError(f"{source} requires exactly one request artifact")
    if not isinstance(variant, str):
        raise ProviderUsageError("variant must be a string")
    ledger, ledger_sha256 = _load_ledger(ledger_path)
    ledger_dir = Path(ledger_path).parent
    manifest = _verified_ledger_artifact(ledger, ledger_dir, "manifest.json")
    manifest.pop("name")
    request_descriptors: list[dict[str, Any]] = []
    request_models: list[str] = []
    aggregate = 0
    for index, path in enumerate(request_paths):
        data = _read_bounded(path, limit=MAX_REQUEST_BYTES, label="request artifact")
        aggregate += len(data)
        if aggregate > MAX_REQUEST_BYTES_TOTAL:
            raise ProviderUsageError(
                "request artifacts exceed the aggregate byte limit"
            )
        request_descriptors.append(_descriptor(data))
        request = _mapping(
            _load_json(data, f"request artifact {index + 1}"),
            f"request artifact {index + 1}",
        )
        request_models.append(
            _safe_label(
                request.get("model"),
                f"request artifact {index + 1}.model",
                _MODEL_LABEL_RE,
            )
        )
    usage_data = _read_bounded(
        usage_path, limit=MAX_USAGE_BYTES, label="usage artifact"
    )
    if source == "responses-input-count":
        model = request_models[0]
        if (
            model_label is not None
            and _safe_label(model_label, "model_label", _MODEL_LABEL_RE) != model
        ):
            raise ProviderUsageError(
                "model_label does not match the exact request artifact model"
            )
        usage = _parse_responses_input_count(usage_data)
        model_record = {"label": model, "provenance": "request_declared"}
    elif source == "responses-json":
        model, usage = _parse_responses_json(usage_data)
        if model_label is not None and _string(model_label, "model_label") != model:
            raise ProviderUsageError(
                "model_label does not match the returned response model"
            )
        model_record = {"label": model, "provenance": "response_returned"}
    elif source == "responses-sse":
        model, usage = _parse_responses_sse(usage_data)
        if model_label is not None and _string(model_label, "model_label") != model:
            raise ProviderUsageError(
                "model_label does not match the returned response model"
            )
        model_record = {"label": model, "provenance": "response_returned"}
    else:
        model, usage = _parse_codex_jsonl(usage_data, model_label)
        if any(request_model != model for request_model in request_models):
            raise ProviderUsageError(
                "model_label must match every exact Codex request artifact model"
            )
        model_record = {"label": model, "provenance": "request_declared"}
    identity = _mapping(ledger.get("corpus_identity"), "ledger corpus identity")
    ordered_key = _mapping(
        identity.get("ordered_key"), "ledger ordered corpus identity"
    )
    diversity_key = _mapping(
        identity.get("diversity_key"), "ledger diversity corpus identity"
    )
    sidecar = {
        "schema_version": PROVIDER_USAGE_SCHEMA_VERSION,
        "kind": PROVIDER_USAGE_KIND,
        "claim_boundary": {
            "caller_supplied_external_artifacts": "hash-bound but not provider-authenticated",
            "provider_requests_made_by_importer": 0,
            "cost_claim": "none",
            "quality_claim": "none",
        },
        "ledger": {
            "sha256": ledger_sha256,
            "corpus_ordered_sha256": _sha256(
                ordered_key.get("sha256"), "ledger ordered corpus hash"
            ),
            "corpus_diversity_sha256": _sha256(
                diversity_key.get("sha256"), "ledger diversity corpus hash"
            ),
        },
        "variant": variant,
        "handoff_artifacts": _handoff_artifacts(ledger, ledger_dir, variant),
        "manifest": manifest,
        "requests": {
            "ordered": request_descriptors,
            "sequence_sha256": _request_sequence(request_descriptors),
        },
        "usage_artifact": _descriptor(usage_data),
        "source": source,
        "model": model_record,
        "usage": usage,
    }
    return _validate_sidecar(sidecar)


def _validate_sidecar(value: Any) -> dict[str, Any]:
    sidecar = _mapping(value, "provider usage sidecar")
    _exact_keys(
        sidecar,
        {
            "schema_version",
            "kind",
            "claim_boundary",
            "ledger",
            "variant",
            "handoff_artifacts",
            "manifest",
            "requests",
            "usage_artifact",
            "source",
            "model",
            "usage",
        },
        "provider usage sidecar",
    )
    schema_version = sidecar.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != PROVIDER_USAGE_SCHEMA_VERSION
    ):
        raise ProviderUsageError("provider usage sidecar schema_version must be 1")
    if sidecar.get("kind") != PROVIDER_USAGE_KIND:
        raise ProviderUsageError("provider usage sidecar kind is invalid")
    claim = _mapping(sidecar.get("claim_boundary"), "claim_boundary")
    _exact_keys(
        claim,
        {
            "caller_supplied_external_artifacts",
            "provider_requests_made_by_importer",
            "cost_claim",
            "quality_claim",
        },
        "claim_boundary",
    )
    if claim != {
        "caller_supplied_external_artifacts": "hash-bound but not provider-authenticated",
        "provider_requests_made_by_importer": 0,
        "cost_claim": "none",
        "quality_claim": "none",
    }:
        raise ProviderUsageError("claim_boundary is invalid")
    ledger = _mapping(sidecar.get("ledger"), "ledger")
    _exact_keys(
        ledger,
        {"sha256", "corpus_ordered_sha256", "corpus_diversity_sha256"},
        "ledger",
    )
    validated_ledger = {
        "sha256": _sha256(ledger.get("sha256"), "ledger.sha256"),
        "corpus_ordered_sha256": _sha256(
            ledger.get("corpus_ordered_sha256"), "ledger.corpus_ordered_sha256"
        ),
        "corpus_diversity_sha256": _sha256(
            ledger.get("corpus_diversity_sha256"), "ledger.corpus_diversity_sha256"
        ),
    }
    source = sidecar.get("source")
    if not isinstance(source, str) or source not in _SOURCES:
        raise ProviderUsageError("source is not supported")
    variant = _string(sidecar.get("variant"), "variant", maximum=96)
    if variant != "raw_input" and variant not in _HANDOFF_ARTIFACTS:
        raise ProviderUsageError("variant must be raw_input or a ledger handoff key")
    artifacts = sidecar.get("handoff_artifacts")
    if not isinstance(artifacts, list):
        raise ProviderUsageError("handoff_artifacts must be an array")
    expected_names = (
        _HANDOFF_ARTIFACTS.get(variant, ()) if variant != "raw_input" else ()
    )
    if len(artifacts) != len(expected_names):
        raise ProviderUsageError("handoff_artifacts do not match the selected variant")
    validated_artifacts: list[dict[str, Any]] = []
    for index, (artifact, expected_name) in enumerate(zip(artifacts, expected_names)):
        artifact = _mapping(artifact, f"handoff_artifacts[{index}]")
        _exact_keys(
            artifact, {"name", "bytes", "sha256"}, f"handoff_artifacts[{index}]"
        )
        if artifact.get("name") != expected_name:
            raise ProviderUsageError(
                "handoff_artifacts do not match the selected variant"
            )
        validated_artifacts.append(
            {
                "name": expected_name,
                "bytes": _integer(
                    artifact.get("bytes"), f"handoff_artifacts[{index}].bytes"
                ),
                "sha256": _sha256(
                    artifact.get("sha256"), f"handoff_artifacts[{index}].sha256"
                ),
            }
        )
        if validated_artifacts[-1]["bytes"] > MAX_LEDGER_ARTIFACT_BYTES:
            raise ProviderUsageError("handoff artifact exceeds its byte limit")
    manifest = _validate_descriptor(sidecar.get("manifest"), "manifest")
    if manifest["bytes"] > MAX_LEDGER_ARTIFACT_BYTES:
        raise ProviderUsageError("manifest exceeds its byte limit")
    requests = _mapping(sidecar.get("requests"), "requests")
    _exact_keys(requests, {"ordered", "sequence_sha256"}, "requests")
    ordered = requests.get("ordered")
    if not isinstance(ordered, list) or not 1 <= len(ordered) <= MAX_REQUESTS:
        raise ProviderUsageError(
            f"requests.ordered must contain between 1 and {MAX_REQUESTS} artifacts"
        )
    validated_requests = [
        _validate_descriptor(item, f"requests.ordered[{index}]")
        for index, item in enumerate(ordered)
    ]
    if sum(item["bytes"] for item in validated_requests) > MAX_REQUEST_BYTES_TOTAL:
        raise ProviderUsageError("request artifacts exceed the aggregate byte limit")
    if any(item["bytes"] > MAX_REQUEST_BYTES for item in validated_requests):
        raise ProviderUsageError("request artifact exceeds its byte limit")
    if requests.get("sequence_sha256") != _request_sequence(validated_requests):
        raise ProviderUsageError(
            "requests.sequence_sha256 does not match ordered artifacts"
        )
    if source != "codex-jsonl" and len(validated_requests) != 1:
        raise ProviderUsageError(f"{source} requires exactly one request artifact")
    usage_artifact = _validate_descriptor(
        sidecar.get("usage_artifact"), "usage_artifact"
    )
    if usage_artifact["bytes"] > MAX_USAGE_BYTES:
        raise ProviderUsageError("usage_artifact exceeds its byte limit")
    model = _mapping(sidecar.get("model"), "model")
    _exact_keys(model, {"label", "provenance"}, "model")
    provenance = model.get("provenance")
    if not isinstance(provenance, str) or provenance not in {
        "response_returned",
        "request_declared",
    }:
        raise ProviderUsageError("model.provenance is invalid")
    if (
        source in {"responses-json", "responses-sse"}
        and provenance != "response_returned"
    ):
        raise ProviderUsageError("Responses model provenance must be response_returned")
    if source == "responses-input-count" and provenance != "request_declared":
        raise ProviderUsageError(
            "Responses input-count model provenance must be request_declared"
        )
    if source == "codex-jsonl" and provenance != "request_declared":
        raise ProviderUsageError("Codex model provenance must be request_declared")
    validated_model = {
        "label": _safe_label(model.get("label"), "model.label", _MODEL_LABEL_RE),
        "provenance": provenance,
    }
    raw_usage = _mapping(sidecar.get("usage"), "usage")
    _exact_keys(raw_usage, {*_USAGE_FIELDS, "omitted_fields"}, "usage")
    canonical_input = {
        field: raw_usage[field]
        for field in _USAGE_FIELDS
        if raw_usage[field] is not None
    }
    validated_usage = _canonical_usage(canonical_input)
    omitted = raw_usage.get("omitted_fields")
    if omitted != validated_usage["omitted_fields"]:
        raise ProviderUsageError(
            "usage.omitted_fields does not match null usage fields"
        )
    for field in _USAGE_FIELDS:
        if raw_usage[field] != validated_usage[field]:
            raise ProviderUsageError("usage fields are invalid")
    if source == "responses-input-count" and any(
        validated_usage[field] is not None
        for field in _USAGE_FIELDS
        if field != "input_tokens"
    ):
        raise ProviderUsageError(
            "Responses input-count artifacts may report only input_tokens"
        )
    return {
        "schema_version": PROVIDER_USAGE_SCHEMA_VERSION,
        "kind": PROVIDER_USAGE_KIND,
        "claim_boundary": claim,
        "ledger": validated_ledger,
        "variant": variant,
        "handoff_artifacts": validated_artifacts,
        "manifest": manifest,
        "requests": {
            "ordered": validated_requests,
            "sequence_sha256": requests["sequence_sha256"],
        },
        "usage_artifact": usage_artifact,
        "source": source,
        "model": validated_model,
        "usage": validated_usage,
    }


def load_provider_usage_sidecar(path: str | Path) -> dict[str, Any]:
    """Load and fully validate a no-follow provider-usage sidecar."""
    return _validate_sidecar(
        _load_json(
            _read_bounded(
                path, limit=MAX_SIDECAR_BYTES, label="provider usage sidecar"
            ),
            "provider usage sidecar",
        )
    )


def build_provider_usage_comparison(
    baseline_sidecar: dict[str, Any], candidate_sidecar: dict[str, Any]
) -> dict[str, Any]:
    """Compare a raw-input baseline with a hash-bound condensed handoff."""
    baseline = _validate_sidecar(baseline_sidecar)
    candidate = _validate_sidecar(candidate_sidecar)
    if baseline["variant"] != "raw_input":
        raise ProviderUsageError("comparison baseline variant must be raw_input")
    if candidate["variant"] == "raw_input":
        raise ProviderUsageError("comparison candidate variant must be a handoff")
    for field in (
        "sha256",
        "corpus_ordered_sha256",
        "corpus_diversity_sha256",
    ):
        if baseline["ledger"][field] != candidate["ledger"][field]:
            raise ProviderUsageError(
                "comparison sidecars must bind the same ledger and corpus"
            )
    if baseline["source"] != candidate["source"]:
        raise ProviderUsageError("comparison sidecars must use the same source")
    if baseline["model"] != candidate["model"]:
        raise ProviderUsageError("comparison sidecars must use the same model")
    if baseline["manifest"] != candidate["manifest"]:
        raise ProviderUsageError("comparison sidecars must bind the same manifest")
    if (
        baseline["requests"]["sequence_sha256"]
        == candidate["requests"]["sequence_sha256"]
    ):
        raise ProviderUsageError(
            "comparison baseline and candidate must bind different request bytes"
        )
    if baseline["usage_artifact"] == candidate["usage_artifact"]:
        raise ProviderUsageError(
            "comparison baseline and candidate must bind different usage artifacts"
        )
    baseline_input = baseline["usage"]["input_tokens"]
    candidate_input = candidate["usage"]["input_tokens"]
    assert isinstance(baseline_input, int) and isinstance(candidate_input, int)
    return {
        "schema_version": 1,
        "kind": "prompt-toon-provider-usage-comparison",
        "claim_boundary": {
            "caller_supplied_external_artifacts": "hash-bound but not provider-authenticated",
            "cost_claim": "none",
            "quality_claim": "none",
            "provider_requests_made_by_importer": 0,
        },
        "ledger": baseline["ledger"],
        "source": baseline["source"],
        "model": baseline["model"],
        "baseline_variant": baseline["variant"],
        "candidate_variant": candidate["variant"],
        "input_tokens": {
            "baseline": baseline_input,
            "candidate": candidate_input,
            "savings": baseline_input - candidate_input,
        },
        "cache_tokens": {
            "baseline": {
                "read": baseline["usage"]["cached_input_tokens"],
                "write": baseline["usage"]["cache_write_tokens"],
            },
            "candidate": {
                "read": candidate["usage"]["cached_input_tokens"],
                "write": candidate["usage"]["cache_write_tokens"],
            },
        },
        "observations": {
            "baseline": {
                "request_sequence_sha256": baseline["requests"]["sequence_sha256"],
                "usage_artifact_sha256": baseline["usage_artifact"]["sha256"],
            },
            "candidate": {
                "request_sequence_sha256": candidate["requests"]["sequence_sha256"],
                "usage_artifact_sha256": candidate["usage_artifact"]["sha256"],
            },
        },
    }
