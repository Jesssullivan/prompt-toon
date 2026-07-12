"""Opt-in loopback shadow gateway for Anthropic Messages.

The HTTP path is deliberately transparent: request bodies are forwarded as
the exact bytes received, while a separate in-memory view identifies typed
``tool_result`` blocks for the resident Chapel transformer. Shadow failures
never alter the provider request or response.
"""

from __future__ import annotations

import hashlib
import http.client
import http.server
import ipaddress
import json
import re
import signal
import socket
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import SplitResult, urlsplit

from prompt_toon.engine import EngineError
from prompt_toon.resident import ResidentEngine


HEALTH_PATH = "/__prompt_toon/health"
READINESS_PATH = "/__prompt_toon/ready"
METRICS_PATH = "/__prompt_toon/metrics"
MESSAGES_PATH = "/v1/messages"
DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_LISTEN = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 300.0
DEFAULT_INGRESS_HEADER_TIMEOUT_SECONDS = 10.0
DEFAULT_INGRESS_BODY_TIMEOUT_SECONDS = 30.0
DEFAULT_SHUTDOWN_GRACE_SECONDS = 10.0

_MAX_DECIMAL_DIGITS = 20
_MAX_CHUNK_LINE_BYTES = 128
_MAX_TRAILER_BYTES = 64 * 1024
_MAX_OBSERVED_RESPONSE_BYTES = 1024 * 1024
_MAX_SSE_LINE_BYTES = 256 * 1024
_MAX_MODEL_CARDINALITY = 32
_ADMIN_CONNECTION_RESERVE = 2
_MODEL_RE = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _rough_token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _safe_model(value: object) -> str | None:
    if isinstance(value, str) and _MODEL_RE.fullmatch(value):
        return value
    return None


@dataclass(frozen=True)
class GatewayPolicy:
    max_concurrent_streams: int
    transform_workers: int
    pending_queue_depth: int
    max_documents_per_request: int
    max_request_bytes: int
    max_response_bytes: int
    max_label_bytes: int
    max_input_bytes: int
    wall_clock_budget_ms: int
    max_cards_per_document: int
    min_savings: float
    trust_tiers: dict[str, str]

    @classmethod
    def load(cls, path: str | Path) -> GatewayPolicy:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
        if not isinstance(raw, dict):
            raise ValueError("IO policy must be a JSON object")
        limits = raw.get("service_limits")
        thresholds = raw.get("thresholds")
        tiers = raw.get("trust_tiers")
        if not isinstance(limits, dict) or not isinstance(thresholds, dict):
            raise ValueError("IO policy is missing service_limits or thresholds")
        if not isinstance(tiers, list):
            raise ValueError("IO policy trust_tiers must be a list")

        values: dict[str, int] = {}
        for key in (
            "max_concurrent_streams",
            "transform_workers",
            "pending_queue_depth",
            "max_documents_per_request",
            "max_request_bytes",
            "max_response_bytes",
            "max_label_bytes",
            "max_cards_per_document",
        ):
            value = limits.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"IO policy {key} must be a positive integer")
            values[key] = value
        for key in ("max_input_bytes", "wall_clock_budget_ms"):
            value = thresholds.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"IO policy {key} must be a positive integer")
            values[key] = value
        min_savings = thresholds.get("min_savings")
        if (
            isinstance(min_savings, bool)
            or not isinstance(min_savings, (int, float))
            or not 0 <= min_savings < 1
        ):
            raise ValueError("IO policy min_savings must be in [0, 1)")

        tier_map: dict[str, str] = {}
        for index, item in enumerate(tiers):
            if not isinstance(item, dict):
                raise ValueError(f"IO policy trust_tiers[{index}] must be an object")
            source = item.get("source")
            tier = item.get("tier")
            if not isinstance(source, str) or not source:
                raise ValueError(f"IO policy trust_tiers[{index}].source is invalid")
            if not isinstance(tier, str) or not tier:
                raise ValueError(f"IO policy trust_tiers[{index}].tier is invalid")
            if source in tier_map:
                raise ValueError(f"IO policy has duplicate trust source {source!r}")
            tier_map[source] = tier

        if values["transform_workers"] > values["max_concurrent_streams"]:
            raise ValueError("IO policy workers exceed max concurrent streams")
        if values["pending_queue_depth"] < values["transform_workers"]:
            raise ValueError("IO policy queue depth is smaller than worker count")
        if values["max_input_bytes"] > values["max_request_bytes"]:
            raise ValueError("IO policy input cap exceeds request cap")
        if values["max_response_bytes"] < values["max_request_bytes"]:
            raise ValueError("IO policy response cap is smaller than request cap")

        return cls(
            **values,
            min_savings=float(min_savings),
            trust_tiers=tier_map,
        )

    def classify_tool(self, name: object) -> tuple[str, str] | None:
        if not isinstance(name, str):
            return None
        tier = self.trust_tiers.get(name)
        if tier is not None:
            return name, tier
        if name.startswith("mcp__"):
            parts = name.split("__", 2)
            if len(parts) >= 2 and parts[1]:
                source = f"mcp:{parts[1]}"
                tier = self.trust_tiers.get(source)
                if tier is not None:
                    return source, tier
        if name.startswith("mcp:"):
            source = ":".join(name.split(":", 2)[:2])
            tier = self.trust_tiers.get(source)
            if tier is not None:
                return source, tier
        return None


