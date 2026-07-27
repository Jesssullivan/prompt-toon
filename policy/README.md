# Toon of Mythos Policies

Single sources of truth for how work routes across model classes and how the
IO layer behaves. Owner tickets: TIN-2698 (delegation), TIN-2700 (io).
Vision: `docs/mythos-vision.md`; architecture: `docs/mythos-delivery-design.md`.

## Layout

- `dhall/DelegationPolicy.dhall` + `dhall/delegation.dhall` — typed
  delegation-policy schema and source (records, lists, enum-like Text only,
  so the JSON stays trivially comparable).
- `delegation.json` — the validated transition artifact tooling reads.
- `delegation.schema.json` — JSON Schema (draft 2020-12) for that artifact:
  first-class seat names (`fable`, `opus`, `sonnet`, `haiku`), closed records,
  and enum-constrained severities. `tests/test_delegation_policy.py` validates
  the JSON against it.
- `dhall/IoPolicy.dhall` + `dhall/io.dhall` — typed IO-layer policy
  (enforcement gate, thresholds, surfaces, trust tiers, failure modes).
- `io.json` — its transition artifact. The enforcement gate stays locked
  (every surface disabled) until TIN-2695 and TIN-2699 close and INV-1..6
  hold; `tests/test_io_policy.py` fails closed on violations.

## Regeneration

Dhall and `dhall-json` are in the Nix dev shell. Regenerate both validated
transition artifacts through the repo recipe:

```sh
just gen-policy
```

## Invariants (fail closed)

- Adversarial/purple-team lanes never resolve to fable-class models.
- The fable persona forbids adversarial-analysis, purple-team, red-team,
  deep-iteration-hammering, and bulk-execution.
- Every persona and lane carries a non-empty `purpose` string.
- `delegation.json` validates against `delegation.schema.json`, and every
  model class a persona routes to (other than `operator`) has a seat record
  whose declared personas do not contradict `personas[].model_classes`.

Warn-class rules (encoded as data in `enforcement`, not test failures):
research/mechanical lanes escalating to fable; pinning default model
selection instead of leaving it provider-managed.
