# Offline Dogfood And Efficiency Ledger

TIN-2819 adds a provider-free way to replay a bounded research spool through
the actual condensation surface and retain the economics beside the result.
It does not unlock IO policy, route a harness, call a model provider, release a
package, or activate a fleet service.

## Run

Keep the source material in a durable, access-controlled path, then run:

```sh
just dogfood path/to/spool/
```

Files and directories may be mixed. Directories expand recursively in stable
path order. Final input symlinks, symlinks discovered inside a directory root,
non-regular entries, invalid UTF-8, an empty spool, and a pre-existing output
directory fail closed. Ancestor path aliases are resolved and the canonical
path is recorded as the source label, so provenance names the location that
actually supplied the bytes. Duplicate canonical paths are processed once.
Each run is capped at 64 documents, 2,000,000 bytes per document, 16 MiB
aggregate input, 24 cards per document, and a 2-second Chapel batch budget. Raw
inputs are not copied into state.

On the supported Darwin/Linux hosts, input descriptors are opened once with
no-follow/nonblocking flags, verified as regular files with `fstat`, and read
through a hard byte ceiling. Source paths that could break Markdown lines and
malformed trust-tier labels are rejected before condensation. Cooperating
writers serialize on a persistent owner-only lock keyed to the output
destination. The empty lock inode remains so unlink/recreate races cannot split
future writers across different locks. Outputs are written to an owner-only
sibling staging directory and renamed into place only after the complete ledger
exists; failed transforms remove their staging directory and leave the output
destination free.

The default `--engine auto` behavior is observable:

- `chapel`: one `ptoon condense` process runs the document fan-in. Chapel's
  `coforall` semantics create a distinct task for each iteration and wait for
  all child tasks, so the CLI keeps the production document ceiling rather
  than permitting an unbounded fan-in. See the
  [Chapel 2.9 task-parallel specification](https://chapel-lang.org/docs/language/spec/task-parallelism-and-synchronization.html).
- `python`: the sequential parity oracle was used because `ptoon` was absent.
  The ledger records the fallback; it is never presented as Chapel evidence.
  It enforces the same input ceilings but has no wall-clock withholding
  mechanism, so its ledger says `bounded-input-only-python-oracle`. Corpus
  timing and budget evidence must be cohort-specific rather than mixing it with
  Chapel runs.

This command measures one spool. The resident 64-stream proof remains
`just gateway-capacity`, where one long-lived Chapel process owns fixed workers
and bounded concurrent request state.

The remote `ptoonParity` derivation exports the real binary before running the
full Python suite. That turns the binary-present dogfood integration test on;
local suites skip only that test when no compatible `ptoon` is installed.

## Artifacts

The run directory contains:

- `summary.md`: minimized synthesis handoff.
- `source-cards.jsonl`: authoritative cards with evidence and source hashes.
- `manifest.json`: input provenance, trust tiers, policy settings, and output
  names.
- `source-cards.toon`: optional compact view selected only when the local
  estimate clears the configured threshold. It omits evidence and hashes.
- `efficiency.json`: measurements and claim boundaries for this run.

`efficiency.json` reports the summary alone, summary plus authoritative JSONL,
and, when selected, summary plus the TOON compact view. Every reduction names
its raw-input denominator. Negative savings are retained rather than hidden.
Artifact hashes make later corpus aggregation auditable without storing raw
content in the report.

Runtime attribution is split: `engine_wall_ms` covers in-memory condensation
through optional TOON format selection for either resolved engine, while
`run_wall_ms` also includes card/summary/manifest emission. The ledger itself is
Python postprocessing even when Chapel owns the batch transform. Engine shape
and budget-enforcement mode stay adjacent to the timing so unlike cohorts are
not presented as a direct benchmark.

The whole-handoff gate defaults to 20% estimated savings and is independent of
the TOON-vs-JSONL selection threshold. A TOON view can be materially smaller
than JSONL while `summary + TOON` still exceeds the raw source bundle. In that
case `recommended_handoff` is null and the ledger reports `below-threshold`
instead of promoting a misleading format-local win. Adjust the experiment gate
with `--min-handoff-savings`; the configured value is recorded in the ledger.
Any withheld document forces the gate to `withheld` regardless of measured
size, so fail-closed omission can never masquerade as an efficiency gain. The
ledger retains only the withheld source label and reason, never body-derived
content.

## Corpus Reports

Schema-v2 ledgers add an artifact-derived emitted-card count plus two path-free
input identities over SHA-256, trust tier, and byte count: an order-sensitive
key binds the actual handoff order, while an order-insensitive multiset key
measures spool diversity. Regenerate schema-v1 runs with the current `dogfood`
command before aggregation.

Pass explicit ledger files to the checked reporter:

```sh
just dogfood-corpus \
  path/to/run-01/efficiency.json \
  path/to/run-02/efficiency.json
```

The reporter opens only the named regular JSON files; it does not discover run
directories or read summaries, cards, manifests, or raw sources. Inputs are
capped at 50 one-megabyte ledgers, final symlinks and duplicate JSON keys fail
closed, and recomputed identities prevent replaying or permuting one spool to
inflate a cohort. The same spool may appear once in each execution/policy
cohort for an apples-to-apples Python/Chapel comparison, but counts only once
toward diversity. One to 19 unique spools produce an `insufficient-corpus`
diagnostic; 20 or more unique spools within the 50-ledger cap satisfy the
sample-count gate.

Estimator ID, pattern, and unit must match exactly. Engine, one-shot shape,
budget enforcement, card cap, Chapel budget, and both savings thresholds form
the execution cohort, so Python-oracle, Chapel one-shot, and policy variants
never share percentiles. Resident gateway evidence is rejected and remains in
`just gateway-capacity`. Each cohort reports nearest-rank p50/p90 only at five
or more runs; smaller cohorts retain sorted values with an
`insufficient-cohort` status. Withheld and zero-token-baseline runs remain in
operational and gate-rate denominators but are excluded explicitly from
economics distributions and weighted aggregates. Output byte counts, lexical
estimates, and savings are reported separately.

The report keeps TOON eligibility/selection separate from whole-handoff pass
rate, records zero provider requests, and contains no implicit timestamp or
filesystem path, making the JSON deterministic for the same ledger set. Its
promotion gate is always `blocked` / `not-evaluated` in C4f.1. C4f.2 must add
deterministic constraint, open-question, redaction, and provenance quality
fixtures before any size result can become promotion evidence. Until then,
ledger metrics are internally checked but self-attested rather than
cryptographically bound to an execution, and timing is not a cross-host
benchmark because host metadata is not recorded. A passing corpus-count gate
does not activate either shadow gateway, alter provider-bound request bytes, or
prove model-visible context reduction.

## Token Claim Boundary

The local estimator counts ASCII word runs and individual non-whitespace
symbols. It is deterministic and useful for before/after comparisons, but it
is not a model tokenizer, context-window guarantee, or billing measurement.

OpenAI's current token-counting guidance says the Responses input-token
endpoint accepts the complete Responses payload and includes request structure
such as roles and boundaries that local text tokenization cannot see. Tools,
files, images, and model-specific behavior also affect the count. Exact GPT
experiments therefore use the
[OpenAI token counting endpoint](https://developers.openai.com/api/docs/guides/token-counting)
only under an explicit authorization and budget; `dogfood` never invokes it.

The optional `--mythos-route` and `--model-label` fields are caller-observed
session labels. They are not proof that a provider routed a request to that
model or that the current delegation policy binds the concrete model label to
the route. TIN-2705 owns that typed policy-parity follow-up.

## Build And Runtime Planes

Bazel/GF REAPI remains the hermetic build-and-test execution plane. Bazel's
[remote execution overview](https://bazel.build/remote/rbe) describes remote
actions, consistent environments, and shared outputs; it does not turn a
local dogfood run into a production service or prove resident concurrency.
Chapel compilation stays remote-only under the repository doctrine.
