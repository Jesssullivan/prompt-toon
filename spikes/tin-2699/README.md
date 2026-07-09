# TIN-2699 Spike: PostToolUse Task-return rewrite — VALIDATED

Date: 2026-07-09. Harness: Claude Code 2.1.201, headless (`claude -p
--settings spikes/tin-2699/settings.json`), three live rounds.

## Result

A PostToolUse hook CAN replace a subagent's return with a prompt-toon
condensation before the parent model sees it. Round 3 evidence: the parent
reported `AGENT_SEEN: [TIN-2699 SPIKE: Task return intercepted and rewritten
by PostToolUse hook] # prompt-toon condensation run-…` instead of the raw
sentinel, and `BASH_SEEN: HOOK_REWRITE_APPLIED_BASH` for the plain-tool
probe. The enforceable harness → model round trip (docs/mythos-delivery-
design.md §2, Phase B) is real on Claude Code.

## Empirical findings (each cost a round)

1. **The subagent tool is named `Agent` in 2.1.201**, not `Task` as the
   hooks docs describe. Production matchers must cover both: `Task|Agent`.
2. **Hook matchers are full-match regex.** `"Task"` matched nothing (not
   `TaskCreate`, not `Agent`); `".*"` fired for every tool. Round-1 silence
   was the matcher, not hook loading — `--settings` hooks work headless.
3. **`updatedToolOutput` must mirror the tool's structured response shape.**
   Emitting a plain string is silently ignored (round 2: rewrite emitted,
   parent still saw raw). Emitting a dict that mirrors the original
   `tool_response` — `{status, prompt, agentId, agentType, content:[{type:
   "text", text: …}]}` for Agent, `{stdout, stderr, …}` for Bash — applies
   (round 3). This is undocumented and version-sensitive: pin/verify per
   Claude Code release.
4. **Observed payload shapes (2.1.201)**: PostToolUse stdin carries
   `{session_id, transcript_path, cwd, permission_mode, prompt_id,
   tool_use_id, duration_ms, hook_event_name, tool_name, tool_input,
   tool_response}`. Agent `tool_response` = `{status, prompt, agentId,
   agentType, content[]}`.

## Residual unknowns (carry into Phase B, do not re-spike now)

- Nested subagents (Agent inside Agent): fired/not-fired behavior unverified.
- Streaming/partial returns: unverified.
- Interactive (non `-p`) sessions: hooks snapshot at startup; behavior
  assumed identical, unverified.

## Files

- `task_condense_hook.py` — the spike hook (logs every payload; condenses
  Task/Agent returns via `python3 -m prompt_toon condense`; fails open on
  condensation per INV-5; structure-mirroring replacement).
- `settings.json` — hook wiring passed via `--settings` (matcher `.*` for
  evidence capture; production adapters should use `Task|Agent`).

## Status vs the enforcement gate

This spike proves the MECHANISM. It does not unlock enforcement:
`policy/io.json` `enforcement_gate.unlocked` stays `false` until TIN-2695
closes and INV-1..INV-6 hold (tests/test_io_policy.py fails closed on any
surface enabling while locked).
