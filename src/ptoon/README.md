# ptoon: the Chapel transform binary

`ptoon` is the Chapel binary behind `--engine=chapel`. It runs the bounded text transforms: NFKC normalization with confusable and zero-width folding, secret redaction and markdown or URI defanging. The Python code in `prompt_toon/cli.py` is the oracle. Every transform must match it byte for byte. The design lives in `docs/mythos-delivery-design.md` sections 7 and 8 (TIN-2708, TIN-2709).

This file covers three things: the CLI contract, the build lanes and the test lanes.

## Why a binary

C1 first built `libptoon`, a shared library that Python loaded through ctypes. Repeated calls into a `chpl --library` build crashed when a buffer was freed. Chapel's runtime does not support re-entry from a foreign thread. So the engine ships as a standalone binary. Python starts it as a subprocess, and Chapel owns its runtime from start to exit. `coforall` handles concurrency inside that one process.

Two callers use the binary. `prompt_toon/engine.py` (`ChapelEngine`) runs the one-shot commands. `prompt_toon/resident.py` owns the long-lived `ptoon serve` child. The provider gateway owns HTTP, auth, SSE and provider adaptation. Only typed context enters the transform pipe, and authority bytes pass through unchanged. Fleet policy keeps `serve` disabled for now.

## CLI contract

`ptoon <command> [limits...]` reads one command from `argv[1]`. The one-shot commands read all of stdin as raw bytes, write stdout and exit.

| Command | Optional limits | Output on stdout |
| --- | --- | --- |
| `normalize` | none | the normalized text |
| `defang` | none | the defanged text |
| `redact` | none | one `findings:<names>` line, then the redacted text |
| `redact-batch` | `maxInputBytes budgetMs` | one framed result for each document, in input order |
| `condense-batch` | `maxInputBytes budgetMs maxCards` | typed JSONL document, card, end and batch events |
| `condense` | `maxInputBytes budgetMs maxCards` | the document, card and end events, then one summary, one manifest and one batch event |
| `serve` | nine launch ceilings, listed below | resident v1 response frames until stdin closes |
| `caps` | none | the engine caps JSON |

The `findings:` line of `redact` holds the matched pattern names, joined by commas. It is empty when nothing matched. `caps` reports the engine, `utf8proc` (true), the Unicode version, the pattern count (9), `serve_protocol` (1) and the feature list.

`redact-batch` uses this framing:

```text
stdin:  <n>\n then n times: <byteLen>\n<raw bytes>
stdout: <n>\n then n times: <metaJson>\n<redactedByteLen>\n<redacted bytes>
```

`condense-batch` reads framed source, tier and body triplets. `condense` first reads a run header of five framed fields: `runId generatedAt minToonSavings defaultTier tierOverridesJson`. The same triplets follow. The binary echoes the header fields and never computes them.

`serve` takes up to nine positive integers in this order: `workers queueDepth maxDocs maxRequestBytes maxLabelBytes maxInputBytes maxBudgetMs maxCards maxResponseBytes`. The defaults are 16, 64, 64, 16 MiB, 4096, 2000000, 2000, 24 and 256 MiB. `maxInputBytes` must not exceed `maxRequestBytes`. `maxResponseBytes` must be at least `maxRequestBytes`. The service checks each ceiling before it allocates a transform. Responses can complete out of order. Each one carries its visible-ASCII request and stream IDs.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success. For `serve`, stdin closed cleanly and every response was written |
| 2 | a usage fault before any output: no command, an unknown command, too many limits, a limit that is not decimal or is out of range, `budgetMs` without `maxInputBytes` or a failed `serve` cross-check |
| 2 | in `serve`, a malformed, truncated or oversized request frame. A failed stdout write also exits 2 |
| other nonzero | a one-shot transform error or a malformed batch frame |

### Fail closed (INV-5)

