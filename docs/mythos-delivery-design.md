# Toon of Mythos — Delivery Design

Date: 2026-07-09; C4 amendment accepted 2026-07-11 (TIN-2790)
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
   fan-out returns. `PostToolBatch` does not widen that replacement surface:
   its current hook output can add `additionalContext`, but cannot replace the
   completed batch. Per-tool replacement remains a PostToolUse operation.
   Codex CLI parses but does not support
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
| 1 | Operator pair-engineering (Fable↔SWE interview) | v0.2.0 is installed but advisory; IO policy is locked; raw returns still reach the model; the PostToolUse adapter is source-only, unpackaged, and unregistered | opt-in gateway condenses typed context before fable synthesis while preserving authority bytes | C4b Claude shadow gateway (TIN-2793) |
| 2 | Fleet onboarding | Home Manager delivers v0.2.0 plus both skills; no gateway/service profile is active | one reviewed unit delivers service, profile, policy, and platform binary, parity-audited | C4d HM shadow rollout (TIN-2791) |
| 3 | External researcher | blocked (private repo); wheel-ready pyproject | one credential-free command (pipx/public flake) + de-tinylanded policy template | `pip wheel .` proof; defer until visibility decision |
| 4 | Subagent self-serve (501 dir) | one-shot framed `ptoon condense`/`condense-batch` exists; there is no durable transform service or registered hook | bounded resident transforms behind provider-owned transport | C4a `ptoon serve` (TIN-2792) |
| 5 | Managed rules update | Dhall→JSON structural tests and manifest drift gate landed; regeneration still degrades to hand-sync when Dhall tooling is absent | one command: regenerate, test, version-bump, propagate, parity-confirm | keep reviewed HM version/policy bumps |
| 6 | Queue/spool consumer | queue/stage are pure file-writers; no runner by design | gated runner with human authorization (INV-9) | `run --dry-run` printing the would-be command |

Stories 1/4 now split at the C4 boundary: the provider gateway identifies
typed transformable context and preserves authority-bearing bytes; the
resident `ptoon` child performs bounded transforms. Story 6 still needs an
explicit operator decision on the deterministic/no-network boundary.

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
  Bazel adoption: Makefile/nix remote-build loop through C1, `chapel_binary`
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
- **C1 (TIN-2708)**: standalone `ptoon` binary behind `--engine=chapel`
  (subprocess, fail-open to Python per INV-5 — pivoted from the original
  original shared-library/ctypes plan, see the "Engine boundary" finding in §8); shared
  `fixtures/` golden corpus; Bazel-drives-chpl walking skeleton on GF REAPI
  (`//src/ptoon:ptoon`, pulled forward from C2); quickchpl property source
  remains advisory until quickchpl is repo-pinned and wired into a remote gate.
- **C2 (TIN-2709)**: `ptoon` streaming/batch + `--stream`
  (bounded-memory, incremental sha256, budget stopwatch; redaction stays
  buffered within `max_input_bytes` — PEM cannot be line-windowed);
  iocache HMAC parity; hook canary; Bazel walking skeleton + TIN-2706
  packaging manifest.
- **C3 (TIN-2710)**: flip defaults, demote Python to oracle, extract and
  publish `rules_chapel` (first-of-kind), open brew/rpm lanes.

## 8. C2 streaming + fan-in architecture (Chapel feature mapping)

Grounded in the 2026-07-09 research spool (Chapel 2.9 capability survey +
opus parallel-IO design lane + opus purple-team critique). The user flow it
serves: a wide research spool returns N subagent outputs near-simultaneously
(typically 5–16); every one must flow through `ptoon` condensation —
fail-closed redaction, provenance-bearing cards — before the expensive
mythos/fable synthesis seat sees any of them.

**Parallelism value is coupled to the deployment surface** (not intrinsic):

- **Claude PostToolUse (surface B, the `Agent` tool, TIN-2699):** each
  subagent return fires an *independent* hook process. The N-way concurrency
  is N separate short-lived `ptoon` subprocess invocations, each
  single-threaded. Chapel task-parallelism does nothing here; the metric is
  per-process runtime init + RE2 compile + utf8proc-table amortization. Chapel
  2.9's initial dynamically loaded parallel-library support
  (`--library --dynamic --no-builtin-runtime`) is the future lever for this
  init cost, but it remains an upstream "initial support" feature and stays
  post-C2 research, not the C1 boundary.
