# TIN-2708/C2: `ptoon` (Chapel normalize/redact/defang core)

This directory is the permanent home of `ptoon`, the standalone Chapel
binary behind `--engine=chapel` (`docs/mythos-delivery-design.md` §7–8,
C1/C2). As of C1 it carries **real semantics** graduated in from
`spikes/tin-2707/ptoon_spike.chpl` (the proven C0 parity port): NFKC
normalization + confusable/zero-width folding, Unicode-boundary-exact
secret redaction, and markdown/URI defanging, all byte-parity-gated
against the `prompt_toon/cli.py` Python oracle. As of C2b it also exposes
the `redact-batch` coforall fan-in entrypoint: N documents are redacted
concurrently inside one Chapel process while preserving byte-identical
per-document semantics. C2d/e add `condense-batch` and `condense`, which emit
typed JSONL events for one framed invocation and then exit. None of these
commands is a resident service.

## Architecture pivot: subprocess binary, not a C-ABI library

C1 originally targeted `libptoon`, a ctypes-loaded shared library with
exported `ptoon_*` procs. That path was **abandoned**: repeated
exported-proc calls into a `chpl --library` build segfaulted on buffer
free (init + first calls succeeded; `ptoon_free` crashed). Chapel's
foreign-thread runtime re-entry is fragile, and `chpl_library_init` +
allocator-consistency (`allocate`/`deallocate`) did not clear it.

The engine is therefore a **standalone `ptoon` binary invoked as a
subprocess**, one call per text-transform or framed batch. This sidesteps the
runtime-reentry wall and lets C2 `coforall` own concurrency *inside* the
Chapel runtime (no N host-thread re-entries). The current PostToolUse adapter
uses this one-shot boundary. PostToolBatch can add `additionalContext`, but
cannot `updatedToolOutput`-rewrite a completed batch; per-tool replacement
remains PostToolUse.

C4a (TIN-2792) targets a separate resident `ptoon serve` mode over private
framed pipes with fixed transform workers and explicit capacity limits. It is
implemented in this source tree and remains disabled in fleet policy. The
provider gateway, not Chapel, owns HTTP, auth, SSE, and provider adaptation;
only typed context enters the transform pipe and authority bytes are preserved.

## Binary protocol (fixed contract)

`ptoon` takes one subcommand as `argv[1]` and reads all of stdin as raw
bytes:

- `ptoon normalize` → normalized text to stdout, exit 0.
- `ptoon defang`    → defanged text to stdout, exit 0.
- `ptoon redact`    → exactly one line `findings:<comma-joined pattern
  names or empty>\n`, then the redacted text verbatim, exit 0.
- `ptoon redact-batch` → length-prefixed N-document stdin; length-prefixed
  per-document JSON+body results in input order, exit 0.
- `ptoon condense-batch` → length-prefixed source/tier/body triplets; typed
  JSONL document/card/end/batch events in input order, exit 0.
- `ptoon condense` → `condense-batch` plus a framed run header and trailing
  summary/manifest events, exit 0.
- `ptoon serve` → resident v1 request/response frames over private pipes;
  responses may complete out of order and are keyed by visible-ASCII request
  and stream IDs. Fixed launch ceilings bound workers, queue slots, documents,
  request/response bytes, labels, per-document bytes, budget, and card count.
- `ptoon caps`      → engine-caps JSON to stdout, exit 0.
- unknown subcommand → stderr message, exit 2.

`prompt_toon/engine.py`'s `ChapelEngine` shells out over this protocol.
The `redact-batch` protocol is:

```text
stdin:  <n>\n then repeated n times: <byteLen>\n<raw bytes>
stdout: <n>\n then repeated n times:
        <metaJson>\n<redactedByteLen>\n<redacted bytes>
```

Malformed batch frames fail closed: malformed lengths, overrun bodies, and
trailing bytes make the process exit nonzero rather than guessing.

## Layout

- `Main.chpl` — `proc main(args)`: the binary entry. Dispatches `argv[1]`
  (normalize/redact/defang/redact-batch/condense-batch/condense/serve/caps) over the
  fixed protocols above;
  reads stdin with `stdin.readAll(bytes)`, writes stdout. `use Normalize,
  Redact, Defang, Batch, Stream, Serve`.
