# TIN-2708 C1: `ptoon` (Chapel normalize/redact/defang core)

This directory is the permanent home of `ptoon`, the standalone Chapel
binary behind `--engine=chapel` (`docs/mythos-delivery-design.md` §7–8,
C1). As of C1 it carries **real semantics** graduated in from
`spikes/tin-2707/ptoon_spike.chpl` (the proven C0 parity port): NFKC
normalization + confusable/zero-width folding, Unicode-boundary-exact
secret redaction, and markdown/URI defanging, all byte-parity-gated
against the `prompt_toon/cli.py` Python oracle.

## Architecture pivot: subprocess binary, not a C-ABI library

C1 originally targeted `libptoon`, a ctypes-loaded shared library with
exported `ptoon_*` procs. That path was **abandoned**: repeated
exported-proc calls into a `chpl --library` build segfaulted on buffer
free (init + first calls succeeded; `ptoon_free` crashed). Chapel's
foreign-thread runtime re-entry is fragile, and `chpl_library_init` +
allocator-consistency (`allocate`/`deallocate`) did not clear it.

The engine is therefore a **standalone `ptoon` binary invoked as a
subprocess**, one call per text-transform. This both sidesteps the
runtime-reentry wall and matches the real deployment surfaces (Claude
Code `PostToolBatch` hooks, an MCP gateway, `ptoon --stream`), which are
all subprocess-or-stream shapes over one process, never in-process FFI.
It also lets the C2 `coforall` batch entrypoint own concurrency *inside*
the Chapel runtime (no N host-thread re-entries). See the memory note
`chapel-engine-boundary` and design §8.

## Binary protocol (fixed contract)

`ptoon` takes one subcommand as `argv[1]` and reads all of stdin as raw
bytes:

- `ptoon normalize` → normalized text to stdout, exit 0.
- `ptoon defang`    → defanged text to stdout, exit 0.
- `ptoon redact`    → exactly one line `findings:<comma-joined pattern
  names or empty>\n`, then the redacted text verbatim, exit 0.
- `ptoon caps`      → engine-caps JSON to stdout, exit 0.
- unknown subcommand → stderr message, exit 2.

`prompt_toon/engine.py`'s `ChapelEngine` shells out over this protocol.

## Layout

- `Main.chpl` — `proc main(args)`: the binary entry. Dispatches `argv[1]`
  (normalize/redact/defang/caps) over the fixed protocol above; reads
  stdin with `stdin.readAll(bytes)`, writes stdout. `use Normalize,
  Redact, Defang`.
- `Normalize.chpl` — `normalizeText` (NFKC via utf8proc + strip/fold),
  plus `isWordCp`/`unicodeVersion`. FFI to the vendored C shim via
  `require "../../c_src/normalize_ffi.h", ...` (file-relative, repo-root
  `c_src/`).
- `Redact.chpl` — `redactText`: the nine `SECRET_PATTERNS` as RE2
  candidate generators, each match re-validated against Python's Unicode
  `\b` (`isWordCp`) and spliced with `[REDACTED]`. This is the C0
  boundary-divergence fix (see the module header).
- `Defang.chpl` — `defangText`: markdown image/link + dangerous-URI
  neutralization in cli.py's exact sub/replace order.
- `Toon.chpl` — `toonEscape`/`encodeRows`. **Not** compiled into the C1
  binary (deliberately excluded from `Main.chpl`). Property-test source
  exists under `test/ptoon/PropertyTests.chpl`, but it is advisory until
  quickchpl is repo-pinned and wired into a remote-only gate. TOON is never
  called through the binary protocol until C2.
- Vendored C dependencies live at repo-root `c_src/` (utf8proc v2.9.0 +
  the `pt_nfkc`/`pt_free`/`pt_is_word`/`pt_unicode_version` FFI shim); the
  modules reach it via `require "../../c_src/..."`. (The spike keeps its
  own self-contained `spikes/tin-2707/c_src/` copy.)

## Fail-open / fail-closed contract (INV-5)

Any caught Chapel error in a transform makes the binary exit nonzero with
a stderr message — never a partial result on stdout (redact/normalize/
defang fail closed). `prompt_toon/engine.py` raises `EngineError` on any
nonzero exit or non-empty stderr; policy lives one layer up in
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

- **nix remote builder** (today's default C1 substrate):
  `nix build .#packages.x86_64-linux.ptoon` compiles `Main.chpl` with
  `chpl --fast -M src/ptoon -o ptoon` on the x86_64-linux builder and
  smoke-tests `echo hi | ./ptoon normalize`.
- **Bazel on GF REAPI** (the C2/endgame execution plane, pulled forward):
  `//src/ptoon:ptoon` (the `chapel_binary` walking-skeleton rule in
  `//tools/bazel/chapel:defs.bzl`) runs the identical compile on the
  GloriousFlywheel REAPI executor. The target is
  `target_compatible_with` linux, so a local darwin build is
  incompatible-by-design, and the executor lane fails closed
  (`--remote_local_fallback=false`) if `BAZEL_REMOTE_EXECUTOR` is not
  armed. nix owns the chpl version; Bazel owns the graph/cache/execution.

## Primary references checked 2026-07-09

Keep this lane grounded in current upstream docs when touching Chapel/Bazel
plumbing:

- Chapel 2.9 `main()` technote:
  https://chapel-lang.org/docs/technotes/main.html. This is the source for
  `proc main(args: [] string)` explicit argument handling and integer return
  status.
- Chapel 2.9 IO docs:
  https://chapel-lang.org/docs/modules/standard/IO.html. `stdin` is a
  predefined `fileReader`; `fileReader.readAll(type t = bytes)` reads the
  remaining stream as `bytes` or `string`.
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
- Bazel platforms and common attributes:
  https://bazel.build/extending/platforms and
  https://bazel.build/reference/be/common-definitions. These are the sources
  for `target_compatible_with` and `tags = ["manual"]` keeping
  `//src/ptoon:ptoon` out of wildcard local builds.
- Bazel Starlark actions and remote execution docs:
  https://bazel.build/rules/lib/builtins/actions,
  https://bazel.build/remote/rbe, and https://bazel.build/remote/rules. These
  anchor the walking-skeleton `ctx.actions.run_shell` action and the C3
  follow-up to replace PATH/env `chpl` with a proper Chapel toolchain rule.
- Bazel command/options and remote-build performance docs:
  https://bazel.build/docs/user-manual and
  https://bazel.build/advanced/performance/build-performance-breakdown. These
  anchor `--platforms`, `--host_platform`, `--extra_execution_platforms`, and
  `--remote_download_minimal`.

## Parity gate

`nix build .#packages.x86_64-linux.ptoon-parity` is the C1 correctness
gate: it regenerates the shared fixture corpus (`tools/gen_fixtures.py` +
`tools/gen_golden.py`), then diffs the `ptoon` binary against the Python
oracle both per-function (`tools/parity_runner.py --functions`) and
through the full `condense` pipeline (`tools/parity_runner.py`), failing
the build on any byte divergence. The engine unittest
(`tests/test_engine.py`) also runs there with the binary present so the
otherwise-skipped chapel-dependent assertions execute.

## What's next (C2 and beyond)

- `coforall` batch entrypoint (`ptoon --stream` / batch-across-documents)
  so a wide research spool condenses N subagent outputs concurrently
  inside the Chapel runtime.
- Compile `Toon.chpl` into the binary with typed-row handling (JSON
  scalar → TOON cell) so TOON can be driven through the protocol.
- Repo-pin quickchpl and wire the property tests into a remote-only gate.
- Promote the Bazel REAPI compile lane once the GF executor is armed.
