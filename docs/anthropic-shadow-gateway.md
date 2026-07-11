# Anthropic Messages shadow gateway

TIN-2793 adds the first provider-facing adoption path. It is an opt-in,
loopback-only HTTP/1.1 gateway for `POST /v1/messages`; it is not an
enforcement proxy.

## Boundary

The gateway forwards the original request entity bytes and all end-to-end
headers, including `x-api-key`/`Authorization`, `anthropic-version`,
`anthropic-beta`, and unknown feature headers. It never serializes the parsed
request back onto the provider path. Status, response bytes, request IDs,
unknown headers, and SSE events are forwarded opaquely. Hop-by-hop HTTP
framing is necessarily terminated and rebuilt. The supported upstream
transfer framing is fixed-length, close-delimited, or a single `chunked`
coding without trailers; other coding chains are rejected rather than
silently corrupted.

In parallel, an in-memory parser correlates assistant `tool_use` blocks with
user `tool_result` blocks by `tool_use_id`. Only text from tools declared in
`policy/io.json:trust_tiers` is submitted to the long-lived `ptoon serve`
child. System content, ordinary user content, tool schemas, cache controls,
images/documents, approval state, unknown fields, and uncorrelated results
never enter the transform lane. A hash of the tool-use ID and the policy
source/tier bind each submitted document without retaining the raw ID in
telemetry.

Shadow analysis is bounded by the same policy ceilings as C4a: 64 concurrent
provider requests/transforms, 16 Chapel workers, a 64-slot queue, 64 documents
and 16 MiB per request, and a 256 MiB aggregate pending-shadow byte ceiling.
Two additional listener slots keep loopback health/metrics reachable under
ordinary provider saturation. Header and body deadlines are 10 and 30 seconds;
stuck resident futures quarantine the engine after a bounded wait. SIGINT and
SIGTERM stop admission, drain for up to 10 seconds, then close remaining
connections. Any parse, capacity, binary, redaction, or transform failure
affects aggregate metrics only. The provider exchange continues unchanged.

The gateway keeps only bounded counters, model labels, usage totals, and
quality outcomes in memory. It has no request-body, credential, cache, log,
or artifact writer. Model label cardinality is capped. `GET
/__prompt_toon/health` and `GET /__prompt_toon/metrics` are available only on
the loopback listener.

## Operator flow

On a host that can execute the current `x86_64-linux` ptoon release asset:

```sh
PROMPT_TOON_PTOON=/absolute/path/to/ptoon \
  just gateway --require-ptoon
```

Point an opted-in Claude Code or Anthropic SDK process at the gateway:

```sh
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
claude
```

The gateway deliberately ignores `ANTHROPIC_BASE_URL` when selecting its own
upstream. It defaults to `https://api.anthropic.com`; use
`PROMPT_TOON_ANTHROPIC_UPSTREAM` or `--upstream` for a reviewed alternative.
Credentials remain owned by the harness and travel only in the forwarded
request.

The explicitly billed live probe is disabled unless every gate is present:
export `ANTHROPIC_API_KEY` from the operator's credential manager first, then
run:

```sh
PROMPT_TOON_LIVE_CANARY=1 \
ANTHROPIC_CANARY_MODEL=... \
  just gateway-canary
```

It sends one small Messages request containing a synthetic `Task` result,
then reports only request ID, requested/returned model, provider usage, and
aggregate shadow outcome. It never prints the credential or request body.

## Adoption truth

- C4a provides the resident Chapel transform service and proves its bounded
  concurrency. C4b puts a transparent provider transport in front of it.
- Python owns HTTP, TLS, provider protocol evolution, and in-memory telemetry.
  Chapel owns parallel normalization/redaction/defanging/card generation.
- C4b source and fixtures do not make this a fleet default. The current Chapel
  release asset is `x86_64-linux`; Darwin and managed Home Manager profiles
  remain C4d (TIN-2791).
- `model_gateway.enabled` and the global enforcement gate remain false. No
  request is rewritten. Promotion requires measured canaries, a reviewed
  replacement grammar, and the existing policy gates.
- The gateway reports requested and returned models. It does not infer private
  provider routing rules, scrub speculative trigger strings, or override model
  selection. Lexical brittleness remains TIN-2697.

## Protocol grounding

The implementation follows the current provider contract:

- [Messages API](https://platform.claude.com/docs/en/api/typescript/beta/messages/create):
  top-level system content, content blocks, tools, `tool_use`, `tool_result`,
  cache controls, and unknown request fields remain provider-owned.
- [Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming):
  SSE can contain ping, error, and future event types, so forwarding is opaque
  and observation is best-effort.
- [API overview](https://platform.claude.com/docs/en/api/overview): direct API
  authentication and version headers are end-to-end.
- [Claude Code LLM gateway configuration](https://docs.anthropic.com/en/docs/claude-code/llm-gateway):
  `ANTHROPIC_BASE_URL` is the supported local-gateway control.
- [API errors](https://platform.claude.com/docs/en/api/errors): status, JSON
  error body, `request-id`, and post-200 stream errors are preserved.
