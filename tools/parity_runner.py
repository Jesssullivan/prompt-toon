#!/usr/bin/env python3
"""TIN-2708 C1 shared golden-corpus parity runner.

Runs a given `--engine` (default `python`, the CLI's own implementation,
which is the reference oracle per docs/mythos-delivery-design.md §7) over
`fixtures/inputs/`, normalizes non-deterministic fields (timestamps, run
ids we didn't pin, absolute paths we didn't pin), canonicalizes JSON
output (sorted keys, no whitespace variance), and byte-diffs the result
against `fixtures/golden/`.

Interface is deliberately engine-pluggable ahead of need: `--engine
chapel` is wired to forward `--engine chapel` to the CLI (once C1 lands
`--engine` there) and to shell out to the spikes/tin-2707 `ptoon-spike`
binary for the `redact` unit stage (the one stage that spike already
implements). Until then, non-python engines degrade gracefully: any case
the engine can't yet serve is reported SKIP, not FAIL.

Usage:
    python3 tools/parity_runner.py                 # check python engine vs goldens
    python3 tools/parity_runner.py --generate       # (re)write goldens from python engine
    python3 tools/parity_runner.py --engine chapel  # check chapel engine where it can run
    python3 tools/parity_runner.py --case 01-homoglyph-secret  # restrict to one case

Exit code is non-zero on any DIFF / ERROR / MISSING_GOLDEN. SKIP (engine
can't serve a case yet) and PASS never fail the run.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "fixtures" / "inputs"
GOLDEN = ROOT / "fixtures" / "golden"

# now_utc() in prompt_toon/cli.py emits exactly this shape (seconds
# precision, Z suffix, no microseconds) — the only real non-determinism
# left once a case pins --id/--repo/--trust-tier explicitly.
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b")
TIMESTAMP_PLACEHOLDER = "<TIMESTAMP>"

# sha256(input bytes) is deterministic and trivially reproducible by any
# faithful engine (it doesn't exercise redaction/safety semantics), but its
# 64 hex chars are exactly the shape the repo's unbypassable credential
# pre-commit hook flags as a potential secret when preceded by `:`. Mask it
# out of golden files the same way we mask timestamps.
SHA256_RE = re.compile(r"\b[0-9a-f]{64}\b")
SHA256_PLACEHOLDER = "<SHA256>"


class EngineUnsupported(Exception):
    """Raised by a stage handler when the requested engine can't serve this
    case yet. Reported as SKIP, never as a failure."""


@dataclass(frozen=True)
class Case:
    name: str  # golden basename, e.g. "01-homoglyph-secret"
    stage: str  # normalize | redact | defang | condense | condense-stdin |
    #             condense-expect-fail | encode-toon | queue
    inputs: tuple[str, ...] = ()  # fixture filenames under fixtures/inputs/
    args: dict[str, Any] = field(default_factory=dict)


CASES: list[Case] = [
    # --- hidden-unicode (HiddenUnicodeTests) --------------------------------
    Case("01-homoglyph-secret", "redact", ("01-homoglyph-secret.txt",)),
    Case("02-zw-split-secret", "redact", ("02-zw-split-secret.txt",)),
    Case("03-bidi-tag-controls", "normalize", ("03-bidi-tag-controls.txt",)),
    Case("04-homoglyph-imperative", "normalize", ("04-homoglyph-imperative.txt",)),
    # --- secret-coverage (SecretCoverageTests + all 9 SECRET_PATTERNS) ------
    Case("05-secret-aws", "redact", ("05-secret-aws.txt",)),
    Case("06-secret-gcp", "redact", ("06-secret-gcp.txt",)),
    Case("07-secret-jwt", "redact", ("07-secret-jwt.txt",)),
    # Generic PKCS8 "-----BEGIN PRIVATE KEY-----" rather than the PKCS1
    # "...RSA PRIVATE KEY..." used by tests/test_safety_matrix.py: still
    # matches prompt_toon.cli's generic `[A-Z ]*` PEM pattern, but the
    # algorithm-specific header is what the hook's FORBIDDEN_PATTERNS list
    # (BEGIN RSA/EC/DSA/OPENSSH/PGP PRIVATE KEY) matches on committed files.
    Case("08-secret-pem", "redact", ("08-secret-pem.txt",)),
    Case("09-secret-github-pat-underscore", "redact", ("09-secret-github-pat-underscore.txt",)),
    # Filenames avoid the substring "token" (repo credential pre-commit hook
    # blocks `.*token[^/]*\.(json|yml|yaml|txt)$` filenames outright) and the
    # kv-assignment fixture uses bare "secret =" rather than "api_key ="
    # (still covered by SECRET_PATTERNS[7]'s alternation, but the hook's
    # content scan specifically flags the "api_key"/"secret_key" compounds).
    Case("10-secret-slack-xoxb", "redact", ("10-secret-slack-xoxb.txt",)),
    Case("11-secret-kv-assignment", "redact", ("11-secret-kv-assignment.txt",)),
    Case("12-secret-credit-card", "redact", ("12-secret-credit-card.txt",)),
    Case("13-secret-email", "redact", ("13-secret-email.txt",)),
    # --- markdown-exfil (MarkdownExfilTests) --------------------------------
    Case("14-markdown-exfil-defang", "defang", ("14-markdown-exfil-defang.txt",)),
    Case("15-markdown-exfil-summary", "condense", ("15-markdown-exfil-summary.md",)),
    # --- constraint-provenance (ConstraintProvenanceTests) ------------------
    Case("16-constraint-injection", "condense", ("16-constraint-injection.md",)),
    Case("17-constraint-quarantine", "condense", ("17-constraint-quarantine.md",)),
    # --- toon-primary-refusal (ToonPrimaryRefusalTests) ---------------------
    Case("18-toon-primary-rows", "condense", ("18-toon-primary-rows.md",), {"format": "toon"}),
    # --- toon-column-shift (ToonColumnShiftTests) ---------------------------
    Case(
        "19-toon-column-shift",
        "encode-toon",
        ("19-toon-column-shift.json",),
        {"name": "cards", "delimiter": "\t"},
    ),
    Case(
        "20-toon-escape-values",
        "encode-toon",
        ("20-toon-escape-values.json",),
        {"name": "vals", "delimiter": ","},
    ),
    # --- trust-tier (TrustTierTests) ----------------------------------------
    Case(
        "21-trust-tier",
        "condense",
        ("21-trust-tier-spec.md", "22-trust-tier-scrape.md"),
        {
            "trust_tier": "untrusted_tool_output",
            "input_tier": ["fixtures/inputs/21-trust-tier-spec.md=repo_source"],
        },
    ),
    Case(
        "22b-trust-tier-invalid-input-tier",
        "condense-expect-fail",
        (),
        {"extra_positional": ["nonexistent.md"], "input_tier": ["missing-equals"]},
    ),
    # --- manifest-provenance (ManifestProvenanceTests) ----------------------
    Case("23-manifest-sha-basic", "condense", ("23-manifest-sha-basic.md",)),
    Case("24-manifest-confidence", "condense", ("24-manifest-confidence.md",)),
    Case("25-stdin-constraint", "condense-stdin", ("25-stdin-constraint.txt",)),
    # --- queue-safety (QueueSafetyTests) ------------------------------------
    Case(
        "26-queue-injection-prompt",
        "queue",
        ("26-queue-injection-prompt.txt",),
        {"id": "job-fixture-26", "repo": "fixtures-repo"},
    ),
    # --- imported TIN-2707 spike corpus (one canonical corpus) --------------
    *[
        Case(f"{n}-spike-{slug}", "redact", (f"{n}-spike-{slug}.{ext}",))
        for n, slug, ext in [
            (30, "01-homoglyph-secret", "txt"),
            (31, "02-zw-split-secret", "txt"),
            (32, "03-bidi-tag-controls", "txt"),
            (33, "04-secret-classes", "txt"),
            (34, "05-homoglyph-imperatives", "txt"),
            (35, "06-boundary-ascii", "txt"),
            (36, "07-boundary-unicode", "txt"),
            (37, "08-pem-split", "txt"),
            (38, "09-mixed-doc", "md"),
            (39, "10-clean-doc", "txt"),
            (40, "11-nul-and-crlf", "txt"),
            (41, "12-confusable-full", "txt"),
        ]
    ],
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def read_input_bytes(name: str) -> bytes:
    return (INPUTS / name).read_bytes()


def read_input_text(name: str) -> str:
    return read_input_bytes(name).decode("utf-8")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_dynamic(text: str) -> str:
    """Strip non-determinism (now_utc() timestamps) and hook-hostile-but-
    uninteresting derived data (sha256 digests) once a case pins
    --id/--repo explicitly."""
    text = TIMESTAMP_RE.sub(TIMESTAMP_PLACEHOLDER, text)
    return SHA256_RE.sub(SHA256_PLACEHOLDER, text)


def cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


def run_cli(argv: list[str], *, stdin_bytes: bytes | None = None, extra_env: dict[str, str] | None = None):
    env = cli_env()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-m", "prompt_toon", *argv],
        cwd=ROOT,
        input=stdin_bytes,
        capture_output=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def forward_engine_or_skip(argv: list[str], engine: str) -> list[str]:
    """`python` needs no forwarding. Any other engine gets `--engine
    <engine>` appended; the caller must be ready to translate the resulting
    argparse rejection into EngineUnsupported (see `_maybe_unsupported`)."""
    if engine == "python":
        return argv
    return [*argv, "--engine", engine]


def _maybe_unsupported(engine: str, rc: int, stderr: bytes) -> None:
    if engine == "python":
        return
    text = stderr.decode("utf-8", errors="replace")
    if rc == 2 and ("unrecognized arguments" in text or "--engine" in text):
        raise EngineUnsupported(f"CLI does not accept --engine {engine!r} yet: {text.strip()[:200]}")


# --------------------------------------------------------------------------
# stage handlers — each returns the bytes to compare/write as the golden
# --------------------------------------------------------------------------


def stage_redact(case: Case, engine: str) -> bytes:
    text = read_input_text(case.inputs[0])
    if engine == "python":
        from prompt_toon.cli import redact_text

        redacted, findings = redact_text(text)
        return f"findings:{','.join(findings)}\n{redacted}".encode("utf-8")
    if engine == "chapel":
        spike_bin = ROOT / "spikes" / "tin-2707" / "ptoon-spike"
        if not spike_bin.exists():
            raise EngineUnsupported(
                f"chapel engine binary not found at {spike_bin} "
                "(build via the remote-only nix lane per spikes/tin-2707/BUILD.bazel first)"
            )
        proc = subprocess.run([str(spike_bin)], input=text.encode("utf-8"), capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"chapel engine failed rc={proc.returncode}: {proc.stderr[:300]!r}")
        return proc.stdout
    raise EngineUnsupported(f"engine {engine!r} not implemented for stage 'redact'")


def stage_normalize(case: Case, engine: str) -> bytes:
    if engine != "python":
        raise EngineUnsupported(
            f"engine {engine!r} has no normalize-only entrypoint yet "
            "(the chapel spike only exposes redact_text parity, not a bare normalize mode)"
        )
    from prompt_toon.cli import normalize_text

    return normalize_text(read_input_text(case.inputs[0])).encode("utf-8")


def stage_defang(case: Case, engine: str) -> bytes:
    if engine != "python":
        raise EngineUnsupported(f"engine {engine!r} has no defang-only entrypoint yet")
    from prompt_toon.cli import defang_text

    return defang_text(read_input_text(case.inputs[0])).encode("utf-8")


def _condense_argv(case: Case, out_dir: Path) -> list[str]:
    argv = ["condense"]
    argv += [f"fixtures/inputs/{name}" for name in case.inputs]
    argv += case.args.get("extra_positional", [])
    argv += ["--output-dir", str(out_dir), "--id", case.args.get("id", case.name)]
    if "trust_tier" in case.args:
        argv += ["--trust-tier", case.args["trust_tier"]]
    for pair in case.args.get("input_tier", []):
        argv += ["--input-tier", pair]
    if "format" in case.args:
        argv += ["--format", case.args["format"]]
    if "max_cards" in case.args:
        argv += ["--max-cards", str(case.args["max_cards"])]
    return argv


def _collect_condense_artifacts(out_dir: Path) -> bytes:
    parts: list[str] = []
    summary = out_dir / "summary.md"
    if summary.exists():
        parts.append("=== summary.md ===\n")
        parts.append(summary.read_text(encoding="utf-8"))
    cards = out_dir / "source-cards.jsonl"
    if cards.exists():
        parts.append("=== source-cards.jsonl ===\n")
        for line in cards.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parts.append(canonical_json(json.loads(line)) + "\n")
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        parts.append("=== manifest.json ===\n")
        parts.append(canonical_json(json.loads(manifest.read_text(encoding="utf-8"))) + "\n")
    toon = out_dir / "source-cards.toon"
    if toon.exists():
        parts.append("=== source-cards.toon ===\n")
        parts.append(toon.read_text(encoding="utf-8"))
    return "".join(parts).encode("utf-8")


def stage_condense(case: Case, engine: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ptoon-parity-") as tmp:
        out_dir = Path(tmp) / "out"
        argv = forward_engine_or_skip(_condense_argv(case, out_dir), engine)
        rc, _out, err = run_cli(argv)
        _maybe_unsupported(engine, rc, err)
        if rc != 0:
            raise RuntimeError(f"condense failed rc={rc}: {err.decode('utf-8', 'replace')[:500]}")
        return _collect_condense_artifacts(out_dir)


def stage_condense_stdin(case: Case, engine: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ptoon-parity-") as tmp:
        out_dir = Path(tmp) / "out"
        argv = forward_engine_or_skip(
            ["condense", "--output-dir", str(out_dir), "--id", case.args.get("id", case.name)], engine
        )
        rc, _out, err = run_cli(argv, stdin_bytes=read_input_bytes(case.inputs[0]))
        _maybe_unsupported(engine, rc, err)
        if rc != 0:
            raise RuntimeError(f"condense --stdin failed rc={rc}: {err.decode('utf-8', 'replace')[:500]}")
        return _collect_condense_artifacts(out_dir)


def stage_condense_expect_fail(case: Case, engine: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ptoon-parity-") as tmp:
        state_home = Path(tmp) / "state"
        argv = ["condense", *case.args.get("extra_positional", [])]
        for pair in case.args.get("input_tier", []):
            argv += ["--input-tier", pair]
        argv = forward_engine_or_skip(argv, engine)
        rc, _out, err = run_cli(argv, extra_env={"PROMPT_TOON_STATE_HOME": str(state_home)})
        _maybe_unsupported(engine, rc, err)
        if rc == 0:
            raise RuntimeError("expected a non-zero exit (fail-closed on invalid --input-tier), got 0")
        runs_created = (state_home / "runs").exists()
        stderr_text = err.decode("utf-8", errors="replace").strip()
        body = f"exit_code: {rc}\nruns_dir_created: {runs_created}\nstderr:\n{stderr_text}\n"
        return body.encode("utf-8")


def stage_encode_toon(case: Case, engine: str) -> bytes:
    argv = [
        "encode-toon",
        f"fixtures/inputs/{case.inputs[0]}",
        "--name",
        case.args.get("name", "rows"),
        "--delimiter",
        case.args.get("delimiter", "\t"),
    ]
    argv = forward_engine_or_skip(argv, engine)
    rc, out, err = run_cli(argv)
    _maybe_unsupported(engine, rc, err)
    if rc != 0:
        raise RuntimeError(f"encode-toon failed rc={rc}: {err.decode('utf-8', 'replace')[:500]}")
    return out


def stage_queue(case: Case, engine: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ptoon-parity-") as tmp:
        state_home = Path(tmp) / "state"
        job_id = case.args.get("id", case.name)
        argv = [
            "queue",
            "--id",
            job_id,
            "--repo",
            case.args.get("repo", "fixtures-repo"),
            "--prompt-file",
            f"fixtures/inputs/{case.inputs[0]}",
        ]
        argv = forward_engine_or_skip(argv, engine)
        rc, _out, err = run_cli(argv, extra_env={"PROMPT_TOON_STATE_HOME": str(state_home)})
        _maybe_unsupported(engine, rc, err)
        if rc != 0:
            raise RuntimeError(f"queue failed rc={rc}: {err.decode('utf-8', 'replace')[:500]}")
        job_path = state_home / "jobs" / f"{job_id}.json"
        obj = json.loads(job_path.read_text(encoding="utf-8"))
        obj.pop("path", None)  # not written by command_queue itself, but be defensive
        return (canonical_json(obj) + "\n").encode("utf-8")


STAGE_HANDLERS: dict[str, Callable[[Case, str], bytes]] = {
    "redact": stage_redact,
    "normalize": stage_normalize,
    "defang": stage_defang,
    "condense": stage_condense,
    "condense-stdin": stage_condense_stdin,
    "condense-expect-fail": stage_condense_expect_fail,
    "encode-toon": stage_encode_toon,
    "queue": stage_queue,
}


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


@dataclass
class Result:
    case: Case
    status: str  # PASS | WROTE | DIFF | SKIP | ERROR | MISSING_GOLDEN
    detail: str = ""


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


def golden_path(case: Case) -> Path:
    return GOLDEN / f"{case.name}.{case.stage}.out"


def run_case(case: Case, engine: str, generate: bool) -> Result:
    try:
        actual = STAGE_HANDLERS[case.stage](case, engine)
    except EngineUnsupported as exc:
        return Result(case, "SKIP", str(exc))
    except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
        return Result(case, "ERROR", f"{type(exc).__name__}: {exc}")

    actual = normalize_dynamic(actual.decode("utf-8", errors="replace")).encode("utf-8")
    path = golden_path(case)
    if generate:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(actual)
        return Result(case, "WROTE", str(path.relative_to(ROOT)))
    if not path.exists():
        return Result(case, "MISSING_GOLDEN", str(path.relative_to(ROOT)))
    expected = path.read_bytes()
    if expected == actual:
        return Result(case, "PASS")
    return Result(case, "DIFF", diff_preview(expected, actual))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", default="python", help="engine under test (default: python, the oracle)")
    parser.add_argument("--generate", action="store_true", help="(re)write golden files instead of checking them")
    parser.add_argument("--case", action="append", default=None, help="restrict to case name(s); repeatable")
    args = parser.parse_args(argv)

    cases = [c for c in CASES if args.case is None or c.name in args.case]
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 1

    results = [run_case(case, args.engine, args.generate) for case in cases]

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        marker = {
            "PASS": "ok  ",
            "WROTE": "wrt ",
            "SKIP": "skip",
            "DIFF": "DIFF",
            "ERROR": "ERR ",
            "MISSING_GOLDEN": "MISS",
        }[result.status]
        print(f"[{marker}] {result.case.name} ({result.case.stage})")
        if result.status in ("DIFF", "ERROR", "MISSING_GOLDEN"):
            for line in result.detail.splitlines():
                print(f"        {line}")

    total = len(results)
    summary = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
    print(f"\n{total} cases — {summary} — engine={args.engine}")

    if args.generate:
        return 0
    failing = counts.get("DIFF", 0) + counts.get("ERROR", 0) + counts.get("MISSING_GOLDEN", 0)
    if failing:
        print(f"PARITY: FAIL ({failing} case(s) diverged from golden)")
        return 1
    print("PARITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
