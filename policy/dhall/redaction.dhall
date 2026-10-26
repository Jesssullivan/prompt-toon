{-
Redaction policy: typed source of truth (J82(d), PT-1b).

Regenerate the JSON artifact with:

    just gen-policy

prompt_toon/cli.py compiles SECRET_PATTERNS from policy/redaction.json.
src/ptoon/Redact.chpl carries the same nine patterns as RE2 candidates.
tests/test_redaction_policy.py fails on drift between them. The JSON is
also the input for a planned parity check in huskycat's hc-mask.

Patterns stay in a portable regex subset. No lookaround, backreferences,
named or atomic groups, inline flags or possessive quantifiers. The only
group forms are (...) and (?:...). Case folding is the case_insensitive
field, never an inline (?i).

Order is behaviour. Patterns run in list order and finding ids are the
pattern ids, so append new patterns and never reorder.
-}
let T = ./RedactionPolicy.dhall

let pattern =
      \(id : Text) ->
      \(kind : Text) ->
      \(source : Text) ->
        { id, kind, source, case_insensitive = False } : T.Pattern

in    { `$comment` =
          "Validated artifact. Typed source of truth: policy/dhall/redaction.dhall. Regenerate with: dhall-to-json --pretty --file policy/dhall/redaction.dhall. Drift and portability are enforced by tests/test_redaction_policy.py."
      , schema_version = 1
      , metadata =
        { name = "prompt-toon-redaction"
        , owner_linear_issue = "TIN-2700"
        , ruling = "J82(d)"
        }
      , dialect =
        { name = "portable-re-v1"
        , forbidden_constructs =
          [ "lookahead"
          , "lookbehind"
          , "backreference"
          , "named-group"
          , "atomic-group"
          , "inline-flag"
          , "conditional"
          , "possessive-quantifier"
          ]
        , word_boundary = "unicode"
        , rule =
            "Python re is the reference engine. Its \\b, \\w and \\d are Unicode-aware. An engine with ASCII-only \\b, such as RE2, must treat each match as a candidate and reject it when a real edge is not a Unicode word boundary, as src/ptoon/Redact.chpl does."
        }
      , replacement =
        { token = "[REDACTED]"
        , preserve_line_breaks = True
        , rule =
            "Each match becomes the token followed by the line-break characters the match contained, in order. Line numbering of the redacted text stays stable."
        }
      , patterns =
        [ pattern
            "pattern-1"
            "provider-token-dash"
            "\\b(?:sk|ghp|gho|github_pat|xox[baprs])-[-_A-Za-z0-9]{16,}\\b"
        , pattern
            "pattern-2"
            "provider-token-underscore"
            "\\b(?:sk|ghp|gho|github_pat)_[-_A-Za-z0-9]{16,}\\b"
        , pattern
            "pattern-3"
            "aws-access-key-id"
            "\\b(?:AKIA|ASIA)[0-9A-Z]{16}\\b"
        , pattern "pattern-4" "google-api-key" "\\bAIza[0-9A-Za-z_-]{35}\\b"
        , pattern
            "pattern-5"
            "jwt"
            "\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b"
        , pattern
            "pattern-6"
            "private-key-block"
            "-----BEGIN[A-Z ]*PRIVATE KEY-----[\\s\\S]+?-----END[A-Z ]*PRIVATE KEY-----"
        , pattern
            "pattern-7"
            "email-address"
            "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b"
        ,     pattern
                "pattern-8"
                "credential-assignment"
                "\\b(api[_-]?key|token|password|secret)\\s*[:=]\\s*['\"]?[^'\"\\s]{8,}"
          //  { case_insensitive = True }
        , pattern "pattern-9" "card-number" "\\b(?:\\d[ -]?){13,19}\\b"
        ]
      }
    : T.RedactionPolicy
