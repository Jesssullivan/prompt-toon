# Toon of Mythos Policies

Single sources of truth for how work routes across model classes and how the
IO layer behaves. Owner tickets: TIN-2698 (delegation), TIN-2700 (io).
Vision: `docs/mythos-vision.md`; architecture: `docs/mythos-delivery-design.md`.

## Layout

- `dhall/DelegationPolicy.dhall` + `dhall/delegation.dhall` — typed
  delegation-policy schema and source (records, lists, enum-like Text only,
  so the JSON stays trivially comparable).
- `delegation.json` — the validated transition artifact tooling reads.
- `dhall/IoPolicy.dhall` + `dhall/io.dhall` — typed IO-layer policy
  (enforcement gate, thresholds, surfaces, trust tiers, failure modes).
- `io.json` — its transition artifact. The enforcement gate stays locked
  (every surface disabled) until TIN-2695 and TIN-2699 close and INV-1..6
  hold; `tests/test_io_policy.py` fails closed on violations.

## Degraded mode

Dhall CLI tools are not yet in the dev shell. Per the lab `test/dhall`
convention, that is a normal degraded mode, not a blocker: `delegation.json`
is hand-synced with the Dhall source, and `tests/test_delegation_policy.py`
enforces the structural invariants on every `just check`.

Target state once dhall lands in the flake dev shell:

```sh
dhall-to-json --file policy/dhall/delegation.dhall > policy/delegation.json
```

## Invariants (fail closed)

- Adversarial/purple-team lanes never resolve to fable-class models.
- The fable persona forbids adversarial-analysis, purple-team, red-team,
  deep-iteration-hammering, and bulk-execution.
- Every persona and lane carries a non-empty `purpose` string.

Warn-class rules (encoded as data in `enforcement`, not test failures):
research/mechanical lanes escalating to fable; pinning default model
selection instead of leaving it provider-managed.
