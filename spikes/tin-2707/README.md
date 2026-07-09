# TIN-2707 C0 spike: Chapel normalize+redact parity

Phase C0 of the Chapel-first rewrite (`docs/mythos-delivery-design.md` §7).
Ports `normalize_text` + `redact_text` (`prompt_toon/cli.py:119-145`) to
Chapel and measures byte parity + throughput against the Python oracle.

## Remote-only compilation (operator doctrine, 2026-07-09)

chpl compilation iteration runs on the remote substrate ONLY — never a
local darwin loop:

- **Today**: `nix build .#packages.x86_64-linux.ptoon-spike-parity` — the
  derivation compiles the spike AND runs the full parity corpus + benchmark
  on the x86_64-linux remote builder, nix cache-first. The parity report
  lands in `$out/parity-report.md`.
- **When the TIN-2704 executor lane arms**: `just flywheel-bazel build
  //spikes/tin-2707:ptoon_spike --config=executor-backed` (target below is
  `manual`-tagged until then so `bazel test //...` stays green).

The nix chapel package (`chapel-nix` flake input, `llvm-21-support` fork)
has no darwin runtime; the flake wrapper (copied from remote-juggler)
fixes the CHPL_HOME lib layout and bakes the compiler env.

## Design notes

- **NFKC**: vendored utf8proc v2.9.0 (Unicode 15.1 — matches CPython 3.13's
  unicodedata) via the `Keychain.chpl` extern-C `require` pattern. Failure
  aborts; text is never passed through un-normalized (INV-2 fail-closed).
- **Regex**: Chapel = RE2, linear-time. All 9 SECRET_PATTERNS ported
  verbatim. RE2 `\b` is ASCII-only vs Python's Unicode `\b` — corpus cases
  06/07 measure that divergence explicitly; the mitigation on divergence is
  explicit `[^\p{L}\p{N}_]`-style anchoring, decided by the report.
- **Confusables**: the 45-pair table is generated from the Python source
  (`gen_corpus.py` sibling logic) — never hand-transcribed.
- **Corpus**: 12 cases in `corpus/` (regenerate with `gen_corpus.py`);
  all secrets synthetic; path is gitleaks-allowlisted.

## Kill criteria (from the C0 plan)

- Any parity gap that anchoring cannot close → C1 keeps normalize+redact in
  Python; Chapel owns tokenize/TOON/card-line only.
- utf8proc integration slipping the afternoon → pivot the spike to the
  pure-Chapel TOON encoder.

## Verdict

See `parity-report.md` in the build output (report pending first full
remote run; recorded on TIN-2707 when in).
