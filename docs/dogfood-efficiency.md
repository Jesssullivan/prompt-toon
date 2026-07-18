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
  [current `coforall` guide](https://chapel-lang.org/docs/users-guide/taskpar/coforall.html)
  and the
  [current task-parallel specification](https://chapel-lang.org/docs/language/spec/task-parallelism-and-synchronization.html)
  used for forward review. The exact locked source has 2.7.0 Nix package
  metadata but reports 2.8.0 pre-release; TIN-2807 owns reconciliation and the
  reviewed compiler upgrade to 2.9.
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

Recommendation also requires complete recall of every source-line occurrence
recognized by the existing critical-constraint and open-question classifiers.
Recall is measured separately for the summary, summary plus authoritative
JSONL, and optional summary plus TOON view. One emitted card cannot satisfy
multiple identical source occurrences, and a safety transformation that changes
summary text is not credited as exact retention. A smaller handoff that loses a
recognized line is removed from eligibility; a larger retaining handoff may be
recommended. If every size-eligible handoff loses a recognized line, the gate
is `recall-loss` and no recommendation is emitted. The ledger stores category
counts only, not anchor text, and labels this as exact classifier-line recall
rather than semantic equivalence or task-quality proof.

Any withheld document forces the gate to `withheld` regardless of measured
size, so fail-closed omission can never masquerade as an efficiency gain. The
ledger retains only the withheld source label and reason, never body-derived
content.

## Corpus Reports

Schema-v2 introduced an artifact-derived emitted-card count plus two path-free
input identities over SHA-256, trust tier, and byte count: an order-sensitive
key binds the actual handoff order, while an order-insensitive multiset key
measures spool diversity. Regenerate schema-v1 runs with the current `dogfood`
command before aggregation. Schema-v3 adds the content-free handoff-recall gate
and requires it on every new ledger. Schema-v2 ledgers remain readable as
`legacy-unmeasured-v2`, but a v2/v3 hybrid fails closed. Legacy ledgers are
isolated from measured-recall cohorts and cannot claim recall evidence or
contribute a handoff-pass result. Their byte/token measurements remain available
for historical diagnostics. Corpus reports carrying these new fields use report
schema v2.

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
budget enforcement, card cap, Chapel budget, both savings thresholds, and the
recall method form the execution cohort, so Python-oracle, Chapel one-shot,
legacy-unmeasured, and policy variants never share percentiles. Resident gateway
evidence is rejected and remains in
`just gateway-capacity`. Each cohort reports nearest-rank p50/p90 only at five
or more runs; smaller cohorts retain sorted values with an
`insufficient-cohort` status. Withheld and zero-token-baseline runs remain in
operational and gate-rate denominators but are excluded explicitly from
economics distributions and weighted aggregates. Output byte counts, lexical
estimates, savings, measured-recall rate, and recall-loss rate are reported
separately.

The report keeps TOON eligibility/selection separate from whole-handoff pass
rate, records zero provider requests, and contains no implicit timestamp or
filesystem path, making the JSON deterministic for the same ledger set. Its
promotion gate remains `blocked` / `not-evaluated`: ledger metrics are
internally checked but self-attested rather than cryptographically bound to an
execution, and timing is not a cross-host benchmark because host metadata is
not recorded. A passing corpus-count gate does not activate either shadow
gateway, alter provider-bound request bytes, or prove model-visible context
reduction.

## Offline Quality Fixtures

C4f.2 adds a separate synthetic artifact-quality gate:

```sh
just quality-fixtures
```

`tools/gen_fixtures.py` writes a deterministic, gitignored schema-v1 fixture
manifest beside the existing generated corpus. Four bounded cases cover
critical constraints/open questions, secret-class redaction, a clean control,
and mixed trust-tier provenance. `tools/quality_runner.py` executes each case
through an explicit engine and evaluates the emitted summary, authoritative
JSONL, optional TOON view, and manifest against the original synthetic bytes.

Every dimension is all-or-nothing: all required constraints and questions must
appear in both the cards and their summary section; forbidden untrusted claims
must stay out of `Critical Constraints`; every required redacted claim must
retain its redaction flag; and no forbidden input fragment may appear in any
model-facing artifact. Clean controls must retain every expected claim without
acquiring redaction. Every positive claim is bound to its exact source and
expected card range, while each card/manifest source, SHA-256, trust tier, and
range is validated. Multiline redaction preserves Python `splitlines()`
separators so later ranges remain aligned with raw source lines. Source reopen
guidance is mandatory. No weighted score can hide a failure.

The canonical report contains fixture/case IDs, counts, engine identity, and
failure codes only. It has no timestamp, absolute path, raw fixture content,
provider request, or timing. Local `just check` runs the Python oracle. The
remote Linux parity derivation runs both Python and Chapel. The attended Darwin
bridge runs bounded native `caps`, normalization, one-shot redaction, and
resident round-trip smoke on the exact GF output and again after byte-preserving
Nix import, without local `chpl` iteration. The pull-request Darwin definition
check does not claim this native artifact proof.

An `offline-fixture-pass` proves this fixed transform regression suite only. It
does not attest arbitrary self-reported corpus ledgers, prove SWE task quality,
or unlock the corpus reporter's promotion field. Adoption still needs a
reviewed binding between an implementation/fixture revision and representative
corpus evidence, followed by separately authorized provider evidence.

## Exact Usage Imports

C4f.4 imports already-produced exact counts or terminal usage without contacting
a provider. Keep the complete request bodies and the Responses count, terminal
Response, or Codex JSONL artifact in an access-controlled local path, then
generate one sidecar per variant:

```sh
just provider-usage-import \
  --ledger path/to/run/efficiency.json \
  --request path/to/raw-request.json \
  --usage path/to/raw-response.json \
  --source responses-json \
  --variant raw_input > raw.provider-usage.json

just provider-usage-import \
  --ledger path/to/run/efficiency.json \
  --request path/to/condensed-request.json \
  --usage path/to/condensed-response.json \
  --source responses-json \
  --variant summary_only > summary.provider-usage.json

just provider-usage-compare \
  raw.provider-usage.json summary.provider-usage.json
```

Repeat `--request` only with `codex-jsonl`, in provider request order, when one
observed Codex turn made multiple Responses requests; the checked ceiling is
256 request artifacts, 16 MiB each and 64 MiB in aggregate. `responses-json`
accepts one terminal Response object, `responses-sse` requires one request and
exactly one terminal `response.completed` event, and `codex-jsonl` requires
exactly one completed turn plus an explicit `--model-label` matching every
request-declared model. Codex JSONL input is separately bounded at 64 MiB so
long tool loops remain measurable without permitting unbounded reads.

For a separately authorized call to `/v1/responses/input_tokens`, use
`responses-input-count` with the exact single request payload and returned
`response.input_tokens` object. Its model label comes from the request and all
cache/output fields remain unknown. The importer opens only explicitly named
regular files through bounded no-follow reads. It hashes raw bytes without JSON
reserialization and emits no filesystem paths, request content, response
content, timestamps, or provider credentials.

The sidecar binds the validated schema-v3 or legacy schema-v2 ledger, corpus
identity, manifest, selected
handoff artifacts, ordered request sequence, and usage artifact. Missing cache
or output fields remain `null`; they are never inferred as zero. Comparison
requires the same ledger, corpus, source class, and model, with a `raw_input`
baseline and a non-raw candidate. It reports exact input-token change and cache
reads/writes separately, but no dollar cost or quality score.

These are caller-supplied external artifacts. The descriptors make drift
detectable when the artifacts are rehashed, but they do not authenticate that a
provider saw the request or that the request semantically contains the named
handoff. The importer itself records zero provider requests. Provider-backed
collection remains separately authorized, and deterministic quality/corpus
gates remain independent.

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

GPT-5.6 also reports prompt-cache reads as `cached_tokens` and writes as
`cache_write_tokens`; cache writes and reads have different economics. Exact
prefix reuse depends on preserving stable prompt order and cache controls.
Terminal usage imports therefore bind total input, cache-read, and cache-write
counts when reported; input-count artifacts deliberately leave cache telemetry
unknown. Keep stable authority-bearing prefixes before variable condensed
context. Fewer lexical pieces or request bytes alone is not a net provider-cost
proof. See the current
[GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
and [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching#requirements).

The optional `--mythos-route` and `--model-label` fields are caller-observed
session labels. They are not proof that a provider routed a request to that
model or that the current delegation policy binds the concrete model label to
the route. TIN-2705 owns that typed policy-parity follow-up.

## Build And Runtime Planes

Bazel/GF REAPI remains the governed shared build-and-test execution plane;
hermeticity and cache eligibility are target-class properties, not platform-wide
claims. Bazel's
[remote execution overview](https://bazel.build/remote/rbe) describes remote
actions, consistent environments, and shared outputs; it does not turn a
local dogfood run into a production service or prove resident concurrency.
Chapel compilation stays remote-only under the repository doctrine.