- **MCP gateway stage (surface C, TIN-2524):** the gateway owns transport and
  submits typed MCP return segments for transformation. Today's
  `condense-batch` command handles one framed batch and exits. C4a's planned
  resident `ptoon serve` process amortizes initialization across requests.
- **Claude Code `PostToolBatch` (surface B-batch, harness delta July 2026):**
  the current hooks reference documents `PostToolBatch` — fires once after a
  batch of tool calls completes, before the next model turn, no matcher
  (always fires). Its hook output can add `additionalContext` for the next
  model turn; it cannot use `updatedToolOutput` to replace the completed
  batch. It is therefore an advisory context surface, not a batch enforcement
  path. Replacement remains per-tool PostToolUse; the `Task` versus `Agent`
  matcher name still requires a live installed-version probe.

### Build for C2/C3 (earn their complexity)

1. **`coforall` batch entrypoint — LANDED as `ptoon redact-batch`
   (TIN-2709 C2b, src/ptoon/Batch.chpl).** One qthreads task per document, full
   sequential redaction independent per doc, N docs read from one framed batch.
   The literal fan-in case (no Amdahl ceiling); it **confines all concurrency
   inside the Chapel runtime** of a single `proc main` process — strictly safer
   than N external host threads re-entering a runtime that assumes it owns its
   workers. (The subprocess-binary pivot is what makes this the natural shape
   rather than a C-ABI batch export.)
   - **Wire format = length-prefixed, not JSON-string-escaped.** stdin:
     `<n>\n` then per doc `<byteLen>\n<raw bytes>`; stdout: `<n>\n` then per doc
     `<metaJson>\n<redactedByteLen>\n<redacted bytes>`, in input order. Length
     prefixes carry embedded newlines with zero escaping — a redacted PEM block
     spans lines, so line-delimiting cannot frame it. Read via one
     `stdin.readAll` + a hand-walked byte cursor (no incremental-IO dependency).
   - **Fail-closed (INV-5):** a doc whose redaction throws is emitted as
     `{"i":I,"withheld":true,"reason":...}` with redacted length 0 and NO
     bytes — its raw text is never emitted.
   - **Proven:** `tools/batch_parity.py` gate — `redact-batch` is byte-identical
     to per-document `redact` on all 18 fixtures (findings + redacted bytes),
     incl. PEM-split (08), embedded-NUL/CRLF (11), and the F1 injected-literal
     rescan (18). Order-independent. `engine.py:redact_batch` consumes it.
2. **Hoist the 9 redaction regexes to module-level `const` — LANDED
   (TIN-2709 C2a, Redact.chpl `const gens`).** Was compiling per `redactText`
   call (9 × N per batch); now compiled once at init, shared read-only across
   `coforall` tasks. RE2 objects are immutable and `regex.search` takes
   `const ref this`, confirmed concurrent-safe under the batch gate (RE2
   `regex.search` is a const method under `coforall` stress).
3. **Runtime lifecycle is `proc main`, not manual `chpl_library_init`.** The
   subprocess pivot (below) obviates the ctypes-era init/finalize handshake:
   the binary's `proc main` brings the runtime up and tears it down per
   invocation. Keep `CHPL_COMM=none` to preserve the single-static-ish-binary
   packaging goal (§7). The remaining per-process init cost (RE2 + utf8proc
   tables) is what the batch/stream entrypoint amortizes across N docs.
4. **ASCII fast-path guard — LANDED** (TIN-2709 C2a, Normalize.chpl): `if c >=
   0x80` before the confusable map probe (all 45 keys are Greek/Cyrillic,
   ≥ U+0391). One-line hot-loop win; byte-identical to the unguarded lookup.

### Budget + input cap — LANDED on `redact-batch` (TIN-2709 C2c)

`ptoon redact-batch` takes two optional positional policy args (argv[2]
`maxInputBytes`, argv[3] `budgetMs`; 0/absent = unlimited, so C2b parity is
untouched). Policy args are **fail-closed**: malformed, negative, extra, or
budget-without-cap args exit nonzero before any output. Valid breaches withhold,
never emit raw:

