# Linear Map

Initiative: Prompt TOON Streaming IO Middleware

- ID: `47641b08-44bc-4bac-956d-0dd8f8f88dad`
- URL: https://linear.app/tinyland/initiative/prompt-toon-streaming-io-middleware-8b0b1b850a52

Project: prompt-toon streaming IO middleware

- ID: `6c824bb3-4e8f-4a7b-9092-2638ed945c54`
- URL: https://linear.app/tinyland/project/prompt-toon-streaming-io-middleware-6ca57e460046

Issues:

- `TIN-2691` Bootstrap private prompt-toon repo with founding prompt and remote-first scaffolding.
- `TIN-2692` Implement deterministic prompt-toon CLI for staging, condensation, and source cards.
- `TIN-2693` Add measured TOON row encoder as an opt-in leaf format.
- `TIN-2694` Publish Codex skill wrapper for research condensation workflows.
- `TIN-2695` Add safety, redaction, and provenance invariant tests.
- `TIN-2696` Wire global install path through lab Home Manager.
- `TIN-2697` Eval-gated lexical safety and clarification for model-sensitive
  context: advisory first, authority bytes immutable, runtime profile off until
  corpus and Python/Chapel parity gates pass; see
  `docs/lexical-safety-clarification.md`.
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
- `TIN-2776` Residual strict TOON round-trip/depth coverage and `Toon.chpl`
  exposure; backlog and not a production-gateway dependency.
- `TIN-2777` Completed v0.2.0 fleet packaging/install ledger; it does not prove
  the C4 gateway request path.

C4 online IO gateway:

- `TIN-2790` Parent: online prompt-toon IO gateway between local harnesses and model providers.
- `TIN-2792` C4a: bounded resident `ptoon serve` protocol, Python client, and
  64-stream parity/RSS capacity gate.
- `TIN-2793` C4b: Claude Messages shadow gateway with typed-context transforms;
  C4b.1 adds real-CLI/SSE proof, readiness, rollback, and the v0.3.0 release line.
- `TIN-2794` C4c: Codex Responses gateway adapter and user-level profile;
  exact-byte HTTP/SSE, opaque remote compaction, and real-CLI loopback proof;
  provider-backed canary waits on a filesystem-confined tool sandbox.
- `TIN-2791` C4d: native Linux package proof, Darwin definition check,
  split-auth managed gateway contract, actual-state doctor/host-ledger schema,
  release consumption, and attended shadow rollout.
- `TIN-2949` C4d.1: commission the physical GF Darwin REAPI worker, prove the
  native Mach-O artifact, bridge it to the closure-stamped manifest, and
  publish v0.3.0 without local Chapel compilation.
- `TIN-2954` C4d.2: completed source delivery of the disabled-by-default lab
  Home Manager consumer. This is not a host activation or traffic claim.
- `TIN-2819` C4e: provider-free durable-spool dogfood command and
  claim-bounded prompt-efficiency ledger; exact provider counts and live
  synthesis crossover remain separately authorized experiments.
- `TIN-2820` C4f: aggregate authorized C4e ledgers, tune whole-handoff
  efficiency under safety/constraint-recall gates, and keep format-local TOON
  savings distinct from end-to-end synthesis-context savings. C4f.1 adds the
  schema-v2 corpus identity/card count and deterministic ledger-only reporter;
  C4f.2 adds a separate all-or-nothing synthetic artifact-quality gate for
  constraint recall, open questions, redaction, and provenance; C4f.3 gates the
  real Chapel one-shot path at 1/8/32/64 documents without making a throughput
  claim; C4f.4 imports externally produced exact counts or terminal usage into
  path-free, request/artifact-hash-bound sidecars without provider IO. These
  gates do not attest arbitrary corpus ledgers, authenticate an external
  provider pairing, or prove provider-visible quality. `TIN-2925` adds a
  content-free per-handoff classifier-line recall gate so token savings cannot
  recommend a handoff that drops recognized constraints or open questions;
  schema-v3 requires the gate, while schema-v2 remains isolated as
  legacy-unmeasured evidence. The current TIN-2820 increment adds
  `compact-source-index-v1`, selects recognized anchors before findings, renders
  each claim once, and isolates legacy/compact summary economics by cohort.
- `TIN-2807`: reconcile the locked Chapel source's version provenance and
  upgrade to current 2.9. The Nix package metadata says 2.7.0, while remote
  `chpl --version` reports 2.8.0 pre-release; neither may be rewritten as 2.9.
- `TIN-2956` C4g: production default flip for ordinary native Claude and Codex
  harness invocations, gated on release/rollout, provider canaries, corpus
  evidence, and explicit policy promotion.

Related prior work:

- `TIN-2494` Skill/harness pare-down and token-IO ADR.
- `TIN-2524` Gateway progressive tool disclosure for MCP token reduction.
- `TIN-2554` TOON-on-uniform-rows experiment, flag-gated and not default.
