#!/usr/bin/env python3
"""TIN-2709 C2d stream parity gate.

Proves that `ptoon condense-batch` produces, for each document, exactly the
source cards the Python oracle (prompt_toon.cli.cards_from_text) produces for
that document — sha256, findings, card count, and every card object
byte-identical under json.dumps(sort_keys=True, ensure_ascii=False). Two
angles per fixture:

  1. ORACLE: chapel cards vs python cards for the same input.
  2. FAN-IN ISOLATION: the doc's results inside the all-fixtures batch must
     equal its results as a solo single-doc batch (no cross-document bleed,
     order preserved).

Headline count is one per fixture (chapel-vs-oracle only — no python-vs-its-
own-goldens inflation). Also preflights the fail-closed surface: malformed
triplet frames and malformed policy args must exit nonzero with NO stdout.

Requires the built binary via `PROMPT_TOON_PTOON` (remote-only nix lane; runs
inside the ptoon-parity derivation). SKIPs cleanly when unset — the
derivation is the place this cannot be skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "fixtures" / "inputs"

sys.path.insert(0, str(ROOT))

from prompt_toon.cli import cards_from_text, redact_text, resolve_engine  # noqa: E402
from prompt_toon.engine import ChapelEngine  # noqa: E402

TRUST_TIER = "untrusted_tool_output"
MAX_CARDS = 24
SYNTHETIC_INPUTS: list[tuple[str, bytes]] = [
    (
        "99-ogham-whitespace.txt",
        "\N{OGHAM SPACE MARK}# MUST trim\N{OGHAM SPACE MARK}\n"
        "\N{OGHAM SPACE MARK}".encode("utf-8"),
    ),
    (
        "99-python-whitespace-regex.txt",
        "curl\N{OGHAM SPACE MARK}http now\n"
        "http://\N{OGHAM SPACE MARK}not-a-url".encode("utf-8"),
    ),
]


def _binary() -> str:
    binary = os.environ.get("PROMPT_TOON_PTOON")
    if not binary:
        print("SKIP: PROMPT_TOON_PTOON not set; nothing to diff.")
        sys.exit(0)
    return binary


def frame(docs: list[tuple[str, str, bytes]]) -> bytes:
    """<n>\\n then per doc: <len>\\n<source> <len>\\n<tier> <len>\\n<body>."""
    parts: list[bytes] = [str(len(docs)).encode(), b"\n"]
    for source, tier, body in docs:
        for field in (source.encode(), tier.encode(), body):
            parts += [str(len(field)).encode(), b"\n", field]
    return b"".join(parts)


def assert_malformed_rejected(binary: str) -> None:
    cases = {
        "huge-count-before-allocation": b"999999999999999999999\n",
        "count-exceeds-minimum-triplet-frame-size": b"2\n0\n0\n0\n",
        "overrun-source": b"1\n10\nabc",
        "missing-fields": b"1\n1\ns\n",
        "trailing-bytes": b"0\ntrailing",
        "non-digit-length": b"1\nx\n",
        "bad-utf8-source": b"1\n1\n\xff1\nt0\n",
    }
    for name, payload in cases.items():
        proc = subprocess.run([binary, "condense-batch"], input=payload, capture_output=True)
        if proc.returncode == 0:
            raise AssertionError(f"{name}: malformed frame unexpectedly succeeded")
        if proc.stdout:
            raise AssertionError(f"{name}: malformed frame wrote stdout")


def assert_policy_args_rejected(binary: str) -> None:
    cases = {
        "negative-max-input": ("-1", "0", "24"),
        "negative-budget": ("1", "-1", "24"),
        "budget-without-cap": ("0", "1", "24"),
        "non-numeric-policy": ("abc", "0", "24"),
        "zero-max-cards": ("0", "0", "0"),
        "negative-max-cards": ("0", "0", "-3"),
        "too-many-policy-args": ("1", "1", "24", "extra"),
    }
    for name, args in cases.items():
        proc = subprocess.run(
            [binary, "condense-batch", *args],
            input=b"0\n",
            capture_output=True,
        )
        if proc.returncode == 0:
            raise AssertionError(f"{name}: malformed policy args unexpectedly succeeded")
        if proc.stdout:
            raise AssertionError(f"{name}: malformed policy args wrote stdout")


def assert_bad_body_utf8_withheld(binary: str) -> None:
    raw_body = b"\xff"
    proc = subprocess.run(
        [binary, "condense-batch"],
        input=frame([("bad-body.txt", TRUST_TIER, raw_body)]),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "bad-utf8-body: expected per-doc withholding, got nonzero exit "
            f"{proc.returncode}: {proc.stderr.decode(errors='replace')}"
        )
    if proc.stderr:
        raise AssertionError(
            f"bad-utf8-body: expected silent success, got stderr {proc.stderr!r}"
        )
    digest = hashlib.sha256(raw_body).hexdigest()
    [got] = ChapelEngine._parse_stream(
        proc.stdout,
        1,
        expected_meta=[
            {
                "source": "bad-body.txt",
                "trust_tier": TRUST_TIER,
                "bytes": len(raw_body),
                "sha256": digest,
            }
        ],
        max_cards=MAX_CARDS,
    )
    if not got.get("withheld") or got.get("reason") != "condense-error":
        raise AssertionError(f"bad-utf8-body: expected condense-error withholding, got {got}")
    if "cards" in got:
        raise AssertionError("bad-utf8-body: withheld doc emitted cards")
    if got.get("sha256") != digest:
        raise AssertionError("bad-utf8-body: sha256 did not cover raw body bytes")
    if got.get("bytes") != len(raw_body):
        raise AssertionError("bad-utf8-body: bytes did not cover raw body bytes")
    if got.get("findings") != []:
        raise AssertionError("bad-utf8-body: withheld malformed text emitted findings")


def oracle_expectations(name: str, data: bytes) -> tuple[str, list[str], list[str]]:
    """Python-oracle (sha256, findings, card json lines) for one input."""
    text = data.decode("utf-8", errors="replace")
    digest = hashlib.sha256(data).hexdigest()
    engine = resolve_engine("python")
    findings = redact_text(text)[1]
    cards = cards_from_text(name, text, digest, TRUST_TIER, MAX_CARDS, engine)
    card_lines = [
        json.dumps(card.as_dict(), ensure_ascii=False, sort_keys=True) for card in cards
    ]
    return digest, findings, card_lines


def chapel_results(engine: ChapelEngine, docs: list[tuple[str, str, bytes]]) -> list[dict]:
    return engine.condense_batch(
        [
            {"source": s, "trust_tier": t, "body": b.decode("utf-8", errors="replace")}
            for s, t, b in docs
        ]
    )


def chapel_raw_card_json(binary: str, docs: list[tuple[str, str, bytes]]) -> list[list[str]]:
    proc = subprocess.run(
        [binary, "condense-batch", "0", "0", str(MAX_CARDS)],
        input=frame(docs),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "raw-card-json: condense-batch failed "
            f"{proc.returncode}: {proc.stderr.decode(errors='replace')}"
        )
    if proc.stderr:
        raise AssertionError(f"raw-card-json: condense-batch wrote stderr {proc.stderr!r}")

    cards_by_doc: list[list[str]] = [[] for _ in docs]
    marker = b',"card":'
    for line in proc.stdout.splitlines():
        event = json.loads(line.decode("utf-8"))
        if event.get("event") != "card":
            continue
        doc_i = event.get("i")
        if (
            not isinstance(doc_i, int)
            or isinstance(doc_i, bool)
            or doc_i < 0
            or doc_i >= len(docs)
        ):
            raise AssertionError(f"raw-card-json: invalid card doc index {doc_i!r}")
        start = line.find(marker)
        if start == -1 or not line.endswith(b"}"):
            raise AssertionError(f"raw-card-json: malformed card event line {line!r}")
        cards_by_doc[doc_i].append(line[start + len(marker) : -1].decode("utf-8"))
    return cards_by_doc


def main() -> None:
    binary = _binary()
    assert_malformed_rejected(binary)
    assert_policy_args_rejected(binary)
    assert_bad_body_utf8_withheld(binary)

    files = sorted(p for p in INPUTS.iterdir() if p.is_file())
    if not files:
        print("SKIP: no fixtures under fixtures/inputs/; run tools/gen_fixtures.py first.")
        sys.exit(0)

    engine = ChapelEngine(Path(binary))
    inputs = [(f.name, f.read_bytes()) for f in files] + SYNTHETIC_INPUTS
    docs = [(name, TRUST_TIER, data) for name, data in inputs]

    batch = chapel_results(engine, docs)
    raw_cards = chapel_raw_card_json(binary, docs)
    if len(batch) != len(docs):
        print(f"FAIL: batch returned {len(batch)} docs, expected {len(docs)}")
        sys.exit(1)

    rows = ["| fixture | sha256 | findings | cards | solo==batch |", "|---|---|---|---|---|"]
    fails = 0
    for i, (name, data) in enumerate(inputs):
        exp_digest, exp_findings, exp_cards = oracle_expectations(name, data)
        got = batch[i]

        sha_ok = got.get("sha256") == exp_digest
        findings_ok = got.get("findings") == exp_findings and not got.get("withheld", False)
        got_cards = [
            json.dumps(card, ensure_ascii=False, sort_keys=True)
            for card in got.get("cards", [])
        ]
        raw_cards_ok = raw_cards[i] == exp_cards
        cards_ok = got_cards == exp_cards and raw_cards_ok

        solo = chapel_results(engine, [docs[i]])[0]
        solo["i"] = got["i"] = 0  # position differs by construction; rest must not
        solo_ok = solo == got

        rows.append(
            f"| {name} | {'PASS' if sha_ok else 'DIFF'} | "
            f"{'PASS' if findings_ok else 'DIFF'} | {'PASS' if cards_ok else 'DIFF'} | "
            f"{'PASS' if solo_ok else 'DIFF'} |"
        )
        if not (sha_ok and findings_ok and cards_ok and solo_ok):
            fails += 1
            if got_cards != exp_cards:
                for j, (exp, gotc) in enumerate(zip(exp_cards, got_cards)):
                    if exp != gotc:
                        print(f"DIFF {name} card {j}:\n  py: {exp}\n  ch: {gotc}")
                if len(exp_cards) != len(got_cards):
                    print(
                        f"DIFF {name}: card count py={len(exp_cards)} ch={len(got_cards)}"
                    )
            if raw_cards[i] != exp_cards:
                for j, (exp, gotc) in enumerate(zip(exp_cards, raw_cards[i])):
                    if exp != gotc:
                        print(f"RAW DIFF {name} card {j}:\n  py: {exp}\n  ch: {gotc}")
                if len(exp_cards) != len(raw_cards[i]):
                    print(
                        f"RAW DIFF {name}: card count py={len(exp_cards)} "
                        f"ch={len(raw_cards[i])}"
                    )

    print("\n".join(rows))
    print(f"\n{len(inputs)} fixture(s) — PASS={len(inputs) - fails}, DIFF={fails}")
    if fails:
        print("STREAM PARITY: FAIL")
        sys.exit(1)
    print("STREAM PARITY: PASS")


if __name__ == "__main__":
    main()
