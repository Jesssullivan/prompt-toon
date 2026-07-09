# TIN-2708 C1: libptoon build-lane skeleton

This directory is the permanent home of `libptoon`, the C ABI library
behind `--engine=chapel` (`docs/mythos-delivery-design.md` §7, C1). Today
it is a **skeleton only**: it proves the `chpl --library --dynamic` build
lane end-to-end and fixes the six-symbol C ABI surface. It carries no
redaction/normalize/defang/encode semantics — those graduate in from
`spikes/tin-2707/ptoon_spike.chpl` (the proven C0 parity port) in a
follow-on change.

## Layout

- `Ptoon.chpl` — the library module. Six `export proc` entry points:
  `ptoon_redact`, `ptoon_normalize`, `ptoon_defang`, `ptoon_toon_encode`,
  `ptoon_free`, `ptoon_engine_caps`. The four text-transform exports are
  stubs returning `PTOON_ERR_UNIMPLEMENTED`; `ptoon_free` and
  `ptoon_engine_caps` are real (they are C-ABI/build-lane plumbing, not
  text-transform semantics).
- `c_src/` — vendored C dependencies, copied from `spikes/tin-2707/c_src/`
  (utf8proc v2.9.0 + the `pt_nfkc`/`pt_free` FFI shim) plus a small new
  `ptoon_alloc.{c,h}` helper for copying Chapel-built strings into
  malloc'd, C-ABI-safe buffers.

## Ownership contract ("Keychain ownership contract")

Every `char*`/`void*` handed back across a `ptoon_*` export boundary is
malloc-owned by the callee (this library). The caller **must** release it
via `ptoon_free` and must not call libc `free()` directly, and must not
free the same pointer twice. `ptoon_free(nil)` is always safe.

## Fail-open contract (INV-5)

Every entry point returns a `c_int` status code. Zero (`PTOON_OK`) is
success; any negative value is a sentinel the C ABI caller (the future
Python `ctypes` wrapper, not built yet) must treat as **fail-open to the
Python engine** — never as a usable or partially-usable result.

## Build

Chapel compilation is remote-only (AGENTS.md doctrine, 2026-07-09) — never
run `chpl` locally on darwin. Build via:

```sh
just build-lib      # or: make build-lib
```

which runs `nix build .#packages.x86_64-linux.libptoon --print-build-logs`
(the flake's `libptoon` derivation). The derivation's check phase compiles
`Ptoon.chpl` with `chpl --fast --library --dynamic`, then `nm -D`s the
resulting `.so` to confirm all six `ptoon_*` symbols are exported. A
darwin variant is defined (`packages.aarch64-darwin.libptoon`) but not
required green yet — the pzm darwin builder is still in burn-in.

## What's next (out of this skeleton's scope)

- Port `normalize_text`/`redact_text`/`defang_text`/`encode_rows_to_toon`
  from `prompt_toon/cli.py` (via the C0 spike) into the four stub exports.
- Populate `ptoon_engine_caps`'s `patterns` field from the real
  `SECRET_PATTERNS` table once `ptoon_redact` is real (never hand-duplicate
  the pattern list here in the meantime).
- The `ctypes` wrapper on the Python side (`--engine=chapel`, fail-open
  per INV-5) and the shared `fixtures/` golden corpus + quickchpl property
  tests called out in the C1 ticket.
