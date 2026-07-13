#!/usr/bin/env python3
"""Run the C4f.2 provider-free quality fixture gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_toon.quality import (  # noqa: E402
    QualityFixtureError,
    build_quality_report,
    evaluate_quality_case,
    load_quality_manifest,
)


def _ledger(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityFixtureError("quality run did not emit a valid efficiency ledger") from exc
    if not isinstance(value, dict):
        raise QualityFixtureError("quality efficiency ledger root must be an object")
    return value


def _run_case(
    case: dict[str, Any],
    *,
    inputs_root: Path,
    engine: str,
) -> tuple[dict[str, Any], str]:
    with tempfile.TemporaryDirectory(prefix="prompt-toon-quality-") as tmp:
        temp_root = Path(tmp)
        output_dir = temp_root / "run"
        argv = [
            sys.executable,
            "-m",
            "prompt_toon",
            "dogfood",
            *(str(inputs_root / item["path"]) for item in case["inputs"]),
            "--engine",
            engine,
            "--id",
            f"quality-{case['id']}",
            "--output-dir",
            str(output_dir),
            "--trust-tier",
            "untrusted_tool_output",
            "--max-cards",
            "24",
        ]
        for item in case["inputs"]:
            if item["trust_tier"] != "untrusted_tool_output":
                argv.extend(
                    [
                        "--input-tier",
                        f"{inputs_root / item['path']}={item['trust_tier']}",
                    ]
                )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT)
        environment["PROMPT_TOON_STATE_HOME"] = str(temp_root / "state")
        try:
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise QualityFixtureError(
                f"quality case {case['id']} exceeded its 120-second process timeout"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            if len(detail) > 1000:
                detail = detail[:1000] + "..."
            raise QualityFixtureError(
                f"quality case {case['id']} failed to run: {detail or 'no stderr'}"
            )
        ledger = _ledger(output_dir / "efficiency.json")
        claim = ledger.get("claim_boundary")
        spool = ledger.get("spool")
        execution = ledger.get("execution")
        if (
            not isinstance(claim, dict)
            or isinstance(claim.get("provider_requests"), bool)
            or claim.get("provider_requests") != 0
            or not isinstance(spool, dict)
            or spool.get("withheld_documents") != 0
            or not isinstance(execution, dict)
            or execution.get("engine_requested") != engine
            or execution.get("engine_resolved") != engine
        ):
            raise QualityFixtureError(
                f"quality case {case['id']} violated its offline execution contract"
            )
        return (
            evaluate_quality_case(
                case,
                inputs_root=inputs_root,
                output_dir=output_dir,
            ),
            execution["engine_resolved"],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "fixtures" / "quality" / "manifest.json",
        help="generated quality fixture manifest",
    )
    parser.add_argument(
        "--engine",
        choices=("python", "chapel"),
        default="python",
        help="explicit transform engine; no fallback is permitted",
    )
    args = parser.parse_args(argv)
    try:
        manifest_path = args.manifest.resolve()
        manifest, manifest_sha256 = load_quality_manifest(manifest_path)
        fixtures_root = manifest_path.parent.parent
        inputs_root = fixtures_root / "inputs"
        case_results: list[dict[str, Any]] = []
        resolved_engines: set[str] = set()
        for case in manifest["cases"]:
            result, resolved = _run_case(
                case,
                inputs_root=inputs_root,
                engine=args.engine,
            )
            case_results.append(result)
            resolved_engines.add(resolved)
        if resolved_engines != {args.engine}:
            raise QualityFixtureError("quality cases did not resolve one explicit engine")
        report = build_quality_report(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            case_results=case_results,
            engine_requested=args.engine,
            engine_resolved=args.engine,
        )
    except QualityFixtureError as exc:
        print(f"quality fixture error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["quality_gate"]["status"] == "offline-fixture-pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