@dataclass(frozen=True)
class ParsedMessage:
    requested_model: str | None
    docs: list[dict[str, str]]
    selected_bytes: int
    truncated_docs: int


def _content_texts(content: object) -> Iterable[str]:
    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, list):
        return
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            yield block["text"]


def parse_message_for_shadow(body: bytes, policy: GatewayPolicy) -> ParsedMessage:
    value = json.loads(body, parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise ValueError("Messages request must be a JSON object")
    requested_model = _safe_model(value.get("model"))
    messages = value.get("messages")
    if not isinstance(messages, list):
        return ParsedMessage(requested_model, [], 0, 0)

    tool_uses: dict[str, str] = {}
    ambiguous_tool_uses: set[str] = set()
    docs: list[dict[str, str]] = []
    selected_bytes = 0
    truncated_docs = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "assistant":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_use_id = block.get("id")
                name = block.get("name")
                if not isinstance(tool_use_id, str) or not isinstance(name, str):
                    continue
                previous = tool_uses.get(tool_use_id)
                if previous is None:
                    if tool_use_id not in ambiguous_tool_uses:
                        tool_uses[tool_use_id] = name
                elif previous != name:
                    del tool_uses[tool_use_id]
                    ambiguous_tool_uses.add(tool_use_id)
            continue
        if role != "user":
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            classified = policy.classify_tool(tool_uses.get(tool_use_id))
            if classified is None:
                continue
            source, trust_tier = classified
            link = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:16]
            for text_index, text in enumerate(_content_texts(block.get("content"))):
                if len(docs) >= policy.max_documents_per_request:
                    truncated_docs += 1
                    continue
                encoded = text.encode("utf-8")
                docs.append(
                    {
                        "source": f"anthropic:{source}:{link}:{text_index}",
                        "trust_tier": trust_tier,
                        "body": text,
                    }
                )
                selected_bytes += len(encoded)
    return ParsedMessage(requested_model, docs, selected_bytes, truncated_docs)


