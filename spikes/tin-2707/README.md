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
  06/07 measure that divergence explicitly. C1 closes it with a
  Unicode-boundary post-filter (see "Verdict"), keeping the patterns
  verbatim rather than rewriting `\b` into consuming anchors.
- **Confusables**: the 45-pair table is generated from the Python source
  (`gen_corpus.py` sibling logic) — never hand-transcribed.
- **Corpus**: 12 cases in `corpus/` (regenerate with `gen_corpus.py`);
  all secrets synthetic; path is gitleaks-allowlisted.

## Kill criteria (from the C0 plan)

- Any parity gap that anchoring cannot close → C1 keeps normalize+redact in
  Python; Chapel owns tokenize/TOON/card-line only.
- utf8proc integration slipping the afternoon → pivot the spike to the
  pure-Chapel TOON encoder.

## Verdict — C0 GO (2026-07-09, first remote run)

First remote run (`ptoon-spike-parity` on x86_64-linux):

- **11/12 cases byte-identical**, including homoglyph/zero-width/BIDI/tag
  redaction, all 9 secret classes, PEM-split, defang inputs, and the
  full confusable table.
- **The single divergence is the predicted one** (07-boundary-unicode):
  RE2's ASCII `\b` treats CJK/accented neighbors as boundaries → Chapel
  redacts MORE than Python (safe direction; nothing leaks). Emoji
  neighbors match identically (not word chars in either engine).
- **Benchmark**: Chapel 2.5x (1 MiB: 0.243s vs 0.612s) and 2.4x (5 MiB:
  1.801s vs 4.330s) faster than the Python oracle on hostile digit-heavy
  input, including process startup and full normalize.
- Compile iterations needed after the toolchain landed: one (CHPL_HOME
  wrapper needed the remote-juggler PATH/-I include flags on Linux).

Neither kill criterion fired: utf8proc NFKC parity is exact (Unicode 15.1
aligned), and the sole regex gap is anchor-closable. **C1 (TIN-2708)
proceeds with redaction in Chapel.**

## Verdict — C1 divergence CLOSED (TIN-2708, 2026-07-09)

`parity-report-2026-07-09.md` (from
`/nix/store/yyv5ilvfiwbkpihkbad5xmn4z80hznmy-ptoon-spike-parity-0.1.0`):
**12/12 cases byte-identical, no divergence.** Case 07 now reports
`pattern-3` on both engines (the C0 run over-reported `pattern-2` from the
CJK-adjacent `ghp_` line). Case 07 was extended with the full adversarial
set — start/end-of-string secrets, 2/3/4-byte Unicode neighbors, composed
vs decomposed é, emoji neighbors, two adjacent secrets sharing one ASCII
separator, and a secret joined to CJK by `_` (a word char in both engines).

### The implemented fix: verbatim patterns + Unicode-boundary post-filter

The 9 SECRET_PATTERNS stay **verbatim** (Python's exact zero-width `\b`
matcher). For non-ASCII input, `redactFiltered` enumerates RE2's matches,
**drops** any whose leading/trailing `\b` rests on a non-ASCII
`\p{L}`/`\p{N}` neighbor, then splices `[REDACTED]` over the survivors.
This is provably exact: because RE2's word class `[A-Za-z0-9_]` is a strict
subset of Python's `[\p{L}\p{N}_]`, and every secret's edge char is ASCII,
RE2 can only ever admit *extra* matches — precisely those with a non-ASCII
word-char neighbor — so filtering exactly those recovers Python's match set,
at identical byte extents. ASCII-only input skips the filter entirely
(`replaceAndCount`, RE2 `\b` == Python `\b` there).

### Why the recorded "captured consuming-anchor" plan was superseded

The C0 note proposed rewriting `\b` into consuming `(^|[^\p{L}\p{N}_])` …
`($|[^\p{L}\p{N}_])` anchors + splice. Implementing it revealed two defects
that the post-filter avoids and that would fail the 12/12 byte gate:

1. **Trailing-separator regression (would break case 04).** Python's `\b`
   is zero-width, so pattern-9 `\b(?:\d[ -]?){13,19}\b` lets its own
   trailing `[ -]?` swallow the separator into the replaced span
   (`card 4111 1111 1111 1111 num` → `card [REDACTED]num`). A *consuming*
   trailing anchor forces backtracking that leaves the separator outside
   the span (`card [REDACTED] num`) — byte-divergent. RE2 has no lookahead,
   so the trailing anchor cannot be made zero-width.
2. **Adjacent-secrets miss.** For two secrets sharing one separator
   (`AKIA… AKIA…`), a consuming trailing anchor eats the separator, so
   match N+1's leading anchor can no longer match and the second secret
   leaks. The verbatim zero-width `\b` + RE2's non-overlapping `matches`
   iterator yields both.

The post-filter keeps the patterns verbatim, so match extents (including
those greedy trailing separators) are exactly Python's, and adjacent secrets
are handled by construction. Corpus cases 07 line-8 (adjacent) and line-6
(é-suffix) pin both facts.