- **Input cap:** a document whose input exceeds `maxInputBytes` is withheld
  (`reason:"input-cap"`) *before* redaction — never truncated-and-emitted, since
  a truncated PEM could leak its tail.
- **Wall-clock budget — completion-time withhold, stated honestly.** Chapel
  `coforall` tasks cannot be preempted mid-run, so this is NOT a hard mid-task
  abort. But redaction is linear-time (RE2) and input-capped, so a single
  document is bounded; `budgetMs` therefore requires a positive `maxInputBytes`.
  The budget guards *aggregate* wall-clock. A document that *completes* past the
  batch deadline is withheld (`reason:"budget"`), its redacted text discarded.
  Rejected the alternative (process-level subprocess kill in engine.py) as worse
  fail-closed granularity — one straggler would lose every document's result.

### `--stream` surface — LANDED as `ptoon condense-batch` (TIN-2709 C2d)

Given the whole-buffer redaction constraint (a PEM block spans lines;
`[\s\S]+?` needs the full input), the stream surface is **bounded-buffering
(≤ `maxInputBytes`) + raw-bytes sha256 + the budget backstop above**, with
true streaming only on the *output* side. It is not a pipeline through
redaction. Say so plainly. As landed:

- **Input** (`Stream.chpl:parseStreamBatch`): length-prefixed
  source/trust_tier/body TRIPLETS — `<n>\n` then per doc three
  `<len>\n<bytes>` fields. Same framing philosophy as `redact-batch` (length
  prefixes carry anything with zero escaping); no JSON parsing exists in the
  binary. Labels decode strictly (malformed frame ⇒ process abort); the body
  decodes inside the per-doc task (bad UTF-8 withholds that doc only).
- **Output**: JSONL events in input order — per doc
  `{"event":"doc",i,source,trust_tier,bytes,sha256,withheld,findings[,reason]}`,
  then (non-withheld only) `{"event":"card",i,card:{…}}` per card, then
  `{"event":"end",i,cards}`; one trailing `{"event":"batch",docs,cards,withheld}`.
  The `card` object is **byte-identical** to Python's source-cards.jsonl line
  (json.dumps sort_keys, ensure_ascii=False, default separators) — Cards.chpl
  reproduces Python splitlines (NEL/LS/PS survive normalize!), Unicode-\b via
  Redact.pySearchBounded, codepoint-counted claim truncation, and Python JSON
  string escaping. Stream records carry **no timestamps and no run ids**:
  output is a pure function of (input, policy args).
- **Engine parser** (`prompt_toon/engine.py:_parse_stream`) treats that JSONL
  as a closed event grammar, not an open bag of fields: doc/card/end/batch
  event keys are allowlisted, bools are not accepted as ints, doc metadata
  must match the framed input (`source`, `trust_tier`, raw byte count, raw
  sha256), card provenance must match the enclosing doc, withheld docs must
  carry an allowed non-empty reason with empty findings and no cards, and card
  count may not exceed `maxCards`. Malformed streams raise `EngineError`
  rather than returning partial or body-derived data.
- **Output-side streaming, honestly scoped**: doc i's events are written as
  soon as doc i completes and docs 0..i-1 have been written (sync-slot array +
  a concurrent writer task draining in input order while later docs compute).
- **sha256** (Sha256.chpl): vendored public-domain B-Con implementation
  (c_src/sha256*.c, utf8proc vendoring pattern), digesting the RAW body bytes
  before any policy decision — a withheld doc is still identifiable by digest
  (INV-1: the digest is the re-derivability key).
- **Policy**: argv[2] `maxInputBytes` / argv[3] `budgetMs` exactly as
  `redact-batch` above, plus argv[4] `maxCards` (absent = 24, must be
  positive). Same fail-closed rejection matrix; a per-doc failure of any kind
  withholds with `reason:"condense-error"` — cards, claims, and raw text are
  never emitted for a withheld doc (INV-5).
- **Gate**: `tools/stream_parity.py` (in the ptoon-parity derivation, remote
  builder): per fixture, chapel cards ≡ python-oracle `cards_from_text`
  (sha256 + findings + every card object) AND solo-batch ≡ 20-doc-batch
  results (18 generated fixtures + U+1680 whitespace parity edges for
  Python `strip()` and `\s`; fan-in isolation). The gate also compares raw
  nested `card` JSON bytes directly from Chapel's JSONL stream, plus
  malformed-frame, bad-body-withholding, and policy-arg preflights.
  Verdict line `STREAM PARITY: PASS`. iocache HMAC parity remains (next
  slice); summary/manifest landed as `ptoon condense` (C2e, below).

