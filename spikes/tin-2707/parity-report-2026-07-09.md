# TIN-2707 / TIN-2708 C1 parity report

Chapel (RE2 + utf8proc NFKC) vs Python oracle (`redact_text`).

Snapshot of the in-build `run_parity.py` report from
`/nix/store/yyv5ilvfiwbkpihkbad5xmn4z80hznmy-ptoon-spike-parity-0.1.0`
(x86_64-linux remote builder). The C0 run left one predicted divergence
(07-boundary-unicode); TIN-2708 C1 closes it — all 12 cases are now
byte-identical.

## Corpus parity

| case | parity | chapel findings | python findings |
|---|---|---|---|
| 01-homoglyph-secret.txt | IDENTICAL | pattern-1 | pattern-1 |
| 02-zw-split-secret.txt | IDENTICAL | pattern-2 | pattern-2 |
| 03-bidi-tag-controls.txt | IDENTICAL | - | - |
| 04-secret-classes.txt | IDENTICAL | pattern-1,pattern-3,pattern-4,pattern-5,pattern-6,pattern-7,pattern-8,pattern-9 | pattern-1,pattern-3,pattern-4,pattern-5,pattern-6,pattern-7,pattern-8,pattern-9 |
| 05-homoglyph-imperatives.txt | IDENTICAL | - | - |
| 06-boundary-ascii.txt | IDENTICAL | pattern-3 | pattern-3 |
| 07-boundary-unicode.txt | IDENTICAL | pattern-3 | pattern-3 |
| 08-pem-split.txt | IDENTICAL | pattern-6 | pattern-6 |
| 09-mixed-doc.md | IDENTICAL | pattern-1,pattern-7 | pattern-1,pattern-7 |
| 10-clean-doc.txt | IDENTICAL | - | - |
| 11-nul-and-crlf.txt | IDENTICAL | - | - |
| 12-confusable-full.txt | IDENTICAL | - | - |

parity: 12/12 identical; divergent: none

## Divergence detail

None — all cases byte-identical.

The C0 divergence was 07-boundary-unicode: RE2's ASCII `\b` treated CJK /
accented neighbors as word boundaries, so Chapel over-redacted (reported
`pattern-2,pattern-3` where Python reported only `pattern-3`). C1 closes it
with a Unicode-boundary post-filter (see README "Verdict"): the 9
SECRET_PATTERNS stay verbatim, and each RE2 match whose leading/trailing
`\b` rests on a non-ASCII `\p{L}`/`\p{N}` neighbor — exactly the matches a
Unicode-aware `\b` would reject — is dropped before the splice. Case 07 now
reports `pattern-3` on both engines, byte-identical.

## Benchmark (hostile digit-heavy input, wall clock)

| size | chapel | python | speedup |
|---|---|---|---|
| 1 MiB | 0.263s | 0.622s | 2.4x |
| 5 MiB | 1.153s | 2.590s | 2.2x |
