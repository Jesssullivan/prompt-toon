# TIN-2707 C0 parity report

Chapel (RE2 + utf8proc NFKC) vs Python oracle (`redact_text`).

## Corpus parity

| case | parity | chapel findings | python findings |
|---|---|---|---|
| 01-homoglyph-secret.txt | IDENTICAL | pattern-1 | pattern-1 |
| 02-zw-split-secret.txt | IDENTICAL | pattern-2 | pattern-2 |
| 03-bidi-tag-controls.txt | IDENTICAL | - | - |
| 04-secret-classes.txt | IDENTICAL | pattern-1,pattern-3,pattern-4,pattern-5,pattern-6,pattern-7,pattern-8,pattern-9 | pattern-1,pattern-3,pattern-4,pattern-5,pattern-6,pattern-7,pattern-8,pattern-9 |
| 05-homoglyph-imperatives.txt | IDENTICAL | - | - |
| 06-boundary-ascii.txt | IDENTICAL | pattern-3 | pattern-3 |
| 07-boundary-unicode.txt | DIVERGENT | pattern-2,pattern-3 | pattern-3 |
| 08-pem-split.txt | IDENTICAL | pattern-6 | pattern-6 |
| 09-mixed-doc.md | IDENTICAL | pattern-1,pattern-7 | pattern-1,pattern-7 |
| 10-clean-doc.txt | IDENTICAL | - | - |
| 11-nul-and-crlf.txt | IDENTICAL | - | - |
| 12-confusable-full.txt | IDENTICAL | - | - |

## Divergence detail

### 07-boundary-unicode.txt

- chapel: `b'\xe6\x97\xa5\xe6\x9c\xac[REDACTED]\n[REDACTED]\xe6\x97\xa5\xe6\x9c\xac\n\xc3\xa9[REDACTED]\n\xc3\xa9[REDACTED]\n\xf0\x9f\x9a\x80[REDACTED]\n\xe4\xb8\xad\xe6\x96\x87[REDACTED]\xe4\xb8\xad\xe6\x96\x87'`
- python: `b'\xe6\x97\xa5\xe6\x9c\xacAKIAIOSFODNN7EXAMPLE\nAKIAIOSFODNN7EXAMPLE\xe6\x97\xa5\xe6\x9c\xac\n\xc3\xa9AKIAIOSFODNN7EXAMPLE\n\xc3\xa9AKIAIOSFODNN7EXAMPLE\n\xf0\x9f\x9a\x80[REDACTED]\n\xe4\xb8\xad\xe6\x96\x87ghp_ABCDEFGHIJKLMNOP123456\xe4\xb8\xad\xe6\x96\x87'`


## Benchmark (hostile digit-heavy input, wall clock)

| size | chapel | python | speedup |
|---|---|---|---|
| 1 MiB | 0.243s | 0.612s | 2.5x |
| 5 MiB | 1.801s | 4.330s | 2.4x |