- A one-shot transform that hits an error exits nonzero with a message on stderr. It never writes a partial result to stdout.
- In `redact-batch`, an error in one document withholds only that document. Its result has `withheld=true`, a reason and zero body bytes.
- A malformed batch frame fails the whole process. That covers a bad length, a body that overruns and trailing bytes.
- In `serve`, a request whose own limits are out of range or whose transform fails gets an `error` response frame. The service keeps running.
- In `serve`, a malformed request frame stops the reader. The workers finish the requests already accepted. Then the process exits 2.
- `engine.py` raises `EngineError` on a nonzero exit or any stderr output.
- `resolve_engine` in `cli.py` sets the policy. `--engine=chapel` stops with an error when the binary is missing. `--engine=auto` falls back to Python. `--engine=python` never runs the binary. It is the default for `condense`, `analyze` and `encode-toon`. `dogfood` defaults to `--engine=auto`.

`resolve_binary_path` in `engine.py` uses `$PROMPT_TOON_PTOON` when it is set. If that path names no file, the lookup finds no binary and stops there. Only when the variable is unset does it try `build/ptoon` under the repo root. `ResidentService` takes an explicit `binary_path` before either.

## Build lanes

Chapel compiles on remote builders only (AGENTS.md doctrine, 2026-07-09). Do not run `chpl` on a developer host. All three lanes compile `Main.chpl` with `chpl --fast -M src/ptoon`.

| Lane | Command | Output |
| --- | --- | --- |
| Nix, Linux | `just build-ptoon` or `make build-ptoon` | the x86_64-linux ELF from the remote builder, cache first |
| Nix, Darwin | `just build-ptoon-darwin` | the aarch64-darwin binary from the remote builder |
| Bazel on GF REAPI | `just flywheel-chapel` or `make bazel-ptoon` | `//src/ptoon:ptoon`, built with no cache and no local fallback |

- **Nix.** `.#packages.<system>.ptoon` builds the binary, then runs two smoke checks. `echo hi | ./ptoon normalize` must print text. `caps` must report `serve_protocol` 1. On Darwin the build also runs the document-count matrix, the resident serve round trip, the dogfood test and the quality fixtures against the binary.
- **Bazel.** The `chapel_binary` rule resolves its compiler through `ChapelToolchainInfo` in `//tools/bazel/chapel`. On x86_64-linux this is the GF worker runtime bridge, which is a stopgap. On aarch64-darwin it is only the GF Nix Chapel package on the managed worker. The Darwin execution platform puts `gf.toolchain-policy=chapel-ab552d8` in the action key. The Linux platform carries only `gf.platform`. The wrapper checks the pinned package and compiler version, and it never falls back to a host `chpl`. Compile and Darwin smoke actions are not cached, because the compiler closure digest is proof evidence and not part of the action key.
- **Manual tag.** `//src/ptoon:ptoon` is tagged `manual`, so `bazel test //...` skips it on every platform. It builds only when a command names it.
- **Darwin release.** The TIN-2949 bridge takes the exact GF output and its native smoke record. After it verifies them, it imports the bytes into a Nix closure. The publisher never compiles Chapel.

The modules reach the vendored C code in the repo-root `c_src/` (utf8proc v2.9.0 and the FFI shim) through file-relative `require` paths.

## Test lanes

| Lane | Command | Needs a Chapel compile | What it proves |
| --- | --- | --- | --- |
| Toolchain contract | `just bazel-chapel-toolchain-contract` | no | both platforms resolve the right toolchain, and the compile and Darwin smoke actions stay uncached |
| Python suite | `just test` | no | the Python oracle. Tests that need the binary skip when it is absent |
| Parity gate | `make parity` | yes, on x86_64-linux | the release correctness gate, listed below |
| Parity runner | `just parity --functions --require-chapel` | a built binary and the generated corpora | function parity against `fixtures/golden/` |
| GF compile proof | `just flywheel-chapel` | yes, on GF REAPI | a fresh remote `ChapelCompile` with its execution log |
| Darwin native smoke | the `PtoonNativeSmoke` action | yes, on the Darwin worker | `caps` bytes, NFKC, a redaction canary and serve output equal to `condense` output |
| Darwin definition | `native-darwin.yml` in CI | no | the aarch64-darwin derivation evaluates |

