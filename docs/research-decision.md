# Research Decision

Date: 2026-07-09

## Current Scope Note

This record captures the first offline increment, not the current whole-product
boundary. The project subsequently expanded into standalone harness-to-model
streaming IO middleware: Python owns provider HTTP/auth/stream pass-through and
a resident Chapel process owns bounded typed-context transforms. The local
condenser and Codex skill remain supported transforms within that middleware.

## Initial Decision

Build `prompt-toon` as a deterministic local condenser and Codex skill.

Do not build a general prompt-compression router first. Do not make TOON the
default interchange format. Keep TOON as an optional row encoder for flat,
uniform scalar data after measured savings against compact JSON/JSONL.

## Research Summary

Six lanes were spawned. Five returned usable reports; one compression-literature
lane failed from subagent context pressure after other lanes had already covered
the implementation-blocking decisions.

Key findings:

- Canonical TOON exists at `toon-format/spec` and `toon-format/toon`; no
  separate LF TOON project/spec was found. TOON itself mandates LF line endings.
- TOON can save substantial tokens for uniform arrays, but compact JSON often
  wins for nested or semi-uniform data.
- CSV/TSV are smaller for flat scalar tables when lossless JSON shape is not
  required.
- Minified JSON remains the safest general-purpose format for nested data,
  tool outputs, logs, and schema validation.
- XML-like tags are useful as prompt section delimiters, not as full data
  serialization for large payloads.
- Prompt minimization can launder tool-output injection, drop critical
  constraints, or erase provenance; redaction and trust-tier separation must
  happen before summarization.
- Codex skills and `codex exec` are stable enough for a local first version.
  MCP can be added later if cross-client tool invocation is needed.
- Claude Code has analogous hooks/skills/subagent surfaces, but the first
  durable integration should be a CLI plus skill, then Home Manager install.

## Sources

- TOON spec/docs: https://github.com/toon-format/spec and https://toonformat.dev/
- TOON benchmarks: https://toonformat.dev/guide/benchmarks
- TOON SDK/CLI: https://github.com/toon-format/toon
- RFC 8259 JSON: https://datatracker.ietf.org/doc/html/rfc8259
- RFC 4180 CSV: https://datatracker.ietf.org/doc/html/rfc4180
- YAML 1.2.2: https://yaml.org/spec/1.2.2/
- JSON Lines: https://jsonlines.org/
- Codex manual, accessed through the official manual helper on 2026-07-09:
  skills, MCP, hooks, subagents, and non-interactive mode.
- Linear constraints: `TIN-2494`, `TIN-2524`, `TIN-2554`.

## Implementation Consequences

- CLI defaults to deterministic extractive source cards.
- Raw input text is not copied to summaries by default; manifests carry hashes.
- Redaction runs before source-card emission.
- `--format auto` may emit `source-cards.toon`, but JSONL remains available.
- Tests cover redaction, critical constraint preservation, and TOON shape checks.
