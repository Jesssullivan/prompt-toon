# OpenAI Responses shadow gateway

TIN-2794 adds an opt-in Codex Responses adapter against the same resident
transform boundary as the Anthropic gateway. It is loopback-only, HTTP/1.1,
and shadow-only. It does not rewrite provider requests.

## Boundary

The adapter accepts `POST /v1/responses` and forwards the original entity
bytes, end-to-end headers, query string, status, response headers, JSON, and
SSE bytes without reserialization. Hop-by-hop framing is terminated and
rebuilt. `POST /v1/responses/compact` is forwarded opaquely for current Codex
long-session compatibility and never enters the transform lane.

An in-memory view of `/v1/responses` correlates `function_call` items with
textual `function_call_output` items by `call_id`. Only outputs whose tool name
exists in `policy/io.json:trust_tiers` are submitted to `ptoon serve`.
`shell_command` and `exec_command` are classified as untrusted tool output.
Instructions, ordinary messages, tool schemas and arguments, approvals,
reasoning items, images, files, custom-tool outputs, orphaned results,
empty or oversized call IDs, repeated call IDs, and unknown fields do not enter
the transform lane. Correlation IDs are bounded to 1024 UTF-8 bytes for shadow
observation; the original request is still forwarded unchanged.

The gateway records bounded aggregate counters, process-local HMAC buckets for
requested and returned models, OpenAI usage totals, and shadow quality. It does
not expose exact model labels or persist request bodies, credentials, tool
output, model output, or session identifiers. Provider errors and future SSE
events remain opaque.

For GPT-5.6 and later Responses telemetry, the aggregate
`provider_cache_write_tokens` counter records valid non-negative integer
`usage.input_tokens_details.cache_write_tokens` values from complete JSON or
SSE responses. This is an observed provider usage metric only: the gateway
does not calculate billing, infer cache writes when the field is absent, or
retain the response body or per-response usage data. Boolean, negative, and
non-integer values are ignored.

Current `codex exec --json` documentation shows completed-turn input,
cache-read, output, and reasoning counts, but not cache-write counts. The
harness parser retains `cache_write_tokens` only when a Codex event explicitly
contains it; it never infers a missing write count or converts absence to zero.
The gateway aggregate is therefore the current observation point for provider
cache writes.

Responses WebSocket transport is not implemented. The generated provider
profile sets `supports_websockets = false`; an attempted upgrade is rejected
with 426 and counted. HTTP/SSE remains the only accepted inference transport.

## User profile

Current Codex loads `$CODEX_HOME/config.toml`, then overlays
`$CODEX_HOME/<name>.config.toml` selected by `--profile <name>`. Provider keys
are machine-local and ignored in project `.codex/config.toml`, so prompt-toon
renders a user-level `prompt-toon-shadow.config.toml` and never edits project
configuration.

Inspect the profile or render deterministic TOML:

```sh
just codex-profile shadow --auth api-key
just codex-profile shadow --auth api-key --format toml
```

The profile selects a custom provider at
`http://127.0.0.1:8788/v1`, fixes `wire_api = "responses"`, disables
WebSockets and retries, and deliberately contains no `model` key. The
`api-key` form reads `PROMPT_TOON_CODEX_GATEWAY_TOKEN`. In managed C4d mode
this is a distinct local relay token: the gateway terminates it and injects
the separately custodied provider token. It must never contain the provider
API key in a persistent profile. The profile limits shell subprocess inheritance to
Codex's `core` environment and explicitly excludes that gateway-token variable.

The alternative `--auth openai` form uses `requires_openai_auth = true` and
the existing Codex login. Its gateway upstream must be explicitly paired with
the endpoint expected by that authentication mode. The default upstream,
`https://api.openai.com/v1`, is the API-key path.

Until C4d delivers the managed profile, install the rendered, credential-free
file at user scope:

```sh
install -d "${CODEX_HOME:-$HOME/.codex}"
just codex-profile shadow --auth api-key --format toml \
  > "${CODEX_HOME:-$HOME/.codex}/prompt-toon-shadow.config.toml"
```

For a developer-only passthrough probe, start the current source gateway with
the remotely built Linux binary:

```sh
nix build .#packages.x86_64-linux.ptoon
PROMPT_TOON_PTOON="$(nix path-info .#packages.x86_64-linux.ptoon)/bin/ptoon" \
  just responses-gateway --require-ptoon
```

The managed source contract fails closed unless both owner-only credential
files exist:

```sh
just responses-gateway --require-ptoon --require-split-auth \
  --client-token-file /run/user/$UID/prompt-toon/openai-client \
  --upstream-token-file /run/user/$UID/prompt-toon/openai-upstream
```

