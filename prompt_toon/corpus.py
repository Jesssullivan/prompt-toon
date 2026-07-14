"""Deterministic, ledger-only aggregation for offline dogfood runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .dogfood import (
    DOGFOOD_LEDGER_SCHEMA_VERSION,
    MAX_DOGFOOD_BUDGET_MS,
    MAX_DOGFOOD_CARDS_PER_DOCUMENT,
    MAX_DOGFOOD_DOCUMENTS,
    MAX_DOGFOOD_INPUT_BYTES,
    MAX_DOGFOOD_REQUEST_BYTES,
    build_corpus_identity,
)


CORPUS_REPORT_SCHEMA_VERSION = 1
MIN_CORPUS_SPOOLS = 20
MAX_CORPUS_LEDGERS = 50
MAX_LEDGER_BYTES = 1_000_000
MIN_COHORT_PERCENTILE_RUNS = 5
ONE_SHOT_SHAPES = {
    "chapel-one-shot-coforall-batch",
    "python-sequential-oracle",
}


class CorpusLedgerError(ValueError):
    """A ledger set cannot be compared without weakening its claim boundary."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusLedgerError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise CorpusLedgerError(f"non-finite JSON number: {value}")


def _read_ledger(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink():
        raise CorpusLedgerError(f"corpus ledger must not be a symlink: {path}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CorpusLedgerError(
            f"corpus ledger could not be opened: {path}: {exc}"
        ) from exc
    try:
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise CorpusLedgerError(
                    f"corpus ledger is not a regular file: {path}"
                )
            if metadata.st_size > MAX_LEDGER_BYTES:
                raise CorpusLedgerError(
                    f"corpus ledger exceeds {MAX_LEDGER_BYTES} bytes: {path}"
                )
            chunks = []
            remaining = MAX_LEDGER_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_LEDGER_BYTES:
                raise CorpusLedgerError(
                    f"corpus ledger exceeds {MAX_LEDGER_BYTES} bytes: {path}"
                )
            after = os.fstat(descriptor)
        except OSError as exc:
            raise CorpusLedgerError(
                f"corpus ledger could not be read: {path}: {exc}"
            ) from exc
        if (
            after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or len(data) != metadata.st_size
        ):
            raise CorpusLedgerError(
                f"corpus ledger changed while being read: {path}"
            )
    finally:
        os.close(descriptor)
    try:
        ledger = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except CorpusLedgerError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise CorpusLedgerError(
            f"corpus ledger is not valid UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(ledger, dict):
        raise CorpusLedgerError(f"corpus ledger root must be an object: {path}")
    return ledger, hashlib.sha256(data).hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusLedgerError(f"{field} must be an object")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CorpusLedgerError(f"{field} must be a non-empty string")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CorpusLedgerError(f"{field} must be an integer >= {minimum}")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CorpusLedgerError(f"{field} must be a number")
    result = float(value)
    if (
        not math.isfinite(result)
        or (minimum is not None and result < minimum)
        or (maximum is not None and result > maximum)
    ):
        raise CorpusLedgerError(f"{field} is outside its accepted range")
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise CorpusLedgerError(f"{field} must be a boolean")
    return value


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise CorpusLedgerError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _validate_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    schema_version = _integer(ledger.get("schema_version"), "schema_version")
    if schema_version != DOGFOOD_LEDGER_SCHEMA_VERSION:
        raise CorpusLedgerError(
            "ledger schema_version must be "
            f"{DOGFOOD_LEDGER_SCHEMA_VERSION}; regenerate older dogfood ledgers"
        )
    claim = _mapping(ledger.get("claim_boundary"), "claim_boundary")
    if (
        _integer(claim.get("provider_requests"), "claim_boundary.provider_requests")
        != 0
    ):
        raise CorpusLedgerError("claim_boundary.provider_requests must be zero")
    provider_counts = _mapping(
        claim.get("provider_token_counts"), "claim_boundary.provider_token_counts"
    )
    if provider_counts.get("exact") is not False:
        raise CorpusLedgerError(
            "claim_boundary.provider_token_counts.exact must remain false"
        )

    identity = _mapping(ledger.get("corpus_identity"), "corpus_identity")
    ordered_inputs = identity.get("ordered_inputs")
    if not isinstance(ordered_inputs, list):
        raise CorpusLedgerError("corpus_identity.ordered_inputs must be an array")
    try:
        expected_identity = build_corpus_identity(ordered_inputs)
    except ValueError as exc:
        raise CorpusLedgerError(str(exc)) from exc
    if identity != expected_identity:
        raise CorpusLedgerError("corpus_identity does not match its ordered inputs")

    estimator = _mapping(ledger.get("estimator"), "estimator")
    estimator_key = {
        "id": _string(estimator.get("id"), "estimator.id"),
        "pattern": _string(estimator.get("pattern"), "estimator.pattern"),
        "unit": _string(estimator.get("unit"), "estimator.unit"),
    }
    if estimator.get("exact") is not False:
        raise CorpusLedgerError("estimator.exact must remain false for offline ledgers")

    execution = _mapping(ledger.get("execution"), "execution")
    shape = _string(execution.get("shape"), "execution.shape")
    if shape not in ONE_SHOT_SHAPES:
        raise CorpusLedgerError(
            "execution.shape must be a dogfood one-shot shape; resident evidence "
            "belongs in the gateway-capacity report"
        )
    engine_resolved = _string(
        execution.get("engine_resolved"), "execution.engine_resolved"
    )
    budget_enforcement = _string(
        execution.get("budget_enforcement"), "execution.budget_enforcement"
    )
    expected_execution = {
        "chapel": (
            "chapel-one-shot-coforall-batch",
            "chapel-wall-clock-withholding",
        ),
        "python": (
            "python-sequential-oracle",
            "bounded-input-only-python-oracle",
        ),
    }
    if expected_execution.get(engine_resolved) != (shape, budget_enforcement):
        raise CorpusLedgerError(
            "execution engine, shape, and budget_enforcement do not form a "
            "supported one-shot cohort"
        )
    engine_wall_ms = _number(
        execution.get("engine_wall_ms"), "execution.engine_wall_ms", minimum=0
    )
    run_wall_ms = _number(
        execution.get("run_wall_ms"), "execution.run_wall_ms", minimum=0
    )
    if run_wall_ms < engine_wall_ms:
        raise CorpusLedgerError("execution.run_wall_ms must be >= engine_wall_ms")

    spool = _mapping(ledger.get("spool"), "spool")
    documents = _integer(spool.get("documents"), "spool.documents", minimum=1)
    if documents != len(ordered_inputs):
        raise CorpusLedgerError(
            "spool.documents must equal corpus_identity.ordered_inputs length"
        )
    emitted_cards = _integer(spool.get("emitted_cards"), "spool.emitted_cards")
    withheld_documents = _integer(
        spool.get("withheld_documents"), "spool.withheld_documents"
    )
    if withheld_documents > documents:
        raise CorpusLedgerError("spool.withheld_documents exceeds documents")
    withheld_details = spool.get("withheld")
    if not isinstance(withheld_details, list):
        raise CorpusLedgerError("spool.withheld must be an array")
    if len(withheld_details) != withheld_documents:
        raise CorpusLedgerError(
            "spool.withheld must contain one entry per withheld document"
        )
    for index, detail in enumerate(withheld_details):
        detail = _mapping(detail, f"spool.withheld[{index}]")
        _string(detail.get("source"), f"spool.withheld[{index}].source")
        _string(detail.get("reason"), f"spool.withheld[{index}].reason")
    input_bytes = _integer(spool.get("input_bytes"), "spool.input_bytes")
    input_tokens = _integer(
        spool.get("input_tokens_estimate"),
        "spool.input_tokens_estimate",
    )
    if input_bytes != sum(item["bytes"] for item in ordered_inputs):
        raise CorpusLedgerError("spool.input_bytes does not match corpus identity")
    limits = _mapping(spool.get("limits"), "spool.limits")
    if (
        _integer(limits.get("documents"), "spool.limits.documents", minimum=1)
        != MAX_DOGFOOD_DOCUMENTS
    ):
        raise CorpusLedgerError("spool.limits.documents does not match schema v2")
    input_bytes_per_document = _integer(
        limits.get("input_bytes_per_document"),
        "spool.limits.input_bytes_per_document",
        minimum=1,
    )
    if input_bytes_per_document != MAX_DOGFOOD_INPUT_BYTES:
        raise CorpusLedgerError(
            "spool.limits.input_bytes_per_document does not match schema v2"
        )
    aggregate_input_bytes = _integer(
        limits.get("aggregate_input_bytes"),
        "spool.limits.aggregate_input_bytes",
        minimum=1,
    )
    if aggregate_input_bytes != MAX_DOGFOOD_REQUEST_BYTES:
        raise CorpusLedgerError(
            "spool.limits.aggregate_input_bytes does not match schema v2"
        )
    if documents > MAX_DOGFOOD_DOCUMENTS or input_bytes > aggregate_input_bytes:
        raise CorpusLedgerError("spool values exceed the schema-v2 input limits")
    if any(item["bytes"] > input_bytes_per_document for item in ordered_inputs):
        raise CorpusLedgerError(
            "corpus identity contains an input above the per-document byte limit"
        )
    cards_per_document = _integer(
        limits.get("cards_per_document"),
        "spool.limits.cards_per_document",
        minimum=1,
    )
    if cards_per_document > MAX_DOGFOOD_CARDS_PER_DOCUMENT:
        raise CorpusLedgerError("spool.limits.cards_per_document exceeds schema v2")
    if emitted_cards > documents * cards_per_document:
        raise CorpusLedgerError("spool.emitted_cards exceeds the configured card limit")
    if spool.get("raw_inputs_copied") is not False:
        raise CorpusLedgerError("spool.raw_inputs_copied must remain false")
    artifacts = _mapping(ledger.get("artifacts"), "artifacts")

    def artifact_measurement(name: str) -> dict[str, Any]:
        measurement = _mapping(artifacts.get(name), f"artifacts.{name}")
        return {
            "bytes": _integer(measurement.get("bytes"), f"artifacts.{name}.bytes"),
            "sha256": _sha256(
                measurement.get("sha256"), f"artifacts.{name}.sha256"
            ),
            "tokens_estimate": _integer(
                measurement.get("tokens_estimate"),
                f"artifacts.{name}.tokens_estimate",
            ),
        }

    summary_artifact = artifact_measurement("summary.md")
    cards_artifact = artifact_measurement("source-cards.jsonl")
    if (
        _integer(
            _mapping(
                artifacts.get("source-cards.jsonl"), "artifacts.source-cards.jsonl"
            ).get("records"),
            "artifacts.source-cards.jsonl.records",
        )
        != emitted_cards
    ):
        raise CorpusLedgerError(
            "spool.emitted_cards does not match the authoritative JSONL record count"
        )
    chapel_budget_ms = _integer(
        limits.get("chapel_wall_clock_budget_ms"),
        "spool.limits.chapel_wall_clock_budget_ms",
    )
    if chapel_budget_ms != MAX_DOGFOOD_BUDGET_MS:
        raise CorpusLedgerError(
            "spool.limits.chapel_wall_clock_budget_ms does not match schema v2"
        )

    toon = _mapping(ledger.get("toon"), "toon")
    toon_selected = _boolean(toon.get("selected"), "toon.selected")
    toon_eligible = _boolean(toon.get("eligible"), "toon.eligible")
    min_toon_savings = _number(
        toon.get("minimum_token_estimate_savings"),
        "toon.minimum_token_estimate_savings",
        minimum=0,
        maximum=1,
    )
    raw_toon_savings = toon.get("token_estimate_savings_vs_jsonl")
    if raw_toon_savings is None:
        if emitted_cards or toon_selected or toon_eligible:
            raise CorpusLedgerError(
                "toon savings may be null only for a zero-card unselected run"
            )
        toon_savings = None
    else:
        toon_savings = _number(
            raw_toon_savings,
            "toon.token_estimate_savings_vs_jsonl",
            maximum=1,
        )
    if toon_selected != toon_eligible:
        raise CorpusLedgerError(
            "toon.selected must equal toon.eligible for dogfood auto-format runs"
        )
    toon_artifact: dict[str, Any] | None = None
    toon_byte_savings: float | None = None
    if toon_selected:
        if cards_artifact["bytes"] <= 0 or cards_artifact["tokens_estimate"] <= 0:
            raise CorpusLedgerError(
                "selected TOON run requires a non-empty authoritative card artifact"
            )
        toon_artifact = artifact_measurement("source-cards.toon")
        toon_byte_savings = _number(
            toon.get("byte_savings_vs_jsonl"),
            "toon.byte_savings_vs_jsonl",
            maximum=1,
        )
        expected_token_savings = round(
            (
                cards_artifact["tokens_estimate"]
                - toon_artifact["tokens_estimate"]
            )
            / cards_artifact["tokens_estimate"],
            4,
        )
        expected_byte_savings = round(
            (cards_artifact["bytes"] - toon_artifact["bytes"])
            / cards_artifact["bytes"],
            4,
        )
        if toon_savings != expected_token_savings:
            raise CorpusLedgerError(
                "toon token savings do not match the selected artifacts"
            )
        if toon_byte_savings != expected_byte_savings:
            raise CorpusLedgerError(
                "toon byte savings do not match the selected artifacts"
            )
        if (
            cards_artifact["tokens_estimate"] - toon_artifact["tokens_estimate"]
        ) / cards_artifact["tokens_estimate"] < min_toon_savings:
            raise CorpusLedgerError(
                "selected TOON artifact does not meet the exact savings threshold"
            )
    elif toon_savings is not None and toon_savings > min_toon_savings:
        raise CorpusLedgerError(
            "unselected TOON result exceeds its recorded savings threshold"
        )
    elif "source-cards.toon" in artifacts or toon.get("byte_savings_vs_jsonl") is not None:
        raise CorpusLedgerError(
            "unselected TOON runs must not carry a TOON artifact or byte savings"
        )

    handoffs = _mapping(ledger.get("handoffs"), "handoffs")
    handoff_metrics: dict[str, dict[str, float | int | None]] = {}
    expected_handoffs = {
        "summary_only": summary_artifact,
        "summary_plus_authoritative_cards": {
            "bytes": summary_artifact["bytes"] + cards_artifact["bytes"],
            "tokens_estimate": (
                summary_artifact["tokens_estimate"]
                + cards_artifact["tokens_estimate"]
            ),
        },
    }
    if toon_selected and toon_artifact is not None:
        expected_handoffs["summary_plus_toon_compact_view"] = {
            "bytes": summary_artifact["bytes"] + toon_artifact["bytes"],
            "tokens_estimate": (
                summary_artifact["tokens_estimate"]
                + toon_artifact["tokens_estimate"]
            ),
        }
    elif "summary_plus_toon_compact_view" in handoffs:
        raise CorpusLedgerError("unselected TOON run contains a compact-view handoff")
    if set(handoffs) != set(expected_handoffs):
        raise CorpusLedgerError("handoffs contains an unexpected or missing handoff")

    for name, expected_handoff in expected_handoffs.items():
        handoff = _mapping(handoffs.get(name), f"handoffs.{name}")
        byte_count = _integer(handoff.get("bytes"), f"handoffs.{name}.bytes")
        token_count = _integer(
            handoff.get("tokens_estimate"),
            f"handoffs.{name}.tokens_estimate",
        )
        if byte_count != expected_handoff["bytes"] or token_count != expected_handoff[
            "tokens_estimate"
        ]:
            raise CorpusLedgerError(
                f"handoffs.{name} does not match its referenced artifact totals"
            )
        raw_byte_savings = handoff.get("byte_savings_vs_raw_input")
        if input_bytes == 0:
            if raw_byte_savings is not None:
                raise CorpusLedgerError(
                    f"handoffs.{name} byte savings require a positive input baseline"
                )
            byte_savings = None
        else:
            byte_savings = _number(
                raw_byte_savings,
                f"handoffs.{name}.byte_savings_vs_raw_input",
                maximum=1,
            )
            expected_byte_savings = round(
                (input_bytes - byte_count) / input_bytes, 4
            )
            if byte_savings != expected_byte_savings:
                raise CorpusLedgerError(
                    f"handoffs.{name} byte savings do not match its byte counts"
                )
        raw_token_savings = handoff.get("token_estimate_savings_vs_raw_input")
        if input_tokens == 0:
            if raw_token_savings is not None:
                raise CorpusLedgerError(
                    f"handoffs.{name} token savings require a positive input baseline"
                )
            token_savings = None
        else:
            token_savings = _number(
                raw_token_savings,
                f"handoffs.{name}.token_estimate_savings_vs_raw_input",
                maximum=1,
            )
            expected_token_savings = round(
                (input_tokens - token_count) / input_tokens, 4
            )
            if token_savings != expected_token_savings:
                raise CorpusLedgerError(
                    f"handoffs.{name} token savings do not match its token counts"
                )
        handoff_metrics[name] = {
            "byte_savings": byte_savings,
            "bytes": byte_count,
            "token_savings": token_savings,
            "tokens_estimate": token_count,
        }

    decision = _mapping(ledger.get("handoff_decision"), "handoff_decision")
    gate = decision.get("gate")
    if gate not in {"pass", "below-threshold", "withheld"}:
        raise CorpusLedgerError("handoff_decision.gate is invalid")
    min_handoff_savings = _number(
        decision.get("minimum_token_estimate_savings"),
        "handoff_decision.minimum_token_estimate_savings",
        minimum=0,
        maximum=1,
    )
    if withheld_documents and gate != "withheld":
        raise CorpusLedgerError("withheld documents require a withheld handoff gate")
    if not withheld_documents and gate == "withheld":
        raise CorpusLedgerError("withheld handoff gate requires a withheld document")
    eligible_handoffs = sorted(
        name
        for name, measurement in handoff_metrics.items()
        if input_tokens > 0
        and (input_tokens - measurement["tokens_estimate"]) / input_tokens
        >= min_handoff_savings
    )
    expected_best = min(
        handoff_metrics,
        key=lambda name: handoff_metrics[name]["tokens_estimate"],
    )
    expected_recommended = (
        min(
            eligible_handoffs,
            key=lambda name: handoff_metrics[name]["tokens_estimate"],
        )
        if eligible_handoffs
        else None
    )
    expected_gate = "pass" if expected_recommended is not None else "below-threshold"
    if withheld_documents:
        eligible_handoffs = []
        expected_best = None
        expected_recommended = None
        expected_gate = "withheld"
    if decision.get("eligible_handoffs") != eligible_handoffs:
        raise CorpusLedgerError(
            "handoff_decision.eligible_handoffs does not match exact counts"
        )
    if decision.get("best_measured_handoff") != expected_best:
        raise CorpusLedgerError(
            "handoff_decision.best_measured_handoff does not match exact counts"
        )
    if decision.get("recommended_handoff") != expected_recommended or gate != expected_gate:
        raise CorpusLedgerError(
            "handoff_decision recommendation or gate does not match exact counts"
        )

    return {
        "corpus_sha256": identity["ordered_key"]["sha256"],
        "diversity_sha256": identity["diversity_key"]["sha256"],
        "estimator": estimator_key,
        "cohort": {
            "budget_enforcement": budget_enforcement,
            "cards_per_document": cards_per_document,
            "chapel_wall_clock_budget_ms": chapel_budget_ms,
            "engine_resolved": engine_resolved,
            "execution_shape": shape,
            "minimum_handoff_savings": min_handoff_savings,
            "minimum_toon_savings": min_toon_savings,
        },
        "metrics": {
            "authoritative_handoff_byte_savings": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["byte_savings"],
            "authoritative_handoff_bytes": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["bytes"],
            "documents": documents,
            "emitted_cards": emitted_cards,
            "engine_wall_ms": engine_wall_ms,
            "input_bytes": input_bytes,
            "input_tokens_estimate": input_tokens,
            "run_wall_ms": run_wall_ms,
            "summary_only_byte_savings": handoff_metrics["summary_only"][
                "byte_savings"
            ],
            "summary_only_bytes": handoff_metrics["summary_only"]["bytes"],
            "summary_only_tokens_estimate": handoff_metrics["summary_only"][
                "tokens_estimate"
            ],
            "summary_only_token_savings": handoff_metrics["summary_only"][
                "token_savings"
            ],
            "authoritative_handoff_tokens_estimate": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["tokens_estimate"],
            "authoritative_handoff_token_savings": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["token_savings"],
            "toon_byte_savings_vs_jsonl": toon_byte_savings,
            "toon_handoff_byte_savings": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("byte_savings"),
            "toon_handoff_bytes": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("bytes"),
            "toon_handoff_token_savings": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("token_savings"),
            "toon_handoff_tokens_estimate": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("tokens_estimate"),
            "toon_token_savings_vs_jsonl": toon_savings,
            "withheld_documents": withheld_documents,
        },
        "weighted": {
            "authoritative_handoff_bytes": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["bytes"],
            "input_tokens_estimate": input_tokens,
            "input_bytes": input_bytes,
            "summary_only_bytes": handoff_metrics["summary_only"]["bytes"],
            "summary_only_tokens_estimate": handoff_metrics["summary_only"][
                "tokens_estimate"
            ],
            "authoritative_handoff_tokens_estimate": handoff_metrics[
                "summary_plus_authoritative_cards"
            ]["tokens_estimate"],
            "toon_handoff_bytes": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("bytes"),
            "toon_handoff_tokens_estimate": handoff_metrics.get(
                "summary_plus_toon_compact_view", {}
            ).get("tokens_estimate"),
        },
        "flags": {
            "economics_eligible": not withheld_documents and input_tokens > 0,
            "handoff_pass": gate == "pass",
            "toon_eligible": toon_eligible,
            "toon_selected": toon_selected,
            "withheld": withheld_documents > 0,
        },
    }


def _nearest_rank(values: Iterable[float | int], percentile: float) -> float | int:
    ordered = sorted(values)
    if not ordered:
        raise CorpusLedgerError("cannot compute a percentile over an empty metric")
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _distribution(values: Iterable[float | int]) -> dict[str, Any]:
    ordered = sorted(values)
    result = {
        "count": len(ordered),
        "max": ordered[-1],
        "min": ordered[0],
    }
    if len(ordered) >= MIN_COHORT_PERCENTILE_RUNS:
        result["p50"] = _nearest_rank(ordered, 0.50)
        result["p90"] = _nearest_rank(ordered, 0.90)
    else:
        result["values"] = ordered
    return result


def _optional_distribution(values: Iterable[float | int | None]) -> dict[str, Any]:
    present = [value for value in values if value is not None]
    return _distribution(present) if present else {"count": 0}


def _rate(records: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    numerator = sum(record["flags"][flag] for record in records)
    denominator = len(records)
    return {
        "denominator": denominator,
        "numerator": numerator,
        "rate": round(numerator / denominator, 4),
    }


def _count_rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "denominator": denominator,
        "numerator": numerator,
        "rate": round(numerator / denominator, 4),
    }


def _weighted_savings(baseline: int, candidate: int) -> float | None:
    if baseline <= 0:
        return None
    return round((baseline - candidate) / baseline, 4)


def _cohort_report(
    key: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    key_json = json.dumps(key, separators=(",", ":"), sort_keys=True).encode("utf-8")
    operational_metrics = (
        "documents",
        "emitted_cards",
        "engine_wall_ms",
        "input_bytes",
        "input_tokens_estimate",
        "run_wall_ms",
        "withheld_documents",
    )
    economics_metrics = (
        "authoritative_handoff_byte_savings",
        "authoritative_handoff_bytes",
        "authoritative_handoff_token_savings",
        "authoritative_handoff_tokens_estimate",
        "summary_only_byte_savings",
        "summary_only_bytes",
        "summary_only_token_savings",
        "summary_only_tokens_estimate",
        "toon_byte_savings_vs_jsonl",
        "toon_handoff_byte_savings",
        "toon_handoff_bytes",
        "toon_handoff_token_savings",
        "toon_handoff_tokens_estimate",
        "toon_token_savings_vs_jsonl",
    )
    economics_records = [
        record for record in records if record["flags"]["economics_eligible"]
    ]
    token_baseline = sum(
        record["weighted"]["input_tokens_estimate"] for record in economics_records
    )
    byte_baseline = sum(
        record["weighted"]["input_bytes"] for record in economics_records
    )
    summary_tokens = sum(
        record["weighted"]["summary_only_tokens_estimate"]
        for record in economics_records
    )
    summary_bytes = sum(
        record["weighted"]["summary_only_bytes"] for record in economics_records
    )
    authoritative_tokens = sum(
        record["weighted"]["authoritative_handoff_tokens_estimate"]
        for record in economics_records
    )
    authoritative_bytes = sum(
        record["weighted"]["authoritative_handoff_bytes"]
        for record in economics_records
    )
    toon_records = [
        record
        for record in economics_records
        if record["weighted"]["toon_handoff_tokens_estimate"] is not None
    ]
    toon_token_baseline = sum(
        record["weighted"]["input_tokens_estimate"] for record in toon_records
    )
    toon_byte_baseline = sum(
        record["weighted"]["input_bytes"] for record in toon_records
    )
    toon_tokens = sum(
        record["weighted"]["toon_handoff_tokens_estimate"]
        for record in toon_records
    )
    toon_bytes = sum(
        record["weighted"]["toon_handoff_bytes"] for record in toon_records
    )
    withheld_documents = sum(
        record["metrics"]["withheld_documents"] for record in records
    )
    documents = sum(record["metrics"]["documents"] for record in records)
    return {
        "cohort_id": hashlib.sha256(key_json).hexdigest()[:16],
        "key": key,
        "percentile_gate": {
            "minimum_runs": MIN_COHORT_PERCENTILE_RUNS,
            "status": (
                "pass"
                if len(records) >= MIN_COHORT_PERCENTILE_RUNS
                else "insufficient-cohort"
            ),
        },
        "rates": {
            "handoff_pass": _rate(records, "handoff_pass"),
            "toon_eligible": _rate(records, "toon_eligible"),
            "toon_selected": _rate(records, "toon_selected"),
            "withheld": _rate(records, "withheld"),
            "withheld_documents": _count_rate(withheld_documents, documents),
        },
        "runs": len(records),
        "statistics": {
            name: _distribution(record["metrics"][name] for record in records)
            for name in operational_metrics
        },
        "economics": {
            "excluded_runs": {
                "total": len(records) - len(economics_records),
                "withheld": sum(record["flags"]["withheld"] for record in records),
                "zero_token_baseline": sum(
                    record["metrics"]["input_tokens_estimate"] == 0
                    and not record["flags"]["withheld"]
                    for record in records
                ),
            },
            "percentile_gate": {
                "minimum_runs": MIN_COHORT_PERCENTILE_RUNS,
                "status": (
                    "pass"
                    if len(economics_records) >= MIN_COHORT_PERCENTILE_RUNS
                    else "insufficient-cohort"
                ),
            },
            "runs": len(economics_records),
            "statistics": (
                {
                    name: _optional_distribution(
                        record["metrics"][name] for record in economics_records
                    )
                    for name in economics_metrics
                }
                if economics_records
                else {}
            ),
            "weighted_aggregate": {
                "authoritative_handoff_byte_savings_vs_raw_input": (
                    _weighted_savings(byte_baseline, authoritative_bytes)
                ),
                "authoritative_handoff_token_savings_vs_raw_input": (
                    _weighted_savings(token_baseline, authoritative_tokens)
                ),
                "input_bytes": byte_baseline,
                "input_tokens_estimate": token_baseline,
                "summary_only_byte_savings_vs_raw_input": _weighted_savings(
                    byte_baseline, summary_bytes
                ),
                "summary_only_token_savings_vs_raw_input": _weighted_savings(
                    token_baseline, summary_tokens
                ),
                "toon_selected_handoff": {
                    "input_bytes": toon_byte_baseline,
                    "input_tokens_estimate": toon_token_baseline,
                    "runs": len(toon_records),
                    "byte_savings_vs_raw_input": _weighted_savings(
                        toon_byte_baseline, toon_bytes
                    ),
                    "token_savings_vs_raw_input": _weighted_savings(
                        toon_token_baseline, toon_tokens
                    ),
                },
            },
        },
    }


def build_corpus_report(raw_paths: list[str]) -> dict[str, Any]:
    """Validate and aggregate explicit efficiency ledgers without source access."""
    if not raw_paths:
        raise CorpusLedgerError("corpus report requires at least one efficiency ledger")
    if len(raw_paths) > MAX_CORPUS_LEDGERS:
        raise CorpusLedgerError(
            f"corpus report accepts at most {MAX_CORPUS_LEDGERS} ledgers"
        )

    records = []
    seen_samples: set[tuple[str, str]] = set()
    seen_spools: set[str] = set()
    estimator: dict[str, str] | None = None
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        ledger, ledger_sha256 = _read_ledger(path)
        try:
            record = _validate_ledger(ledger)
        except CorpusLedgerError as exc:
            raise CorpusLedgerError(f"{path}: {exc}") from exc
        if estimator is None:
            estimator = record["estimator"]
        elif record["estimator"] != estimator:
            raise CorpusLedgerError(
                f"{path}: mixed estimator id/pattern/unit values cannot share a "
                "corpus report"
            )
        cohort_encoded = json.dumps(
            record["cohort"], separators=(",", ":"), sort_keys=True
        )
        sample_key = (record["diversity_sha256"], cohort_encoded)
        if sample_key in seen_samples:
            raise CorpusLedgerError(
                f"{path}: duplicate corpus diversity within execution cohort: "
                f"{record['diversity_sha256']}"
            )
        seen_samples.add(sample_key)
        seen_spools.add(record["diversity_sha256"])
        record["ledger_sha256"] = ledger_sha256
        records.append(record)

    records.sort(
        key=lambda record: (
            record["diversity_sha256"],
            record["corpus_sha256"],
            json.dumps(record["cohort"], separators=(",", ":"), sort_keys=True),
            record["ledger_sha256"],
        )
    )
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohort_keys: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["cohort"]
        encoded = json.dumps(key, separators=(",", ":"), sort_keys=True)
        cohort_keys[encoded] = key
        cohorts[encoded].append(record)

    unique_spools = len(seen_spools)
    return {
        "schema_version": CORPUS_REPORT_SCHEMA_VERSION,
        "claim_boundary": {
            "provider_requests": 0,
            "raw_sources_read": False,
            "scope": "offline aggregation of explicit efficiency.json ledgers only",
            "ledger_metrics": (
                "self-attested and internally checked; not cryptographically bound "
                "to execution"
            ),
            "timing_comparability": (
                "host metadata is not recorded; timing is not a cross-host benchmark"
            ),
        },
        "corpus_gate": {
            "ledgers": len(records),
            "maximum_ledgers": MAX_CORPUS_LEDGERS,
            "minimum_unique_spools": MIN_CORPUS_SPOOLS,
            "status": (
                "pass"
                if unique_spools >= MIN_CORPUS_SPOOLS
                else "insufficient-corpus"
            ),
            "unique_spools": unique_spools,
        },
        "estimator": estimator,
        "inputs": [
            {
                "corpus_sha256": record["corpus_sha256"],
                "diversity_sha256": record["diversity_sha256"],
                "ledger_sha256": record["ledger_sha256"],
            }
            for record in records
        ],
        "percentile_method": "nearest-rank",
        "cohorts": [
            _cohort_report(cohort_keys[key], cohorts[key]) for key in sorted(cohorts)
        ],
        "quality_gate": {
            "promotion": "blocked",
            "status": "not-evaluated",
            "required_next": (
                "reviewed implementation/fixture/corpus binding plus separately "
                "authorized provider evidence"
            ),
        },
    }
