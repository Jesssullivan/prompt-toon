"""In-build parity runner for the TIN-2707 C0 spike.

Runs ptoon-spike (Chapel) and py_driver.py (Python oracle) over the corpus,
byte-diffs the outputs, benchmarks both on hostile digit-heavy inputs, and
writes parity-report.md. Divergence does NOT fail the build — the report is
the C0 artifact and the go/no-go input. Operational failures (either engine
crashing) DO fail the build.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: list[str], data: bytes) -> tuple[bytes, float]:
    start = time.perf_counter()
    proc = subprocess.run(cmd, input=data, capture_output=True)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} failed rc={proc.returncode}: {proc.stderr[:500]!r}")
    return proc.stdout, elapsed


def main() -> int:
    # Corpus is generated, not committed (credential-scanner hygiene).
    subprocess.run([sys.executable, str(HERE / "gen_corpus.py")], check=True)

    chapel = [str(HERE / "ptoon-spike")]
    python = [sys.executable, str(HERE / "py_driver.py")]

    lines = [
        "# TIN-2707 C0 parity report",
        "",
        "Chapel (RE2 + utf8proc NFKC) vs Python oracle (`redact_text`).",
        "",
        "## Corpus parity",
        "",
        "| case | parity | chapel findings | python findings |",
        "|---|---|---|---|",
    ]
    divergent: list[str] = []
    for fixture in sorted((HERE / "corpus").iterdir()):
        data = fixture.read_bytes()
        c_out, _ = run(chapel, data)
        p_out, _ = run(python, data)
        c_findings = c_out.split(b"\n", 1)[0].decode()
        p_findings = p_out.split(b"\n", 1)[0].decode()
        ok = c_out == p_out
        if not ok:
            divergent.append(fixture.name)
        lines.append(
            f"| {fixture.name} | {'IDENTICAL' if ok else 'DIVERGENT'} "
            f"| {c_findings.removeprefix('findings:') or '-'} "
            f"| {p_findings.removeprefix('findings:') or '-'} |"
        )

    lines += ["", "## Divergence detail", ""]
    if divergent:
        for name in divergent:
            data = (HERE / "corpus" / name).read_bytes()
            c_out, _ = run(chapel, data)
            p_out, _ = run(python, data)
            c_body = c_out.split(b"\n", 1)[1] if b"\n" in c_out else b""
            p_body = p_out.split(b"\n", 1)[1] if b"\n" in p_out else b""
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"- chapel: `{c_body[:300]!r}`")
            lines.append(f"- python: `{p_body[:300]!r}`")
            lines.append("")
    else:
        lines.append("None — all cases byte-identical.")

    lines += ["", "## Benchmark (hostile digit-heavy input, wall clock)", ""]
    lines.append("| size | chapel | python | speedup |")
    lines.append("|---|---|---|---|")
    for mb in (1, 5):
        blob = (b"492 8371 0284 5566 19 " * 48)[:1024] * (1024 * mb)
        blob = blob[: mb * 1024 * 1024]
        _, c_t = run(chapel, blob)
        _, p_t = run(python, blob)
        lines.append(f"| {mb} MiB | {c_t:.3f}s | {p_t:.3f}s | {p_t / c_t:.1f}x |")

    report = "\n".join(lines) + "\n"
    (HERE / "parity-report.md").write_text(report)
    print(report)
    print(f"parity: {12 - len(divergent)}/12 identical; divergent: {divergent or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
