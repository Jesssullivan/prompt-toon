#!/usr/bin/env python3
"""TIN-2709 C2f: PostToolUse condensation hook — the production-shaped
successor to spikes/tin-2699/task_condense_hook.py (which stays as the
firing-evidence probe).

THE USER FLOW: a wide research spool returns N subagent outputs; each
PostToolUse firing hands one Task/Agent return to this hook, which must
condense it (fail-closed redaction, provenance cards, defanged summary)
before the mythos/fable synthesis seat reads it — via
hookSpecificOutput.updatedToolOutput.

CHAPEL-FIRST: derivation runs through ChapelEngine.condense_run (the
`ptoon condense` binary — one process, deterministic output). There is
deliberately NO Python-pipeline fallback here: if the binary is absent the
hook FAILS OPEN (emits nothing; the session sees the raw return) and logs
why. INV-5 splits the failure modes exactly as policy/io.json declares:
condensation is fail_open, redaction is fail_closed — a redaction failure
inside the binary withholds the doc, which this hook treats as "do not
rewrite" (raw passes through, but nothing HALF-redacted is ever emitted).

POLICY (policy/io.json, override via $PROMPT_TOON_IO_POLICY for tests):
- enforcement_gate.unlocked must be true AND the `subagent` surface must be
  enabled, else the hook is a silent no-op. Flipping the real policy is a
  reviewed Dhall-side operator change (INV-8), never something this hook
  assumes.
- thresholds: enforce_threshold_tokens (below it, not worth condensing),
  max_input_bytes + wall_clock_budget_ms (passed straight to the binary's
  fail-closed cap/budget policy args), min_savings (the beats_margin
  emission gate — a condensation that does not beat raw by the margin
  passes raw through unchanged, by construction never inflating).
- trust_tiers: tool_name -> tier (Task => subagent_return).

CACHE (INV-6): iocache-first. The cache key binds the raw return bytes to
the full settings dict (engine, policy knobs, format version); entries are
HMAC-authenticated and quarantined on any verification failure — this hook
only ever serves what iocache.load() authenticates, and re-derives on miss.

AUDIT: every invocation appends one JSONL line to
$PROMPT_TOON_STATE_HOME/hook-log.jsonl (bypass|hit|miss|withheld|error +
reason + cache key when known). The canary gate asserts on this trail.
"""

from __future__ import annotations

import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from prompt_toon import iocache  # noqa: E402
from prompt_toon.cli import now_utc, rough_token_count, state_root  # noqa: E402
from prompt_toon.engine import ChapelEngine  # noqa: E402

CONDENSE_FORMAT_VERSION = 1
HOOK_TOOLS = ("Task", "Agent")


