"""Subprocess wrapper for the standalone `ptoon` binary (TIN-2708 C1).

ARCHITECTURE PIVOT: the Chapel engine is no longer a ctypes-loaded shared
library. It is a standalone `ptoon` binary invoked as a subprocess, one
call per one-shot transform. C4a adds a separate long-lived `ptoon serve`
client in `prompt_toon.resident`; both boundaries keep Chapel runtime ownership
inside the child process rather than re-entering it through foreign threads.
It also sidesteps a Chapel foreign-thread runtime-reentry wall the ctypes
path hit: repeated exported-proc calls into a `--library` build segfaulted
on buffer free (init/caps/normalize succeeded; free crashed).

Binary protocol (fixed contract; see spikes/tin-2707/ptoon_spike.chpl for
the reference implementation this binary's `redact` framing matches
byte-for-byte). The binary takes one subcommand as argv[1] and reads all
of stdin as raw bytes:

- ``ptoon normalize`` -> writes normalized text to stdout, exit 0.
- ``ptoon defang``    -> writes defanged text to stdout, exit 0.
- ``ptoon redact``    -> writes exactly one line ``findings:<comma-joined
  pattern names or empty>\\n`` then the redacted text verbatim, exit 0.
- ``ptoon redact-batch`` -> writes length-prefixed per-document redaction
  results in input order; this is the C2b coforall fan-in entrypoint.
- ``ptoon condense-batch`` -> triplet-framed docs in, JSONL doc/card/end/
  batch events out (C2d streaming source cards).
- ``ptoon condense`` -> condense-batch plus a caller-supplied run header;
  adds one summary event and one manifest event (C2e run-level artifacts).
- ``ptoon serve``    -> resident v1 request/response framing; owned by
  ``ResidentEngine`` rather than this one-shot wrapper.
- ``ptoon caps``      -> writes the engine-caps JSON to stdout, exit 0.
- unknown subcommand  -> stderr message, exit 2.

Fail-open / fail-closed contract (INV-5, docs/mythos-delivery-design.md
Sec7 C1 entry): this module itself never decides fail-open vs fail-closed
-- it only reports ``available()`` and raises ``EngineError`` on any
nonzero exit code or non-empty stderr. ``prompt_toon/cli.py``'s
``resolve_engine`` is the policy layer: ``--engine=chapel`` fails closed
(raises) when unavailable; ``--engine=auto`` fails open to the Python
implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .dogfood import COMPACT_HANDOFF_FORMAT


class EngineError(RuntimeError):
    """Raised when the ptoon binary exits nonzero or writes to stderr."""


def _default_search_paths() -> list[Path]:
    # Repo root is the parent of this package directory (prompt_toon/..).
    root = Path(__file__).resolve().parent.parent
    return [root / "build" / "ptoon"]


def resolve_binary_path() -> Path | None:
    """Search order: $PROMPT_TOON_PTOON, then build/ptoon relative to the
    repo root, then None. An explicit env override that does not point at
    an existing file is treated the same as "not set" -- callers only
    ever see a real path or None, never a dangling one."""
    env_path = os.environ.get("PROMPT_TOON_PTOON")
    if env_path:
        candidate = Path(env_path).expanduser()
        return candidate if candidate.is_file() else None
    for candidate in _default_search_paths():
        if candidate.is_file():
            return candidate
    return None


class ChapelEngine:
    """Thin subprocess wrapper over the `ptoon` binary, mirroring the
    normalize_text/redact_text/defang_text signatures in
    prompt_toon/cli.py so callers can swap engines without branching."""

    def __init__(self, binary_path: Path | None = None) -> None:
        self._path = binary_path if binary_path is not None else resolve_binary_path()

    def available(self) -> bool:
        return self._path is not None

    def _require_binary(self) -> Path:
        if self._path is None:
            raise EngineError(
                "ptoon binary is not available: set PROMPT_TOON_PTOON or build "
                "build/ptoon relative to the repo root"
            )
        return self._path

    def _run_bytes(self, subcommand: str, data: bytes, extra_args: tuple[str, ...] = ()) -> bytes:
        binary = self._require_binary()
        try:
            proc = subprocess.run(
                [str(binary), subcommand, *extra_args],
                input=data,
                capture_output=True,
            )
        except OSError as exc:
            raise EngineError(f"ptoon {subcommand} could not be started: {exc}") from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise EngineError(
                f"ptoon {subcommand} exited with status {proc.returncode}: {stderr}"
            )
        if proc.stderr:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise EngineError(f"ptoon {subcommand} wrote to stderr: {stderr}")
        return proc.stdout

    def _run(self, subcommand: str, text: str) -> bytes:
        return self._run_bytes(subcommand, text.encode("utf-8"))

    def normalize_text(self, text: str) -> str:
        return self._run("normalize", text).decode("utf-8", errors="replace")

    def defang_text(self, text: str) -> str:
        return self._run("defang", text).decode("utf-8", errors="replace")

    def redact_text(self, text: str) -> tuple[str, list[str]]:
        raw = self._run("redact", text)
        prefix = b"findings:"
        if not raw.startswith(prefix):
            raise EngineError("ptoon redact output is missing the 'findings:' header line")
        newline_index = raw.find(b"\n")
        if newline_index == -1:
            raise EngineError("ptoon redact output is missing the findings line terminator")
        findings_text = raw[len(prefix) : newline_index].decode("utf-8", errors="replace")
        findings = [item for item in findings_text.split(",") if item] if findings_text else []
        redacted = raw[newline_index + 1 :].decode("utf-8", errors="replace")
        return redacted, findings

    def redact_batch(
        self,
        docs: list[str],
        max_input_bytes: int = 0,
        budget_ms: int = 0,
    ) -> list[dict[str, Any]]:
        """Fan-in redaction (TIN-2709 C2b/C2c): frame N documents, run one
        `ptoon redact-batch` process (coforall one task per doc inside the
        Chapel runtime), and return per-document results IN INPUT ORDER.

        This is the shape the wide-research-spool user flow needs: N subagent
        outputs condensed concurrently in a single process before the synthesis
        seat sees any. Each result dict carries ``i`` (position), ``withheld``
        (bool), and ``findings`` (list). A non-withheld result also carries
        ``redacted`` (str); a withheld one carries ``reason`` and NO redacted
        text -- fail-closed (INV-5), the raw document is never returned.

        ``max_input_bytes`` (C2c): a document whose UTF-8 length exceeds this
        cap is withheld (reason ``input-cap``) rather than truncated -- a
        truncated PEM could leak its tail. ``budget_ms`` (C2c): a wall-clock
        backstop; a document COMPLETING past the batch deadline is withheld
        (reason ``budget``). Both default 0 (unlimited); a positive budget
        requires a positive input cap because Chapel tasks cannot be preempted
        mid-run and the cap bounds per-document work. With both 0 the call is
        byte-identical to the C2b entrypoint. Both are passed as the two
        positional policy args the binary reads (argv[2], argv[3]).

        Wire format mirrors src/ptoon/Batch.chpl: length-prefixed framing,
        which carries embedded newlines (a redacted PEM spans lines) with no
        escaping.
        """
        if (
            not isinstance(max_input_bytes, int)
            or isinstance(max_input_bytes, bool)
            or not isinstance(budget_ms, int)
            or isinstance(budget_ms, bool)
        ):
            raise ValueError("redact_batch policy args must be integers")
        if max_input_bytes < 0 or budget_ms < 0:
            raise ValueError("redact_batch policy args must be nonnegative")
        if budget_ms > 0 and max_input_bytes == 0:
            raise ValueError("redact_batch budget_ms requires positive max_input_bytes")

        framed = bytearray()
        framed += f"{len(docs)}\n".encode("utf-8")
        for doc in docs:
            body = doc.encode("utf-8")
            framed += f"{len(body)}\n".encode("utf-8")
            framed += body
        # Positional policy args are all-or-nothing: to set budget you must also
        # pass maxInputBytes (argv[2]), so emit both whenever either is nonzero.
        extra_args: tuple[str, ...] = ()
        if max_input_bytes or budget_ms:
            extra_args = (str(max_input_bytes), str(budget_ms))
        return self._parse_batch(self._run_bytes("redact-batch", bytes(framed), extra_args))

    @staticmethod
    def _parse_batch(raw: bytes) -> list[dict[str, Any]]:
        pos = 0

        def read_line() -> bytes:
            nonlocal pos
            newline_index = raw.find(b"\n", pos)
            if newline_index == -1:
                raise EngineError("ptoon redact-batch output truncated (no newline)")
            line = raw[pos:newline_index]
            pos = newline_index + 1
            return line

        def read_int(context: str) -> int:
            try:
                value = int(read_line())
            except ValueError as exc:
                raise EngineError(f"ptoon redact-batch output has invalid {context}") from exc
            if value < 0:
                raise EngineError(f"ptoon redact-batch output has negative {context}")
            return value

        count = read_int("document count")
        results: list[dict[str, Any]] = []
        for expected_i in range(count):
            meta = json.loads(read_line().decode("utf-8"))
            if meta.get("i") != expected_i:
                raise EngineError(
                    "ptoon redact-batch output index "
                    f"{meta.get('i')!r} does not match position {expected_i}"
                )
            body_len = read_int(f"body length for document {expected_i}")
            if pos + body_len > len(raw):
                raise EngineError(
                    "ptoon redact-batch output truncated: "
                    f"document {expected_i} declares {body_len} bytes with "
                    f"{len(raw) - pos} remaining"
                )
            body = raw[pos : pos + body_len]
            pos += body_len
            if not meta.get("withheld", False):
                meta["redacted"] = body.decode("utf-8", errors="replace")
            results.append(meta)
        if pos != len(raw):
            raise EngineError(
                f"ptoon redact-batch output has {len(raw) - pos} trailing byte(s)"
            )
        return results

    def condense_batch(
        self,
        docs: list[dict[str, str]],
        max_input_bytes: int = 0,
        budget_ms: int = 0,
        max_cards: int = 24,
    ) -> list[dict[str, Any]]:
        """Streaming condensation (TIN-2709 C2d): frame N documents as
        source/trust_tier/body triplets, run one `ptoon condense-batch`
        process, and return per-document results IN INPUT ORDER.

        Each input doc is a dict with ``source``, ``trust_tier``, and
        ``body`` (str). Each result carries ``i``, ``source``,
        ``trust_tier``, ``bytes``, ``sha256`` (of the RAW body bytes),
        ``withheld``, and ``findings``. A non-withheld result carries
        ``cards`` -- a list of card dicts identical to the objects Python's
        condense writes to source-cards.jsonl. A withheld one carries
        ``reason`` and NO cards -- fail-closed (INV-5): nothing derived from
        the document body is returned.

        Policy args mirror redact_batch (argv[2] cap, argv[3] budget) plus
        argv[4] ``max_cards`` (binary default 24). All-or-nothing positional:
        emitting any means emitting the earlier ones too.
        """
        for name, value in (
            ("max_input_bytes", max_input_bytes),
            ("budget_ms", budget_ms),
            ("max_cards", max_cards),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"condense_batch {name} must be an int")
        if max_input_bytes < 0 or budget_ms < 0:
            raise ValueError("condense_batch policy args must be nonnegative")
        if budget_ms > 0 and max_input_bytes == 0:
            raise ValueError("condense_batch budget_ms requires positive max_input_bytes")
        if max_cards <= 0:
            raise ValueError("condense_batch max_cards must be positive")

        framed, expected_meta = self._frame_docs(docs)
        extra_args: tuple[str, ...] = ()
        if max_input_bytes or budget_ms or max_cards != 24:
            extra_args = (str(max_input_bytes), str(budget_ms), str(max_cards))
        raw = self._run_bytes("condense-batch", bytes(framed), extra_args)
        return self._parse_stream(
            raw, len(docs), expected_meta=expected_meta, max_cards=max_cards
        )

    @staticmethod
    def _frame_docs(docs: list[dict[str, str]]) -> tuple[bytearray, list[dict[str, Any]]]:
        """Triplet framing shared by condense_batch and condense_run, plus
        the per-doc metadata used to bind the binary's doc events back to
        the framed input (provenance binding)."""
        framed = bytearray()
        framed += f"{len(docs)}\n".encode("utf-8")
        expected_meta: list[dict[str, Any]] = []
        for index, doc in enumerate(docs):
            if not isinstance(doc, dict):
                raise ValueError(f"condense doc {index} must be a dict")
            try:
                fields = (doc["source"], doc["trust_tier"], doc["body"])
            except KeyError as exc:
                raise ValueError(
                    f"condense doc {index} missing required field {exc.args[0]!r}"
                ) from exc
            if not all(isinstance(field, str) for field in fields):
                raise ValueError(
                    f"condense doc {index} source, trust_tier, and body must be strings"
                )
            source, trust_tier, body = fields
            body_bytes = body.encode("utf-8")
            expected_meta.append(
                {
                    "source": source,
                    "trust_tier": trust_tier,
                    "bytes": len(body_bytes),
                    "sha256": hashlib.sha256(body_bytes).hexdigest(),
                }
            )
            for encoded in (source.encode("utf-8"), trust_tier.encode("utf-8"), body_bytes):
                framed += f"{len(encoded)}\n".encode("utf-8")
                framed += encoded
        return framed, expected_meta

    def condense_run(
        self,
        docs: list[dict[str, str]],
        run_id: str,
        generated_at: str,
        max_input_bytes: int = 0,
        budget_ms: int = 0,
        max_cards: int = 24,
        min_toon_savings: str = "0.2",
        default_trust_tier: str = "untrusted_tool_output",
        tier_overrides: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        """Run-level condensation (TIN-2709 C2e): condense_batch plus the
        summary.md text and manifest object, rendered inside the binary.

        The binary computes nothing time- or identity-shaped: ``run_id`` and
        ``generated_at`` are echoed verbatim, as are the settings values the
        manifest merely reports (``min_toon_savings`` as a raw JSON number
        string, ``tier_overrides`` serialized here with json.dumps). Returns
        ``(per_doc_results, summary_text, manifest)`` after cross-checking
        the manifest's echoes and inputs[] against what was framed --
        a spoofed or drifted manifest raises EngineError, never returns.
        """
        for name, value in (
            ("run_id", run_id),
            ("generated_at", generated_at),
            ("min_toon_savings", min_toon_savings),
            ("default_trust_tier", default_trust_tier),
        ):
            if not isinstance(value, str):
                raise ValueError(f"condense_run {name} must be a string")
        for name, value in (
            ("max_input_bytes", max_input_bytes),
            ("budget_ms", budget_ms),
            ("max_cards", max_cards),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"condense_run {name} must be an int")
        if max_input_bytes < 0 or budget_ms < 0:
            raise ValueError("condense_run policy args must be nonnegative")
        if budget_ms > 0 and max_input_bytes == 0:
            raise ValueError("condense_run budget_ms requires positive max_input_bytes")
        if max_cards <= 0:
            raise ValueError("condense_run max_cards must be positive")
        try:
            parsed_savings = json.loads(min_toon_savings)
        except json.JSONDecodeError as exc:
            raise ValueError("condense_run min_toon_savings must be a JSON number") from exc
        if isinstance(parsed_savings, bool) or not isinstance(parsed_savings, (int, float)):
            raise ValueError("condense_run min_toon_savings must be a JSON number")
        overrides = tier_overrides if tier_overrides is not None else {}
        if not isinstance(overrides, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in overrides.items()
        ):
            raise ValueError("condense_run tier_overrides must be a dict[str, str]")
        overrides_json = json.dumps(overrides, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"))

        header = bytearray()
        for field in (run_id, generated_at, min_toon_savings,
                      default_trust_tier, overrides_json):
            encoded = field.encode("utf-8")
            header += f"{len(encoded)}\n".encode("utf-8")
            header += encoded
        framed, expected_meta = self._frame_docs(docs)
        raw = self._run_bytes("condense", bytes(header) + bytes(framed), (
            (str(max_input_bytes), str(budget_ms), str(max_cards))
            if (max_input_bytes or budget_ms or max_cards != 24) else ()
        ))
        results, summary_text, manifest = self._parse_stream(
            raw, len(docs), expected_meta=expected_meta, max_cards=max_cards,
            expect_run_events=True,
        )

        # Provenance binding for the run-level artifacts: every echo and
        # every computed inputs[] entry must match what was framed.
        if manifest.get("id") != run_id or manifest.get("generated_at") != generated_at:
            raise EngineError("ptoon condense: manifest id/generated_at echo mismatch")
        expected_inputs = [
            {
                "bytes": meta["bytes"],
                "sha256": meta["sha256"],
                "source": meta["source"],
                "trust_tier": meta["trust_tier"],
            }
            for meta in expected_meta
        ]
        if manifest.get("inputs") != expected_inputs:
            raise EngineError("ptoon condense: manifest inputs do not match framed docs")
        distinct_tiers = {meta["trust_tier"] for meta in expected_meta}
        if manifest.get("mixed_trust_tiers") != (len(distinct_tiers) > 1):
            raise EngineError("ptoon condense: manifest mixed_trust_tiers mismatch")
        settings = manifest.get("settings")
        if (
            not isinstance(settings, dict)
            or settings.get("format") != "jsonl"
            or settings.get("handoff_format") != COMPACT_HANDOFF_FORMAT
            or settings.get("max_cards_per_input") != max_cards
            or settings.get("min_toon_savings") != parsed_savings
            or settings.get("trust_tier") != default_trust_tier
            or settings.get("input_tier_overrides") != overrides
            or settings.get("store_raw") is not False
        ):
            raise EngineError("ptoon condense: manifest settings echo mismatch")
        return results, summary_text, manifest

    @staticmethod
    def _parse_stream(
        raw: bytes,
        expected_docs: int,
        expected_meta: list[dict[str, Any]] | None = None,
        max_cards: int | None = None,
        expect_run_events: bool = False,
    ) -> Any:
        """Parse the condense-batch JSONL event stream, enforcing the event
        grammar: for each doc IN ORDER, one `doc` event, then `card` events
        (non-withheld docs only), then one `end` event whose count must
        match; exactly one trailing `batch` event whose tallies must match.
        With ``expect_run_events`` (the C2e `condense` subcommand), exactly
        one `summary` event and one `manifest` event must sit between the
        last doc's `end` and the `batch` tally, and the return value becomes
        ``(results, summary_text, manifest)``. Any grammar violation raises
        EngineError -- a malformed stream is never partially trusted."""
        if expected_meta is not None and len(expected_meta) != expected_docs:
            raise EngineError("ptoon condense-batch: expected metadata length mismatch")
        if max_cards is not None and (
            isinstance(max_cards, bool) or not isinstance(max_cards, int) or max_cards <= 0
        ):
            raise EngineError("ptoon condense-batch: invalid max_cards parser cap")

        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        else:
            raise EngineError("ptoon condense-batch output is not newline-terminated")

        events: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EngineError(
                    f"ptoon condense-batch line {index} is not valid JSON"
                ) from exc
            if not isinstance(event, dict):
                raise EngineError(
                    f"ptoon condense-batch line {index} is not a JSON object"
                )
            events.append(event)

        def require_int(event: dict[str, Any], key: str, context: str) -> int:
            value = event.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                raise EngineError(f"ptoon condense-batch: {context} has invalid {key}")
            if value < 0:
                raise EngineError(f"ptoon condense-batch: {context} has negative {key}")
            return value

        def require_bool(event: dict[str, Any], key: str, context: str) -> bool:
            value = event.get(key)
            if not isinstance(value, bool):
                raise EngineError(f"ptoon condense-batch: {context} has invalid {key}")
            return value

        def require_str(event: dict[str, Any], key: str, context: str) -> str:
            value = event.get(key)
            if not isinstance(value, str):
                raise EngineError(f"ptoon condense-batch: {context} has invalid {key}")
            return value

        def reject_unknown_keys(
            event: dict[str, Any], allowed: set[str], context: str
        ) -> None:
            unknown = set(event) - allowed
            if unknown:
                formatted = ", ".join(sorted(unknown))
                raise EngineError(
                    f"ptoon condense-batch: {context} has unexpected key(s): {formatted}"
                )

        def validate_doc_event(
            event: dict[str, Any],
            expected_i: int,
            expected: dict[str, Any] | None,
        ) -> tuple[bool, dict[str, str]]:
            actual_i = require_int(event, "i", f"doc {expected_i}")
            if actual_i != expected_i:
                raise EngineError(
                    f"ptoon condense-batch: doc index {actual_i!r} does not "
                    f"match position {expected_i}"
                )
            source = require_str(event, "source", f"doc {expected_i}")
            trust_tier = require_str(event, "trust_tier", f"doc {expected_i}")
            byte_count = require_int(event, "bytes", f"doc {expected_i}")
            digest = require_str(event, "sha256", f"doc {expected_i}")
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise EngineError(f"ptoon condense-batch: doc {expected_i} has invalid sha256")
            findings = event.get("findings")
            if not isinstance(findings, list) or not all(
                isinstance(item, str) for item in findings
            ):
                raise EngineError(f"ptoon condense-batch: doc {expected_i} has invalid findings")
            if expected is not None and (
                source != expected["source"]
                or trust_tier != expected["trust_tier"]
                or byte_count != expected["bytes"]
                or digest != expected["sha256"]
            ):
                raise EngineError(
                    f"ptoon condense-batch: doc {expected_i} metadata does not match input"
                )
            withheld = require_bool(event, "withheld", f"doc {expected_i}")
            reason = event.get("reason")
            if withheld:
                if findings:
                    raise EngineError(
                        f"ptoon condense-batch: withheld doc {expected_i} carries findings"
                    )
                if reason not in {"input-cap", "budget", "condense-error"}:
                    raise EngineError(
                        f"ptoon condense-batch: withheld doc {expected_i} has invalid reason"
                    )
            elif reason is not None:
                raise EngineError(
                    f"ptoon condense-batch: non-withheld doc {expected_i} carries reason"
                )
            allowed = {
                "event",
                "i",
                "source",
                "trust_tier",
                "bytes",
                "sha256",
                "withheld",
                "findings",
            }
            if withheld:
                allowed.add("reason")
            reject_unknown_keys(event, allowed, f"doc {expected_i}")
            return withheld, {"source": source, "trust_tier": trust_tier, "sha256": digest}

        def validate_card(
            card: dict[str, Any], expected_i: int, doc_meta: dict[str, str]
        ) -> None:
            reject_unknown_keys(
                card,
                {
                    "claim",
                    "confidence",
                    "evidence",
                    "flags",
                    "id",
                    "line_end",
                    "line_start",
                    "sha256",
                    "source",
                    "trust_tier",
                },
                f"card for doc {expected_i}",
            )
            for key in ("claim", "confidence", "evidence", "id", "source", "trust_tier"):
                require_str(card, key, f"card for doc {expected_i}")
            digest = require_str(card, "sha256", f"card for doc {expected_i}")
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise EngineError(
                    f"ptoon condense-batch: card for doc {expected_i} has invalid sha256"
                )
            if (
                card["source"] != doc_meta["source"]
                or card["trust_tier"] != doc_meta["trust_tier"]
                or digest != doc_meta["sha256"]
            ):
                raise EngineError(
                    f"ptoon condense-batch: card for doc {expected_i} provenance mismatch"
                )
            line_start = require_int(card, "line_start", f"card for doc {expected_i}")
            line_end = require_int(card, "line_end", f"card for doc {expected_i}")
            if line_start < 1:
                raise EngineError(
                    f"ptoon condense-batch: card for doc {expected_i} has invalid line_start"
                )
            if line_end < line_start:
                raise EngineError(
                    f"ptoon condense-batch: card for doc {expected_i} has invalid line_end"
                )
            flags = card.get("flags")
            if not isinstance(flags, list) or not all(isinstance(item, str) for item in flags):
                raise EngineError(
                    f"ptoon condense-batch: card for doc {expected_i} has invalid flags"
                )

        results: list[dict[str, Any]] = []
        pos = 0
        total_cards = 0
        total_withheld = 0
        for expected_i in range(expected_docs):
            if pos >= len(events) or events[pos].get("event") != "doc":
                raise EngineError(
                    f"ptoon condense-batch: expected doc event for document {expected_i}"
                )
            doc = events[pos]
            pos += 1
            expected = expected_meta[expected_i] if expected_meta is not None else None
            withheld, doc_meta = validate_doc_event(doc, expected_i, expected)
            cards: list[dict[str, Any]] = []
            while pos < len(events) and events[pos].get("event") == "card":
                card_event = events[pos]
                pos += 1
                if max_cards is not None and len(cards) >= max_cards:
                    raise EngineError(
                        f"ptoon condense-batch: document {expected_i} exceeded max_cards"
                    )
                if require_int(card_event, "i", f"card event for doc {expected_i}") != expected_i:
                    raise EngineError(
                        f"ptoon condense-batch: card event for doc {card_event.get('i')!r} "
                        f"inside document {expected_i}"
                    )
                if withheld:
                    raise EngineError(
                        f"ptoon condense-batch: withheld document {expected_i} emitted a card"
                    )
                card = card_event.get("card")
                if not isinstance(card, dict):
                    raise EngineError(
                        f"ptoon condense-batch: card event for doc {expected_i} has invalid card"
                    )
                reject_unknown_keys(card_event, {"event", "i", "card"}, f"card event for doc {expected_i}")
                validate_card(card, expected_i, doc_meta)
                cards.append(card)
            if pos >= len(events) or events[pos].get("event") != "end":
                raise EngineError(
                    f"ptoon condense-batch: expected end event for document {expected_i}"
                )
            end = events[pos]
            pos += 1
            reject_unknown_keys(end, {"event", "i", "cards"}, f"end event for doc {expected_i}")
            if (
                require_int(end, "i", f"end event for doc {expected_i}") != expected_i
                or require_int(end, "cards", f"end event for doc {expected_i}") != len(cards)
            ):
                raise EngineError(
                    f"ptoon condense-batch: end event mismatch for document {expected_i}"
                )
            del doc["event"]
            if withheld:
                total_withheld += 1
            else:
                doc["cards"] = cards
                total_cards += len(cards)
            results.append(doc)

        summary_text: str | None = None
        manifest: dict[str, Any] | None = None
        if expect_run_events:
            if pos >= len(events) or events[pos].get("event") != "summary":
                raise EngineError("ptoon condense: missing summary event")
            summary_event = events[pos]
            pos += 1
            reject_unknown_keys(summary_event, {"event", "text"}, "summary event")
            summary_text = require_str(summary_event, "text", "summary event")
            if pos >= len(events) or events[pos].get("event") != "manifest":
                raise EngineError("ptoon condense: missing manifest event")
            manifest_event = events[pos]
            pos += 1
            reject_unknown_keys(manifest_event, {"event", "manifest"}, "manifest event")
            manifest = manifest_event.get("manifest")
            if not isinstance(manifest, dict):
                raise EngineError("ptoon condense: manifest event has invalid manifest")

        if pos >= len(events) or events[pos].get("event") != "batch":
            raise EngineError("ptoon condense-batch: missing trailing batch event")
        batch = events[pos]
        pos += 1
        if pos != len(events):
            raise EngineError("ptoon condense-batch: trailing events after batch summary")
        reject_unknown_keys(batch, {"event", "docs", "cards", "withheld"}, "batch summary")
        if (
            require_int(batch, "docs", "batch summary") != expected_docs
            or require_int(batch, "cards", "batch summary") != total_cards
            or require_int(batch, "withheld", "batch summary") != total_withheld
        ):
            raise EngineError(
                "ptoon condense-batch: batch summary tallies do not match events"
            )
        if expect_run_events:
            return results, summary_text, manifest
        return results

    def engine_caps(self) -> dict[str, Any]:
        raw = self._run("caps", "")
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8", errors="replace"))