The parity runner reads the gitignored `fixtures/` tree. Run `tools/gen_fixtures.py` and then `tools/gen_golden.py` before it.

`just check` runs the compile check, the secrets scan, the Python suite, the quality fixtures, the Bazel module graph, the toolchain contract and `bazel test //...`. It never compiles Chapel, because `//src/ptoon:ptoon` is tagged `manual`.

`make parity` builds `.#packages.x86_64-linux.ptoon-parity`. It stops at the first failure. It regenerates the fixture and golden corpora, then runs these against the binary:

- the quality fixtures under both engines
- function parity and full `condense` parity against the Python goldens
- batch parity: `redact-batch` against one `redact` call for each document
- stream parity: `condense-batch` cards against the oracle cards
- the one-shot document-count matrix (1, 8, 32 and 64 documents)
- resident service parity and capacity (64 streams with bounded RSS)
- gateway capacity (64 HTTP and SSE streams for each provider)
- the PostToolUse hook canary
- the analyze regression against the committed baseline
- the full unit test suite, with the binary present so that the Chapel tests run

## Layout

| File | Role |
| --- | --- |
| `Main.chpl` | the entry point. It dispatches `argv[1]`, reads stdin and writes stdout |
| `Normalize.chpl` | `normalizeText`: NFKC through utf8proc, then strip and fold |
| `Redact.chpl` | `redactText`: the nine `SECRET_PATTERNS` as RE2 candidates. Each match is checked against Python's Unicode word boundary |
| `Defang.chpl` | `defangText`: markdown image, link and URI defanging in the oracle's order |
| `Batch.chpl` | `redactBatch`: one `coforall` task for each document |
| `Stream.chpl` | the framed condense event stream |
| `Cards.chpl`, `Sha256.chpl`, `Summary.chpl` | card extraction, raw-byte digests and the run summary |
| `Serve.chpl` | the resident v1 service with a fixed worker set and a bounded request ring |
| `Toon.chpl` | `toonEscape` and `encodeRows`. It is not compiled into the binary |
| `native_smoke.sh`, `native_smoke_expected.sh` | the Darwin native smoke script and its expected values |

`test/ptoon/PropertyTests.chpl` holds property tests for `Toon.chpl`. They stay advisory until quickchpl is pinned in the repo and runs in a remote gate.

## Next

- C4b (TIN-2793): run the Claude Messages adapter in shadow mode around the resident service. It must not change request bytes that carry authority.
- Compile `Toon.chpl` into the binary, with JSON scalars mapped to TOON cells.
- Pin quickchpl and run the property tests in a remote gate.
- Promote the Bazel REAPI compile lane when the repo-scoped ARC runner anchor lands (TIN-2704).

## References

These were checked on 2026-07-17. Recheck them before you change the Chapel or Bazel plumbing.

- Chapel 2.9: [`main()`](https://chapel-lang.org/docs/technotes/main.html), [IO](https://chapel-lang.org/docs/modules/standard/IO.html), [`chpl`](https://chapel-lang.org/docs/usingchapel/man.html), [C interop](https://chapel-lang.org/docs/technotes/extern.html), [libraries](https://chapel-lang.org/docs/technotes/libraries.html) and [Mason manifests](https://chapel-lang.org/docs/tools/mason/guide/manifestfile.html)
- Bazel: [platforms](https://bazel.build/extending/platforms), [toolchains](https://bazel.build/extending/toolchains), [common attributes](https://bazel.build/reference/be/common-definitions), [actions](https://bazel.build/rules/lib/builtins/actions), [remote execution](https://bazel.build/remote/rbe) and [the Build Event Protocol](https://bazel.build/remote/bep)
- [Remote Execution API v2](https://github.com/bazelbuild/remote-apis/blob/main/build/bazel/remote/execution/v2/remote_execution.proto), for the action-keyed `gf.toolchain-policy` property
