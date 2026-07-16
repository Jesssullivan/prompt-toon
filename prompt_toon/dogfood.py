"""Offline dogfood measurements for durable prompt-toon spools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


MAX_DOGFOOD_DOCUMENTS = 64
MAX_DOGFOOD_INPUT_BYTES = 2_000_000
MAX_DOGFOOD_REQUEST_BYTES = 16 * 1024 * 1024
MAX_DOGFOOD_CARDS_PER_DOCUMENT = 24
MAX_DOGFOOD_BUDGET_MS = 2000
LEXICAL_ESTIMATOR_ID = "prompt-toon-rough-lexical-v1"
LEXICAL_ESTIMATOR_PATTERN = r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]"
LEGACY_DOGFOOD_LEDGER_SCHEMA_VERSION = 2
DOGFOOD_LEDGER_SCHEMA_VERSION = 3
HANDOFF_RECALL_METHOD = "recognized-critical-question-line-recall-v1"
HANDOFF_RECALL_KINDS = ("critical_constraints", "open_questions")
HANDOFF_RECALL_CLAIM_BOUNDARY = (
    "Occurrence-counted exact retention of recognized normalized and redacted "
    "source lines; safety transformations that alter text are not credited, and "
    "this is not semantic-equivalence or task-quality proof."
)
CORPUS_KEY_SCHEME = "prompt-toon-ordered-input-metadata-v1"
CORPUS_DIVERSITY_SCHEME = "prompt-toon-input-multiset-v1"


def build_corpus_identity(input_metadata: list[dict[str, Any]]) -> dict[str, Any]:
    """Hash ordered content metadata without retaining source paths."""
    normalized = []
    for index, item in enumerate(input_metadata):
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        trust_tier = item.get("trust_tier")
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(trust_tier, str)
            or not trust_tier
        ):
            raise ValueError(f"invalid corpus input metadata at index {index}")
        normalized.append(
            {
                "bytes": byte_count,
                "sha256": digest,
                "trust_tier": trust_tier,
            }
        )
    if not normalized:
        raise ValueError("corpus input metadata must not be empty")
    ordered_payload = json.dumps(
        {"inputs": normalized, "scheme": CORPUS_KEY_SCHEME},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    diversity_inputs = sorted(
        normalized,
        key=lambda item: (item["sha256"], item["trust_tier"], item["bytes"]),
    )
    diversity_payload = json.dumps(
        {"inputs": diversity_inputs, "scheme": CORPUS_DIVERSITY_SCHEME},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "diversity_key": {
            "scheme": CORPUS_DIVERSITY_SCHEME,
            "sha256": hashlib.sha256(diversity_payload).hexdigest(),
        },
        "ordered_key": {
            "scheme": CORPUS_KEY_SCHEME,
            "sha256": hashlib.sha256(ordered_payload).hexdigest(),
        },
        "ordered_inputs": normalized,
    }


def expand_spool_inputs(
    raw_paths: list[str], *, max_documents: int = MAX_DOGFOOD_DOCUMENTS
) -> list[str]:
    """Expand files/directories into a stable, duplicate-free file list.

    Symlinks and other non-regular filesystem entries fail closed so a durable
    spool cannot silently reach material outside the named source tree.
    """
    if not raw_paths:
        raise ValueError("dogfood requires at least one durable file or directory")
    if max_documents <= 0:
        raise ValueError("max_documents must be positive")

    expanded: dict[Path, str] = {}

    def add_file(path: Path) -> None:
        identity = path.resolve(strict=True)
        if identity in expanded:
            return
        # Record the canonical path that supplies the bytes. Final symlinks and
        # symlinks discovered inside a walked root are rejected; resolving
        # ancestor aliases keeps provenance tied to the location opened later.
        expanded[identity] = str(identity)
        if len(expanded) > max_documents:
            raise ValueError(
                f"dogfood spool has more than {max_documents} documents; "
                f"limit is {max_documents}"
            )

    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_symlink():
            raise ValueError(f"dogfood input must not be a symlink: {path}")
        if not path.exists():
            raise ValueError(f"dogfood input does not exist: {path}")
        if path.is_file():
            add_file(path)
            continue
        if not path.is_dir():
            raise ValueError(
                f"dogfood input is not a regular file or directory: {path}"
            )

        def raise_walk_error(error: OSError) -> None:
            raise error

        for current, dirnames, filenames in os.walk(
            path, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            dirnames.sort()
            filenames.sort()
            current_path = Path(current)
            for dirname in dirnames:
                child = current_path / dirname
                if child.is_symlink():
                    raise ValueError(f"dogfood directory contains a symlink: {child}")
            for filename in filenames:
                child = current_path / filename
                if child.is_symlink():
                    raise ValueError(f"dogfood directory contains a symlink: {child}")
                if not child.is_file():
                    raise ValueError(
                        f"dogfood directory contains a non-regular entry: {child}"
                    )
                add_file(child)

    unique = sorted(expanded.values())

    if not unique:
        raise ValueError("dogfood spool contains no regular files")
    return unique


def _savings(baseline: int, candidate: int) -> float | None:
    if baseline <= 0:
        return None
    return round((baseline - candidate) / baseline, 4)


def _meets_savings(baseline: int, candidate: int, minimum: float) -> bool:
    if baseline <= 0:
        return False
    return (baseline - candidate) / baseline >= minimum


def build_handoff_recall(
    handoff_names: list[str],
    recognized: dict[str, int],
    retained_by_handoff: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Build a content-free recall gate over recognized source-line classes."""

    if set(recognized) != set(HANDOFF_RECALL_KINDS):
        raise ValueError("recognized anchor counts do not match the recall contract")
    if set(retained_by_handoff) != set(handoff_names):
        raise ValueError("retained anchor counts do not match the emitted handoffs")

    normalized_recognized: dict[str, int] = {}
    for kind in HANDOFF_RECALL_KINDS:
        count = recognized[kind]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"recognized {kind} count must be a non-negative integer")
        normalized_recognized[kind] = count

    total = sum(normalized_recognized.values())
    handoffs: dict[str, dict[str, Any]] = {}
    for name in handoff_names:
        retained = retained_by_handoff[name]
        if set(retained) != set(HANDOFF_RECALL_KINDS):
            raise ValueError(f"retained anchor counts for {name} are incomplete")
        normalized_retained: dict[str, int] = {}
        for kind in HANDOFF_RECALL_KINDS:
            count = retained[kind]
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count > normalized_recognized[kind]
            ):
                raise ValueError(
                    f"retained {kind} count for {name} is outside the recognized total"
                )
            normalized_retained[kind] = count
        retained_total = sum(normalized_retained.values())
        if total == 0:
            status = "not-applicable"
        elif normalized_retained == normalized_recognized:
            status = "pass"
        else:
            status = "recall-loss"
        handoffs[name] = {
            "retained": normalized_retained,
            "retained_total": retained_total,
            "status": status,
        }

    return {
        "method": HANDOFF_RECALL_METHOD,
        "recognized": {
            **normalized_recognized,
            "total": total,
        },
        "handoffs": handoffs,
        "source_text_recorded": False,
        "claim_boundary": HANDOFF_RECALL_CLAIM_BOUNDARY,
    }


