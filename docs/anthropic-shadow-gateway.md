# Anthropic Messages shadow gateway

TIN-2793 adds the first provider-facing adoption path. It is an opt-in,
loopback-only HTTP/1.1 gateway for `POST /v1/messages` (including Claude
Code's `?beta=true` query); it is not an enforcement proxy.

## Boundary

In explicit passthrough mode, the gateway forwards the original request entity
bytes and all end-to-end headers, including `x-api-key`/`Authorization`, `anthropic-version`,
`anthropic-beta`, and unknown feature headers. It never serializes the parsed
request back onto the provider path. Status, response bytes, request IDs,
unknown headers, and SSE events are forwarded opaquely. Hop-by-hop HTTP
framing is necessarily terminated and rebuilt. The supported upstream
transfer framing is fixed-length, close-delimited, or a single `chunked`
coding without trailers; other coding chains are rejected rather than
silently corrupted.

Managed C4d mode is different only at the credential boundary: it requires two
owner-only, non-symlink files, authenticates the local client token, strips
both auth header forms, and injects the separately custodied upstream token.
`--require-split-auth` prevents a missing-template regression from silently
starting passthrough mode. Request entity bytes and every non-auth end-to-end
header retain the same transparency contract.

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
and 16 MiB per request, a 128 MiB aggregate in-flight HTTP-body ceiling, and a
256 MiB aggregate pending-shadow byte ceiling. The resident child runs with two
Chapel runtime worker threads; the gateway never re-enters Chapel from host
threads.
Two additional listener slots keep loopback health/metrics reachable under
ordinary provider saturation. Header and body deadlines are 10 and 30 seconds;
stuck resident futures quarantine the engine after a bounded wait. SIGINT and
SIGTERM stop admission, drain for up to 10 seconds, then close remaining
connections. Any parse, capacity, binary, redaction, or transform failure
affects aggregate metrics only. The provider exchange continues unchanged.

The gateway keeps only bounded counters, process-local HMAC model buckets,
usage totals, and quality outcomes in memory. It has no request-body,
credential, cache, log, or artifact writer. Model cardinality is capped. `GET
/__prompt_toon/health`, `GET /__prompt_toon/ready`, and `GET
/__prompt_toon/metrics` are available only on the loopback listener. Readiness
means the process is accepting requests and the resident engine is available;
the separate `upstream` field is `unknown`, `reachable`, or `failed` based on
the latest observed provider exchange. `HEAD /` is a local connectivity probe.

Completed, identity-encoded Anthropic responses also contribute the following
fixed-cardinality counters. Non-stream JSON observation is capped at 1 MiB;
each SSE line is capped at 256 KiB, each assembled event at 1 MiB, and
content-block tracking at 4096 indices. SSE telemetry is committed only after
a terminated `message_stop` event. Truncated, malformed, compressed,
over-limit, or incomplete responses increment the existing
telemetry-unavailable signal instead of contributing partial safety counts.

For SSE, safety fields are read only from their documented event positions:
the initial `message_start`, `fallback` blocks in `content_block_start`, and
the final `message_delta` before `message_stop`. Unknown event types cannot
contribute safety fields. Any data event after `message_stop` invalidates the
observed copy while the original response bytes continue to the client.
Content-block indices must be unique and every start must be paired with a
stop before the final delta; fallback blocks additionally require schema-valid
`from` and `to` objects and carry no deltas. The current public response
example omits `trigger`; when a newer SDK-schema response includes one, it must
be a valid refusal trigger.

- `provider_refusal_responses` branches only on final
  `stop_reason: "refusal"`, including normal HTTP 200 refusals.
- `provider_refusal_category_{cyber,bio,frontier_llm,reasoning_extraction,other}`
  assigns exactly one fixed bucket per refusal. Missing, null, malformed, and
  future category values map to `other`; `stop_details.explanation` is never
  retained.
- `provider_fallback_transitions` counts schema-valid `fallback` content blocks.
- `provider_fallback_served_responses` requires both a
  schema-valid `usage.iterations[].type: "fallback_message"` entry in the
  final response usage and a final non-refusal stop reason. A chain whose final
  model also refuses records its transitions and refusal, but not a
  fallback-served response.

Fallback blocks and `fallback_message` iteration usage belong to Anthropic's
beta Messages schema. The server-side form is selected by
`server-side-fallback-2026-06-01`. The official SDK also offers client-side
refusal-fallback middleware on `?beta=true` requests using
`fallback-credit-2026-06-01`, but that middleware runs above the HTTP gateway:
prompt-toon sees the individual provider exchanges, not the combined response
the SDK later synthesizes for the application. The gateway does not add either
beta, choose a fallback chain, redeem credits, retry, or infer a client-side
handoff. It passes the caller's `anthropic-beta` header unchanged and records
fallback counters only from a complete server-emitted response that matches
the documented shape.

These counters do not retain iteration text, explanations, or model names.
Requested and returned models remain visible only through the existing
process-local HMAC buckets. Observation never changes forwarded response bytes
or the provider's fallback behavior.

## Operator flow

The v0.2.0 release asset predates `ptoon serve` and cannot back this gateway.
Until v0.3.0 is tagged, the following is an explicit developer passthrough
probe, not a persistent service recipe. Build the current x86_64-linux source
revision on the configured remote builder:

```sh
nix build .#packages.x86_64-linux.ptoon
PROMPT_TOON_PTOON="$(nix path-info .#packages.x86_64-linux.ptoon)/bin/ptoon" \
  just gateway --require-ptoon
```

The managed source contract instead requires distinct local and provider
credentials and fails startup if either disappears:

```sh
just gateway --require-ptoon --require-split-auth \
  --client-token-file /run/user/$UID/prompt-toon/anthropic-client \
  --upstream-token-file /run/user/$UID/prompt-toon/anthropic-upstream
```

Both files must be regular, owned by the service user, mode `0600`, and not
symlinks. The local token is a sensitive billed-relay capability even though
it is not the provider credential. Before exposing a client route, `doctor`
uses a nonce/HMAC ownership challenge that does not send the token to the
listener. The MAC covers a versioned canonical attestation, including the actual
bound endpoint, process instance, service version, reviewed upstream, policy and
resident-binary digests, readiness inputs, and observed upstream state:

```sh
just prompt-toon doctor \
  --anthropic-client-token-file /run/user/$UID/prompt-toon/anthropic-client
```

Check local readiness. `just claude-profile shadow` prints structured routing
state and fails closed when competing auth or another Claude provider mode is
active. Managed activation also requires both `NO_PROXY` casings to contain
`127.0.0.1,localhost,::1`; this prevents inherited proxies from intercepting the
loopback hop. The Home Manager consumer must remove the conflict variables named
by `packaging/home-manager.json` before exposing its local token and base URL.
For a manual process-scoped probe, the equivalent routing shape is:

```sh
curl --fail --silent http://127.0.0.1:8787/__prompt_toon/ready
just claude-profile shadow
(
IFS= read -r ANTHROPIC_API_KEY < /run/user/$UID/prompt-toon/anthropic-client
export ANTHROPIC_API_KEY
env \
  -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_CUSTOM_HEADERS -u CLAUDE_CODE_OAUTH_TOKEN \
  -u CLAUDE_CODE_USE_BEDROCK -u ANTHROPIC_BEDROCK_BASE_URL \
  -u CLAUDE_CODE_USE_VERTEX -u ANTHROPIC_VERTEX_BASE_URL \
  -u CLAUDE_CODE_USE_FOUNDRY -u ANTHROPIC_FOUNDRY_BASE_URL \
  -u CLAUDE_CODE_USE_MANTLE -u ANTHROPIC_AWS_BASE_URL \
  NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
  PROMPT_TOON_GATEWAY_URL=http://127.0.0.1:8787 \
  claude-api
)
```

On managed lab hosts, `claude-api` is the API-preserving wrapper; the ordinary
`claude` wrapper deliberately removes provider-routing variables. Standalone
installs can pass an equivalent API-preserving binary through `CLAUDE_BIN` or
`--claude-bin`. Because activation is process-scoped, the parent shell is
unchanged. A direct rollback process removes only prompt-toon routing and leaves
credentials, model choice, and any original provider mode untouched:

```sh
just claude-profile direct
env -u ANTHROPIC_BASE_URL -u PROMPT_TOON_GATEWAY_URL claude
```

The gateway deliberately ignores `ANTHROPIC_BASE_URL` when selecting its own
upstream. It defaults to `https://api.anthropic.com`; use
`PROMPT_TOON_ANTHROPIC_UPSTREAM` or `--upstream` for a reviewed alternative.
Passthrough credentials remain harness-owned. Managed credentials follow the
split-custody contract above. The current source does not export
`ANTHROPIC_BASE_URL` globally and does not install a managed service; that
separate lab Home Manager unit remains disabled until review.

Before authorizing provider spend, run the deterministic harness proof:

```sh
just gateway-harness-probe
```

It resolves the Claude Code binary from `CLAUDE_BIN`, then `claude-api` on
`PATH`, then `claude`. It launches that real binary under `--bare` with an
ephemeral config and no session persistence. Claude Code sends a streaming
`Read` tool round trip through the real gateway to a scripted loopback upstream;
the probe checks the `?beta=true` path, header values and stable session ID,
correlated tool result, shadow completion, readiness transition, same-port
rebind, and direct profile rendering. Its transform engine is an in-memory
protocol fixture, not the Chapel binary. The API key is local fixture data and
no external provider is contacted.

The live probe starts its own zero-traffic gateway and real `ResidentEngine`,
then uses the same Claude Code path against the configured provider. It is
disabled unless every gate is present: export `ANTHROPIC_API_KEY` from the
operator's credential manager, set `PROMPT_TOON_PTOON` (or pass `--ptoon`),
choose an explicit model, and set a dollar ceiling:

```sh
PROMPT_TOON_LIVE_CANARY=1 \
ANTHROPIC_CANARY_MODEL=... \
ANTHROPIC_CANARY_MAX_BUDGET_USD=... \
  just gateway-canary
```

It gives Claude Code permission to read exactly one ephemeral fixture, uses a
minimal child environment and ephemeral home, and accepts no concurrent
traffic. `PASS` requires exactly two requests, no retries, transport/SSE errors,
unavailable telemetry, withholding, or non-eligible transform. It reports only
the exact-marker harness check plus model counts, provider usage, transform
quality, and estimated transform token savings. The estimate is not
provider-billed savings because shadow mode does not rewrite requests. It is
also not a claim of SWE-quality preservation. The canary never prints the
credential, request body, tool result, model answer, or session ID.

## Adoption truth

- C4a provides the resident Chapel transform service and proves its bounded
  concurrency. C4b puts a transparent provider transport in front of it.
- Python owns HTTP, TLS, provider protocol evolution, and in-memory telemetry.
  Chapel owns parallel normalization/redaction/defanging/card generation.
- C4b source and fixtures do not make this a fleet default. v0.2.0 predates C4;
  v0.3.0 is prepared as the first C4-capable release line but is not a release
  until its tag and stamped manifest exist. C4d now publishes a disabled,
  drift-gated consumption contract; the lab service and host observations have
  not landed.
- `model_gateway.enabled` and the global enforcement gate remain false. No
  request is rewritten. Promotion requires measured canaries, a reviewed
  replacement grammar, and the existing policy gates.
- The gateway reports process-local HMAC buckets and counts for requested and
  returned models, not exact identifiers. It does not infer private provider
  routing rules, scrub speculative trigger strings, or override model selection.
  Lexical brittleness remains TIN-2697.

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
- [Claude Code gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol):
  inference uses `/v1/messages?beta=true`, responses must stream, headers and
  body fields are open lists, `HEAD /` is best-effort startup traffic, and the
  token-count endpoint is optional.
- [API errors](https://platform.claude.com/docs/en/api/errors): status, JSON
  error body, `request-id`, and post-200 stream errors are preserved.
- [Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback):
  refusals are HTTP 200 responses, fallback boundaries are content blocks, and
  `fallback_message` iterations distinguish a fallback-served response from a
  chain that ultimately refuses.
- [Streaming refusals](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/handle-streaming-refusals):
  `stop_reason` and `stop_details` arrive in `message_delta`; `message_stop`
  terminates the stream.
- [Official Python SDK fallback middleware](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/middleware/_fallbacks.py)
  and [fallback block type](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/beta/beta_fallback_block.py):
  fallback retries are beta-only; SDK-side synthesis occurs above this gateway,
  while server-emitted blocks and final iteration usage form its observable
  wire contract.
