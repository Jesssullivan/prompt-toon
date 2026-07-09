#!/usr/bin/env python3
"""TIN-2699 spike: PostToolUse hook that rewrites Task (subagent) returns.

Contract under test: Claude Code PostToolUse hooks can replace the tool
result the model sees via hookSpecificOutput.updatedToolOutput, including
for tool_name == "Task" (subagent fan-out returns).

Evidence trail: every invocation logs its full stdin payload and any
rewrite it emitted under $PROMPT_TOON_SPIKE_LOG (default:
~/.local/state/prompt-toon/spike-tin2699/), so firing behavior and payload
shape are captured even if the rewrite itself does not take effect.

Failure doctrine (INV-5, even in a spike): condensation fails OPEN — on any
error the hook emits nothing and the session sees the raw return.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "[TIN-2699 SPIKE: Task return intercepted and rewritten by PostToolUse hook]"


def log_dir() -> Path:
    configured = os.environ.get("PROMPT_TOON_SPIKE_LOG")
    base = Path(configured) if configured else Path.home() / ".local" / "state" / "prompt-toon" / "spike-tin2699"
    base.mkdir(parents=True, exist_ok=True)
    return base


def extract_text(resp):
    if resp is None:
        return None
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        parts = [extract_text(item) for item in resp]
        parts = [p for p in parts if p]
        return "\n".join(parts) if parts else None
    if isinstance(resp, dict):
        if isinstance(resp.get("text"), str):
            return resp["text"]
        for key in ("content", "output", "result", "message", "tool_result"):
            if key in resp:
                got = extract_text(resp[key])
                if got:
                    return got
    return None


def condense(text: str, out_dir: Path) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "prompt_toon",
            "condense",
            "--output-dir",
            str(out_dir),
            "--trust-tier",
            "subagent_return",
        ],
        input=text.encode("utf-8"),
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=10,
    )
    summary = out_dir / "summary.md"
    if proc.returncode != 0 or not summary.exists():
        raise RuntimeError(f"condense failed rc={proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:500]}")
    return summary.read_text(encoding="utf-8")


def main() -> int:
    raw = sys.stdin.read()
    base = log_dir()
    stamp = time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    (base / f"payload-{stamp}.json").write_text(raw, encoding="utf-8")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    tool_name = payload.get("tool_name")
    response = payload.get("tool_response")

    if tool_name == "Bash" and isinstance(response, dict):
        # Structure-mirroring replacement probe for a plain tool.
        updated_response = dict(response)
        updated_response["stdout"] = "HOOK_REWRITE_APPLIED_BASH"
    elif tool_name in ("Task", "Agent"):
        text = extract_text(response)
        if not text:
            (base / f"no-text-{stamp}.txt").write_text("Task payload had no extractable text", encoding="utf-8")
            return 0
        try:
            condensed = condense(text, base / f"condense-{stamp}")
            updated = f"{MARKER}\n\n{condensed}"
        except Exception as exc:  # condensation fails open (INV-5)
            (base / f"condense-error-{stamp}.txt").write_text(str(exc), encoding="utf-8")
            updated = f"{MARKER}\n\n[condensation failed open; first 800 chars of raw follow]\n{text[:800]}"
        if isinstance(response, dict) and "content" in response:
            # Mirror the Agent tool's structured response shape.
            updated_response = dict(response)
            updated_response["content"] = [{"type": "text", "text": updated}]
        else:
            updated_response = updated
    else:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated_response,
        }
    }
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    (base / f"rewrite-{stamp}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