def _log(record: dict[str, Any]) -> None:
    try:
        root = state_root()
        root.mkdir(parents=True, exist_ok=True)
        record = {"ts": now_utc(), **record}
        with (root / "hook-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass  # the audit trail must never break the hook (fail open)


def load_policy() -> dict[str, Any]:
    configured = os.environ.get("PROMPT_TOON_IO_POLICY")
    path = Path(configured) if configured else REPO_ROOT / "policy" / "io.json"
    return json.loads(path.read_text(encoding="utf-8"))


def surface_enabled(policy: dict[str, Any], surface_id: str) -> bool:
    if not policy.get("enforcement_gate", {}).get("unlocked", False):
        return False
    for surface in policy.get("surfaces", []):
        if surface.get("id") == surface_id:
            return bool(surface.get("enabled", False))
    return False


def tier_for(policy: dict[str, Any], tool_name: str) -> str:
    for entry in policy.get("trust_tiers", []):
        if entry.get("source") == tool_name:
            return entry["tier"]
    return "untrusted_tool_output"


def extract_text(resp: Any) -> str | None:
    """Same extraction contract the TIN-2699 spike proved against live
    payloads: str | list-of-parts | dicts keyed text/content/output/..."""
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


def condense_via_binary(
    text: str, tier: str, thresholds: dict[str, Any]
) -> tuple[str, dict[str, str]] | None:
    """One doc through `ptoon condense`. Returns (summary_text, artifacts)
    or None when the doc was withheld (the fail-closed side: nothing
    half-redacted is emitted; the hook then passes raw through)."""
    engine = ChapelEngine()
    if not engine.available():
        raise RuntimeError("ptoon binary unavailable")
    digest = sha256(text.encode("utf-8")).hexdigest()
    results, summary_text, manifest = engine.condense_run(
        [{"source": "task-return", "trust_tier": tier, "body": text}],
        run_id=f"hook-{digest[:16]}",
        generated_at=now_utc(),
        max_input_bytes=int(thresholds.get("max_input_bytes", 0)),
        budget_ms=int(thresholds.get("wall_clock_budget_ms", 0)),
        default_trust_tier=tier,
    )
    if results[0].get("withheld", False):
        return None
    cards_jsonl = "".join(
        json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n"
        for card in results[0]["cards"]
    )
    artifacts = {
        "summary.md": summary_text,
        "source-cards.jsonl": cards_jsonl,
        "manifest.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    }
    return summary_text, artifacts


def rewrite(response: Any, summary: str) -> Any:
    if isinstance(response, dict) and "content" in response:
        updated = dict(response)
        updated["content"] = [{"type": "text", "text": summary}]
        return updated
    return summary


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    tool_name = payload.get("tool_name")
    if tool_name not in HOOK_TOOLS:
        return 0

    try:
        policy = load_policy()
    except Exception as exc:  # fail open: no policy, no rewrite
        _log({"outcome": "error", "reason": f"policy-load: {exc}"})
        return 0
    if not surface_enabled(policy, "subagent"):
        _log({"outcome": "bypass", "reason": "surface-disabled"})
        return 0

    text = extract_text(payload.get("tool_response"))
    if not text:
        _log({"outcome": "bypass", "reason": "no-extractable-text"})
        return 0

    thresholds = policy.get("thresholds", {})
    if rough_token_count(text) < int(thresholds.get("enforce_threshold_tokens", 0)):
        _log({"outcome": "bypass", "reason": "below-token-threshold"})
        return 0

    tier = tier_for(policy, tool_name)
    raw = text.encode("utf-8")
    settings = {
        "engine": "chapel",
        "format_version": CONDENSE_FORMAT_VERSION,
        "max_input_bytes": int(thresholds.get("max_input_bytes", 0)),
        "budget_ms": int(thresholds.get("wall_clock_budget_ms", 0)),
        "min_savings": thresholds.get("min_savings", 0.25),
        "surface": "subagent",
        "tier": tier,
    }
    key = iocache.entry_key(raw, settings)

    try:
        cached = iocache.load(raw, settings)
    except Exception as exc:
        _log({"outcome": "error", "reason": f"cache-load: {exc}", "key": key})
        cached = None

    if cached is not None and "summary.md" in cached["artifacts"]:
        summary = cached["artifacts"]["summary.md"]
        outcome = "hit"
    else:
        try:
            derived = condense_via_binary(text, tier, thresholds)
        except Exception as exc:  # condensation fails OPEN (INV-5)
            _log({"outcome": "error", "reason": f"derive: {exc}", "key": key})
            return 0
        if derived is None:
            # The binary withheld the doc (redaction fail-closed). Do not
            # rewrite; raw passes through the harness untouched.
            _log({"outcome": "withheld", "key": key})
            return 0
        summary, artifacts = derived
        try:
            iocache.store(raw, settings, artifacts)
        except Exception as exc:
            _log({"outcome": "error", "reason": f"cache-store: {exc}", "key": key})
        outcome = "miss"

    if not iocache.beats_margin(text, summary, float(settings["min_savings"])):
        _log({"outcome": "bypass", "reason": "below-savings-margin", "key": key})
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": rewrite(payload.get("tool_response"), summary),
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    _log({"outcome": outcome, "key": key})
    return 0


if __name__ == "__main__":
    sys.exit(main())