### Run-level artifacts — LANDED as `ptoon condense` (TIN-2709 C2e)

`condense` = `condense-batch` plus a five-field run header the caller
supplies and the binary only ECHOES (determinism doctrine: nothing time- or
identity-shaped is computed in-binary): `runId`, `generatedAt`,
`minToonSavings` (raw JSON number string), `defaultTier`,
`tierOverridesJson` (pre-serialized object). Header echoes that splice into
JSON verbatim are grammar-checked fail-closed (no control chars — a raw
newline would break JSONL framing; RFC 8259 JSON-number grammar;
brace-delimited object).
After the per-doc events, the binary emits `{"event":"summary","text":…}`
(Summary.chpl `renderSummary`, BYTE-exact vs Python `render_summary` — the
golden's regex mask is a no-op when the gate passes
`generatedAt=GENERATED_AT`) and `{"event":"manifest","manifest":{…}}`
(VALUE-exact: gen_golden's structural mask re-serializes through Python's
json module, so compact sorted-key emission suffices), then the batch tally.
Two more Python-`\s` ports ride in Summary.chpl as manual codepoint scans
(the C2d U+1680 lesson): `OPEN_QUESTION_RE` and `rough_token_count` (the
`format_analysis.jsonl_tokens` source). engine.py `condense_run` binds the
run artifacts back to what was framed: id/generated_at echoes, `inputs[]`
against per-doc sha256/bytes/source/tier, `mixed_trust_tiers`, and every
settings echo — a drifted manifest raises, never returns. Gate: the
stream-parity derivation now replays all 17 `gen_golden` CONDENSE_CASES
(16 solo + the mixed-tier composite) through `ptoon condense` and diffs
summary.md (byte), manifest.json (masked-structural), and a reassembled
source-cards.jsonl (byte) against the existing python-oracle goldens, plus a
malformed-run-header preflight.

### iocache + hook adapter — LANDED, cache stays Python-owned (TIN-2709 C2f)

