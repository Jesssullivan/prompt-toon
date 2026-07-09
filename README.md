# prompt-toon

Private local tool and Codex skill for safe agent research condensation.

`prompt-toon` turns noisy subagent/research output into a durable handoff:
`summary.md`, `source-cards.jsonl`, and `manifest.json`. It is deterministic by
default: no LLM call, no network call, no hidden transport change.

TOON support is deliberately narrow. The tool measures flat uniform row sets
against compact JSON/JSONL and emits TOON only when explicitly requested or when
`--format auto` proves a material savings.

## Quick Start

```sh
direnv allow
just check
just prompt-toon doctor
```

Condense files:

```sh
just prompt-toon condense docs/founding-prompt.md --output-dir /tmp/prompt-toon-demo
```

Analyze JSON rows:

```sh
just prompt-toon analyze path/to/results.json
```

## Project Surfaces

- CLI package: `prompt_toon/`
- Codex skill: `.agents/skills/prompt-toon/SKILL.md`
- Delegation skill: `.agents/skills/mythos-delegation/SKILL.md`
- Delegation policy SSOT: `policy/delegation.json` (Dhall source: `policy/dhall/`)
- Founding prompt: `docs/founding-prompt.md`
- Mythos vision addendum: `docs/mythos-vision.md`
- Delivery design record: `docs/mythos-delivery-design.md`
- Research decision record: `docs/research-decision.md`
- Linear map: `docs/linear.md`

## Operating Position

- Default data format for arbitrary nested data: compact JSON or JSONL.
- Default prompt boundaries: Markdown/XML-style sectioning.
- Default model-output contract: structured JSON where provider support exists.
- TOON: optional for flat uniform scalar rows only.
