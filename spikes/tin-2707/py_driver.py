"""Python parity oracle for the TIN-2707 C0 spike.

Emits the identical output protocol as ptoon_spike.chpl: one findings line,
then redact_text()'s output verbatim.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prompt_toon.cli import redact_text  # noqa: E402


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="strict")
    redacted, findings = redact_text(raw)
    sys.stdout.write(f"findings:{','.join(findings)}\n")
    sys.stdout.write(redacted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
