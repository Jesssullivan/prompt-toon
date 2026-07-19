#!/usr/bin/env python3
"""Provider-free C4f.3 one-shot Chapel document-count matrix."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prompt_toon.engine import ChapelEngine  # noqa: E402


COUNTS = (1, 8, 32, 64)
GENERATED_AT = "2026-07-13T00:00:00Z"
TRUST_TIER = "untrusted_tool_output"


def _body(index: int) -> str:
    return f"- document-{index} MUST preserve provenance.\n"


def _documents(count: int) -> list[dict[str, str]]:
    return [
        {
            "source": f"matrix-{index}.md",
            "trust_tier": TRUST_TIER,
            "body": _body(index),
        }
        for index in range(count)
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_document(result: dict[str, Any], index: int) -> None:
    source = f"matrix-{index}.md"
    body = _body(index)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

    _require(result.get("i") == index, f"document {index}: output index drift")
    _require(result.get("source") == source, f"document {index}: source drift")
    _require(
        result.get("trust_tier") == TRUST_TIER,
        f"document {index}: trust tier drift",
    )
    _require(result.get("bytes") == len(body), f"document {index}: byte count drift")
    _require(result.get("sha256") == digest, f"document {index}: digest drift")
    _require(result.get("withheld") is False, f"document {index}: unexpectedly withheld")

    cards = result.get("cards")
    _require(isinstance(cards, list), f"document {index}: cards are missing")
    _require(len(cards) == 1, f"document {index}: expected exactly one card")
    card = cards[0]
    _require(isinstance(card, dict), f"document {index}: card is not an object")
    _require(card.get("source") == source, f"document {index}: card source drift")
    _require(
        card.get("trust_tier") == TRUST_TIER,
        f"document {index}: card trust tier drift",
    )
    _require(card.get("sha256") == digest, f"document {index}: card digest drift")
    _require(card.get("line_start") == 1, f"document {index}: card start drift")
    _require(card.get("line_end") == 1, f"document {index}: card end drift")


def _run_case(engine: ChapelEngine, count: int) -> None:
    docs = _documents(count)
    arguments = {
        "docs": docs,
        "run_id": f"one-shot-matrix-{count}",
        "generated_at": GENERATED_AT,
        "max_input_bytes": 0,
        "budget_ms": 0,
        "max_cards": 24,
    }
    first = engine.condense_run(**arguments)
    second = engine.condense_run(**arguments)
    _require(first == second, f"{count} documents: repeated output changed")

    results, summary, manifest = first
    _require(len(results) == count, f"{count} documents: result count drift")
    for index, result in enumerate(results):
        _require(isinstance(result, dict), f"document {index}: result is not an object")
        _check_document(result, index)

    summary_lines = set(summary.splitlines())
    _require(f"- Inputs: {count}" in summary_lines, f"{count} documents: summary input drift")
    _require(
        f"- Claims: {count}" in summary_lines,
        f"{count} documents: summary claim-count drift",
    )
    _require(
        "- Format: compact-source-index-v1" in summary_lines,
        f"{count} documents: summary format drift",
    )
    for index, doc in enumerate(docs):
        digest = hashlib.sha256(doc["body"].encode("utf-8")).hexdigest()
        ref = f"c{index + 1}@s{index + 1}/src-001"
        claim = _body(index).removeprefix("- ").strip()
        _require(
            f"- s{index + 1} [{TRUST_TIER}] sha256={digest}" in summary_lines,
            f"{count} documents: source index drift at document {index}",
        )
        _require(
            f"- {ref} L1-1: ` {claim} `" in summary_lines,
            f"{count} documents: compact claim drift at document {index}",
        )
        _require(
            f"- {ref} [{TRUST_TIER}]" in summary_lines,
            f"{count} documents: constraint reference drift at document {index}",
        )
    inputs = manifest.get("inputs")
    _require(isinstance(inputs, list), f"{count} documents: manifest inputs missing")
    _require(
        [item.get("source") for item in inputs if isinstance(item, dict)]
        == [doc["source"] for doc in docs],
        f"{count} documents: manifest input order drift",
    )


def main() -> int:
    binary = os.environ.get("PROMPT_TOON_PTOON")
    if not binary:
        print("ONE-SHOT COUNT MATRIX: FAIL: PROMPT_TOON_PTOON is not set", file=sys.stderr)
        return 1

    engine = ChapelEngine(Path(binary))
    rows = [
        "| documents | order | cards | provenance | summary | manifest | repeat |",
        "|---:|---|---|---|---|---|---|",
    ]
    for count in COUNTS:
        try:
            _run_case(engine, count)
        except Exception as exc:
            print(f"ONE-SHOT COUNT MATRIX: FAIL {count}: {exc}", file=sys.stderr)
            return 1
        rows.append(f"| {count} | PASS | PASS | PASS | PASS | PASS | PASS |")

    print("\n".join(rows))
    print(f"\nONE-SHOT COUNT MATRIX: PASS {len(COUNTS)}/{len(COUNTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