**Decision (the plan's sanctioned fallback, recorded honestly): the HMAC
cache is NOT reproduced in Chapel.** The binary's stream output is a pure
function of (input, policy, run header) — the perfect cache VALUE; key
derivation, digest-map canonicalization, and the HMAC (plus its 0600
store-local secret) stay in prompt_toon/iocache.py, whose on-disk format is
now pinned by literal goldens (tests/test_iocache.py: exact entry_key
string, exact MAC hex under a fixed key) so drift is a reviewed decision,
never an accident. Putting secret handling inside the binary would widen
its surface for zero throughput gain — caching is an orchestration concern.

`hooks/post_tool_condense.py` is the production-shaped hook (the TIN-2699
spike stays as the live-firing evidence probe): policy-gated on io.json's
enforcement gate + `subagent` surface (flipping the fleet policy remains a
Dhall-side operator change, INV-8), Task/Agent text extraction (the spike's
proven contract), token-threshold + beats_margin gates (never inflates, by
construction), iocache-first with authenticated serve and
quarantine-on-tamper (INV-6), derivation via ChapelEngine.condense_run with
the policy's cap/budget as the binary's fail-closed args, deliberate
NO-Python-fallback (binary absent ⇒ fail open, raw passes), a withheld doc
⇒ no rewrite (nothing half-redacted is ever emitted), JSONL audit trail.

Gate: tools/hook_canary.py in the ptoon-parity derivation — locked-policy
no-op, binary-absent fail-open, rewrite shape (provenance header, INV-3
tier tags, INV-4 defang), planted-secret never-leak, miss→hit identical
bytes, tamper→quarantine→re-derive. Verdict `HOOK CANARY: PASS`. Residual
(operator-side): the live-harness PostToolUse matcher probe (Task vs Agent
naming) per the Sec8 note, and the Dhall policy flip itself.

### Packaging-SSOT manifest — LANDED (TIN-2706, TIN-2046 pattern)

`packaging/manifest.json` is the packaging truth the install lanes derive
from, COMMITTED and drift-gated (the dhall→json artifact pattern: the only
writer is `tools/packaging/gen_manifest.py`; `//tools/packaging:
manifest_drift_test` regenerates + byte-diffs inside `bazel test //...`, so
drift is a required-lane CI failure). Version SSOT = `prompt_toon/
__init__.py:__version__` — pyproject derives it (setuptools dynamic), the
nix derivations read it from the manifest (`lib.importJSON`), the shipped
skill set is the manifest's `skills[]`, and the one residual static
declaration (MODULE.bazel, cosmetic until the registry lane opens) is
asserted equal by the generator. `policy[]` carries sha256 digests of the
delegation/io SSOT artifacts, tying packaging integrity to INV-8.
DETERMINISM SPLIT: the committed manifest is a pure function of repo
content (`git_rev: UNSTAMPED`, targets[] sha256 null); the release lane
re-runs the generator with `--git-rev/--tag/--ci-run/--with-binary` to
stamp provenance and inject the real ptoon digest — GH-Release
`source.json`, brew, and nfpm lanes consume the stamped emission. Lane
flags in `derived_lanes`: nix/HM enabled; bazel-registry + GH Release
phase gates OPEN since C2 (static binary exists) but not yet built;
brew/rpm stay C3-gated.

### Decline (do not re-propose without new facts)

- **Parallel pattern-sweep over the original text + disjointness guard.**
  The purple team proved it **unsound** (finding F1): sequentially, pattern
  8 matches the `[REDACTED]` literal that pattern 7 *injects* — e.g.
  `token=a@b.co` → oracle `[REDACTED]` (`pattern-7,pattern-8`), but a
  parallel-over-original pass sees no pattern-8 candidate and the
  disjointness guard greenlights the wrong answer. C2 redaction stays
  **sequential per document**; parallelism is only *across* documents.
  (Regression fixture: `18-injected-literal-rescan.txt`.)
- **Multilocale / GASNet.** Single-node, latency-bound (2 s budget), inputs
  ≤ 2 MB, fan-in ≤ 16 — saturates one host. Multilocale also breaks the
  single-binary distribution goal. Stay single-locale qthreads.
- **Intra-document pipeline parallelism through redaction.** Blocked by the
  whole-buffer PEM constraint; only raw-sha256 ∥ normalize and a `forall`
  card-scan are legal, both marginal.
- **`param`/compile-time RE2 or confusable codegen.** RE2 compiles at
  runtime; `param` only unrolls the 9-iteration loop. The real win is the
  one-line ASCII guard above.

### Engine boundary: subprocess binary, not in-process ctypes (C1 finding)

C1 attempted a ctypes-loaded shared library (`libptoon` + exported `ptoon_*`
procs). It compiled and linked (utf8proc + RE2 statically), init and the
first calls succeeded, but **repeated exported-proc calls segfault on buffer
free** — the Chapel runtime's foreign-thread re-entry model (a host process
calling exported procs that allocate + touch the runtime, then free) is
fragile in exactly the way the purple team flagged (finding f2). Neither the
`chpl_library_init` lifecycle (required, and it removed the first segfault)
nor allocator-consistency (`allocate`/`deallocate` vs libc `malloc`/`free`)
cleared it.

**Resolution — pivot the engine boundary to a standalone `ptoon` binary
invoked as a subprocess.** The C0 spike proved this model runs clean
(`proc main`, stdin→stdout, byte-parity, no segfaults). Today's
`condense-batch` and `condense` commands each consume one framed invocation
and exit; they are not a resident service, and PostToolBatch cannot rewrite
their completed tool batch. The subprocess boundary still lets the C2
`coforall` entrypoint own concurrency **inside** the Chapel runtime instead
of exposing N host-thread re-entries. C4a extends that boundary with a
resident framed-pipe protocol and fixed workers. The Chapel modules
(Normalize/Redact/Defang) are unchanged; only the Abi/ctypes layer is dropped
in favor of a `proc main` dispatcher.
`prompt_toon/engine.py` shells out via `subprocess`; `--engine=chapel` runs
the binary, `auto` falls open to Python when the binary is absent.

### Hard corrections baked in (purple-team)

