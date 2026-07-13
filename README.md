# prompt-toon

Private local tool and agent skill for safe research condensation.

`prompt-toon` turns noisy subagent/research output into a durable handoff:
`summary.md`, `source-cards.jsonl`, and `manifest.json`. It is deterministic by
default: no LLM call, no network call, no hidden transport change.

Since v0.2.0 the hot path is Chapel-first: a standalone `ptoon` process
(native remote-built Nix outputs) owns normalize/redact/defang, the coforall
batch fan-in, and the full condense rendering — proven byte-identical to the
Python oracle by the parity gates. Python remains the oracle and the default
engine until the C3 flip (TIN-2710).

The installed v0.2.0 fleet surface remains advisory and predates `ptoon serve`
and the provider gateway. Current v0.3.0 source contains the C4a resident
transform boundary plus C4b Anthropic and C4c OpenAI Responses shadow
gateways plus the C4d managed-consumption source contract, but no v0.3.0
release or fleet service profile exists yet. C4e adds provider-free local
spool dogfooding and an efficiency ledger; IO enforcement remains locked.

TOON support is deliberately narrow. The tool measures flat uniform row sets
against compact JSON/JSONL and emits TOON only when explicitly requested or when
`--format auto` proves a material savings.

## Quick Start

```sh
direnv allow
just check
just prompt-toon doctor        # local policy, engine, gateway, and route state
```

Condense files (Python engine by default; `--engine chapel` fails closed,
`--engine auto` fails open):

```sh
just prompt-toon condense docs/founding-prompt.md --output-dir /tmp/prompt-toon-demo
```

Analyze JSON rows:

```sh
just prompt-toon analyze path/to/results.json
```

Dogfood a durable multi-document spool without contacting a provider:

```sh
just dogfood path/to/spool/ \
  --mythos-route mythos.synthesis \
  --model-label gpt-5.6-sol
```

The run writes `summary.md`, authoritative `source-cards.jsonl`,
`manifest.json`, optional `source-cards.toon`, and `efficiency.json` under the
state directory. Token figures are explicitly labeled lexical estimates, not
provider usage or billing telemetry. A separate whole-handoff gate prevents a
TOON-vs-JSONL win from being presented as an end-to-end prompt win. See
`docs/dogfood-efficiency.md`.

## The `ptoon` binary (Chapel engine)

Built remote-only (`just build-ptoon`; never local `chpl` — see AGENTS.md).
Subcommands: `normalize | defang | redact | redact-batch | condense-batch |
condense | serve | caps`. The batch/stream surfaces are length-prefix framed on
stdin; `condense-batch` and `condense` emit JSONL events in input order,
while `redact-batch` emits length-prefixed per-document results (one
meta-JSON line, then raw redacted bytes — see `src/ptoon/Batch.chpl`). These
remain bounded one-shot commands. `serve` keeps one Chapel runtime alive,
drains a bounded request ring with fixed workers, and returns out-of-order
length-prefixed responses keyed by request and stream IDs. Provider HTTP,
credentials, and SSE never enter this process. Every
policy breach withholds fail-closed — raw text is never emitted (INV-5).
`prompt_toon/engine.py` resolves the binary via `$PROMPT_TOON_PTOON`, then
`build/ptoon` at the repo root (never PATH), and cross-checks all stream
output against what was framed.

`prompt_toon/resident.py:ResidentEngine` owns the child, enforces the typed IO
policy ceilings, bounds active streams, continuously drains stdout/stderr, and
validates each response against the framed provenance before completing its
future. Defaults are 64 streams, 16 workers, a 64-request ring, 64 documents,
16 MiB request / 256 MiB response frames, 4096-byte labels, 2 MiB per input,
2 seconds, and 24 cards per document. The child runtime is pinned to two Chapel
worker threads; concurrency remains owned by the one resident Chapel process.

`hooks/post_tool_condense.py` is a source-only PostToolUse adapter. It is
policy-gated by `policy/io.json` (enforcement ships locked), cache-first via
the HMAC-authenticated iocache (INV-6), and derives through `ptoon condense`.
It is not in the packaging manifest or any harness registration.

## C4 boundary

C4 (TIN-2790) keeps provider transport out of Chapel. C4a establishes the
resident transform service; C4b adds the opt-in Anthropic Messages shadow
gateway; C4c adds OpenAI Responses and a user-level Codex profile; C4d adds
split local/provider credential custody, authenticated instance ownership,
two native platform targets, and a drift-gated Home Manager consumption
contract. Python owns
HTTP, auth/header forwarding, errors, and SSE; `ptoon serve` owns a fixed pool
of bounded transform workers over private framed pipes. Only typed,
provenance-bearing context is eligible for transformation. Authority-bearing
request bytes, including instructions, approval state, tool schemas, and
provider controls, remain unchanged. See `docs/linear.md` for C4a-e.

