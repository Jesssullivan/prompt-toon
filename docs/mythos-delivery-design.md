# Toon of Mythos — Delivery Design

Date: 2026-07-09
Status: synthesized from the 2026-07-09 delivery spool (8 lanes: 6 recon on
sonnet/opus + 2 opus adversarial critics; ~626k research tokens; fable
synthesis seat). Owner tickets: TIN-2698 (SSOT), TIN-2696 (HM delivery),
TIN-2695 (safety gate). Vision: `docs/mythos-vision.md`.

The endgame: one-click / minimal-step install around popular and OEM
harnesses, fleet-managed rules + skills, and a structured, modifiable IO
layer with an enforceable harness → model round trip — traced backward to
shape architecture and perf decisions.

## 1. Verdicts that shape everything (adversarially verified)

1. **No upstream Bazel↔Dhall rules exist.** `tweag/rules_dhall` does not
   exist; the three GitHub `rules_dhall` repos are WORKSPACE-era and dead
   (2021–2022, no MODULE.bazel); zero dhall modules in the BCR (1,192
   modules checked) or the tinyland registry. Verdict: do NOT wire Dhall
   into Bazel. Generation stays just+nix (`dhall-to-json` in the dev shell)
   with a skip-if-absent structural-equality unittest as the Bazel-visible
   drift check (INV-8).
