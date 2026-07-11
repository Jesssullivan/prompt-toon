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
condense | caps`. The batch/stream surfaces are length-prefix framed on
stdin; `condense-batch` and `condense` emit JSONL events in input order,
while `redact-batch` emits length-prefixed per-document results (one
meta-JSON line, then raw redacted bytes — see `src/ptoon/Batch.chpl`). Every
policy breach withholds fail-closed — raw text is never emitted (INV-5).
`prompt_toon/engine.py` resolves the binary via `$PROMPT_TOON_PTOON`, then
`build/ptoon` at the repo root (never PATH), and cross-checks all stream
output against what was framed.

`hooks/post_tool_condense.py` is the PostToolUse adapter: policy-gated by
`policy/io.json` (enforcement gate ships locked), cache-first via the
HMAC-authenticated iocache (INV-6), derives through `ptoon condense`, and
fails open — the session sees the raw return unless a provably-better,
fully-redacted summary exists.

## Gates

- `just check` — compile/secrets/tests + Bazel graph and test (includes the
  packaging-manifest drift gate).
- Remote parity derivation (`make parity`): functions 48/48, condense 34/34,
  redact-batch, stream + condense-run vs goldens, analyze vs the committed
  pinned baseline (`tests/goldens/analyze/`), hook canary. Runs at release
  time via the `just release` preflight.

## Releases & packaging

`packaging/manifest.json` is the committed, drift-gated packaging SSOT
(TIN-2706): version, skills, policy digests, and per-lane enablement all
derive from it. A release bumps `prompt_toon/__init__.py` (+ MODULE.bazel),
regenerates via `just manifest`, merges, then `just release X.Y.Z` — parity
preflight, remote binary build, stamped manifest (provenance + binary
sha256), tag, GitHub Release. Fleet install rides the lab home-manager module
(rev-pinned per INV-7; `bump-prompt-toon` recipe there).

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