C4e is an offline adoption-measurement lane. With `--engine chapel`, one
`ptoon condense` process owns a bounded coforall fan-in across at most 64
documents. `--engine auto` uses that path when the binary is available and
records an explicit Python-oracle fallback otherwise. It does not claim the
resident 64-stream production path, which remains covered by
`just gateway-capacity`.

## Gates

- `just check` — compile/secrets/tests + Bazel graph and test (includes the
  packaging-manifest drift gate).
- `just dogfood <files-or-directories...>` — provider-free spool replay with
  output hashes, local byte/token estimates, TOON selection evidence, engine
  identity, and caller-observed Mythos/model labels.
- `just gateway-harness-probe` — unbilled real-Claude-Code/SSE gateway proof
  plus local readiness, same-port rebind, and profile-rendering checks against
  a scripted loopback upstream.
- `just responses-gateway-harness-probe` — unbilled real-Codex/SSE proof of
  the user profile, correlated function-call output, request ceiling, and
  direct rollback against a scripted loopback upstream.
- `just gateway-capacity` — scripted-loopback HTTP/SSE saturation over the
  real resident child; the remote parity derivation requires 64/64 streams for
  both providers, near-cap aggregate ingress residency, deterministic overload,
  split auth, and process-isolated gateway/resident resource bounds. Scripted
  clients and the upstream run outside the measured gateway process.
- Remote parity derivation (`make parity`): functions 48/48, condense 34/34,
  redact-batch, stream + condense-run vs goldens, resident multiplex parity,
  64-stream RSS capacity, analyze vs the committed pinned baseline
  (`tests/goldens/analyze/`), hook canary. Runs at release time via the
  `just release` preflight.

## Releases & packaging

`packaging/manifest.json` is the committed, drift-gated packaging SSOT
(TIN-2706): version, skills, policy digests, and per-lane enablement all
derive from it. A release bumps `prompt_toon/__init__.py` (+ MODULE.bazel),
regenerates via `just manifest`, merges, then `just release X.Y.Z` — Linux
parity, native remote Linux/Darwin builds, stamped manifest, wheel, and full
Nix closure exports. The tagged flake is the canonical online install path;
the `.nar` assets import with `nix-store --import`. Raw `ptoon` executables are
Nix-store linked and are not claimed portable. The stamped manifest authenticates
both each closure export and its `bin/ptoon` entrypoint. Fleet install will ride the lab
Home Manager module (rev-pinned per INV-7).

The v0.2.0 release asset does not implement `ptoon serve` and cannot back the
C4 gateway. v0.3.0 is the first release line whose manifest declares
per-target `serve_protocol = 1` and `anthropic_shadow_gateway = 1`
plus `openai_responses_shadow_gateway = 1` capabilities. Manifest schema v2
identifies ptoon targets by `(artifact, platform)` and authenticates complete
Linux and Darwin Nix closure exports, their entrypoint binaries, and the universal wheel.
Source version preparation is not a release claim until the tag and stamped
manifest exist.

## Project Surfaces

- CLI package: `prompt_toon/` · Chapel engine: `src/ptoon/` + `c_src/`
- Hook adapter: `hooks/post_tool_condense.py`
- Skills: `.agents/skills/{prompt-toon,mythos-delegation}/`
- Policy SSOTs: `policy/{delegation,io}.json` (Dhall sources: `policy/dhall/`)
- Packaging SSOT: `packaging/manifest.json` (`tools/packaging/gen_manifest.py`)
- Managed-consumption contract: `packaging/home-manager.json` and
  `docs/home-manager-adoption.md`
- Design record: `docs/mythos-delivery-design.md` · Linear map: `docs/linear.md`
- Dogfood measurement contract: `docs/dogfood-efficiency.md`
- Provider guides: `docs/anthropic-shadow-gateway.md` ·
  `docs/openai-responses-gateway.md`
- Founding prompt: `docs/founding-prompt.md` · Vision: `docs/mythos-vision.md`

## Operating Position

- Default data format for arbitrary nested data: compact JSON or JSONL.
- Default prompt boundaries: Markdown/XML-style sectioning.
- Default model-output contract: structured JSON where provider support exists.
- TOON: optional for flat uniform scalar rows only.
