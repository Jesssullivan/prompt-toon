"""Deterministic offline quality fixtures for C4f.2.

This module evaluates synthetic fixture artifacts. It does not attest arbitrary
dogfood ledgers, make provider requests, or measure model-visible quality.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from prompt_toon.dogfood import MAX_DOGFOOD_INPUT_BYTES


QUALITY_FIXTURE_SCHEMA_VERSION = 1
QUALITY_REPORT_SCHEMA_VERSION = 1
MAX_QUALITY_MANIFEST_BYTES = 1_000_000
MAX_QUALITY_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_QUALITY_CASES = 16
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_TIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_REOPEN_ANCHORS = (
    "Use hashes and source references in `manifest.json`.",
    "authority-bearing text should be re-opened from source before action.",
)


class QualityFixtureError(ValueError):
    """The quality fixture contract or emitted artifacts are malformed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualityFixtureError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_bytes(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except QualityFixtureError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise QualityFixtureError(f"{label} is not valid UTF-8 JSON") from exc


def _reject_nonfinite_constant(value: str) -> None:
    raise QualityFixtureError(f"non-finite JSON number: {value}")


def _read_bounded(path: Path, *, limit: int, label: str) -> bytes:
    if path.is_symlink():
        raise QualityFixtureError(f"{label} must be a regular non-symlink file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QualityFixtureError(f"{label} could not be opened safely") from exc
    try:
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise QualityFixtureError(
                    f"{label} must be a regular non-symlink file"
                )
            if before.st_size > limit:
                raise QualityFixtureError(f"{label} exceeds {limit} bytes")
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
                raise QualityFixtureError(f"{label} exceeds {limit} bytes")
            after = os.fstat(descriptor)
        except OSError as exc:
            raise QualityFixtureError(f"{label} could not be read safely") from exc
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(data) != before.st_size
        ):
            raise QualityFixtureError(f"{label} changed while being read")
        return data
    finally:
        os.close(descriptor)


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise QualityFixtureError(f"{label} fields do not match schema v1")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualityFixtureError(f"{label} must be an object")
    return value


def _string(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise QualityFixtureError(f"{label} must be a bounded non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise QualityFixtureError(f"{label} must be valid Unicode") from exc
    if len(encoded) > maximum:
        raise QualityFixtureError(f"{label} must be a bounded non-empty string")
    return value


def _string_list(value: Any, label: str, *, maximum: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise QualityFixtureError(f"{label} must be an array of at most {maximum} strings")
    result = [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise QualityFixtureError(f"{label} must not contain duplicates")
    return result


def load_quality_manifest(path: Path) -> tuple[dict[str, Any], str]:
    data = _read_bounded(
        path,
        limit=MAX_QUALITY_MANIFEST_BYTES,
        label="quality fixture manifest",
    )
    manifest = _load_json_bytes(data, "quality fixture manifest")
    validate_quality_manifest(manifest)
    return manifest, hashlib.sha256(data).hexdigest()


def validate_quality_manifest(manifest: Any) -> None:
    manifest = _mapping(manifest, "quality fixture manifest")
    _exact_keys(manifest, {"schema_version", "id", "cases"}, "quality fixture manifest")
    if manifest.get("schema_version") != QUALITY_FIXTURE_SCHEMA_VERSION:
        raise QualityFixtureError(
            f"quality fixture schema_version must be {QUALITY_FIXTURE_SCHEMA_VERSION}"
        )
    fixture_id = _string(manifest.get("id"), "quality fixture id", maximum=64)
    if not _ID_RE.fullmatch(fixture_id):
        raise QualityFixtureError("quality fixture id must be lowercase kebab-case")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_QUALITY_CASES:
        raise QualityFixtureError(
            f"quality fixture cases must contain 1-{MAX_QUALITY_CASES} entries"
        )
    case_ids: set[str] = set()
    for case_index, raw_case in enumerate(cases):
        label = f"cases[{case_index}]"
        case = _mapping(raw_case, label)
        _exact_keys(case, {"id", "inputs", "expect"}, label)
        case_id = _string(case.get("id"), f"{label}.id", maximum=64)
        if not _ID_RE.fullmatch(case_id) or case_id in case_ids:
            raise QualityFixtureError("quality case ids must be unique lowercase kebab-case")
        case_ids.add(case_id)
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 64:
            raise QualityFixtureError(f"{label}.inputs must contain 1-64 entries")
        input_names: set[str] = set()
        for input_index, raw_input in enumerate(inputs):
            input_label = f"{label}.inputs[{input_index}]"
            input_item = _mapping(raw_input, input_label)
            _exact_keys(input_item, {"path", "trust_tier"}, input_label)
            name = _string(input_item.get("path"), f"{input_label}.path", maximum=255)
            if Path(name).name != name or "/" in name or "\\" in name:
                raise QualityFixtureError(f"{input_label}.path must be a basename")
            if name in input_names:
                raise QualityFixtureError(f"{label}.inputs must not repeat paths")
            input_names.add(name)
            tier = _string(
                input_item.get("trust_tier"),
                f"{input_label}.trust_tier",
                maximum=128,
            )
            if not _TIER_RE.fullmatch(tier):
                raise QualityFixtureError(f"{input_label}.trust_tier is invalid")
        expect = _mapping(case.get("expect"), f"{label}.expect")
        _exact_keys(
            expect,
            {
                "constraints",
                "forbidden_constraints",
                "open_questions",
                "redacted_claims",
                "forbidden_fragments",
                "clean_sources",
                "clean_claims",
                "claim_provenance",
            },
            f"{label}.expect",
        )
        for field in (
            "constraints",
            "forbidden_constraints",
            "open_questions",
            "redacted_claims",
            "forbidden_fragments",
            "clean_sources",
            "clean_claims",
        ):
            values = _string_list(expect.get(field), f"{label}.expect.{field}")
            if field == "clean_sources" and not set(values).issubset(input_names):
                raise QualityFixtureError(
                    f"{label}.expect.clean_sources must name case inputs"
                )
        expected_claims = set(expect["constraints"])
        expected_claims.update(expect["open_questions"])
        expected_claims.update(expect["redacted_claims"])
        expected_claims.update(expect["clean_claims"])
        provenance = expect.get("claim_provenance")
        if not isinstance(provenance, list) or len(provenance) > 192:
            raise QualityFixtureError(
                f"{label}.expect.claim_provenance must be an array of at most 192 entries"
            )
        seen_claims: set[str] = set()
        for provenance_index, raw_provenance in enumerate(provenance):
            provenance_label = (
                f"{label}.expect.claim_provenance[{provenance_index}]"
            )
            item = _mapping(raw_provenance, provenance_label)
            _exact_keys(
                item,
                {"claim", "source", "line_start", "line_end"},
                provenance_label,
            )
            claim = _string(item.get("claim"), f"{provenance_label}.claim")
            source = _string(
                item.get("source"), f"{provenance_label}.source", maximum=255
            )
            line_start = item.get("line_start")
            line_end = item.get("line_end")
            if (
                claim not in expected_claims
                or claim in seen_claims
                or source not in input_names
                or (
                    claim in set(expect["clean_claims"])
                    and source not in set(expect["clean_sources"])
                )
                or isinstance(line_start, bool)
                or not isinstance(line_start, int)
                or isinstance(line_end, bool)
                or not isinstance(line_end, int)
                or not 1 <= line_start <= line_end
            ):
                raise QualityFixtureError(
                    f"{provenance_label} must bind one expected claim to a case source and positive line range"
                )
            seen_claims.add(claim)
        if seen_claims != expected_claims:
            raise QualityFixtureError(
                f"{label}.expect.claim_provenance must bind every expected claim exactly once"
            )


def _source_name(value: Any, label: str) -> str:
    source = _string(value, label)
    name = Path(source).name
    if not name:
        raise QualityFixtureError(f"{label} has no basename")
    return name


def _raw_summary_section(summary: str, heading: str) -> list[str]:
    lines = summary.splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker) + 1
    except ValueError:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def _summary_section(summary: str, heading: str) -> str:
    section = _raw_summary_section(summary, heading)
    if heading == "Claims":
        return "\n".join(section)

    claims: dict[str, str] = {}
    for line in _raw_summary_section(summary, "Claims"):
        match = re.match(r"^- (\S+)\s+L\d+-\d+.*?:\s+(.*)$", line)
        if match:
            claims[match.group(1)] = line

    resolved = list(section)
    for line in section:
        match = re.fullmatch(r"- (\S+)(?: \[[^\]]*\])*", line)
        if match and match.group(1) in claims:
            resolved.append(claims[match.group(1)])
    return "\n".join(resolved)


def _claim_contains(anchor: str, claim: Any) -> bool:
    if not isinstance(claim, str):
        return False
    # Accept legacy apostrophe rendering and the compact format's safe
    # dynamically fenced literal backticks.
    return anchor.replace("`", "'") in claim.replace("`", "'")


def _load_cards(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    data = _read_bounded(
        path,
        limit=MAX_QUALITY_ARTIFACT_BYTES,
        label="source-cards.jsonl",
    )
    cards: list[dict[str, Any]] = []
    for index, line in enumerate(data.splitlines()):
        if not line:
            continue
        card = _load_json_bytes(line, f"source-cards.jsonl line {index + 1}")
        cards.append(_mapping(card, f"source-cards.jsonl line {index + 1}"))
    return cards, data


def evaluate_quality_case(
    case: dict[str, Any],
    *,
    inputs_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Evaluate one already-produced dogfood run without emitting source text."""
    expected_inputs: dict[str, dict[str, Any]] = {}
    for input_item in case["inputs"]:
        name = input_item["path"]
        path = inputs_root / name
        data = _read_bounded(
            path,
            limit=MAX_DOGFOOD_INPUT_BYTES,
            label=f"fixture input {name}",
        )
        item = {
            "source": str(path),
            "trust_tier": input_item["trust_tier"],
            "bytes": data,
        }
        expected_inputs[name] = item
        item["sha256"] = hashlib.sha256(item["bytes"]).hexdigest()
        try:
            item["lines"] = max(1, len(item["bytes"].decode("utf-8").splitlines()))
        except UnicodeDecodeError as exc:
            raise QualityFixtureError(f"fixture input is not UTF-8: {name}") from exc

    summary_data = _read_bounded(
        output_dir / "summary.md",
        limit=MAX_QUALITY_ARTIFACT_BYTES,
        label="summary.md",
    )
    try:
        summary = summary_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QualityFixtureError("summary.md is not UTF-8") from exc
    cards, cards_data = _load_cards(output_dir / "source-cards.jsonl")
    manifest_data = _read_bounded(
        output_dir / "manifest.json",
        limit=MAX_QUALITY_ARTIFACT_BYTES,
        label="manifest.json",
    )
    emitted_manifest = _mapping(
        _load_json_bytes(manifest_data, "manifest.json"), "manifest.json"
    )
    toon_path = output_dir / "source-cards.toon"
    toon_data = (
        _read_bounded(
            toon_path,
            limit=MAX_QUALITY_ARTIFACT_BYTES,
            label="source-cards.toon",
        )
        if toon_path.exists()
        else b""
    )

    failures: list[str] = []
    critical_section = _summary_section(summary, "Critical Constraints")
    question_section = _summary_section(summary, "Open Questions")
    card_claims = [card.get("claim") for card in cards]

    constraints_found = 0
    for index, anchor in enumerate(case["expect"]["constraints"]):
        in_summary = anchor in critical_section
        in_cards = any(_claim_contains(anchor, claim) for claim in card_claims)
        if in_summary and in_cards:
            constraints_found += 1
        else:
            failures.append(f"constraints.missing[{index}]")

    forbidden_constraints_found = 0
    for index, anchor in enumerate(case["expect"]["forbidden_constraints"]):
        if _claim_contains(anchor, critical_section):
            forbidden_constraints_found += 1
            failures.append(f"constraints.forbidden[{index}]")

    questions_found = 0
    for index, anchor in enumerate(case["expect"]["open_questions"]):
        in_summary = anchor in question_section
        in_cards = any(_claim_contains(anchor, claim) for claim in card_claims)
        if in_summary and in_cards:
            questions_found += 1
        else:
            failures.append(f"open_questions.missing[{index}]")

    redacted_found = 0
    for index, anchor in enumerate(case["expect"]["redacted_claims"]):
        matching = [
            card
            for card in cards
            if isinstance(card.get("claim"), str) and anchor in card["claim"]
        ]
        if (
            matching
            and all(
                isinstance(card.get("flags"), list)
                and "redacted" in card["flags"]
                for card in matching
            )
            and anchor in summary
        ):
            redacted_found += 1
        else:
            failures.append(f"redaction.required_claim_missing[{index}]")

    model_facing = (
        summary_data
        + b"\n"
        + cards_data
        + b"\n"
        + toon_data
        + b"\n"
        + manifest_data
    )
    forbidden_found = 0
    for index, fragment in enumerate(case["expect"]["forbidden_fragments"]):
        if fragment.encode("utf-8") in model_facing:
            forbidden_found += 1
            failures.append(f"redaction.forbidden_fragment[{index}]")

    clean_violations = 0
    clean_sources = set(case["expect"]["clean_sources"])
    clean_claims_found = 0
    for index, anchor in enumerate(case["expect"]["clean_claims"]):
        if anchor in summary and any(
            _claim_contains(anchor, claim) for claim in card_claims
        ):
            clean_claims_found += 1
        else:
            failures.append(f"redaction.clean_claim_missing[{index}]")
    for card_index, card in enumerate(cards):
        source_name = _source_name(card.get("source"), f"cards[{card_index}].source")
        if source_name not in clean_sources:
            continue
        flags = card.get("flags")
        claim = card.get("claim")
        evidence = card.get("evidence")
        if (
            not isinstance(flags, list)
            or "redacted" in flags
            or not isinstance(claim, str)
            or not isinstance(evidence, str)
            or "[REDACTED]" in claim
            or "[REDACTED]" in evidence
        ):
            clean_violations += 1
            failures.append(f"redaction.clean_source_violation[{card_index}]")
    if clean_sources == set(expected_inputs) and b"[REDACTED]" in model_facing:
        clean_violations += 1
        failures.append("redaction.clean_artifact_violation")

    provenance_mismatches = 0

    def provenance_failure(code: str) -> None:
        nonlocal provenance_mismatches
        provenance_mismatches += 1
        failures.append(code)

    emitted_inputs = emitted_manifest.get("inputs")
    seen_manifest_sources: set[str] = set()
    if not isinstance(emitted_inputs, list) or len(emitted_inputs) != len(expected_inputs):
        provenance_failure("provenance.manifest_input_count")
        emitted_inputs = []
    for input_index, raw_item in enumerate(emitted_inputs):
        if not isinstance(raw_item, dict):
            provenance_failure(f"provenance.manifest_input_type[{input_index}]")
            continue
        try:
            source_name = _source_name(
                raw_item.get("source"), f"manifest.inputs[{input_index}].source"
            )
        except QualityFixtureError:
            provenance_failure(f"provenance.manifest_source[{input_index}]")
            continue
        expected = expected_inputs.get(source_name)
        if expected is None or source_name in seen_manifest_sources:
            provenance_failure(f"provenance.manifest_source[{input_index}]")
            continue
        seen_manifest_sources.add(source_name)
        if (
            raw_item.get("source") != expected["source"]
            or raw_item.get("sha256") != expected["sha256"]
            or raw_item.get("bytes") != len(expected["bytes"])
            or raw_item.get("trust_tier") != expected["trust_tier"]
        ):
            provenance_failure(f"provenance.manifest_metadata[{input_index}]")
    if seen_manifest_sources != set(expected_inputs):
        provenance_failure("provenance.manifest_sources_incomplete")

    represented_sources: set[str] = set()
    for card_index, card in enumerate(cards):
        try:
            source_name = _source_name(card.get("source"), f"cards[{card_index}].source")
        except QualityFixtureError:
            provenance_failure(f"provenance.card_source[{card_index}]")
            continue
        expected = expected_inputs.get(source_name)
        if expected is None:
            provenance_failure(f"provenance.card_source[{card_index}]")
            continue
        represented_sources.add(source_name)
        line_start = card.get("line_start")
        line_end = card.get("line_end")
        if (
            card.get("source") != expected["source"]
            or card.get("sha256") != expected["sha256"]
            or card.get("trust_tier") != expected["trust_tier"]
            or isinstance(line_start, bool)
            or not isinstance(line_start, int)
            or isinstance(line_end, bool)
            or not isinstance(line_end, int)
            or not 1 <= line_start <= line_end <= expected["lines"]
        ):
            provenance_failure(f"provenance.card_metadata[{card_index}]")
    if represented_sources != set(expected_inputs):
        provenance_failure("provenance.card_sources_incomplete")

    claim_bindings_verified = 0
    for binding_index, binding in enumerate(case["expect"]["claim_provenance"]):
        expected = expected_inputs[binding["source"]]
        matching = [
            card
            for card in cards
            if _claim_contains(binding["claim"], card.get("claim"))
        ]
        if matching and all(
            card.get("source") == expected["source"]
            and card.get("line_start") == binding["line_start"]
            and card.get("line_end") == binding["line_end"]
            for card in matching
        ):
            claim_bindings_verified += 1
        else:
            provenance_failure(f"provenance.claim_binding[{binding_index}]")

    reopen_ok = all(anchor in summary for anchor in _REOPEN_ANCHORS)
    if not reopen_ok:
        provenance_failure("provenance.source_reopen_instructions")

    dimensions = {
        "constraints": {
            "status": (
                "pass"
                if constraints_found == len(case["expect"]["constraints"])
                and forbidden_constraints_found == 0
                else "fail"
            ),
            "expected": len(case["expect"]["constraints"]),
            "observed": constraints_found,
            "forbidden_expected": len(case["expect"]["forbidden_constraints"]),
            "forbidden_observed": forbidden_constraints_found,
        },
        "open_questions": {
            "status": (
                "pass"
                if questions_found == len(case["expect"]["open_questions"])
                else "fail"
            ),
            "expected": len(case["expect"]["open_questions"]),
            "observed": questions_found,
        },
        "redaction": {
            "status": (
                "pass"
                if redacted_found == len(case["expect"]["redacted_claims"])
                and forbidden_found == 0
                and clean_violations == 0
                and clean_claims_found == len(case["expect"]["clean_claims"])
                else "fail"
            ),
            "required_claims": len(case["expect"]["redacted_claims"]),
            "required_claims_observed": redacted_found,
            "forbidden_fragments": len(case["expect"]["forbidden_fragments"]),
            "forbidden_fragments_observed": forbidden_found,
            "clean_sources": len(clean_sources),
            "clean_claims": len(case["expect"]["clean_claims"]),
            "clean_claims_observed": clean_claims_found,
            "clean_violations": clean_violations,
        },
        "provenance": {
            "status": "pass" if provenance_mismatches == 0 else "fail",
            "expected_sources": len(expected_inputs),
            "represented_sources": len(represented_sources),
            "cards_checked": len(cards),
            "mismatches": provenance_mismatches,
            "claim_bindings_expected": len(case["expect"]["claim_provenance"]),
            "claim_bindings_verified": claim_bindings_verified,
            "source_reopen_instructions": reopen_ok,
        },
    }
    return {
        "id": case["id"],
        "status": (
            "pass"
            if all(dimension["status"] == "pass" for dimension in dimensions.values())
            else "fail"
        ),
        "dimensions": dimensions,
        "failures": sorted(set(failures)),
    }


def build_quality_report(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    case_results: list[dict[str, Any]],
    engine_requested: str,
    engine_resolved: str,
) -> dict[str, Any]:
    expected_ids = [case["id"] for case in manifest["cases"]]
    if [result.get("id") for result in case_results] != expected_ids:
        raise QualityFixtureError("quality results do not match manifest case order")
    dimension_names = ("constraints", "open_questions", "redaction", "provenance")

    def is_applicable(result: dict[str, Any], name: str) -> bool:
        dimension = result["dimensions"][name]
        if name in {"constraints", "open_questions"}:
            return dimension["expected"] > 0 or dimension.get("forbidden_expected", 0) > 0
        if name == "redaction":
            return any(
                dimension[field] > 0
                for field in (
                    "required_claims",
                    "forbidden_fragments",
                    "clean_sources",
                    "clean_claims",
                )
            )
        return dimension["expected_sources"] > 0

    def aggregate_dimension(name: str) -> dict[str, Any]:
        applicable = [result for result in case_results if is_applicable(result, name)]
        passed = sum(
            result["dimensions"][name]["status"] == "pass" for result in applicable
        )
        return {
            "status": "pass" if applicable and passed == len(applicable) else "fail",
            "cases_total": len(case_results),
            "cases_applicable": len(applicable),
            "cases_passed": passed,
        }

    dimensions = {
        name: aggregate_dimension(name)
        for name in dimension_names
    }
    passed = all(result["status"] == "pass" for result in case_results) and all(
        dimension["status"] == "pass" for dimension in dimensions.values()
    )
    return {
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "fixture_set": {
            "id": manifest["id"],
            "schema_version": manifest["schema_version"],
            "sha256": manifest_sha256,
            "cases": len(case_results),
        },
        "engine": {
            "requested": engine_requested,
            "resolved": engine_resolved,
            "shape": "one-shot-dogfood-fixture-runs",
        },
        "quality_gate": {
            "status": "offline-fixture-pass" if passed else "offline-fixture-fail",
            "all_dimensions_required": True,
            "dimensions": dimensions,
            "scope": (
                "synthetic transform-fixture evidence only; does not attest arbitrary "
                "corpus ledgers or model-visible SWE quality"
            ),
        },
        "cases": case_results,
        "claim_boundary": {
            "provider_requests": 0,
            "provider_token_counts": "not_measured",
            "raw_inputs_in_report": False,
            "gateway_or_policy_activation": False,
        },
    }
