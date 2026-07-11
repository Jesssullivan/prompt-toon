"""Thread-safe client for the fixed ``ptoon serve`` v1 pipe protocol."""

from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from prompt_toon.engine import ChapelEngine, EngineError, resolve_binary_path


DEFAULT_MAX_STREAMS = 64
DEFAULT_WORKERS = 16
DEFAULT_QUEUE_DEPTH = 64
DEFAULT_MAX_DOCS = 64
DEFAULT_MAX_REQUEST_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_LABEL_BYTES = 4096
DEFAULT_MAX_INPUT_BYTES = 2_000_000
DEFAULT_BUDGET_MS = 2000
DEFAULT_MAX_CARDS = 24

_MAX_DECIMAL_DIGITS = 20
_MAX_POLICY_VALUE = 2_147_483_647
_STDERR_LIMIT = 64 * 1024
_ERROR_BODY_LIMIT = 64 * 1024
_CAPS_TIMEOUT_SECONDS = 5

CondenseRunResult = tuple[list[dict[str, Any]], str, dict[str, Any]]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _safe_diagnostic(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").encode("unicode_escape").decode("ascii")


@dataclass(frozen=True)
class _PendingRequest:
    future: Future[CondenseRunResult]
    stream_id: str
    expected_meta: list[dict[str, Any]]
    max_cards: int
    run_id: str
    generated_at: str
    parsed_savings: int | float
    default_trust_tier: str
    tier_overrides: dict[str, str]


class ResidentEngine:
    """Own one resident ``ptoon serve`` process and demultiplex its replies.

    ``submit_condense_run`` returns a ``Future``. Calls are safe from multiple
    threads: request frames are written atomically under one lock while one
    reader thread dispatches out-of-order responses by request ID.
    """

    def __init__(
        self,
        binary_path: str | Path | None = None,
        *,
        max_streams: int = DEFAULT_MAX_STREAMS,
        workers: int = DEFAULT_WORKERS,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_label_bytes: int = DEFAULT_MAX_LABEL_BYTES,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
        budget_ms: int = DEFAULT_BUDGET_MS,
        max_cards: int = DEFAULT_MAX_CARDS,
    ) -> None:
        for name, value in (
            ("max_streams", max_streams),
            ("workers", workers),
            ("queue_depth", queue_depth),
            ("max_docs", max_docs),
            ("max_request_bytes", max_request_bytes),
            ("max_response_bytes", max_response_bytes),
            ("max_label_bytes", max_label_bytes),
            ("max_input_bytes", max_input_bytes),
            ("budget_ms", budget_ms),
            ("max_cards", max_cards),
        ):
            self._require_positive_int(name, value)
        if workers > max_streams:
            raise ValueError("workers must not exceed max_streams")
        if queue_depth < workers:
            raise ValueError("queue_depth must be at least workers")
        if max_input_bytes > max_request_bytes:
            raise ValueError("max_input_bytes must not exceed max_request_bytes")
        if max_response_bytes < max_request_bytes:
            raise ValueError("max_response_bytes must be at least max_request_bytes")
        if budget_ms > _MAX_POLICY_VALUE:
            raise ValueError(f"budget_ms must not exceed {_MAX_POLICY_VALUE}")
        if max_cards > _MAX_POLICY_VALUE:
            raise ValueError(f"max_cards must not exceed {_MAX_POLICY_VALUE}")

        resolved = Path(binary_path) if binary_path is not None else resolve_binary_path()
        if resolved is None:
            raise EngineError(
                "ptoon binary is not available: set PROMPT_TOON_PTOON or build "
                "build/ptoon relative to the repo root"
            )
        try:
            caps_proc = subprocess.run(
                [str(resolved), "caps"],
                capture_output=True,
                timeout=_CAPS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineError(f"ptoon serve caps preflight failed: {exc}") from exc
        if caps_proc.returncode != 0 or caps_proc.stderr:
            detail = _safe_diagnostic(caps_proc.stderr).strip()
            raise EngineError(
                f"ptoon serve caps preflight failed with status "
                f"{caps_proc.returncode}: {detail}"
            )
        try:
            caps = json.loads(caps_proc.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EngineError(f"ptoon serve caps preflight returned invalid JSON: {exc}") from exc
        features = caps.get("features") if isinstance(caps, dict) else None
        if (
            not isinstance(features, list)
            or not all(isinstance(item, str) for item in features)
            or caps.get("serve_protocol") != 1
            or "serve" not in features
        ):
            raise EngineError("ptoon binary does not advertise serve protocol v1")

        self.max_streams = max_streams
        self.workers = workers
        self.queue_depth = queue_depth
        self.max_docs = max_docs
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_label_bytes = max_label_bytes
        self.max_input_bytes = max_input_bytes
        self.budget_ms = budget_ms
        self.max_cards = max_cards
        self.argv = (
            str(resolved),
            "serve",
            str(workers),
            str(queue_depth),
            str(max_docs),
            str(max_request_bytes),
            str(max_label_bytes),
            str(max_input_bytes),
            str(budget_ms),
            str(max_cards),
            str(max_response_bytes),
        )

        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._active_streams: set[str] = set()
        self._closed = False
        self._terminal_error: EngineError | None = None
        self._stderr = bytearray()

        try:
            self._process = subprocess.Popen(
                list(self.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise EngineError(f"ptoon serve could not be started: {exc}") from exc
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            self._process.kill()
            self._process.wait()
            raise EngineError("ptoon serve did not expose all required pipes")

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="ptoon-serve-stderr",
            daemon=True,
        )
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="ptoon-serve-reader",
            daemon=True,
        )
        self._stderr_thread.start()
        self._reader_thread.start()

    @staticmethod
    def _require_positive_int(name: str, value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive int")

    def _encode_label(
        self,
        value: object,
        name: str,
        *,
        allow_empty: bool = False,
        visible_ascii: bool = False,
    ) -> bytes:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        if not value and not allow_empty:
            raise ValueError(f"{name} must not be empty")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{name} must be valid UTF-8") from exc
        if visible_ascii and any(byte < 0x21 or byte > 0x7E for byte in encoded):
            raise ValueError(f"{name} must contain only visible ASCII characters")
        if len(encoded) > self.max_label_bytes:
            raise ValueError(
                f"{name} is {len(encoded)} bytes; limit is {self.max_label_bytes}"
            )
        return encoded

    @staticmethod
    def _length_prefixed(data: bytes) -> bytes:
        return str(len(data)).encode("ascii") + b"\n" + data

    def _resolve_policy(
        self,
        max_input_bytes: int | None,
        budget_ms: int | None,
        max_cards: int | None,
    ) -> tuple[int, int, int]:
        resolved = (
            self.max_input_bytes if max_input_bytes is None else max_input_bytes,
            self.budget_ms if budget_ms is None else budget_ms,
            self.max_cards if max_cards is None else max_cards,
        )
        for name, value in zip(
            ("max_input_bytes", "budget_ms", "max_cards"), resolved, strict=True
        ):
            self._require_positive_int(name, value)
        input_cap, budget, card_cap = resolved
        if input_cap > self.max_input_bytes:
            raise ValueError(
                f"max_input_bytes must not exceed configured ceiling {self.max_input_bytes}"
            )
        if budget > self.budget_ms:
            raise ValueError(
                f"budget_ms must not exceed configured ceiling {self.budget_ms}"
            )
        if card_cap > self.max_cards:
            raise ValueError(
                f"max_cards must not exceed configured ceiling {self.max_cards}"
            )
        return input_cap, budget, card_cap

    def _prepare_payload(
        self,
        request_id: str,
        docs: list[dict[str, str]],
        run_id: str,
        generated_at: str,
        min_toon_savings: str,
        default_trust_tier: str,
        tier_overrides: dict[str, str] | None,
    ) -> tuple[bytes, list[dict[str, Any]], int | float, dict[str, str]]:
        if request_id != run_id:
            raise ValueError("request_id must match condense run_id")
        for name, value in (
            ("run_id", run_id),
            ("generated_at", generated_at),
            ("min_toon_savings", min_toon_savings),
            ("default_trust_tier", default_trust_tier),
        ):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        try:
            parsed_savings = json.loads(
                min_toon_savings, parse_constant=_reject_json_constant
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("min_toon_savings must be a JSON number") from exc
        if isinstance(parsed_savings, bool) or not isinstance(parsed_savings, (int, float)):
            raise ValueError("min_toon_savings must be a JSON number")

        overrides = {} if tier_overrides is None else tier_overrides
        if not isinstance(overrides, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in overrides.items()
        ):
            raise ValueError("tier_overrides must be a dict[str, str]")
        overrides_copy = dict(overrides)
        overrides_json = json.dumps(
            overrides_copy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        if not isinstance(docs, list):
            raise ValueError("docs must be a list")
        if len(docs) > self.max_docs:
            raise ValueError(f"document count {len(docs)} exceeds limit {self.max_docs}")
        framed_docs, expected_meta = ChapelEngine._frame_docs(docs)

        header_fields = (
            ("run_id", run_id),
            ("generated_at", generated_at),
            ("min_toon_savings", min_toon_savings),
            ("default_trust_tier", default_trust_tier),
            ("tier_overrides", overrides_json),
        )
        header = bytearray()
        for name, value in header_fields:
            header += self._length_prefixed(
                self._encode_label(value, name, allow_empty=True)
            )
        for index, doc in enumerate(docs):
            self._encode_label(
                doc["source"], f"doc {index} source", allow_empty=True
            )
            self._encode_label(
                doc["trust_tier"], f"doc {index} trust_tier", allow_empty=True
            )

        payload = bytes(header) + bytes(framed_docs)
        if len(payload) > self.max_request_bytes:
            raise ValueError(
                f"request payload is {len(payload)} bytes; limit is "
                f"{self.max_request_bytes}"
            )
        return payload, expected_meta, parsed_savings, overrides_copy

    def submit_condense_run(
        self,
        request_id: str,
        stream_id: str,
        docs: list[dict[str, str]],
        run_id: str,
        generated_at: str,
        *,
        max_input_bytes: int | None = None,
        budget_ms: int | None = None,
        max_cards: int | None = None,
        min_toon_savings: str = "0.2",
        default_trust_tier: str = "untrusted_tool_output",
        tier_overrides: dict[str, str] | None = None,
    ) -> Future[CondenseRunResult]:
        """Submit one condense run and return its response future."""
        request_bytes = self._encode_label(
            request_id, "request_id", visible_ascii=True
        )
        stream_bytes = self._encode_label(
            stream_id, "stream_id", visible_ascii=True
        )
        input_cap, budget, card_cap = self._resolve_policy(
            max_input_bytes, budget_ms, max_cards
        )
        payload, expected_meta, parsed_savings, overrides = self._prepare_payload(
            request_id,
            docs,
            run_id,
            generated_at,
            min_toon_savings,
            default_trust_tier,
            tier_overrides,
        )
        frame = b"".join(
            (
                self._length_prefixed(request_bytes),
                self._length_prefixed(stream_bytes),
                f"{input_cap}\n{budget}\n{card_cap}\n".encode("ascii"),
                self._length_prefixed(payload),
            )
        )

        future: Future[CondenseRunResult] = Future()
        future.set_running_or_notify_cancel()
        pending = _PendingRequest(
            future=future,
            stream_id=stream_id,
            expected_meta=expected_meta,
            max_cards=card_cap,
            run_id=run_id,
            generated_at=generated_at,
            parsed_savings=parsed_savings,
            default_trust_tier=default_trust_tier,
            tier_overrides=overrides,
        )

        with self._write_lock:
            with self._state_lock:
                self._raise_if_unavailable_locked()
                if request_id in self._pending:
                    raise ValueError(f"duplicate active request_id {request_id!r}")
                if stream_id in self._active_streams:
                    raise ValueError(f"duplicate active stream_id {stream_id!r}")
                if len(self._active_streams) >= self.max_streams:
                    raise EngineError(
                        f"ptoon serve client stream limit {self.max_streams} reached"
                    )
                self._pending[request_id] = pending
                self._active_streams.add(stream_id)
            try:
                self._write_all(self._process.stdin, frame)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                error = EngineError(f"ptoon serve request write failed: {exc}")
                self._fail_all(error)
                self._terminate_process()
                raise error from exc
        return future

    @staticmethod
    def _write_all(stream: BinaryIO, data: bytes) -> None:
        pos = 0
        while pos < len(data):
            written = stream.write(data[pos:])
            if written is None or written <= 0:
                raise BrokenPipeError("write returned no progress")
            pos += written

    def _raise_if_unavailable_locked(self) -> None:
        if self._terminal_error is not None:
            raise EngineError(str(self._terminal_error))
        if self._closed:
            raise EngineError("ResidentEngine is closed")
        return_code = self._process.poll()
        if return_code is not None:
            detail = self._stderr_text_locked()
            suffix = f": {detail}" if detail else ""
            raise EngineError(
                f"ptoon serve exited with status {return_code}{suffix}"
            )

    def _drain_stderr(self) -> None:
        try:
            while True:
                chunk = self._process.stderr.read(4096)
                if not chunk:
                    return
                with self._state_lock:
                    remaining = _STDERR_LIMIT - len(self._stderr)
                    if remaining > 0:
                        self._stderr += chunk[:remaining]
        except (OSError, ValueError):
            return

    def _stderr_text_locked(self) -> str:
        return _safe_diagnostic(bytes(self._stderr)).strip()

    @staticmethod
    def _read_decimal_line(
        stream: BinaryIO,
        what: str,
        *,
        max_value: int,
        allow_eof: bool = False,
    ) -> int | None:
        line = stream.readline(_MAX_DECIMAL_DIGITS + 2)
        if line == b"":
            if allow_eof:
                return None
            raise EngineError(f"ptoon serve response truncated reading {what}")
        if not line.endswith(b"\n"):
            raise EngineError(f"ptoon serve response has overlong {what}")
        digits = line[:-1]
        if (
            not digits
            or len(digits) > _MAX_DECIMAL_DIGITS
            or any(byte < 0x30 or byte > 0x39 for byte in digits)
        ):
            raise EngineError(f"ptoon serve response has invalid {what}")
        value = int(digits)
        if value > max_value:
            raise EngineError(f"ptoon serve response {what} exceeds limit")
        return value

    @staticmethod
    def _read_exact(stream: BinaryIO, length: int, what: str) -> bytes:
        result = bytearray()
        while len(result) < length:
            chunk = stream.read(min(length - len(result), 64 * 1024))
            if not chunk:
                raise EngineError(f"ptoon serve response truncated reading {what}")
            result += chunk
        return bytes(result)

    def _read_prefixed(
        self, stream: BinaryIO, what: str, *, max_value: int
    ) -> bytes:
        length = self._read_decimal_line(stream, f"{what} length", max_value=max_value)
        assert length is not None
        return self._read_exact(stream, length, what)

    def _read_response(self) -> tuple[str, str, str, bytes] | None:
        stdout = self._process.stdout
        request_length = self._read_decimal_line(
            stdout,
            "request id length",
            max_value=self.max_label_bytes,
            allow_eof=True,
        )
        if request_length is None:
            return None
        if request_length == 0:
            raise EngineError("ptoon serve response has invalid request id")
        request_raw = self._read_exact(stdout, request_length, "request id")
        stream_raw = self._read_prefixed(
            stdout, "stream id", max_value=self.max_label_bytes
        )
        status_raw = self._read_prefixed(stdout, "status", max_value=5)
        if any(byte < 0x21 or byte > 0x7E for byte in request_raw):
            raise EngineError("ptoon serve response has invalid request id")
        if any(byte < 0x21 or byte > 0x7E for byte in stream_raw):
            raise EngineError("ptoon serve response has invalid stream id")
        try:
            request_id = request_raw.decode("ascii")
            stream_id = stream_raw.decode("ascii")
            status = status_raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise EngineError("ptoon serve response contains an invalid text field") from exc
        if not request_id:
            raise EngineError("ptoon serve response has invalid request id")
        if not stream_id:
            raise EngineError("ptoon serve response has invalid stream id")
        if status not in {"ok", "error"}:
            raise EngineError(f"ptoon serve response has invalid status {status!r}")
        body_limit = _ERROR_BODY_LIMIT if status == "error" else self.max_response_bytes
        body_length = self._read_decimal_line(
            stdout, "body length", max_value=body_limit
        )
        assert body_length is not None
        body = self._read_exact(stdout, body_length, "body")
        return request_id, stream_id, status, body

    def _reader_loop(self) -> None:
        try:
            while True:
                response = self._read_response()
                if response is None:
                    with self._state_lock:
                        if self._closed:
                            return
                        return_code = self._process.poll()
                        detail = self._stderr_text_locked()
                    suffix = f": {detail}" if detail else ""
                    if return_code is None:
                        raise EngineError("ptoon serve closed stdout unexpectedly")
                    raise EngineError(
                        f"ptoon serve exited with status {return_code}{suffix}"
                    )
                self._dispatch_response(*response)
        except Exception as exc:
            with self._state_lock:
                if self._closed:
                    return
            error = exc if isinstance(exc, EngineError) else EngineError(
                f"ptoon serve response reader failed: {exc}"
            )
            self._fail_all(error)
            self._terminate_process()

    def _dispatch_response(
        self, request_id: str, stream_id: str, status: str, body: bytes
    ) -> None:
        with self._state_lock:
            pending = self._pending.get(request_id)
            if pending is None:
                raise EngineError(
                    f"ptoon serve response has unknown request_id {request_id!r}"
                )
            if pending.stream_id != stream_id:
                raise EngineError(
                    f"ptoon serve response stream_id mismatch for {request_id!r}"
                )

        if status == "error":
            try:
                message = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EngineError("ptoon serve error response is not valid UTF-8") from exc
            message = _safe_diagnostic(body)
            self._complete_request(
                request_id,
                pending,
                error=EngineError(
                    f"ptoon serve request {request_id!r} failed: {message}"
                ),
            )
            return

        try:
            result = ChapelEngine._parse_stream(
                body,
                len(pending.expected_meta),
                expected_meta=pending.expected_meta,
                max_cards=pending.max_cards,
                expect_run_events=True,
            )
            self._validate_manifest(result[2], pending)
        except Exception as exc:
            if isinstance(exc, EngineError):
                raise EngineError(
                    f"ptoon serve request {request_id!r} returned malformed body: {exc}"
                ) from exc
            raise EngineError(
                f"ptoon serve request {request_id!r} response validation failed: {exc}"
            ) from exc
        self._complete_request(request_id, pending, result=result)

    @staticmethod
    def _validate_manifest(
        manifest: dict[str, Any], pending: _PendingRequest
    ) -> None:
        if (
            manifest.get("id") != pending.run_id
            or manifest.get("generated_at") != pending.generated_at
        ):
            raise EngineError("ptoon condense: manifest id/generated_at echo mismatch")
        expected_inputs = [
            {
                "bytes": meta["bytes"],
                "sha256": meta["sha256"],
                "source": meta["source"],
                "trust_tier": meta["trust_tier"],
            }
            for meta in pending.expected_meta
        ]
        if manifest.get("inputs") != expected_inputs:
            raise EngineError("ptoon condense: manifest inputs do not match framed docs")
        tiers = {meta["trust_tier"] for meta in pending.expected_meta}
        if manifest.get("mixed_trust_tiers") != (len(tiers) > 1):
            raise EngineError("ptoon condense: manifest mixed_trust_tiers mismatch")
        settings = manifest.get("settings")
        if (
            not isinstance(settings, dict)
            or settings.get("format") != "jsonl"
            or settings.get("max_cards_per_input") != pending.max_cards
            or settings.get("min_toon_savings") != pending.parsed_savings
            or settings.get("trust_tier") != pending.default_trust_tier
            or settings.get("input_tier_overrides") != pending.tier_overrides
            or settings.get("store_raw") is not False
        ):
            raise EngineError("ptoon condense: manifest settings echo mismatch")

    def _complete_request(
        self,
        request_id: str,
        pending: _PendingRequest,
        *,
        result: CondenseRunResult | None = None,
        error: EngineError | None = None,
    ) -> None:
        with self._state_lock:
            if self._pending.get(request_id) is not pending:
                return
            del self._pending[request_id]
            self._active_streams.discard(pending.stream_id)
        if error is not None:
            pending.future.set_exception(error)
        else:
            assert result is not None
            pending.future.set_result(result)

    def _fail_all(self, error: EngineError) -> None:
        with self._state_lock:
            if self._terminal_error is None:
                self._terminal_error = error
            terminal = self._terminal_error
            pending = list(self._pending.values())
            self._pending.clear()
            self._active_streams.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(EngineError(str(terminal)))

    def _terminate_process(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass

    @property
    def pending_count(self) -> int:
        with self._state_lock:
            return len(self._pending)

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed or self._terminal_error is not None

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def close(self) -> None:
        """Fail outstanding calls and stop the child process. Idempotent."""
        with self._close_lock:
            with self._state_lock:
                already_closed = self._closed
                self._closed = True
                pending = list(self._pending.values())
                self._pending.clear()
                self._active_streams.clear()
            if not already_closed:
                for item in pending:
                    if not item.future.done():
                        item.future.set_exception(EngineError("ResidentEngine is closed"))

            # A writer can be blocked in a full OS pipe while holding
            # _write_lock. Killing a child with outstanding work forces that
            # write to unwind, so close cannot wait forever behind it.
            if pending:
                self._terminate_process()

            with self._write_lock:
                try:
                    self._process.stdin.close()
                except (OSError, ValueError):
                    pass

            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._terminate_process()
                try:
                    self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        self._process.kill()
                    except OSError:
                        pass
                    self._process.wait()

            for stream in (self._process.stdout, self._process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            current = threading.current_thread()
            for thread in (self._reader_thread, self._stderr_thread):
                if thread is not current:
                    thread.join(timeout=1.0)

    def __enter__(self) -> ResidentEngine:
        with self._state_lock:
            self._raise_if_unavailable_locked()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
