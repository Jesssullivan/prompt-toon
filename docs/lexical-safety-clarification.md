# Lexical Safety Clarification

Issue: `TIN-2697`

Status: provider-evidence and evaluation contract; runtime behavior remains off.

Evidence review date: 2026-07-13. All provider pages below were accessed on
that date. They are a dated public-evidence snapshot, not a promise that a
provider's private classifiers or product behavior will remain unchanged.

## Decision

Treat lexical safety work as provenance-aware clarification, not a keyword
sanitizer. The bounded review found no provider-authored deterministic keyword
list and no provider claim that one string, independent of intent and context,
causes model routing. This is an absence of public evidence, not proof that
lexical features never influence a classifier.

The current deliverable is advisory only:

- audit operator-owned prompts, skills, and harness instructions;
- report evidence-backed clarification suggestions without mutating input;
- keep unsafe content redacted, quarantined, or withheld under existing policy;
- collect fixed, text-free telemetry; and
- preserve provider-visible request bytes in the shadow gateways.

No rule may obscure unsafe intent, weaken redaction, or make prohibited content
appear benign. No inferred or community-sourced trigger list is production
evidence.

## Public Provider Evidence

OpenAI documents automated classifier-based cyber monitoring that may reroute
high-risk Codex traffic to GPT-5.2. It also acknowledges that legitimate or
non-cyber activity can be flagged and says the responding model is visible in
request logs and client notices. See [Cyber Safety](https://learn.chatgpt.com/docs/cyber-safety).

OpenAI's current GPT-5.6 guidance says real-time cyber and biology misuse
classifiers can block, refuse, or pause output and may intervene on legitimate
dual-use work. It recommends a stable, privacy-preserving `safety_identifier`
for end-user applications. See [GPT-5.6 safeguards](https://developers.openai.com/api/docs/guides/latest-model#safeguards).

Anthropic documents automated Fable 5 checks across everything the model reads,
including memory, connectors, web results, and files. Requests in specified
cyber, life-science, reasoning-extraction, and frontier-model areas may fall
back from Fable 5 to Opus 4.8, and Anthropic explicitly acknowledges benign
false positives. See [Why Claude switched models](https://support.claude.com/en/articles/15363606-why-claude-switched-models-in-your-conversation-with-fable-5).

Anthropic's API contract returns classifier refusals as HTTP 200 responses with
`stop_reason: "refusal"`, an optional fixed-category `stop_details`, and, when
configured, model-transition evidence in fallback blocks and usage iterations.
See [Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback).

One concrete harness-level lexical warning is provider-authored: Anthropic says
prompts, skills, or harness instructions that ask Fable 5 to echo, transcribe,
or explain internal reasoning as response text can trigger the
`reasoning_extraction` refusal category and increase fallback to Opus 4.8. Its
migration guidance is to audit those instructions and consume structured
adaptive-thinking blocks when reasoning visibility is required. See
[Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5).

These sources support contextual classification, observable refusals or
fallbacks, and the possibility of false positives. They do not support a
general-purpose lexical bypass table.

## Authority And Safety Boundary

The analyzer may inspect operator-owned harness text and typed,
provenance-bearing non-authority context. It may emit a source location,
versioned rule ID, evidence URL, and suggested clarification. Suggestions must
preserve intent and remain subject to explicit review.

It must never rewrite:

- system, developer, or user instructions;
- approval state or operator authorization;
- tool schemas, descriptions, arguments, or results that retain authority;
- provider model, safety, cache, routing, or protocol controls;
- unknown request fields or original request/response bytes; or
- canonical claims, evidence, source IDs, hashes, trust tiers, or line ranges.

Redaction runs first and remains fail-closed. Injection-shaped or genuinely
unsafe content is flagged and quarantined or withheld; it is not euphemized.
Ambiguity, an unknown profile, an unsupported language, or analyzer failure
produces no clarification. Trust tier is supplied by policy, never inferred
from vocabulary.

## Advisory Output

The first implementation may only emit an advisory record:

```json
{
  "schema_version": 1,
  "profile": "lexical-v1",
  "rule_id": "anthropic-reasoning-extraction-harness-v1",
  "source_sha256": "<64 lowercase hex>",
  "source_byte_start": 120,
  "source_byte_end": 158,
  "offset_basis": "inspected-artifact-utf8-v1",
  "authority": false,
  "decision": "suggest",
  "evidence_url": "https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5"
}
```

Offsets are zero-based, half-open UTF-8 offsets in the inspected artifact and
must name their offset basis. Runtime reports omit source text, replacement
text, URLs discovered in source material, paths, and identifiers. Suggested
wording comes from a reviewed static profile, never from the inspected input.

## Provider-Free Fixture Contract

Version 1 contains exactly 64 accepted UTF-8 cases plus four rejected-input
cases. Each accepted case carries a reviewed expected decision, exact input and
output hashes, protected spans, trust tier, authority bit, and expected flags.

| Dimension | Cases | Required result |
| --- | ---: | --- |
| Benign sensitive-domain prose | 8 | Advisory candidates are explicit; canonical bytes stay exact. |
| Negation, modality, actor, object, and condition | 8 | Every protected semantic span stays byte-identical. |
| Quoted or reported untrusted instructions | 6 | Quote scope and attribution survive; no authority is gained. |
| Reasoning-extraction harness forms and safe controls | 6 | Reviewed forms are detected; concise-rationale and structured-thinking controls are no-ops. |
| Code fences, inline code, URLs, paths, and identifiers | 6 | Protected technical spans receive no edits or derived labels. |
| Unsafe imperatives and prompt injection | 6 | `no_rewrite`; existing unsafe or injection flags remain set. |
| Unicode, confusables, combining marks, and typos | 6 | Detection uses the defined view; offsets still validate against original UTF-8. |
| Multilingual and unsupported-language controls | 6 | Unsupported text is an exact no-op. |
| OpenAI and Anthropic authority envelopes | 6 | Instructions, messages, tools, arguments, approvals, controls, and unknown fields are unchanged. |
| CRLF, CR, LF, NEL, LS, PS, and multibyte offsets | 6 | Line topology and every declared byte span recompute exactly. |

The four rejected-input cases are invalid UTF-8, an oversized artifact, a
malformed provider envelope, and duplicate JSON keys. They fail before advisory
emission and retain no input text.

Provider-free acceptance is all-or-nothing:

- `authority_edits == 0` and upstream request entities are byte-identical;
- all clean and unsupported controls are exact no-ops;
- all reviewed positive spans produce the expected advisory and no others;
- polarity, modality, actor, object, quantity, condition, and attribution do
  not drift;
- source ID, hash, tier, byte span, and line range recompute for every record;
- output is idempotent and has one digest across 20 cold runs;
- shuffled batches equal individual results at 1, 8, 32, and 64 documents;
- Python and Chapel outputs and canonical sidecars are byte-identical, with no
  remote skip; and
- `provider_requests_total == 0`.

A fixture pass proves deterministic detection and preservation properties only.
It cannot prove that a provider classifier reacts to a string, that a route
changed, or that a clarification caused an observed route.

## Telemetry Contract

Provider-free and shadow telemetry exposes only these fixed counters:

- `lexical_segments_seen_total`
- `lexical_segments_eligible_total`
- `lexical_advisories_total`
- `lexical_notes_emitted_total`
- `lexical_noop_authority_total`
- `lexical_noop_unsafe_total`
- `lexical_noop_ambiguous_total`
- `lexical_noop_unsupported_language_total`
- `lexical_guard_failures_total`
- `provider_requests_total`
- `provider_responses_total`
- `provider_refusal_responses_total`
- `provider_refusal_cyber_total`
- `provider_refusal_bio_total`
- `provider_refusal_frontier_llm_total`
- `provider_refusal_reasoning_extraction_total`
- `provider_refusal_other_or_null_total`
- `provider_fallback_transitions_total`
- `provider_fallback_served_responses_total`
- `provider_returned_model_unknown_total`

Metrics carry no source text, candidate text, source IDs, paths, URLs, dynamic
rule labels, or dynamic model labels. A deterministic manifest records the
profile version, ruleset hash, policy hash, binary version, and fixture digest.

A separately authorized provider-backed experiment writes one path-free
evidence sidecar per paired observation. The closed schema records:

- experiment and corpus identity hashes;
- provider and harness surface;
- requested and returned model IDs as observations, never inferred routes;
- baseline or candidate arm and clarification-profile hash;
- request-artifact, transformed-artifact, policy, and authority-byte hashes;
- HTTP status, stop reason, fixed refusal category or `null`;
- fallback-transition count and whether a fallback served the response;
- exact input, cached-input, output, reasoning, and provider-billed usage when
  exposed; and
- observation time, response ID hash, and an explicit missing-field list.

For Anthropic, detect refusal from `stop_reason`, not HTTP status, and derive
fallback observations only from documented response fields. For OpenAI, record
the returned model or reroute notice exposed by the supported client/log
surface; absence of such evidence is `unknown`, not `no_reroute`. Explanatory
free text is never parsed into a category.

## Promotion Gate

Any future transform is a separate opt-in profile, default `off`. It may add a
fixed clarification note only to the model-facing projection of eligible,
non-authority context after normalization, redaction, card construction, and
defanging. It may not replace canonical claim or evidence bytes.

Promotion remains blocked until:

1. the 64-case fixture contract passes locally and under remote Python/Chapel
   parity;
2. the existing `TIN-2820` representative 20-to-50-spool corpus passes every
   constraint-recall, open-question, redaction, clean-claim, and provenance
   gate;
3. one-shot and resident service outputs agree at the 1/8/32/64 concurrency
   matrix;
4. a reviewed ruleset proves zero authority edits and zero unexpected edits;
5. any provider-backed comparison is separately authorized, artifact-bound,
   paired, and scoped to its exact provider/model/version/harness/corpus tuple;
   and
6. policy activation, release, and fleet rollout receive their own review.

Even a successful paired experiment establishes correlation for that exact
tuple, not a private routing rule. Failure, missing evidence, unknown versions,
or policy mismatch leaves the profile off and existing safety behavior intact.