Before selecting the profile, `doctor` proves listener ownership with a
nonce/HMAC challenge that does not transmit the local token and verifies the
MAC over the actual listener endpoint plus the running instance's upstream,
policy digest, resident-binary digest, and readiness fields:

```sh
just prompt-toon doctor \
  --openai-client-token-file /run/user/$UID/prompt-toon/openai-client
```

Then opt one Codex process into the profile without changing its model:

```sh
PROMPT_TOON_CODEX_GATEWAY_TOKEN="$(cat /run/user/$UID/prompt-toon/openai-client)" \
  codex --profile prompt-toon-shadow
```

`just codex-profile shadow` fails closed when `OPENAI_BASE_URL` or
`CHATGPT_BASE_URL` is already active and reports only the conflicting variable
names. The gateway deliberately ignores those variables when choosing its own
upstream; use `PROMPT_TOON_OPENAI_UPSTREAM` or `--upstream` for an explicit,
reviewed route.

Ingress remains independent of connection count: each request is capped at
16 MiB and all concurrently retained request bodies share a 128 MiB byte
budget. The remote 64-stream gate drives that ledger to its configured test cap
and proves the next request is rejected before it reaches the upstream.

Rollback is selecting no prompt-toon profile. The profile file is inert when
not selected, and the base user configuration, authentication, and model
choice remain unchanged:

```sh
just codex-profile direct
codex
```

## Proof and canary

Run the deterministic proof before any provider-backed test:

```sh
just responses-gateway-harness-probe
```

It runs the installed Codex CLI with strict config, an ephemeral `CODEX_HOME`,
read-only sandbox, no approvals, no persistence, and nonessential app/plugin
features disabled. A scripted loopback upstream drives one real shell-tool
round trip over two `/v1/responses` SSE requests. The probe requires exact
marker completion, correlated tool output, one shadow transform, zero
WebSocket attempts, exact authorization forwarding, and a fail-closed
two-request gateway ceiling. Its scripted shell command also proves the
gateway token is absent from tool subprocess output. It makes no external
provider request.

No provider-backed Codex canary ships in C4c. Disabling `shell_tool` removes
`exec_command`, but Codex 0.144.1 can still advertise host-reading built-ins
such as `view_image`; its read-only sandbox does not confine reads to the
fixture directory. A request-count ceiling also does not bound dollars or
output tokens. Shipping a billed canary under those conditions would overstate
both host isolation and spend control.

Live evidence remains deferred until a purpose-built tool sandbox confines
filesystem access and a separate billing project enforces an external limit.
The real-Codex function-call/Chapel proof stays in the unbilled scripted-upstream
probe, which is mandatory in the release preflight.

## Adoption truth

- C4c source does not enable enforcement, install a user profile, publish a
  release, or change fleet configuration.
- The resident Chapel process remains provider-neutral. Python owns HTTP,
  authentication forwarding, Responses evolution, SSE, and telemetry.
- Shadow token reduction is an estimate because the request is not rewritten.
  Promotion still requires provider-backed transform, token, and SWE-quality
  evidence from a purpose-built tool sandbox.
- C4d publishes a disabled, drift-gated multi-platform/Home Manager source
  contract. A disabled lab consumer exists; authenticated release pinning and
  attended host rollout remain separate work.

## Protocol grounding

- [Codex configuration reference](https://developers.openai.com/codex/config-reference/):
  provider configuration is user-level; custom providers use the Responses
  wire API and expose the WebSocket capability flag.
- [Codex advanced configuration](https://learn.chatgpt.com/codex/config-file/config-advanced#profiles):
  named profiles are separate `$CODEX_HOME/<name>.config.toml` overlays.
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode):
  `turn.completed` JSONL documents input, cache-read, output, and reasoning
  usage counters.
- [Responses create reference](https://developers.openai.com/api/reference/resources/responses/methods/create):
  input items, function calls, textual/image/file call outputs, tools, and
  unknown request controls remain provider-owned.
- [Streaming Responses](https://developers.openai.com/api/docs/guides/streaming-responses):
  lifecycle, delta, completion, and error events are typed SSE events.
- [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching#requirements):
  GPT-5.6 and later report prompt cache writes as
  `usage.input_tokens_details.cache_write_tokens`.
- [Codex 0.144.1 Responses fixtures](https://github.com/openai/codex/blob/rust-v0.144.1/codex-rs/core/tests/common/responses.rs):
  current Codex exercises `/v1/responses`, `/v1/responses/compact`, SSE, and
  `function_call_output` follow-up requests.
