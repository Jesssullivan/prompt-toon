# Linear Map

Initiative: Prompt TOON Agent Spool

- ID: `47641b08-44bc-4bac-956d-0dd8f8f88dad`
- URL: https://linear.app/tinyland/initiative/prompt-toon-agent-spool-8b0b1b850a52

Project: prompt-toon local research condenser

- ID: `6c824bb3-4e8f-4a7b-9092-2638ed945c54`
- URL: https://linear.app/tinyland/project/prompt-toon-local-research-condenser-6ca57e460046

Issues:

- `TIN-2691` Bootstrap private prompt-toon repo with founding prompt and remote-first scaffolding.
- `TIN-2692` Implement deterministic prompt-toon CLI for staging, condensation, and source cards.
- `TIN-2693` Add measured TOON row encoder as an opt-in leaf format.
- `TIN-2694` Publish Codex skill wrapper for research condensation workflows.
- `TIN-2695` Add safety, redaction, and provenance invariant tests.
- `TIN-2696` Wire global install path through lab Home Manager.
- `TIN-2697` Design future confusing-keyword and model-brittleness tuner.
- `TIN-2698` Adopt Toon of Mythos delegation-policy SSOT: Dhall-shaped policy, publishable skill, Bazel/Nix packaging.
- `TIN-2699` Spike: prove Claude PostToolUse Task-return rewrite (updatedToolOutput) through the condense path.
- `TIN-2700` IO layer Phase 0: policy/io.json SSOT + integrity-verified sha256-keyed condensation cache.
- `TIN-2701` Decision: queue→execute runner crosses the deterministic/no-network boundary (INV-9).
- `TIN-2702` MCP condenser stage on the TIN-2524 disclosure gateway (blocked by TIN-2524).
- `TIN-2703` M0 CI scaffold: secrets-scan / build-and-test / bazel-graph.
- `TIN-2704` GloriousFlywheel enrollment + two-tier CI lanes (dormant until overlay/allowlist arm).
- `TIN-2705` mythos-policy-parity check: declared delegation lanes vs actual OMO routes.
- `TIN-2706` Packaging-SSOT manifest (TIN-2046 pattern, Chapel-phased derived lanes).
- `TIN-2707` C0 Chapel spike: normalize+redact parity (GO, 2026-07-09).
- `TIN-2708` C1 `ptoon` binary behind `--engine=chapel` (subprocess pivot from the original shared-library plan) + parity runner + Bazel-drives-chpl skeleton on GF REAPI; quickchpl property source is advisory until pinned/wired.
- `TIN-2709` C2 ptoon streaming: `coforall` batch entrypoint, cache parity, full Bazel executor lane.
- `TIN-2710` C3 flip to ptoon + rules_chapel extraction/registry publication + brew/rpm lanes.

C4 online IO gateway:

- `TIN-2790` Parent: online prompt-toon IO gateway between local harnesses and model providers.
- `TIN-2792` C4a: bounded resident `ptoon serve` protocol, Python client, and
  64-stream parity/RSS capacity gate.
- `TIN-2793` C4b: Claude Messages shadow gateway with typed-context transforms.
- `TIN-2794` C4c: Codex Responses gateway adapter and user-level profile.
- `TIN-2791` C4d: multi-platform binaries and Home Manager gateway rollout.

Related prior work:

- `TIN-2494` Skill/harness pare-down and token-IO ADR.
- `TIN-2524` Gateway progressive tool disclosure for MCP token reduction.
- `TIN-2554` TOON-on-uniform-rows experiment, flag-gated and not default.
