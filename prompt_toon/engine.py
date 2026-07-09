"""Subprocess wrapper for the standalone `ptoon` binary (TIN-2708 C1).

ARCHITECTURE PIVOT: the Chapel engine is no longer a ctypes-loaded shared
library. It is a standalone `ptoon` binary invoked as a subprocess, one
call per text-transform. This mirrors the actual user flow -- Claude Code
PostToolBatch hooks, an MCP gateway, or `ptoon --stream` -- all of which
are subprocess-or-stream shapes over one process, never in-process FFI.
It also sidesteps a Chapel foreign-thread runtime-reentry wall the ctypes
path hit: repeated exported-proc calls into a `--library` build segfaulted
on buffer free (init/caps/normalize succeeded; free crashed).

Binary protocol (fixed contract; see spikes/tin-2707/ptoon_spike.chpl for
the reference implementation this binary's `redact` framing matches
byte-for-byte). The binary takes one subcommand as argv[1] and reads all
of stdin as raw bytes:

- ``ptoon normalize`` -> writes normalized text to stdout, exit 0.
- ``ptoon defang``    -> writes defanged text to stdout, exit 0.
- ``ptoon redact``    -> writes exactly one line ``findings:<comma-joined
  pattern names or empty>\\n`` then the redacted text verbatim, exit 0.
- ``ptoon redact-batch`` -> writes length-prefixed per-document redaction
  results in input order; this is the C2b coforall fan-in entrypoint.
- ``ptoon caps``      -> writes the engine-caps JSON to stdout, exit 0.
- unknown subcommand  -> stderr message, exit 2.

Fail-open / fail-closed contract (INV-5, docs/mythos-delivery-design.md
Sec7 C1 entry): this module itself never decides fail-open vs fail-closed
-- it only reports ``available()`` and raises ``EngineError`` on any
nonzero exit code or non-empty stderr. ``prompt_toon/cli.py``'s
``resolve_engine`` is the policy layer: ``--engine=chapel`` fails closed
(raises) when unavailable; ``--engine=auto`` fails open to the Python
implementation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


class EngineError(RuntimeError):
    """Raised when the ptoon binary exits nonzero or writes to stderr."""


def _default_search_paths() -> list[Path]:
    # Repo root is the parent of this package directory (prompt_toon/..).
    root = Path(__file__).resolve().parent.parent
    return [root / "build" / "ptoon"]


def resolve_binary_path() -> Path | None:
    """Search order: $PROMPT_TOON_PTOON, then build/ptoon relative to the
    repo root, then None. An explicit env override that does not point at
    an existing file is treated the same as "not set" -- callers only
    ever see a real path or None, never a dangling one."""
    env_path = os.environ.get("PROMPT_TOON_PTOON")
    if env_path:
        candidate = Path(env_path).expanduser()
        return candidate if candidate.is_file() else None
    for candidate in _default_search_paths():
        if candidate.is_file():
            return candidate
    return None


class ChapelEngine:
    """Thin subprocess wrapper over the `ptoon` binary, mirroring the
    normalize_text/redact_text/defang_text signatures in
    prompt_toon/cli.py so callers can swap engines without branching."""

    def __init__(self, binary_path: Path | None = None) -> None:
        self._path = binary_path if binary_path is not None else resolve_binary_path()

    def available(self) -> bool:
        return self._path is not None

    def _require_binary(self) -> Path:
        if self._path is None:
            raise EngineError(
                "ptoon binary is not available: set PROMPT_TOON_PTOON or build "
                "build/ptoon relative to the repo root"
            )
        return self._path

    def _run_bytes(self, subcommand: str, data: bytes, extra_args: tuple[str, ...] = ()) -> bytes:
        binary = self._require_binary()
        try:
            proc = subprocess.run(
                [str(binary), subcommand, *extra_args],
                input=data,
                capture_output=True,
            )
        except OSError as exc:
            raise EngineError(f"ptoon {subcommand} could not be started: {exc}") from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise EngineError(
                f"ptoon {subcommand} exited with status {proc.returncode}: {stderr}"
            )
        if proc.stderr:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise EngineError(f"ptoon {subcommand} wrote to stderr: {stderr}")
        return proc.stdout

    def _run(self, subcommand: str, text: str) -> bytes:
        return self._run_bytes(subcommand, text.encode("utf-8"))

    def normalize_text(self, text: str) -> str:
        return self._run("normalize", text).decode("utf-8", errors="replace")

    def defang_text(self, text: str) -> str:
        return self._run("defang", text).decode("utf-8", errors="replace")

    def redact_text(self, text: str) -> tuple[str, list[str]]:
        raw = self._run("redact", text)
        prefix = b"findings:"
        if not raw.startswith(prefix):
            raise EngineError("ptoon redact output is missing the 'findings:' header line")
        newline_index = raw.find(b"\n")
        if newline_index == -1:
            raise EngineError("ptoon redact output is missing the findings line terminator")
        findings_text = raw[len(prefix) : newline_index].decode("utf-8", errors="replace")
        findings = [item for item in findings_text.split(",") if item] if findings_text else []
        redacted = raw[newline_index + 1 :].decode("utf-8", errors="replace")
        return redacted, findings

    def redact_batch(
        self,
        docs: list[str],
        max_input_bytes: int = 0,
        budget_ms: int = 0,
    ) -> list[dict[str, Any]]:
        """Fan-in redaction (TIN-2709 C2b/C2c): frame N documents, run one
        `ptoon redact-batch` process (coforall one task per doc inside the
        Chapel runtime), and return per-document results IN INPUT ORDER.

        This is the shape the wide-research-spool user flow needs: N subagent
        outputs condensed concurrently in a single process before the synthesis
        seat sees any. Each result dict carries ``i`` (position), ``withheld``
        (bool), and ``findings`` (list). A non-withheld result also carries
        ``redacted`` (str); a withheld one carries ``reason`` and NO redacted
        text -- fail-closed (INV-5), the raw document is never returned.

        ``max_input_bytes`` (C2c): a document whose UTF-8 length exceeds this
        cap is withheld (reason ``input-cap``) rather than truncated -- a
        truncated PEM could leak its tail. ``budget_ms`` (C2c): a wall-clock
        backstop; a document COMPLETING past the batch deadline is withheld
        (reason ``budget``). Both default 0 (unlimited); a positive budget
        requires a positive input cap because Chapel tasks cannot be preempted
        mid-run and the cap bounds per-document work. With both 0 the call is
        byte-identical to the C2b entrypoint. Both are passed as the two
        positional policy args the binary reads (argv[2], argv[3]).

        Wire format mirrors src/ptoon/Batch.chpl: length-prefixed framing,
        which carries embedded newlines (a redacted PEM spans lines) with no
        escaping.
        """
        if (
            not isinstance(max_input_bytes, int)
            or isinstance(max_input_bytes, bool)
            or not isinstance(budget_ms, int)
            or isinstance(budget_ms, bool)
        ):
            raise ValueError("redact_batch policy args must be integers")
        if max_input_bytes < 0 or budget_ms < 0:
            raise ValueError("redact_batch policy args must be nonnegative")
        if budget_ms > 0 and max_input_bytes == 0:
            raise ValueError("redact_batch budget_ms requires positive max_input_bytes")

        framed = bytearray()
        framed += f"{len(docs)}\n".encode("utf-8")
        for doc in docs:
            body = doc.encode("utf-8")
            framed += f"{len(body)}\n".encode("utf-8")
            framed += body
        # Positional policy args are all-or-nothing: to set budget you must also
        # pass maxInputBytes (argv[2]), so emit both whenever either is nonzero.
        extra_args: tuple[str, ...] = ()
        if max_input_bytes or budget_ms:
            extra_args = (str(max_input_bytes), str(budget_ms))
        return self._parse_batch(self._run_bytes("redact-batch", bytes(framed), extra_args))

    @staticmethod
    def _parse_batch(raw: bytes) -> list[dict[str, Any]]:
        pos = 0

        def read_line() -> bytes:
            nonlocal pos
            newline_index = raw.find(b"\n", pos)
            if newline_index == -1:
                raise EngineError("ptoon redact-batch output truncated (no newline)")
            line = raw[pos:newline_index]
            pos = newline_index + 1
            return line

        def read_int(context: str) -> int:
            try:
                value = int(read_line())
            except ValueError as exc:
                raise EngineError(f"ptoon redact-batch output has invalid {context}") from exc
            if value < 0:
                raise EngineError(f"ptoon redact-batch output has negative {context}")
            return value

        count = read_int("document count")
        results: list[dict[str, Any]] = []
        for expected_i in range(count):
            meta = json.loads(read_line().decode("utf-8"))
            if meta.get("i") != expected_i:
                raise EngineError(
                    "ptoon redact-batch output index "
                    f"{meta.get('i')!r} does not match position {expected_i}"
                )
            body_len = read_int(f"body length for document {expected_i}")
            if pos + body_len > len(raw):
                raise EngineError(
                    "ptoon redact-batch output truncated: "
                    f"document {expected_i} declares {body_len} bytes with "
                    f"{len(raw) - pos} remaining"
                )
            body = raw[pos : pos + body_len]
            pos += body_len
            if not meta.get("withheld", False):
                meta["redacted"] = body.decode("utf-8", errors="replace")
            results.append(meta)
        if pos != len(raw):
            raise EngineError(
                f"ptoon redact-batch output has {len(raw) - pos} trailing byte(s)"
            )
        return results

    def engine_caps(self) -> dict[str, Any]:
        raw = self._run("caps", "")
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8", errors="replace"))