2. **Claude Code is the only harness that can rewrite tool output today.**
   PostToolUse `hookSpecificOutput.updatedToolOutput` replaces the tool
   result the model sees — including `tool_name == "Task"`, i.e. subagent
   fan-out returns. Codex CLI parses but does not support
   `updatedMCPToolOutput` (hooks fire reliably for Bash only); OpenCode's
   `tool.execute.after` output mutation is silently dropped upstream
   (anomalyco/opencode#13574). Any roadmap promising uniform cross-harness
   enforcement is currently false.
3. **The skills standard already covers Codex + OpenCode zero-adapter.**
   `.agents/skills/<name>/SKILL.md` (agentskills.io) is natively scanned by
   Codex CLI and OpenCode. Claude Code scans only `.claude/skills/` — fixed
   in-repo by checked-in symlinks, and in the fleet by PR #761's dual-write.
4. **PR #761 already implements most of the fleet channel** (flake input +
   overlay + `home.packages` + dual-write `.claude/skills` and
   `.codex/skills` + tests). Remaining gaps before it lands: the
   mythos-delegation skill is missing from `promptToonSkillFiles`, no
   OpenCode surface, and INV-7 (rev-pinned input with a reviewed bump gate —
   the input is branch-following; only flake.lock pins today, and root
   flake.lock is outside Renovate's scope).
5. **Channel triage:** fund **nix/Home-Manager** (real fleet channel, ~90%
   built) and **defer-but-keep pipx** (pyproject already has
   `[project.scripts]`; gated on repo-visibility + policy templating for the
   external-researcher persona). **Amended 2026-07-09 (Chapel-first
   decision): rpm, brew, and bazel-registry are phase-gated, not declined** —
   they open when the single `ptoon` binary exists (bazel-registry + static
   GH release at C2; brew/rpm nfpm at C3, derived from the TIN-2706
   packaging manifest). The original rationale — the fleet's only
   RHEL-family host is served by Home Manager user-space — is why they stay
   closed *until the binary ships*, not a refusal.
6. **Perf correction (supersedes the TIN-2695 matrix item #3):** the numeric
   secret pattern is linear, not catastrophic — measured ~25ms/MB, 127ms on
   5MB of all-digit hostile input. The hot-loop mitigation is
   `max_input_bytes` + `wall_clock_budget_ms` caps, not a regex rewrite.
7. **Live-verified redaction bypass (strengthens TIN-2695 item #2):** a
   homoglyph secret (Cyrillic `ѕk-…`) passes `redact_text` with zero
   findings today. NFKC + confusable folding before matching is a hard
   precondition for any enforcement (INV-2).

## 2. IO-layer architecture (the load-bearing decision)

Hybrid, phased, with enforcement ordering **A → C → B** (advisory first,
MCP-scoped gateway second, in-harness replacement last — blast radius
ordering per the purple team, reversing the raw payoff ranking):

- **A. Advisory skills** (today): baseline + permanent fallback on every
  harness. The model still sees raw output; zero enforcement risk.
- **C. MCP condenser stage** — a pluggable transform on TIN-2524's
  `research.call` return path. Do NOT build a parallel Mythos gateway:
  TIN-2524 is design-only today; building our own forks the trust boundary.
  This workstream **blocks on TIN-2524 shipping** and is scoped to
  MCP-sourced output only.
- **B. Claude hook enforcement** — PostToolUse `updatedToolOutput`, starting
  with Task returns only (controlled source set, highest fable-token
  payoff), then Bash/Read/WebFetch. **Hard-gated on TIN-2695 closure and on
  INV-1..INV-6.** Fail-open on condensation (pass raw through on
  error/timeout), fail-closed on redaction, per-tool trust-tier map
  (enforcement surfaces carry better tier signal than the CLI's single
  `--trust-tier`: tool name / upstream server id name the source).

**Phase 0 (ships now, no enforcement, no TIN-2695 dependency):**
`policy/io.json` SSOT (thresholds: `enforce_threshold_tokens`,
`min_savings`, `max_input_bytes`, `wall_clock_budget_ms`; per-surface
enables; per-source trust-tier map; split failure modes) + the
sha256-keyed cache-first condensation store under `state_root()`. The cache
persists raw keyed by `sha256(raw)`, which simultaneously closes the
TIN-2695 provenance re-derivability gap (store_raw=False) — **provided
INV-6 holds**: the cache must be integrity-verified (MAC or re-derivation on
serve) because it lives on a tool-writable filesystem and is otherwise a
permanent poisoning surface. The measured-savings gate reuses
`choose_card_format`'s comparator: never emit a condensation that does not
beat raw by the policy margin — the round trip can never inflate.

**Phase gate spike — VALIDATED 2026-07-09 (TIN-2699, spikes/tin-2699/):**
a PostToolUse hook on the subagent tool CAN rewrite its return through the
condense path before the parent model sees it. Three empirical nuances the
docs don't state: the tool is named `Agent` in Claude Code 2.1.201 (match
`Task|Agent`); matchers are full-match regex; and `updatedToolOutput` must
mirror the tool's structured response shape (content blocks for Agent) — a
plain string is silently dropped. Stories 1/4 and Phase B proceed. TOON
stays off every provenance-bearing enforced path until the encoder carries
sha256+evidence.

## 3. Hard invariants before enforcement (purple-team INV set)

- **INV-1** raw retention: no enforced path while store_raw=False; TOON
  banned as primary on provenance-bearing paths until it carries
  sha256+evidence.
- **INV-2** obfuscation-resistant redaction: NFKC + confusable folding
  before matching; homoglyph regression fixture.
- **INV-3** constraint provenance: `## Critical Constraints` entries carry
  trust_tier + flags (today flags are dropped exactly there), injection-
  shaped spans code-fenced/de-imperativized.
- **INV-4** defang before emission: neutralize markdown image/link/data:/
  javascript: URIs (flagging is not neutralizing).
- **INV-5** split failure modes + hot-path bounds: redaction fail-closed,
  condensation fail-open, `max_input_bytes` + wall-clock caps.
- **INV-6** cache integrity: no cache-first serve of entries that cannot be
  authenticated or re-derived.
- **INV-7** fleet supply chain: rev-pinned prompt-toon input in lab with a
  reviewed/signed bump gate + canary switch, before PR #761 lands
  (branch-following + unattended `nix flake update` = fleet-wide
  instruction-injection lane from one compromised push).
- **INV-8** policy artifact integrity: executable dhall-to-json structural
  equality in tests (substring ID checks are not integrity); fleet
  materialization verifies a checksum of the JSON.
- **INV-9** queue authorization: no queue→execute runner without an explicit
  human gate between dequeue and model invocation; `INJECTION_RE` applied at
  queue time; start with `run --dry-run`. The runner is a **trust-boundary
  decision** (crosses deterministic/no-network), not a feature ticket.
- **INV-10** gateway trust boundary: per-server tier stamping, no tier
  collapse, no "re-open before action" claims for ephemeral sources (that
  rule is decorative until INV-1 holds).

## 4. User stories (today → endgame)

| # | Story | Today | Endgame | Smallest slice |
|---|-------|-------|---------|----------------|
| 1 | Operator pair-engineering (Fable↔SWE interview) | 7 manual steps; routing advisory-only; raw returns flood the synthesis seat; hand-written session notes; captured-but-unread budget_tokens | 1 command: policy-resolved fan-out, pre-condensed returns, auto-attributed note, budget readout | the PostToolUse:Task spike |
| 2 | Fleet onboarding | PR #761 in review; ~2 commands (auth + switch); mythos-delegation skill missing; OpenCode unserved | `home-manager switch` delivers CLI + both skills to every live harness, parity-audited | add mythos-delegation to promptToonSkillFiles + test assertion |
| 3 | External researcher | blocked (private repo); wheel-ready pyproject | one credential-free command (pipx/public flake) + de-tinylanded policy template | `pip wheel .` proof; defer until visibility decision |
| 4 | Subagent self-serve (501 dir) | binary absent from subagent PATH on this very machine; no threshold contract | return path auto-condenses above threshold | `prompt-toon doctor` from inside an ephemeral subagent shell |
| 5 | Managed rules update | hand-sync Dhall→JSON; no content-level drift check; zero lab propagation wiring | one command: regenerate, test, version-bump, propagate, parity-confirm | dhall-json in devshell + structural equality test (INV-8) |
| 6 | Queue/spool consumer | queue/stage are pure file-writers; no runner by design | gated runner with human authorization (INV-9) | `run --dry-run` printing the would-be command |

Cross-cutting risk: stories 1/4/6 assumed an interception point that only
the spike can prove; story 6's endgame needs an explicit operator decision
on the deterministic/no-network boundary (recommended: the runner lives in
an orchestrator-side wrapper, not in this CLI).

## 5. Fleet propagation (managed rules channel)

- **Distribution**: lab HM only (ansible is system-layer). CLI via the
  three-hop chain (flake input → overlay → home.packages); skills + policy
  via ai-skills.nix home.file. Both in PR #761 modulo gaps above.
- **Parity**: `mythos-policy-parity` check cloned from
  `agent-cost-ledger-audit.py`'s triad (`run_agent_parity` →
  `actual_routes_from_parity` → `route_alignment_errors`): declared =
  `policy/delegation.json` lanes; actual = OMO agent/category models from
  nix-eval. Scope initially to OMO/OpenCode — Claude Code has no per-lane
  routing surface to diff yet. Long-term: regenerate OMO's
  `oh-my-openagent.jsonc` personas FROM delegation.json to kill the
  two-schema drift.
- **Versioning**: root flake inputs are manual + Renovate-excluded; the
  scoped bump landed as lab's `just bump-prompt-toon <rev>` (sed pin →
  `nix flake update prompt-toon` → `ai-skills-module-test`), rev-pinned per
  INV-7 with canary-before-fleet guidance inline. Post-C2, fleet
  propagation shifts from flake-input rev-pin to a bazel-registry version
  bump (registry-first per TIN-1306).

