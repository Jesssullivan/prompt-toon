# Toon of Mythos Policies

Single sources of truth for how work routes across model classes, how the
IO layer behaves and what redaction removes. Owner tickets: TIN-2698
(delegation) and TIN-2700 (io and redaction). Redaction as data follows SSOT
ruling J82(d). Vision: `docs/mythos-vision.md`. Architecture:
`docs/mythos-delivery-design.md`.

## Layout

Each policy has a typed Dhall source under `dhall/` and a rendered JSON
artifact that tooling reads. The Dhall uses records, lists and enum-like
Text only, so the JSON stays easy to compare.

| Dhall source | Rendered artifact | Gate |
|---|---|---|
| `dhall/DelegationPolicy.dhall`, `dhall/delegation.dhall` | `delegation.json` | `tests/test_delegation_policy.py` |
| `dhall/IoPolicy.dhall`, `dhall/io.dhall` | `io.json` | `tests/test_io_policy.py` |
| `dhall/RedactionPolicy.dhall`, `dhall/redaction.dhall` | `redaction.json` | `tests/test_redaction_policy.py` |

- `delegation.schema.json` is the JSON Schema (draft 2020-12) for
  `delegation.json`. It fixes the seat names (`fable`, `opus`, `sonnet` and
  `haiku`), closes every record and constrains severities to an enum.
- `io.json` holds the enforcement gate, thresholds, surfaces, trust tiers
  and failure modes. The gate stays locked, with every surface disabled,
  until TIN-2695 and TIN-2699 close and INV-1 to INV-6 hold.
- `redaction.json` holds the secret patterns, the replacement token and the
  regex dialect. `prompt_toon/cli.py` compiles `SECRET_PATTERNS` from it at
  import time. `src/ptoon/Redact.chpl` carries the same patterns as RE2
  candidates.

## Regeneration

Dhall and `dhall-json` are in the Nix dev shell. Regenerate every rendered
artifact through the repo recipe:

```sh
just gen-policy
```

Each gate compares the Dhall output with the committed JSON when
`dhall-to-json` is on `PATH`. Outside the dev shell that comparison skips
and the other checks still run.

## Invariants (fail closed)

Delegation:

- Adversarial and purple-team lanes never resolve to fable-class models.
- Every critical lane and the adversarial lane exists and resolves to opus
  or the operator only (J81). Critical lanes are implementation that
  merges, the merge gate, security and host acts. A new lane that is not on
  the known non-critical list must sit on a critical persona. Seats carry
  the 5.5 model IDs. The Dhall and the JSON agree on them.
- `opus-quota-exhausted` is the only budget fallback. It lets Sonnet
  continue `mythos.implement.merge` and `mythos.gate.security`. Merges wait
  for an Opus review. No host act or credential change runs on Sonnet. The
  receipt records the downgrade. The fallback never reaches the merge gate,
  host acts, the adversarial persona, fable or haiku.
- Harness seats and the fable `mythos.review` lane never gate a merge.
- The fable persona forbids adversarial-analysis, purple-team, red-team,
  deep-iteration-hammering and bulk-execution.
- Every persona and lane carries a non-empty `purpose` string.
- `delegation.json` validates against `delegation.schema.json`. Every model
  class a persona routes to, other than `operator`, has a seat record. Its
  declared personas do not contradict `personas[].model_classes`.

Redaction:

- Patterns stay in the portable regex subset named in `dialect`. The
  subset has no lookaround, backreferences, named or atomic groups, inline
  flags or possessive quantifiers. Case folding is the `case_insensitive`
  field.
- Pattern ids are positional (`pattern-1` onward) and name the findings.
  Append new patterns. Never reorder or remove one without a ruling.
- A missing or malformed `redaction.json` stops `prompt_toon.cli` at
  import. `cli.py` also pins the sha256 of the patterns list. A truncated,
  weakened or foreign policy file fails the same way. Appending a pattern
  moves that pin in the same change.
- The Python patterns, the Chapel RE2 specs and the historic `cli.py`
  patterns agree. The gate also checks that redaction output is unchanged
  on a fixed sample set.

Warn-class delegation rules live as data in `enforcement`, not as test
failures. They cover research or mechanical lanes escalating to fable and
pinning the harness-wide default model without a host justification.
Each agent's model choice follows the seat `model_id`.

## Release lifecycle

A policy change reaches estate consumers through one prompt-toon release.
Registry versions are immutable, so tag only after every policy change the
release needs has merged.

| State | Entered when | Left when | Owner |
|---|---|---|---|
| Rendered | `just gen-policy` and `just manifest` pass their drift gates on `main` | A release tag is pushed | prompt-toon |
| Tagged | `just release X.Y.Z` pushes the signed `vX.Y.Z` tag and the GitHub release | The registry entry merges | prompt-toon |
| Registered | `modules/prompt_toon/X.Y.Z` merges in xoxd-ai/bazel-registry with the tag tarball SRI, and its validate and smoke checks pass | Never. A registered version is immutable | bazel-registry |
| Ingested | huskycat declares the version as a `dev_dependency` and its lockfile records the registry hashes | huskycat moves to a newer version | huskycat |
| Delivered | lab's `bump-prompt-toon` recipe moves the flake pin and the skill lock check passes | lab moves the pin again | lab |

huskycat's first planned consumer is a redaction parity check against
`redaction.json`.
