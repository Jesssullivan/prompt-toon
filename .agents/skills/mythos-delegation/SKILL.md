---
name: mythos-delegation
description: Route agent work across model classes per the Toon of Mythos delegation policy. Use when orchestrating subagents or workflows, choosing a model for a lane, deciding whether a fable-class model should run a task, or attributing a session to a lane. Core rule - never route adversarial, purple-team, red-team, or deep-iteration hammering work to fable-class models.
when_to_use: Spawning subagents/workflows, picking model classes for lanes, session attribution, or reviewing whether scarce fable tokens and operator attention are being spent where they shine.
---

# Mythos Delegation

Two scarce resources mirror each other: the operator's SWE attention and
fable-class model tokens (expensive, not always available, can be slow,
compute costly). Everything cheaper — haiku/sonnet/opus bandwidth and
deterministic infra — is spent freely to protect them.

The machine-readable SSOT is `policy/delegation.json` (typed Dhall source in
`policy/dhall/`). Read it rather than restating it:

```sh
jq '.personas[] | {id, model_classes, forbidden_tasks}' policy/delegation.json
jq -r '.lanes[] | "\(.route) -> \(.persona): \(.purpose)"' policy/delegation.json
```

## Routing rules

1. **fable** takes the synthesis seat only: umbrella orchestration,
   architecture, engineering judgment, review alongside the operator. It
   never runs adversarial-analysis, purple-team, red-team,
   deep-iteration-hammering, or bulk-execution.
2. **Adversarial / purple-team / refutation lanes** go to opus or the
   operator personally. Prompt the skeptic to refute, not to agree.
3. **Research lanes** (wide recon, deep Linear exploration, web sweeps) go to
   haiku/sonnet/opus, escalating model class with task depth.
4. **Mechanical lanes** (locators, inventories, conversions) go to haiku.
5. Research and mechanical lanes escalating to fable is a warn-class
   violation: it needs explicit operator justification.
6. Leave harness default model selection provider-managed; never pin a model
   globally without a host-level justification.

## Attribution

Attribute every synthesis/recon session to a lane in session notes (lab
`docs/agent-notes/INDEX.md` style), e.g. `fable ultracode (workflow)`,
`research dispatch (fable)`, `lab hygiene (fable/opus)`, `opus-ultracode`.

## Enforcement

`tests/test_delegation_policy.py` fails closed on: adversarial lanes
resolving to fable, missing fable task prohibitions, missing purpose strings,
and Dhall/JSON drift on load-bearing IDs. It runs in `just check`.