## 6. What was declined, and why (do not re-propose without new facts)

- **rules_dhall / Dhall-in-Bazel**: no viable upstream; disproportionate for
  one policy file; executor-backed RBE (`--remote_local_fallback=false`)
  makes a niche Haskell binary in the action graph strictly riskier than
  the existing python3 dependency.
- **rpm / brew / bazel-registry channels**: ~~no concrete persona (single
  Rocky host served by HM user-space)~~ **SUPERSEDED 2026-07-09** — now
  phase gates keyed to the single-binary artifact (see §1.5 amendment and
  §7); the old rationale is retained as the reason they stay closed until
  the binary exists.
- **A standalone Mythos MCP gateway**: forks TIN-2524's design and doubles
  the trust surface; condensation becomes a pluggable stage on TIN-2524.
- **Chapel acceleration**: ~~refuted at current scale; revisit only on
  demonstrated bulk fan-in volume, via nix+chpl (never Bazel), behind
  `--engine=chapel`~~ **SUPERSEDED 2026-07-09 by the Chapel-first operator
  decision** (see §7). `--engine=chapel` survives as the C1 opt-in stepping
  stone, not the endgame. The "never Bazel" rider is replaced by phased
  Bazel adoption: Makefile/Mason dev loop through C1, `chapel_binary`
  genrule walking skeleton over nix-provided `chpl` at C2 (lab doctrine:
  nix owns versions, Bazel validates/bundles), house `rules_chapel`
  extraction + tinyland bazel-registry publication post-C2. Compilation
  iteration is **remote-only** (GloriousFlywheel REAPI where armed, nix
  x86_64-linux remote builders otherwise) — no local darwin chpl loop.
- **Dhall-in-Bazel remains declined** — the §6 first bullet is NOT touched
  by the Chapel-first amendment. The policy/ SSOTs (delegation, io) and
  their Dhall→JSON generation path are likewise unaffected.

## 7. Chapel-first endgame (operator decision, 2026-07-09)

A `ptoon` Chapel binary becomes prompt-toon; the Python implementation
retires into the golden-file parity oracle. Rationale: the single
static-ish binary supplies the missing delivery persona (rpm / brew /
bazel-registry become trivially derivable) and makes Chapel the core
streaming/transformation language. Phases, each shipping independently
with the enforced TIN-2699 hook path Python-backed and byte-identical
until C3:

- **C0 (TIN-2707)**: normalize+redact spike, utf8proc NFKC (vendored
  v2.9.0, Unicode 15.1 = CPython 3.13) + RE2 secret patterns; parity
  corpus incl. the `\b` ASCII-vs-Unicode boundary cases and PEM-split;
  benchmark vs the 25ms/MB Python baseline; kill criteria recorded in
  `spikes/tin-2707/README.md`.
- **C1 (TIN-2708)**: `libptoon` C ABI behind `--engine=chapel` (ctypes,
  fail-open to Python per INV-5); shared `fixtures/` golden corpus;
  quickchpl property tests.
- **C2 (TIN-2709)**: standalone `ptoon` full parity + `--stream`
  (bounded-memory, incremental sha256, budget stopwatch; redaction stays
  buffered within `max_input_bytes` — PEM cannot be line-windowed);
  iocache HMAC parity; hook canary; Bazel walking skeleton + TIN-2706
  packaging manifest.
- **C3 (TIN-2710)**: flip defaults, demote Python to oracle, extract and
  publish `rules_chapel` (first-of-kind), open brew/rpm lanes.