- `Batch.chpl` — `redactBatch`: parses the C2b length-prefixed batch,
  runs one `coforall` task per document, each task calling the full sequential
  `redactText`, then emits input-ordered length-prefixed results. A per-doc
  redaction exception withholds that document with zero body bytes.
- `Stream.chpl` — parses framed source/tier/body documents and emits the
  one-shot condensation event stream used by `condense-batch` and `condense`.
- `Serve.chpl` — incrementally reads resident v1 frames, applies launch
  ceilings before transform allocation, drains a bounded sync-slot ring with a
  fixed worker set, and emits one indivisible response frame per request.
- `Normalize.chpl` — `normalizeText` (NFKC via utf8proc + strip/fold),
  plus `isWordCp`/`unicodeVersion`. FFI to the vendored C shim via
  `require "../../c_src/normalize_ffi.h", ...` (file-relative, repo-root
  `c_src/`).
- `Redact.chpl` — `redactText`: the nine `SECRET_PATTERNS` as RE2
  candidate generators, each match re-validated against Python's Unicode
  `\b` (`isWordCp`) and spliced with `[REDACTED]`. This is the C0
  boundary-divergence fix (see the module header). C2a hoists the generators
  to module-level `const` so C2b batch tasks share them read-only.
- `Defang.chpl` — `defangText`: markdown image/link + dangerous-URI
  neutralization in cli.py's exact sub/replace order.
- `Toon.chpl` — `toonEscape`/`encodeRows`. **Not** compiled into the current
  binary (deliberately excluded from `Main.chpl`). Property-test source
  exists under `test/ptoon/PropertyTests.chpl`, but it is advisory until
  quickchpl is repo-pinned and wired into a remote-only gate. No current
  binary protocol command calls TOON.
- Vendored C dependencies live at repo-root `c_src/` (utf8proc v2.9.0 +
  the `pt_nfkc`/`pt_free`/`pt_is_word`/`pt_unicode_version` FFI shim); the
  modules reach it via `require "../../c_src/..."`. (The spike keeps its
  own self-contained `spikes/tin-2707/c_src/` copy.)

## Fail-open / fail-closed contract (INV-5)

Any caught Chapel error in a top-level single-document transform makes the
binary exit nonzero with a stderr message — never a partial result on stdout
(redact/normalize/defang fail closed). `redact-batch` adds a per-document
fail-closed result: a redaction exception for one document emits
`withheld=true`, a reason, and zero body bytes; malformed framing still makes
the whole process exit nonzero. `prompt_toon/engine.py` raises `EngineError`
on any nonzero exit or non-empty stderr; policy lives one layer up in
`prompt_toon/cli.py`'s `resolve_engine`: `--engine=chapel` fails closed
(SystemExit) when the `ptoon` binary is unavailable, `--engine=auto`
fails open to the Python engine, and `--engine=python` (the default)
never touches the binary.

## Build — remote-only, two execution planes

Chapel compilation is remote-only (AGENTS.md doctrine, 2026-07-09) — never
run `chpl` locally on darwin. Two remote planes produce the same
x86_64-linux ELF:

```sh
just build-ptoon      # or: make build-ptoon   — nix remote builder (cache-first)
just flywheel-chapel  #      make bazel-ptoon   — Bazel on GF REAPI (executor-backed)
```

- **nix remote builder** (current release substrate):
  `nix build .#packages.x86_64-linux.ptoon` compiles `Main.chpl` with
  `chpl --fast -M src/ptoon -o ptoon` on the x86_64-linux builder and
  smoke-tests `echo hi | ./ptoon normalize`.
- **Bazel on GF REAPI** (executor-backed graph and cache plane):
  `//src/ptoon:ptoon` uses the mandatory `ChapelToolchainInfo` boundary in
  `//tools/bazel/chapel`. The registered x86_64-linux implementation is the
  explicitly transitional GF worker-runtime bridge. Darwin has a distinct
  execution platform but no registered compiler, so analysis fails closed
  until TIN-2949 supplies a declared hermetic toolchain; it cannot fall through
  to a developer-host `chpl`. `just bazel-chapel-toolchain-contract` proves
  both analysis outcomes without running a compile action. The executor lane
  also keeps `--remote_local_fallback=false`.