class GatewayMetrics:
    """Bounded in-memory counters; never stores request bodies or credentials."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._quality: Counter[str] = Counter()
        self._requested_models: Counter[str] = Counter()
        self._returned_models: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def record_request(self, requested_model: str | None, docs: int) -> None:
        with self._lock:
            self._counters["messages_requests"] += 1
            self._counters["typed_documents_selected"] += docs
            if requested_model is None:
                self._counters["requested_model_unavailable"] += 1
            else:
                self._record_model(self._requested_models, requested_model)

    @staticmethod
    def _record_model(counter: Counter[str], model: str) -> None:
        if model in counter or len(counter) < _MAX_MODEL_CARDINALITY:
            counter[model] += 1
        else:
            counter["__other__"] += 1

    def record_shadow_result(
        self,
        *,
        quality: str,
        docs: int,
        withheld: int,
        cards: int,
        raw_tokens: int,
        condensed_tokens: int,
    ) -> None:
        with self._lock:
            self._counters["shadow_completed"] += 1
            self._counters["shadow_documents_completed"] += docs
            self._counters["shadow_documents_withheld"] += withheld
            self._counters["shadow_cards"] += cards
            self._counters["estimated_raw_tokens"] += raw_tokens
            self._counters["estimated_condensed_tokens"] += condensed_tokens
            if quality == "eligible":
                self._counters["estimated_tokens_saved"] += max(
                    0, raw_tokens - condensed_tokens
                )
            self._quality[quality] += 1

    def record_shadow_error(self) -> None:
        with self._lock:
            self._counters["shadow_failed"] += 1
            self._quality["error"] += 1

    def record_provider_response(
        self,
        *,
        status: int,
        returned_model: str | None,
        usage: dict[str, int],
        stream_errors: int,
        telemetry_available: bool,
    ) -> None:
        with self._lock:
            self._counters["upstream_responses"] += 1
            self._counters[f"upstream_status_{status // 100}xx"] += 1
            self._counters["sse_error_events"] += stream_errors
            if not telemetry_available:
                self._counters["response_telemetry_unavailable"] += 1
            if returned_model is None:
                self._counters["returned_model_unavailable"] += 1
            else:
                self._record_model(self._returned_models, returned_model)
            for field in _USAGE_FIELDS:
                self._counters[f"provider_{field}"] += usage.get(field, 0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "mode": "shadow",
                "counters": dict(sorted(self._counters.items())),
                "models": {
                    "requested": dict(sorted(self._requested_models.items())),
                    "returned": dict(sorted(self._returned_models.items())),
                },
                "quality": dict(sorted(self._quality.items())),
            }


class ShadowAnalyzer:
    def __init__(
        self,
        policy: GatewayPolicy,
        metrics: GatewayMetrics,
        engine: ResidentEngine | Any | None,
        *,
        max_pending_bytes: int | None = None,
        completion_timeout_seconds: float | None = None,
    ) -> None:
        self.policy = policy
        self.metrics = metrics
        self.engine = engine
        self.max_pending_bytes = (
            policy.max_response_bytes
            if max_pending_bytes is None
            else max_pending_bytes
        )
        if self.max_pending_bytes <= 0:
            raise ValueError("max_pending_bytes must be positive")
        self.completion_timeout_seconds = (
            max(5.0, policy.wall_clock_budget_ms / 1000 + 2.0)
            if completion_timeout_seconds is None
            else completion_timeout_seconds
        )
        if self.completion_timeout_seconds <= 0:
            raise ValueError("completion_timeout_seconds must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=policy.transform_workers,
            thread_name_prefix="prompt-toon-shadow",
        )
        self._lock = threading.Lock()
        self._pending_jobs = 0
        self._pending_bytes = 0
        self._closed = False

    @property
    def engine_available(self) -> bool:
        with self._lock:
            return self.engine is not None

    @property
    def pending_jobs(self) -> int:
        with self._lock:
            return self._pending_jobs

    @property
    def pending_bytes(self) -> int:
        with self._lock:
            return self._pending_bytes

    def observe_request(self, body: bytes) -> None:
        try:
            parsed = parse_message_for_shadow(body, self.policy)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.metrics.increment("shadow_request_parse_errors")
            self.metrics.record_request(None, 0)
            return
        self.metrics.record_request(parsed.requested_model, len(parsed.docs))
        if parsed.truncated_docs:
            self.metrics.increment("shadow_documents_truncated", parsed.truncated_docs)
        if not parsed.docs:
            self.metrics.increment("shadow_skipped_no_typed_context")
            return
        with self._lock:
            if (
                self._closed
                or self.engine is None
                or self._pending_jobs >= self.policy.max_concurrent_streams
                or self._pending_bytes + parsed.selected_bytes > self.max_pending_bytes
            ):
                if self.engine is None:
                    self.metrics.increment("shadow_skipped_engine_unavailable")
                else:
                    self.metrics.increment("shadow_dropped_capacity")
                return
            self._pending_jobs += 1
            self._pending_bytes += parsed.selected_bytes
        self.metrics.increment("shadow_admitted")
        try:
            self._executor.submit(self._run_shadow, parsed)
        except RuntimeError:
            self._release(parsed.selected_bytes)
            self.metrics.increment("shadow_dropped_capacity")

    def _run_shadow(self, parsed: ParsedMessage) -> None:
        request_id = f"gw-{uuid.uuid4().hex}"
        try:
            with self._lock:
                engine = self.engine
            if engine is None:
                self.metrics.increment("shadow_skipped_engine_unavailable")
                return
            response = engine.submit_condense_run(
                request_id=request_id,
                stream_id=f"anthropic-{uuid.uuid4().hex}",
                docs=parsed.docs,
                run_id=request_id,
                generated_at=_now_utc(),
                max_input_bytes=self.policy.max_input_bytes,
                budget_ms=self.policy.wall_clock_budget_ms,
                max_cards=self.policy.max_cards_per_document,
                min_toon_savings=json.dumps(
                    self.policy.min_savings, separators=(",", ":")
                ),
            ).result(timeout=self.completion_timeout_seconds)
            per_doc, summary, _manifest = response
            withheld = sum(bool(item.get("withheld")) for item in per_doc)
            cards = sum(
                len(item.get("cards", []))
                for item in per_doc
                if isinstance(item.get("cards", []), list)
            )
            raw_tokens = sum(_rough_token_count(item["body"]) for item in parsed.docs)
            condensed_tokens = _rough_token_count(summary)
            if withheld:
                quality = "withheld"
            elif cards == 0:
                quality = "no_cards"
            elif condensed_tokens >= raw_tokens * (1 - self.policy.min_savings):
                quality = "below_savings_gate"
            else:
                quality = "eligible"
            self.metrics.record_shadow_result(
                quality=quality,
                docs=len(parsed.docs),
                withheld=withheld,
                cards=cards,
                raw_tokens=raw_tokens,
                condensed_tokens=condensed_tokens,
            )
        except FutureTimeoutError:
            self.metrics.increment("shadow_timeouts")
            self.metrics.record_shadow_error()
            self._disable_engine(engine)
        except Exception:
            self.metrics.record_shadow_error()
        finally:
            self._release(parsed.selected_bytes)

    def _release(self, selected_bytes: int) -> None:
        with self._lock:
            self._pending_jobs -= 1
            self._pending_bytes -= selected_bytes

    def _disable_engine(self, engine: Any) -> None:
        with self._lock:
            if self.engine is engine:
                self.engine = None
                should_close = True
            else:
                should_close = False
        if should_close:
            engine.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            engine = self.engine
            self.engine = None
        if engine is not None:
            engine.close()
        self._executor.shutdown(wait=True, cancel_futures=True)


class ResponseObserver:
    """Observe a bounded copy of a response while forwarding every byte."""

    def __init__(
        self,
        metrics: GatewayMetrics,
        status: int,
        headers: list[tuple[str, str]],
    ) -> None:
        self.metrics = metrics
        self.status = status
        header_map = {name.lower(): value for name, value in headers}
        content_type = header_map.get("content-type", "").lower()
        content_encoding = header_map.get("content-encoding", "identity").lower()
        self._sse = content_type.split(";", 1)[0].strip() == "text/event-stream"
        self._enabled = content_encoding in ("", "identity")
        self._json_body = bytearray()
        self._line_buffer = bytearray()
        self._event_data: list[bytes] = []
        self._event_bytes = 0
        self._returned_model: str | None = None
        self._usage: dict[str, int] = {}
        self._stream_errors = 0
        self._sse_complete = False
        self._json_parsed = False

    def abort(self) -> None:
        self._enabled = False
        self._json_body.clear()
        self._line_buffer.clear()
        self._event_data.clear()
        self._event_bytes = 0

    def feed(self, chunk: bytes) -> None:
        if not self._enabled:
            return
        try:
            if self._sse:
                self._feed_sse(chunk)
            elif len(self._json_body) + len(chunk) <= _MAX_OBSERVED_RESPONSE_BYTES:
                self._json_body += chunk
            else:
                self._enabled = False
                self._json_body.clear()
        except Exception:
            self._enabled = False

    def _feed_sse(self, chunk: bytes) -> None:
        self._line_buffer += chunk
        if len(self._line_buffer) > _MAX_SSE_LINE_BYTES and b"\n" not in self._line_buffer:
            self._enabled = False
            self._line_buffer.clear()
            self._event_data.clear()
            return
        while True:
            newline = self._line_buffer.find(b"\n")
            if newline < 0:
                return
            line = bytes(self._line_buffer[:newline])
            del self._line_buffer[: newline + 1]
            if len(line) > _MAX_SSE_LINE_BYTES:
                self.abort()
                return
            if line.endswith(b"\r"):
                line = line[:-1]
            self._consume_sse_line(line)

    def _consume_sse_line(self, line: bytes) -> None:
        if not line:
            self._dispatch_sse_event()
            return
        if line.startswith(b"data:"):
            value = line[5:]
            if value.startswith(b" "):
                value = value[1:]
            added = len(value) + (1 if self._event_data else 0)
            if self._event_bytes + added > _MAX_OBSERVED_RESPONSE_BYTES:
                self.abort()
                return
            self._event_data.append(value)
            self._event_bytes += added

    def _dispatch_sse_event(self) -> None:
        if not self._event_data:
            return
        data = b"\n".join(self._event_data)
        self._event_data.clear()
        self._event_bytes = 0
        try:
            value = json.loads(data, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return
        self._observe_value(value)

    def _observe_value(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        event_type = value.get("type")
        if event_type == "error":
            self._stream_errors += 1
        elif event_type == "message_stop":
            self._sse_complete = True
        message = value.get("message")
        if isinstance(message, dict):
            model = _safe_model(message.get("model"))
            if model is not None:
                self._returned_model = model
            self._observe_usage(message.get("usage"))
        model = _safe_model(value.get("model"))
        if model is not None:
            self._returned_model = model
        self._observe_usage(value.get("usage"))

        block = value.get("content_block")
        if isinstance(block, dict) and block.get("type") == "fallback":
            target = block.get("to")
            if isinstance(target, dict):
                fallback_model = _safe_model(target.get("model"))
                if fallback_model is not None:
                    self._returned_model = fallback_model

    def _observe_usage(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        for field in _USAGE_FIELDS:
            amount = value.get(field)
            if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
                self._usage[field] = amount

    def finish(self) -> None:
        if self._enabled:
            if self._sse:
                # An SSE event is dispatched only by its terminating blank
                # line. EOF with a partial line or unterminated event must not
                # promote partial model/usage data to complete telemetry.
                self._line_buffer.clear()
                self._event_data.clear()
                self._event_bytes = 0
            elif self._json_body:
                try:
                    value = json.loads(
                        self._json_body, parse_constant=_reject_json_constant
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    value = None
                self._json_parsed = value is not None
                self._observe_value(value)
        telemetry_complete = self._enabled and (
            self._sse_complete if self._sse else self._json_parsed
        )
        self.metrics.record_provider_response(
            status=self.status,
            returned_model=self._returned_model if telemetry_complete else None,
            usage=self._usage if telemetry_complete else {},
            stream_errors=self._stream_errors,
            telemetry_available=telemetry_complete,
        )


@dataclass(frozen=True)
class GatewayConfig:
    listen_host: str
    listen_port: int
    upstream: str
    max_request_bytes: int
    max_concurrent_requests: int
    upstream_timeout_seconds: float = DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    ingress_header_timeout_seconds: float = DEFAULT_INGRESS_HEADER_TIMEOUT_SECONDS
    ingress_body_timeout_seconds: float = DEFAULT_INGRESS_BODY_TIMEOUT_SECONDS
    shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS

    def __post_init__(self) -> None:
        if not _is_loopback(self.listen_host):
            raise ValueError("gateway listen address must be loopback")
        if not 0 <= self.listen_port <= 65535:
            raise ValueError("gateway port must be in [0, 65535]")
        if self.max_request_bytes <= 0 or self.max_concurrent_requests <= 0:
            raise ValueError("gateway request limits must be positive")
        for name, value in (
            ("upstream timeout", self.upstream_timeout_seconds),
            ("ingress header timeout", self.ingress_header_timeout_seconds),
            ("ingress body timeout", self.ingress_body_timeout_seconds),
            ("shutdown grace", self.shutdown_grace_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        parsed = urlsplit(self.upstream)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("upstream must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("upstream URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("upstream URL must not contain a query or fragment")
        if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
            raise ValueError("non-loopback upstreams must use HTTPS")

    @property
    def upstream_parts(self) -> SplitResult:
        return urlsplit(self.upstream)


class _RequestBodyError(Exception):
    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


class _UnsupportedUpstreamResponse(Exception):
    pass


class AnthropicGatewayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: GatewayConfig,
        analyzer: ShadowAnalyzer,
        metrics: GatewayMetrics,
    ) -> None:
        self.config = config
        self.analyzer = analyzer
        self.metrics = metrics
        self._connection_slots = threading.BoundedSemaphore(
            config.max_concurrent_requests + _ADMIN_CONNECTION_RESERVE
        )
        self.request_slots = threading.BoundedSemaphore(
            config.max_concurrent_requests
        )
        self._active_condition = threading.Condition()
        self._active_connections: set[socket.socket] = set()
        self._upstream_state = "unknown"
        self._closing = False
        self._closed = False
        super().__init__(server_address, AnthropicGatewayHandler)

    def process_request(
        self, request: socket.socket, client_address: tuple[Any, ...]
    ) -> None:
        with self._active_condition:
            closing = self._closing
        if closing or not self._connection_slots.acquire(blocking=False):
            self.metrics.increment("ingress_connections_rejected")
            self._reject_connection(request)
            return
        with self._active_condition:
            self._active_connections.add(request)
        try:
            # The timer in handle_one_request enforces the absolute header
            # deadline; this socket timeout is a slightly later fallback.
            request.settimeout(self.config.ingress_header_timeout_seconds + 1.0)
            super().process_request(request, client_address)
        except Exception:
            self._connection_finished(request)
            self.shutdown_request(request)
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: tuple[Any, ...]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_finished(request)

    def _connection_finished(self, request: socket.socket) -> None:
        with self._active_condition:
            if request not in self._active_connections:
                return
            self._active_connections.remove(request)
            self._connection_slots.release()
            self._active_condition.notify_all()

    @property
    def active_connection_count(self) -> int:
        with self._active_condition:
            return len(self._active_connections)

    @property
    def accepting(self) -> bool:
        with self._active_condition:
            return not self._closing and not self._closed

    @property
    def upstream_state(self) -> str:
        with self._active_condition:
            return self._upstream_state

    def record_upstream_state(self, state: str) -> None:
        if state not in ("reachable", "failed"):
            raise ValueError(f"invalid upstream state {state!r}")
        with self._active_condition:
            self._upstream_state = state

    def begin_shutdown(self) -> None:
        with self._active_condition:
            self._closing = True

    @staticmethod
    def _reject_connection(request: socket.socket) -> None:
        body = (
            b'{"error":{"message":"gateway connection limit reached",'
            b'"type":"overloaded_error"},"type":"error"}\n'
        )
        response = b"".join(
            (
                b"HTTP/1.1 503 Service Unavailable\r\n",
                b"Content-Type: application/json\r\n",
                f"Content-Length: {len(body)}\r\n".encode("ascii"),
                b"Connection: close\r\n\r\n",
                body,
            )
        )
        try:
            request.settimeout(1.0)
            request.sendall(response)
        except OSError:
            pass
        finally:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            request.close()

    def server_close(self) -> None:
        with self._active_condition:
            if self._closed:
                return
            self._closed = True
            self._closing = True
            deadline = time.monotonic() + self.config.shutdown_grace_seconds
            while self._active_connections:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._active_condition.wait(remaining)
            remaining_connections = list(self._active_connections)
        for connection in remaining_connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            super().server_close()
        finally:
            self.analyzer.close()


class AnthropicGatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: AnthropicGatewayServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def handle_one_request(self) -> None:
        header_complete = threading.Event()
        self._header_complete = header_complete
        self._header_expired = threading.Event()
        timer = threading.Timer(
            self.server.config.ingress_header_timeout_seconds,
            self._expire_incomplete_headers,
            args=(header_complete,),
        )
        timer.daemon = True
        timer.start()
        try:
            super().handle_one_request()
        finally:
            header_complete.set()
            timer.cancel()

    def parse_request(self) -> bool:
        try:
            parsed = super().parse_request()
        finally:
            self._header_complete.set()
        if self._header_expired.is_set():
            self.close_connection = True
            return False
        return parsed

    def _expire_incomplete_headers(self, header_complete: threading.Event) -> None:
        if header_complete.is_set():
            return
        self._header_expired.set()
        self.server.metrics.increment("ingress_header_timeouts")
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def do_GET(self) -> None:
        self.close_connection = True
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            self._send_json(
                200,
                {
                    "status": "ok",
                    "mode": "shadow",
                    "accepting": self.server.accepting,
                    "engine_available": self.server.analyzer.engine_available,
                    "pending_transforms": self.server.analyzer.pending_jobs,
                    "pending_transform_bytes": self.server.analyzer.pending_bytes,
                    "upstream": self.server.upstream_state,
                },
            )
            return
        if path == READINESS_PATH:
            accepting = self.server.accepting
            engine_available = self.server.analyzer.engine_available
            ready = accepting and engine_available
            self._send_json(
                200 if ready else 503,
                {
                    "status": "ready" if ready else "not_ready",
                    "mode": "shadow",
                    "accepting": accepting,
                    "engine_available": engine_available,
                    "upstream": self.server.upstream_state,
                },
            )
            return
        if path == METRICS_PATH:
            self._send_json(200, self.server.metrics.snapshot())
            return
        self._send_local_error(404, "not_found_error", "local endpoint not found")

    def do_HEAD(self) -> None:
        self.close_connection = True
        if urlsplit(self.path).path != "/":
            self.send_response_only(404)
        else:
            self.send_response_only(204 if self.server.accepting else 503)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != MESSAGES_PATH:
            self.close_connection = True
            self._send_local_error(
                404, "not_found_error", "only POST /v1/messages is supported"
            )
            return
        if not self.server.request_slots.acquire(blocking=False):
            self.close_connection = True
            self._send_local_error(
                503, "overloaded_error", "gateway concurrent request limit reached"
            )
            return
        try:
            deadline = time.monotonic() + self.server.config.ingress_body_timeout_seconds
            body = self._read_request_body(deadline)
            self.connection.settimeout(
                self.server.config.ingress_body_timeout_seconds
            )
            self._proxy_messages(body)
        except _RequestBodyError as exc:
            self.close_connection = True
            self._send_local_error(exc.status, exc.error_type, exc.message)
        finally:
            self.server.request_slots.release()

    def _read_request_body(self, deadline: float) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        transfer_values = self.headers.get_all("Transfer-Encoding", [])
        if lengths and transfer_values:
            raise _RequestBodyError(
                400,
                "invalid_request_error",
                "conflicting request body framing",
            )
        if transfer_values:
            tokens = [
                token.strip().lower()
                for value in transfer_values
                for token in value.split(",")
                if token.strip()
            ]
            if tokens != ["chunked"]:
                raise _RequestBodyError(
                    400,
                    "invalid_request_error",
                    "unsupported transfer encoding",
                )
            return self._read_chunked_body(deadline)
        if len(lengths) != 1:
            raise _RequestBodyError(
                411,
                "invalid_request_error",
                "exactly one Content-Length header is required",
            )
        raw_length = lengths[0]
        if (
            not raw_length.isascii()
            or not raw_length.isdigit()
            or len(raw_length) > _MAX_DECIMAL_DIGITS
        ):
            raise _RequestBodyError(
                400, "invalid_request_error", "invalid Content-Length"
            )
        length = int(raw_length)
        if length > self.server.config.max_request_bytes:
            raise _RequestBodyError(
                413, "request_too_large", "request body exceeds gateway limit"
            )
        return self._read_exact(length, deadline)

    def _read_exact(self, length: int, deadline: float) -> bytes:
        body = bytearray()
        while len(body) < length:
            self._set_ingress_deadline(deadline)
            try:
                chunk = self.rfile.read(min(length - len(body), 64 * 1024))
            except (TimeoutError, socket.timeout) as exc:
                raise _RequestBodyError(
                    408, "timeout_error", "request body deadline exceeded"
                ) from exc
            if not chunk:
                raise _RequestBodyError(
                    400, "invalid_request_error", "truncated request body"
                )
            body += chunk
        return bytes(body)

    def _read_chunked_body(self, deadline: float) -> bytes:
        body = bytearray()
        while True:
            line = self._readline_with_deadline(deadline)
            if (
                not line
                or len(line) > _MAX_CHUNK_LINE_BYTES
                or not line.endswith(b"\r\n")
            ):
                raise _RequestBodyError(
                    400, "invalid_request_error", "invalid chunk framing"
                )
            size_text = line.strip().split(b";", 1)[0]
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise _RequestBodyError(
                    400, "invalid_request_error", "invalid chunk size"
                ) from exc
            if size < 0 or len(body) + size > self.server.config.max_request_bytes:
                raise _RequestBodyError(
                    413, "request_too_large", "request body exceeds gateway limit"
                )
            if size == 0:
                trailer_bytes = 0
                while True:
                    trailer = self._readline_with_deadline(deadline)
                    trailer_bytes += len(trailer)
                    if (
                        not trailer
                        or len(trailer) > _MAX_CHUNK_LINE_BYTES
                        or trailer_bytes > _MAX_TRAILER_BYTES
                    ):
                        raise _RequestBodyError(
                            400, "invalid_request_error", "invalid chunk trailers"
                        )
                    if trailer == b"\r\n":
                        return bytes(body)
                    raise _RequestBodyError(
                        400,
                        "invalid_request_error",
                        "request trailers are not supported",
                    )
            body += self._read_exact(size, deadline)
            if self._read_exact(2, deadline) != b"\r\n":
                raise _RequestBodyError(
                    400, "invalid_request_error", "invalid chunk delimiter"
                )

    def _readline_with_deadline(self, deadline: float) -> bytes:
        self._set_ingress_deadline(deadline)
        try:
            return self.rfile.readline(_MAX_CHUNK_LINE_BYTES + 1)
        except (TimeoutError, socket.timeout) as exc:
            raise _RequestBodyError(
                408, "timeout_error", "request body deadline exceeded"
            ) from exc

    def _set_ingress_deadline(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _RequestBodyError(
                408, "timeout_error", "request body deadline exceeded"
            )
        self.connection.settimeout(remaining)

    def _proxy_messages(self, body: bytes) -> None:
        config = self.server.config
        parts = config.upstream_parts
        port = parts.port or (443 if parts.scheme == "https" else 80)
        connection_type = (
            http.client.HTTPSConnection
            if parts.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parts.hostname,
            port,
            timeout=config.upstream_timeout_seconds,
        )
        response: http.client.HTTPResponse | None = None
        try:
            incoming = urlsplit(self.path)
            base_path = parts.path.rstrip("/")
            upstream_path = f"{base_path}{incoming.path}"
            if incoming.query:
                upstream_path += f"?{incoming.query}"
            connection.putrequest(
                "POST", upstream_path, skip_host=True, skip_accept_encoding=True
            )
            authority = parts.hostname
            if ":" in authority:
                authority = f"[{authority}]"
            if port != (443 if parts.scheme == "https" else 80):
                authority = f"{authority}:{port}"
            connection.putheader("Host", authority)

            connection_tokens = {
                token.strip().lower()
                for value in self.headers.get_all("Connection", [])
                for token in value.split(",")
            }
            stripped = _HOP_BY_HOP | connection_tokens | {
                "host",
                "content-length",
            }
            for name, value in self.headers.raw_items():
                if name.lower() not in stripped:
                    connection.putheader(name, value)
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)

            # The provider has received the exact entity bytes before any
            # JSON parsing or shadow transform can consume local latency.
            try:
                self.server.analyzer.observe_request(body)
            except Exception:
                # Shadow observation is never allowed to change provider IO.
                self.server.metrics.record_shadow_error()
            response = connection.getresponse()
            self.server.record_upstream_state("reachable")
            self._forward_response(response)
        except _UnsupportedUpstreamResponse:
            self.server.metrics.increment("upstream_framing_rejected")
            self.close_connection = True
            self._send_local_error(
                502, "api_error", "unsupported upstream response framing"
            )
        except (OSError, http.client.HTTPException):
            self.server.metrics.increment("upstream_transport_failures")
            if response is None and not self.wfile.closed:
                self.server.record_upstream_state("failed")
                self.close_connection = True
                self._send_local_error(
                    502, "api_error", "upstream transport failed"
                )
            else:
                self.close_connection = True
        finally:
            connection.close()

    def _forward_response(self, response: http.client.HTTPResponse) -> None:
        headers = response.getheaders()
        observer = ResponseObserver(self.server.metrics, response.status, headers)
        connection_tokens = {
            token.strip().lower()
            for name, value in headers
            if name.lower() == "connection"
            for token in value.split(",")
        }
        transfer_values = [
            value
            for name, value in headers
            if name.lower() == "transfer-encoding"
        ]
        transfer_tokens = [
            token.strip().lower()
            for value in transfer_values
            for token in value.split(",")
            if token.strip()
        ]
        has_trailers = any(
            name.lower() == "trailer" and value.strip()
            for name, value in headers
        )
        if transfer_tokens not in ([], ["chunked"]) or has_trailers:
            raise _UnsupportedUpstreamResponse
        upstream_chunked = transfer_tokens == ["chunked"]
        stripped = _HOP_BY_HOP | connection_tokens
        if upstream_chunked:
            stripped.add("content-length")

        self.send_response_only(response.status, response.reason)
        for name, value in headers:
            if name.lower() not in stripped:
                self.send_header(name, value)
        if upstream_chunked:
            self.send_header("Transfer-Encoding", "chunked")
        elif response.length is None and response.status not in (204, 304):
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        client_failed = False
        try:
            while True:
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                observer.feed(chunk)
                if client_failed:
                    continue
                try:
                    if upstream_chunked:
                        self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                    else:
                        self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    client_failed = True
                    observer.abort()
                    self.server.metrics.increment("downstream_disconnects")
                    break
            if not client_failed and response.length not in (None, 0):
                observer.abort()
                raise http.client.IncompleteRead(b"")
            if upstream_chunked and not client_failed:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    observer.abort()
                    self.server.metrics.increment("downstream_disconnects")
        except (OSError, http.client.HTTPException):
            observer.abort()
            self.server.metrics.increment("upstream_response_read_failures")
            raise
        finally:
            observer.finish()

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_local_error(self, status: int, error_type: str, message: str) -> None:
        self._send_json(
            status,
            {
                "type": "error",
                "error": {"type": error_type, "message": message},
            },
        )


def create_gateway_server(
    config: GatewayConfig,
    analyzer: ShadowAnalyzer,
    metrics: GatewayMetrics,
) -> AnthropicGatewayServer:
    listen_host = "127.0.0.1" if config.listen_host == "localhost" else config.listen_host
    address = (listen_host, config.listen_port)
    if ipaddress.ip_address(listen_host).version == 6:
        class IPv6GatewayServer(AnthropicGatewayServer):
            address_family = socket.AF_INET6

        return IPv6GatewayServer(address, config, analyzer, metrics)
    return AnthropicGatewayServer(address, config, analyzer, metrics)


def run_gateway(args: Any) -> int:
    policy = GatewayPolicy.load(args.policy)
    metrics = GatewayMetrics()
    config = GatewayConfig(
        listen_host=args.listen,
        listen_port=args.port,
        upstream=args.upstream,
        max_request_bytes=policy.max_request_bytes,
        max_concurrent_requests=policy.max_concurrent_streams,
        upstream_timeout_seconds=args.upstream_timeout,
        ingress_header_timeout_seconds=args.ingress_header_timeout,
        ingress_body_timeout_seconds=args.ingress_body_timeout,
        shutdown_grace_seconds=args.shutdown_grace,
    )
    try:
        engine: ResidentEngine | None = ResidentEngine(
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
        if args.require_ptoon:
            raise SystemExit(str(exc)) from exc
        engine = None
    analyzer = ShadowAnalyzer(policy, metrics, engine)
    try:
        server = create_gateway_server(config, analyzer, metrics)
    except Exception:
        analyzer.close()
        raise
    host, port = server.server_address[:2]
    print(
        f"prompt-toon shadow gateway listening on http://{host}:{port}; "
        f"upstream={args.upstream}; engine_available={engine is not None}",
        flush=True,
    )
    previous_handlers: dict[signal.Signals, Any] = {}

    def request_shutdown(signum: int, _frame: Any) -> None:
        metrics.increment("shutdown_signals")
        server.begin_shutdown()
        thread = threading.Thread(
            target=server.shutdown,
            name=f"prompt-toon-shutdown-{signum}",
            daemon=True,
        )
        thread.start()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        server.server_close()
    return 0