def build_efficiency_ledger(
    *,
    run_id: str,
    generated_at: str,
    output_dir: Path,
    documents: int,
    input_metadata: list[dict[str, Any]],
    max_cards_per_document: int,
    input_bytes: int,
    input_tokens_estimate: int,
    withheld_documents: int,
    withheld_details: list[dict[str, str]],
    engine_requested: str,
    engine_resolved: str,
    execution_shape: str,
    engine_wall_ms: float,
    run_wall_ms: float,
    min_toon_savings: float,
    min_handoff_savings: float,
    format_analysis: dict[str, Any],
    mythos_route: str | None,
    model_label: str | None,
    token_counter: Callable[[str], int],
    recognized_anchors: dict[str, int],
    retained_anchors_by_handoff: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Build a claim-bounded ledger from already-written run artifacts."""
    artifacts: dict[str, dict[str, Any]] = {}
    for name in (
        "summary.md",
        "source-cards.jsonl",
        "source-cards.toon",
        "manifest.json",
    ):
        path = output_dir / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        measurement: dict[str, Any] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if name != "manifest.json":
            measurement["tokens_estimate"] = token_counter(
                data.decode("utf-8", errors="strict")
            )
        if name == "source-cards.jsonl":
            measurement["records"] = sum(bool(line) for line in data.splitlines())
        artifacts[name] = measurement

    jsonl = artifacts["source-cards.jsonl"]

    def handoff(*names: str) -> dict[str, Any]:
        byte_count = sum(artifacts[name]["bytes"] for name in names)
        token_count = sum(artifacts[name]["tokens_estimate"] for name in names)
        return {
            "artifacts": list(names),
            "bytes": byte_count,
            "tokens_estimate": token_count,
            "byte_savings_vs_raw_input": _savings(input_bytes, byte_count),
            "token_estimate_savings_vs_raw_input": _savings(
                input_tokens_estimate, token_count
            ),
        }

    handoffs = {
        "summary_only": handoff("summary.md"),
        "summary_plus_authoritative_cards": handoff("summary.md", "source-cards.jsonl"),
    }
    toon_selected = "source-cards.toon" in artifacts
    if toon_selected:
        handoffs["summary_plus_toon_compact_view"] = handoff(
            "summary.md", "source-cards.toon"
        )

    best_measured = min(
        handoffs,
        key=lambda name: handoffs[name]["tokens_estimate"],
    )
    savings_eligible_handoffs = sorted(
        name
        for name, measurement in handoffs.items()
        if _meets_savings(
            input_tokens_estimate,
            measurement["tokens_estimate"],
            min_handoff_savings,
        )
    )
    handoff_recall = build_handoff_recall(
        list(handoffs), recognized_anchors, retained_anchors_by_handoff
    )
    eligible_handoffs = [
        name
        for name in savings_eligible_handoffs
        if handoff_recall["handoffs"][name]["status"] != "recall-loss"
    ]
    if withheld_documents:
        eligible_handoffs = []
        best_measured = None
        recommended_handoff = None
        handoff_gate = "withheld"
    else:
        recommended_handoff = (
            min(
                eligible_handoffs,
                key=lambda name: handoffs[name]["tokens_estimate"],
            )
            if eligible_handoffs
            else None
        )
        if recommended_handoff is not None:
            handoff_gate = "pass"
        elif savings_eligible_handoffs:
            handoff_gate = "recall-loss"
        else:
            handoff_gate = "below-threshold"

    toon_savings = format_analysis.get("toon_savings")
    # Selection is decided from the unrounded ratio in choose_card_format.
    # Keep the displayed ratio rounded without recomputing a contradictory gate.
    toon_eligible = format_analysis.get("toon_eligible") is True
    toon: dict[str, Any] = {
        "eligible": toon_eligible,
        "selected": toon_selected,
        "minimum_token_estimate_savings": min_toon_savings,
        "token_estimate_savings_vs_jsonl": toon_savings,
        "authority": (
            "non-authoritative compact view; source-cards.jsonl retains "
            "sha256 and evidence"
        ),
    }
    if toon_selected:
        toon["byte_savings_vs_jsonl"] = _savings(
            jsonl["bytes"], artifacts["source-cards.toon"]["bytes"]
        )

    return {
        "schema_version": DOGFOOD_LEDGER_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "corpus_identity": build_corpus_identity(input_metadata),
        "claim_boundary": {
            "scope": "offline local transform only; no provider request was made",
            "provider_requests": 0,
            "provider_token_counts": {
                "status": "not_measured",
                "exact": False,
                "note": (
                    "Exact request counts require an explicitly authorized provider "
                    "token-count endpoint using the complete request payload."
                ),
            },
        },
        "attribution": {
            "mythos_route": mythos_route,
            "model_label": model_label,
            "binding": (
                "caller-supplied observation only; not delegation-policy or "
                "provider-routing proof"
            ),
        },
        "execution": {
            "engine_requested": engine_requested,
            "engine_resolved": engine_resolved,
            "shape": execution_shape,
            "engine_wall_ms": round(engine_wall_ms, 3),
            "engine_wall_scope": (
                "in-memory condensation through optional TOON format selection"
            ),
            "run_wall_ms": round(run_wall_ms, 3),
            "postprocessing": "python-ledger-and-optional-toon-view",
            "budget_enforcement": (
                "chapel-wall-clock-withholding"
                if engine_resolved == "chapel"
                else "bounded-input-only-python-oracle"
            ),
        },
        "estimator": {
            "id": LEXICAL_ESTIMATOR_ID,
            "exact": False,
            "unit": "lexical pieces",
            "pattern": LEXICAL_ESTIMATOR_PATTERN,
            "note": (
                "A local regex proxy for repeatable before/after comparison; "
                "not a tokenizer, context-window guarantee, or billing metric."
            ),
        },
        "spool": {
            "documents": documents,
            "emitted_cards": artifacts["source-cards.jsonl"]["records"],
            "withheld_documents": withheld_documents,
            "withheld": withheld_details,
            "input_bytes": input_bytes,
            "input_tokens_estimate": input_tokens_estimate,
            "raw_inputs_copied": False,
            "limits": {
                "documents": MAX_DOGFOOD_DOCUMENTS,
                "input_bytes_per_document": MAX_DOGFOOD_INPUT_BYTES,
                "aggregate_input_bytes": MAX_DOGFOOD_REQUEST_BYTES,
                "cards_per_document": max_cards_per_document,
                "chapel_wall_clock_budget_ms": MAX_DOGFOOD_BUDGET_MS,
            },
        },
        "artifacts": artifacts,
        "handoffs": handoffs,
        "handoff_recall": handoff_recall,
        "handoff_decision": {
            "minimum_token_estimate_savings": min_handoff_savings,
            "best_measured_handoff": best_measured,
            "savings_eligible_handoffs": savings_eligible_handoffs,
            "eligible_handoffs": eligible_handoffs,
            "recommended_handoff": recommended_handoff,
            "gate": handoff_gate,
            "note": (
                "TOON-vs-JSONL savings do not satisfy this gate unless the complete "
                "handoff also beats the raw-input estimate and retains every recognized "
                "critical-constraint and open-question line; any withheld document "
                "forces a non-pass result."
            ),
        },
        "toon": toon,
    }
