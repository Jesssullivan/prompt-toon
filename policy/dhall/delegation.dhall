{-
Toon of Mythos delegation policy — typed source of truth.

Regeneration target once dhall tooling is in the dev shell:

    dhall-to-json --file policy/dhall/delegation.dhall > policy/delegation.json

Until then policy/delegation.json is the hand-synced validated transition
artifact and tests/test_delegation_policy.py enforces its invariants.
-}

let T = ./DelegationPolicy.dhall

in    { `$comment` =
          "Validated transition artifact. Typed source of truth: policy/dhall/delegation.dhall. Regenerate with: just gen-policy (dhall-to-json --pretty --file policy/dhall/delegation.dhall). Hand-synced while dhall tooling is absent from the dev shell (normal degraded mode per lab test/dhall convention). Document shape is described by policy/delegation.schema.json. Invariants enforced by tests/test_delegation_policy.py."
      , schema_version = 2
      , metadata =
          { name = "toon-of-mythos-delegation"
          , owner_linear_issue = "TIN-2698"
          , founding_docs =
              [ "docs/founding-prompt.md", "docs/mythos-vision.md" ]
          }
      , scarce_resources =
          [ { id = "operator-attention"
            , kind = "human"
            , notes =
                "Operator SWE/systems-engineer attention. Limited. Spend on decisions, design, and verification of load-bearing claims."
            }
          , { id = "fable-tokens"
            , kind = "model"
            , notes =
                "Fable-class model tokens: expensive, not always available, can be slow, compute costly, token-limited. Spend on the synthesis seat only."
            }
          ]
      , seats =
          [ { id = "fable"
            , model_class = "fable"
            , cost_tier = "scarce"
            , default_personas = [ "fable" ]
            , forbidden_personas = [ "adversarial" ]
            , notes =
                "Fable-class synthesis seat: umbrella orchestration, architecture, engineering judgment, and review alongside the operator. Never the adversarial seat."
            }
          , { id = "opus"
            , model_class = "opus"
            , cost_tier = "high"
            , default_personas = [ "adversarial", "research" ]
            , forbidden_personas = [] : List Text
            , notes =
                "Adversarial, purple-team, and deepest-research seat. Default escalation target whenever fable tokens must be protected."
            }
          , { id = "sonnet"
            , model_class = "sonnet"
            , cost_tier = "medium"
            , default_personas = [ "research" ]
            , forbidden_personas = [ "adversarial" ]
            , notes =
                "Mid-cost research seat: pattern extraction, structured recon, and medium-depth sweeps."
            }
          , { id = "haiku"
            , model_class = "haiku"
            , cost_tier = "low"
            , default_personas = [ "mechanical", "research" ]
            , forbidden_personas = [ "adversarial" ]
            , notes =
                "Cheapest seat: locators, inventories, shallow greps, format conversions, and checklist execution."
            }
          ]
      , personas =
          [ { id = "fable"
            , model_classes = [ "fable" ]
            , purpose =
                "Synthesis, architecture, and engineering judgment; the second engineer in the room."
            , doctrine =
                "Fast synthesis and review seat. Avoid taking over bulk implementation or verification hammering unless explicitly asked. Never run adversarial, purple-team, or red-team analysis."
            , forbidden_tasks =
                [ "adversarial-analysis"
                , "purple-team"
                , "red-team"
                , "deep-iteration-hammering"
                , "bulk-execution"
                ]
            }
          , { id = "adversarial"
            , model_classes = [ "opus", "operator" ]
            , purpose =
                "Adversarial, purple-team, red-team, refutation, and deep-iteration verification lanes."
            , doctrine =
                "Route to opus or the operator personally. Never route to fable by default. Prompt the skeptic to refute, not to agree."
            , forbidden_tasks = [] : List Text
            }
          , { id = "research"
            , model_classes = [ "haiku", "sonnet", "opus" ]
            , purpose =
                "Wide research, deep Linear exploration, web sweeps, multi-repo recon."
            , doctrine =
                "Fan out wide and return dense structured reports. Escalate model class with task depth: haiku for locators, sonnet for pattern extraction, opus for the deepest research lanes."
            , forbidden_tasks = [ "final-synthesis" ]
            }
          , { id = "mechanical"
            , model_classes = [ "haiku" ]
            , purpose =
                "Locators, inventories, shallow greps, format conversions, checklist execution."
            , doctrine =
                "Cheapest lane. Keep prompts narrow and mechanical; return raw structured data."
            , forbidden_tasks = [ "design-decisions", "final-synthesis" ]
            }
          ]
      , lanes =
          [ { route = "mythos.synthesis"
            , persona = "fable"
            , purpose =
                "Umbrella orchestration, cross-lane synthesis, architecture, and decision framing for the operator."
            }
          , { route = "mythos.review"
            , persona = "fable"
            , purpose = "Code and design review seat alongside the operator."
            }
          , { route = "mythos.adversarial"
            , persona = "adversarial"
            , purpose =
                "Purple-team and refutation passes over synthesized findings before they reach the operator."
            }
          , { route = "mythos.research.wide"
            , persona = "research"
            , purpose = "Parallel domain recon across repos, docs, and infra."
            }
          , { route = "mythos.research.linear"
            , persona = "research"
            , purpose = "Deep Linear initiative/project/issue exploration."
            }
          , { route = "mythos.research.web"
            , persona = "research"
            , purpose = "Web sweeps: specs, releases, ecosystem discussion."
            }
          , { route = "mythos.mechanical.locate"
            , persona = "mechanical"
            , purpose = "File, directory, and config locators and inventories."
            }
          ]
      , enforcement =
          [ { id = "no-adversarial-on-fable"
            , severity = "error"
            , rule =
                "A lane whose persona is adversarial must never resolve to a persona that includes fable in its model classes."
            }
          , { id = "fable-forbidden-tasks"
            , severity = "error"
            , rule =
                "The fable persona must forbid adversarial-analysis, purple-team, red-team, deep-iteration-hammering, and bulk-execution."
            }
          , { id = "purpose-required"
            , severity = "error"
            , rule =
                "Every persona and lane carries a non-empty purpose string; routes self-document why, not just what."
            }
          , { id = "cheap-lane-escalation"
            , severity = "warn"
            , rule =
                "A research or mechanical lane routed to fable warns; escalation requires explicit operator justification."
            }
          , { id = "seat-registry-complete"
            , severity = "error"
            , rule =
                "Every model class a persona routes to, other than the operator, has an explicit seat record in seats; a seat never appears in the default personas of a persona that excludes its model class, and never serves a persona it lists as forbidden."
            }
          , { id = "provider-managed-defaults"
            , severity = "warn"
            , rule =
                "Harness default model selection stays provider-managed; do not pin fable, opus, sonnet, or haiku globally unless a host-level override is explicitly justified."
            }
          ]
      , attribution =
          { convention =
              "Attribute every synthesis/recon session to a lane in session notes, following lab docs/agent-notes/INDEX.md."
          , values =
              [ "fable ultracode"
              , "fable ultracode (workflow)"
              , "research dispatch (fable)"
              , "lab hygiene"
              , "lab hygiene (fable/opus)"
              , "opus-ultracode"
              , "read-only workflow designer"
              ]
          }
      }
    : T.Policy
