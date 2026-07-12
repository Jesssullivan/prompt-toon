# prompt-toon

Private local tool and agent skill for safe research condensation.

`prompt-toon` turns noisy subagent/research output into a durable handoff:
`summary.md`, `source-cards.jsonl`, and `manifest.json`. It is deterministic by
default: no LLM call, no network call, no hidden transport change.

Since v0.2.0 the hot path is Chapel-first: a standalone `ptoon` binary
(x86_64-linux, built remote-only) owns normalize/redact/defang, the coforall
batch fan-in, and the full condense rendering — proven byte-identical to the
Python oracle by the parity gates. Python remains the oracle and the default
engine until the C3 flip (TIN-2710).

The installed v0.2.0 fleet surface remains advisory and predates `ptoon serve`
and the provider gateway. Current v0.3.0 source contains the C4a resident
transform boundary and C4b Anthropic shadow gateway, but no v0.3.0 release or
fleet service profile exists yet. IO enforcement remains locked.

TOON support is deliberately narrow. The tool measures flat uniform row sets
against compact JSON/JSONL and emits TOON only when explicitly requested or when
`--format auto` proves a material savings.

## Quick Start

```sh
direnv allow
just check
just prompt-toon doctor        # reports both engines; chapel needs the binary
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
2 seconds, and 24 cards per document.

`hooks/post_tool_condense.py` is a source-only PostToolUse adapter. It is
policy-gated by `policy/io.json` (enforcement ships locked), cache-first via
the HMAC-authenticated iocache (INV-6), and derives through `ptoon condense`.
It is not in the packaging manifest or any harness registration.

## C4 boundary

C4 (TIN-2790) keeps provider transport out of Chapel. C4a establishes the
resident transform service; C4b adds the opt-in Anthropic Messages shadow
gateway. Python owns HTTP, auth/header forwarding, errors, and SSE; `ptoon
serve` owns a fixed pool of bounded transform workers over private framed
pipes. Only typed, provenance-bearing context is eligible for transformation.
Authority-bearing request bytes, including instructions, approval state, tool
schemas, and provider controls, remain unchanged. See `docs/linear.md` for
C4a-d.

## Gates

- `just check` — compile/secrets/tests + Bazel graph and test (includes the
  packaging-manifest drift gate).
- `just gateway-harness-probe` — unbilled real-Claude-Code/SSE gateway proof
  plus local readiness, same-port rebind, and profile-rendering checks against
  a scripted loopback upstream.
- Remote parity derivation (`make parity`): functions 48/48, condense 34/34,
  redact-batch, stream + condense-run vs goldens, resident multiplex parity,
  64-stream RSS capacity, analyze vs the committed pinned baseline
  (`tests/goldens/analyze/`), hook canary. Runs at release time via the
  `just release` preflight.

## Releases & packaging

`packaging/manifest.json` is the committed, drift-gated packaging SSOT
(TIN-2706): version, skills, policy digests, and per-lane enablement all
derive from it. A release bumps `prompt_toon/__init__.py` (+ MODULE.bazel),
regenerates via `just manifest`, merges, then `just release X.Y.Z` — parity
preflight, remote binary build, stamped manifest (provenance + binary
sha256), tag, GitHub Release. Fleet install rides the lab home-manager module
(rev-pinned per INV-7; `bump-prompt-toon` recipe there).

The v0.2.0 release asset does not implement `ptoon serve` and cannot back the
C4 gateway. v0.3.0 is the first release line whose manifest declares
per-target `serve_protocol = 1` and `anthropic_shadow_gateway = 1`
capabilities. The release lane builds, installs, hashes, and publishes both the
binary and universal wheel. Source version preparation is not a release claim
until the tag and stamped manifest exist.

## Project Surfaces

- CLI package: `prompt_toon/` · Chapel engine: `src/ptoon/` + `c_src/`
- Hook adapter: `hooks/post_tool_condense.py`
- Skills: `.agents/skills/{prompt-toon,mythos-delegation}/`
- Policy SSOTs: `policy/{delegation,io}.json` (Dhall sources: `policy/dhall/`)
- Packaging SSOT: `packaging/manifest.json` (`tools/packaging/gen_manifest.py`)
- Design record: `docs/mythos-delivery-design.md` · Linear map: `docs/linear.md`
- Founding prompt: `docs/founding-prompt.md` · Vision: `docs/mythos-vision.md`

## Operating Position

- Default data format for arbitrary nested data: compact JSON or JSONL.
- Default prompt boundaries: Markdown/XML-style sectioning.
- Default model-output contract: structured JSON where provider support exists.
- TOON: optional for flat uniform scalar rows only.