- **Budget breach is fail-CLOSED, not fail-open.** `io.json` sets
  `redaction: fail_closed`; on a redaction/pipeline budget breach the
  breaching document is **withheld/suppressed**, never passed through raw.
  (Only *condensation* — the savings step — is fail-open.) Passing raw on
  timeout would ship unredacted secrets to the synthesis seat.
- **The process-wide budget is process-wide.** Per-task deadlines under
  `coforall` oversubscription do not bound the `wall_clock_budget_ms` for
  the batch; enforce a single batch deadline with straggler abort.
- **Gate: all TIN-2709 parallelism is gated behind demonstrated sequential
  byte-parity** (all corpus cases IDENTICAL, chapel vs Python oracle). C1's
  `ptoon-parity` derivation is that gate. Do not layer task-parallelism onto
  an engine with any live divergence.

### Chapel/Bazel source grounding (checked 2026-07-11)

- Chapel 2.9 `proc main(args: [] string)` and integer exit status:
  https://chapel-lang.org/docs/technotes/main.html.
- Chapel 2.9 `stdin`, `fileReader.readAll`, incremental `readLine`/`readBinary`,
  and locking standard writers:
  https://chapel-lang.org/docs/modules/standard/IO.html.
- Chapel 2.9 `chpl --fast`, `-M`, and `-o` compiler options:
  https://chapel-lang.org/docs/usingchapel/man.html.
- Chapel 2.9 `require` C-resource linkage, file-relative:
  https://chapel-lang.org/docs/technotes/extern.html.
- Chapel 2.9 library interop caveats and the 2.9 dynamic-library release
  note: https://chapel-lang.org/docs/technotes/libraries.html and
  https://chapel-lang.org/blog/posts/announcing-chapel-2.9/.
- Bazel current platform/compatibility, `manual` tag, `run_shell`, and
  remote-execution rule guidance:
  https://bazel.build/extending/platforms,
  https://bazel.build/reference/be/common-definitions,
  https://bazel.build/rules/lib/builtins/actions, and
  https://bazel.build/remote/rules.
- Bazel current command-line platform flags, RBE overview, and
  `--remote_download_minimal` performance guidance:
  https://bazel.build/docs/user-manual, https://bazel.build/remote/rbe, and
  https://bazel.build/advanced/performance/build-performance-breakdown.

## 9. C4 online IO gateway (accepted 2026-07-11)

**Current baseline:** v0.2.0 is installed and advisory. Enforcement policy
ships locked. The PostToolUse adapter exists in source but is not packaged or
registered with a harness. C4a now adds the source-level resident transform
service; it is not yet in a release or fleet profile.

**Target boundary:** the provider gateway owns HTTP, auth/header forwarding,
provider request/response adaptation, errors, and SSE. A private long-lived
`ptoon serve` child owns only bounded transforms over framed stdin/stdout,
with a fixed worker pool and explicit document, request/response byte,
active-stream, and queue limits. Only typed, provenance-bearing context
segments are transformable.
System/developer/user instructions, approval state, tool schemas, provider
controls, and other authority-bearing request bytes remain unchanged.

Shadow mode precedes enforcement. Optional condensation fails open;
`must_transform` segments fail closed. Claude Messages is first, Codex
Responses second. The capacity gate targets 64 concurrent streams with
bounded RSS/queue depth and deterministic overload behavior. This is distinct
from the MCP-only TIN-2702 stage and the queue-execution decision in TIN-2701.

**C4a fixed defaults:** 64 active streams, 16 transform workers, a 64-slot
request ring, 64 documents/request, 16 MiB request frames, 256 MiB response
frames, 4096-byte labels, 2 MiB/document, a 2-second transform budget, and 24
cards/document. Request policy values may tighten but never expand the launch
ceilings. Well-framed policy violations are request-scoped; malformed outer
framing terminates the service because it cannot be safely resynchronized.

- **Parent TIN-2790:** C4 online prompt-toon IO gateway.
- **C4a TIN-2792:** bounded resident `ptoon serve` protocol and capacity gate.
- **C4b TIN-2793:** Claude Messages shadow gateway with typed-context transforms.
- **C4c TIN-2794:** Codex Responses adapter and user-level provider profile.
- **C4d TIN-2791:** multi-platform binaries and Home Manager gateway rollout.
