# Safety Reference

Prompt/output minimization is a security-sensitive boundary because it decides
what the next model sees.

Guardrails:

- Keep trust tiers separate: trusted instructions, user intent, untrusted tool
  output, retrieved evidence, and agent notes.
- Preserve provenance: source path/URL, timestamp where available, SHA-256, line
  range, and confidence.
- Redact before minimization. Validate after minimization.
- Prefer extractive cards for critical constraints. Use lossy summaries only as
  navigation aids.
- Preserve `must`, `never`, `only`, approvals, denials, scope, actor, deadline,
  and source labels.
- Quarantine prompt-injection-shaped strings as data. Do not convert them into
  instructions.
- If provenance is missing, redaction is uncertain, or a critical field is
  dropped, fail closed or require source re-open.
- Normalize before redaction: NFKC, zero-width/bidi/tag stripping, and
  targeted Cyrillic/Greek confusable folding run before secret patterns, so
  homoglyph and hidden-character obfuscation cannot split a match.
- Defang before emission: summary.md carries no live URLs (hxxp), no markdown
  images/links, no javascript:/vbscript:/data: URIs; claims are code-fenced.
- Every constraint/finding/question line carries its card's trust tier and
  flags; injection-shaped text is never promoted unflagged.
- TOON is never the primary card artifact: it drops sha256 and evidence, so
  it ships only as a compact view alongside the provenance-bearing JSONL.

Regression fixture classes:

- Fake role headers: `System:`, `Developer:`, `Tool:`.
- Indirect injection in web/email/tool text.
- Hidden Unicode/control characters.
- Markdown link/image exfil patterns.
- Secret-like tokens, API keys, emails, and long numeric identifiers.
- Critical constraints buried late or surrounded by filler.
- Benign scary words that must not be blindly removed.