## Primary references checked 2026-07-16

Keep this lane grounded in current upstream docs when touching Chapel/Bazel
plumbing:

- Chapel 2.9 `main()` technote:
  https://chapel-lang.org/docs/technotes/main.html. This is the source for
  `proc main(args: [] string)` explicit argument handling and integer return
  status.
- Chapel 2.9 IO docs:
  https://chapel-lang.org/docs/modules/standard/IO.html. `stdin` is a
  predefined `fileReader`; one-shot commands use `readAll(bytes)`, while
  `serve` uses bounded `readLine`/`readBinary` reads and the standard locking
  writer.
- Chapel 2.9 `chpl` man page:
  https://chapel-lang.org/docs/usingchapel/man.html. This is the source for
  `--fast`, `-M/--module-dir`, and `-o` in the exact C1 compile command.
- Chapel 2.9 C interop / `require` docs:
  https://chapel-lang.org/docs/technotes/extern.html. `require` entries are
  file-relative to the Chapel source file, which is why `Normalize.chpl`
  owns the vendored C shim linkage instead of routing it through Mason.
- Chapel 2.9 library technote and release note:
  https://chapel-lang.org/docs/technotes/libraries.html and
  https://chapel-lang.org/blog/posts/announcing-chapel-2.9/. Chapel library
  interop remains under-development and requires runtime setup/cleanup; 2.9's
  new dynamically loaded parallel-library support is promising, but it stays a
  post-C2 investigation, not the C1 engine boundary.
- Mason manifest docs:
  https://chapel-lang.org/docs/tools/mason/guide/manifestfile.html. The
  local `Mason.toml` is `type = "application"` with `compopts = "-M src/ptoon"`;
  quickchpl remains advisory until pinned into a remote gate.
- Bazel platforms, toolchains, and common attributes:
  https://bazel.build/extending/platforms and
  https://bazel.build/extending/toolchains and
  https://bazel.build/reference/be/common-definitions. These are the sources
  for execution/target platform separation, compiler selection, and
  `tags = ["manual"]` keeping `//src/ptoon:ptoon` out of wildcard local builds.
- Bazel Starlark actions and remote execution docs:
  https://bazel.build/rules/lib/builtins/actions,
  https://bazel.build/remote/rbe, and https://bazel.build/remote/rules. These
  anchor the `ctx.actions.run` action with declared tool/runfiles inputs. The
  remaining C3 work replaces the Linux environmental bridge and registers the
  Darwin compiler closure; it does not regress to action-time downloads.
- Bazel command/options and remote-build performance docs:
  https://bazel.build/docs/user-manual and
  https://bazel.build/advanced/performance/build-performance-breakdown. These
  anchor `--platforms`, `--host_platform`, `--extra_execution_platforms`, and
  `--remote_download_minimal`.

## Parity gate

`nix build .#packages.x86_64-linux.ptoon-parity` is the release correctness
gate: it regenerates the shared fixture corpus (`tools/gen_fixtures.py` +
`tools/gen_golden.py`), then diffs the `ptoon` binary against the Python
oracle both per-function (`tools/parity_runner.py --functions`) and
through the full `condense` pipeline (`tools/parity_runner.py`), failing
the build on any byte divergence. The engine unittest
(`tests/test_engine.py`) also runs there with the binary present so the
otherwise-skipped chapel-dependent assertions execute.

## What's next

- C4b (TIN-2793): put the Claude Messages adapter in shadow mode around the
  resident service without changing authority-bearing request bytes.
- Compile `Toon.chpl` into the binary with typed-row handling (JSON
  scalar → TOON cell) so TOON can be driven through the protocol.
- Repo-pin quickchpl and wire the property tests into a remote-only gate.
- Promote the Bazel REAPI compile lane after the repo-scoped ARC runner anchor
  lands (TIN-2704); the repo/executor switches are already armed.
