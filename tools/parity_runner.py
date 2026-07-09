#!/usr/bin/env python3
"""TIN-2708 C1 shared golden-corpus parity runner.

Two independent modes, both engine-pluggable ahead of full Chapel
parity (`docs/mythos-delivery-design.md` Sec7, C1):

- **Default (condense) mode.** For every `CondenseCase` in
  `tools/gen_golden.py`'s `CONDENSE_CASES`, runs `python3 -m prompt_toon
  condense --engine <engine> --id fixed-run-id ...` for each of
  `python` and `chapel` into a fresh temp state dir, applies the same
  `mask_manifest_bytes`/`mask_summary_text` masking `gen_golden.py`
  applied when writing the goldens, and byte-diffs every artifact
  (`source-cards.jsonl`, `summary.md`, `manifest.json`) against
  `fixtures/golden/<case>/`. Prints a markdown table and exits nonzero
  on any DIFF/ERROR/MISSING_GOLDEN. The `python` column is expected to
  always PASS trivially (it's the oracle that wrote the goldens); the
  `chapel` column reports SKIP whenever `libptoon` hasn't been built on
  this host (`--engine=chapel` fails closed in `prompt_toon.cli`, with
  "libptoon is not available" in stderr -- that specific, well-defined
  failure is what this runner treats as SKIP, not a parity failure).

- **`--functions` mode.** Diffs the three per-function outputs
  (`redact_text`, `normalize_text`, `defang_text`) between the Python
  oracle (direct call, same as `gen_golden.py`'s text-fixture path) and
  `prompt_toon.engine.ChapelEngine`'s equivalent methods, across every
  `TextFixture`. Skips cleanly (exit 0) if `prompt_toon.engine` can't be
  imported or `ChapelEngine().available()` is False -- i.e. libptoon
  hasn't been built yet. This is a lower-level check than condense
  parity: it isolates the three text-transform primitives from
  card/manifest assembly.

Usage:
    python3 tools/parity_runner.py                    # condense parity, python vs chapel
    python3 tools/parity_runner.py --case 01-homoglyph-secret
    python3 tools/parity_runner.py --functions         # per-function parity via prompt_toon.engine

Exit code is non-zero on any DIFF/ERROR/MISSING_GOLDEN (condense mode)
or any function-level mismatch (--functions mode). SKIP never fails the
run.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GOLDEN = ROOT / "fixtures" / "golden"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from gen_golden import (  # noqa: E402
    CONDENSE_CASES,
    TEXT_FIXTURES,
    INPUTS,
    CondenseCase,
    TextFixture,
    run_condense,
)
from prompt_toon.cli import defang_text, normalize_text, redact_text  # noqa: E402

ENGINES = ["python", "chapel"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def diff_preview(expected: bytes, actual: bytes, limit: int = 40) -> str:
    try:
        exp_lines = expected.decode("utf-8").splitlines(keepends=True)
        act_lines = actual.decode("utf-8").splitlines(keepends=True)
        diff = list(difflib.unified_diff(exp_lines, act_lines, "golden", "actual"))
        if diff:
            return "".join(diff[:limit])
    except UnicodeDecodeError:
        pass
    return f"golden={expected[:120]!r}\nactual={actual[:120]!r}"


def is_libptoon_unavailable(stderr: bytes) -> bool:
    return b"libptoon is not available" in stderr


# --------------------------------------------------------------------------
# condense (default) mode
# --------------------------------------------------------------------------


def check_condense_case(case: CondenseCase, engine: str) -> tuple[str, str]:
    """Returns (status, detail). status in PASS|SKIP|DIFF|ERROR|MISSING_GOLDEN."""
    case_dir = GOLDEN / case.name
    if not case_dir.exists():
        return "MISSING_GOLDEN", str(case_dir.relative_to(ROOT))

    try:
        rc, artifacts, err = run_condense(case, engine=engine)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
        return "ERROR", f"{type(exc).__name__}: {exc}"

    if rc != 0:
        if engine == "chapel" and is_libptoon_unavailable(err):
            return "SKIP", "libptoon not available (build via the remote-only nix lane, see src/ptoon/README.md)"
        return "ERROR", f"condense failed rc={rc}: {err.decode('utf-8', 'replace')[:300]}"

    diffs = []
    for artifact_name, actual_bytes in sorted(artifacts.items()):
        golden_path = case_dir / artifact_name
        if not golden_path.exists():
            diffs.append(f"{artifact_name}: MISSING_GOLDEN ({golden_path.relative_to(ROOT)})")
            continue
        expected_bytes = golden_path.read_bytes()
        if expected_bytes != actual_bytes:
            diffs.append(f"{artifact_name}:\n{diff_preview(expected_bytes, actual_bytes)}")
    if diffs:
        return "DIFF", "\n".join(diffs)
    return "PASS", ""


def run_condense_mode(cases: list[CondenseCase]) -> int:
    rows: list[tuple[str, dict[str, tuple[str, str]]]] = []
    for case in cases:
        per_engine = {}
        for engine in ENGINES:
            per_engine[engine] = check_condense_case(case, engine)
        rows.append((case.name, per_engine))

    print("| case | " + " | ".join(ENGINES) + " |")
    print("|---" * (1 + len(ENGINES)) + "|")
    counts: dict[str, int] = {}
    details: list[str] = []
    for name, per_engine in rows:
        cells = []
        for engine in ENGINES:
            status, detail = per_engine[engine]
            counts[status] = counts.get(status, 0) + 1
            cells.append(status)
            if status in ("DIFF", "ERROR", "MISSING_GOLDEN") and detail:
                details.append(f"### {name} ({engine}): {status}\n```\n{detail}\n```")
        print(f"| {name} | " + " | ".join(cells) + " |")

    if details:
        print("\n".join(["", "## Divergence detail", *details]))

    total = len(rows) * len(ENGINES)
    summary = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
    print(f"\n{total} case/engine combination(s) — {summary}")

    failing = counts.get("DIFF", 0) + counts.get("ERROR", 0) + counts.get("MISSING_GOLDEN", 0)
    if failing:
        print(f"PARITY: FAIL ({failing} combination(s) diverged)")
        return 1
    print("PARITY: PASS")
    return 0


# --------------------------------------------------------------------------
# --functions mode
# --------------------------------------------------------------------------


def run_functions_mode(fixtures: list[TextFixture]) -> int:
    try:
        from prompt_toon import engine as engine_module
    except ImportError as exc:
        print(f"SKIP: prompt_toon.engine not importable ({exc}); nothing to diff.")
        return 0

    chapel_engine = engine_module.ChapelEngine()
    if not chapel_engine.available():
        print("SKIP: libptoon not available (ChapelEngine().available() is False); nothing to diff.")
        return 0

    functions = [
        ("redact_text", redact_text, chapel_engine.redact_text),
        ("normalize_text", normalize_text, lambda t: (chapel_engine.normalize_text(t), None)),
        ("defang_text", defang_text, lambda t: (chapel_engine.defang_text(t), None)),
    ]

    print("| fixture | redact_text | normalize_text | defang_text |")
    print("|---|---|---|---|")
    counts: dict[str, int] = {}
    details: list[str] = []
    for fixture in fixtures:
        text = (INPUTS / fixture.filename).read_text(encoding="utf-8")
        cells = []
        for fn_name, python_fn, chapel_fn in functions:
            try:
                python_result = python_fn(text)
                chapel_result = chapel_fn(text)
            except Exception as exc:  # noqa: BLE001
                status = "ERROR"
                details.append(f"### {fixture.name} ({fn_name}): ERROR\n```\n{type(exc).__name__}: {exc}\n```")
            else:
                py_text = python_result[0] if isinstance(python_result, tuple) else python_result
                ch_text = chapel_result[0] if isinstance(chapel_result, tuple) else chapel_result
                if py_text == ch_text:
                    status = "PASS"
                else:
                    status = "DIFF"
                    details.append(
                        f"### {fixture.name} ({fn_name}): DIFF\n```\n"
                        f"{diff_preview(py_text.encode('utf-8'), ch_text.encode('utf-8'))}\n```"
                    )
            counts[status] = counts.get(status, 0) + 1
            cells.append(status)
        print(f"| {fixture.name} | " + " | ".join(cells) + " |")

    if details:
        print("\n".join(["", "## Divergence detail", *details]))

    total = len(fixtures) * len(functions)
    summary = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
    print(f"\n{total} fixture/function combination(s) — {summary}")

    failing = counts.get("DIFF", 0) + counts.get("ERROR", 0)
    if failing:
        print(f"FUNCTIONS PARITY: FAIL ({failing} combination(s) diverged)")
        return 1
    print("FUNCTIONS PARITY: PASS")
    return 0


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", default=None, help="restrict to condense case name(s); repeatable")
    parser.add_argument(
        "--functions",
        action="store_true",
        help="diff redact_text/normalize_text/defang_text via prompt_toon.engine instead of condense parity",
    )
    args = parser.parse_args(argv)

    if args.functions:
        fixtures = [f for f in TEXT_FIXTURES if args.case is None or f.name in args.case]
        if not fixtures:
            print("no matching fixtures", file=sys.stderr)
            return 1
        return run_functions_mode(fixtures)

    cases = [c for c in CONDENSE_CASES if args.case is None or c.name in args.case]
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 1
    return run_condense_mode(cases)


if __name__ == "__main__":
    raise SystemExit(main())
