{-
Toon of Mythos delegation policy — typed source of truth.

policy/delegation.json is generated from this file. dhall-json ships in the
flake dev shell, so the regeneration path is live:

    just gen-policy
    # == dhall-to-json --pretty --file policy/dhall/delegation.dhall

Degraded mode (dhall-to-json off PATH) is still supported: hand-sync the JSON
to this source byte-for-byte, per the lab test/dhall convention.
tests/test_delegation_policy.py enforces the invariants either way, and
compares generated-vs-committed whenever dhall-to-json is reachable.
-}

let T = ./DelegationPolicy.dhall

in    { `$comment` =
          "Generated artifact. Typed source of truth: policy/dhall/delegation.dhall. Regenerate with: just gen-policy (dhall-to-json --pretty --file policy/dhall/delegation.dhall); dhall-json ships in the flake dev shell. Degraded mode when dhall-to-json is off PATH: hand-sync this file to the Dhall byte-for-byte, per the lab test/dhall convention. Document shape is described by policy/delegation.schema.json. Invariants enforced by tests/test_delegation_policy.py."
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
      , harness_seats =
          [ { id = "pi"
            , harness = "pi"
            , model_lane = "provider-managed"
            , purpose =
                "Bounded technical-review harness seat. Fable delegates GitHub PR review, review commentary, and mechanical code-quality output here and reads the result back as evidence."
            , delegable_tasks =
                [ "technical-review", "pr-review", "code-quality-mechanical" ]
            , forbidden_tasks =
                [ "word-leg-ratification"
                , "adversarial-refutation-of-record"
                , "final-synthesis"
                , "secret-mutation"
                , "process-control"
                ]
            , dispatch_surface =
                "lab-managed bounded pi-* launchers (never raw binaries)"
            , posture = "evidentiary-only"
            }
          , { id = "pi-k3"
            , harness = "pi"
            , model_lane = "kimi-coding/k3"
            , purpose =
                "This IS the Kimi reviewer lane: Kimi rides the pi harness because the native kimi CLI has no dispatch envelope. Same bounded technical-review envelope and same evidentiary-only posture as the pi seat."
            , delegable_tasks =
                [ "technical-review", "pr-review", "code-quality-mechanical" ]
            , forbidden_tasks =
                [ "word-leg-ratification"
                , "adversarial-refutation-of-record"
                , "final-synthesis"
                , "secret-mutation"
                , "process-control"
                ]
            , dispatch_surface =
                "lab-managed bounded pi-* launchers (never raw binaries)"
            , posture = "evidentiary-only"
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
                , "process-control"
                ]
            }
          , { id = "adversarial"
            , model_classes = [ "opus", "operator" ]
            , purpose =
                "Adversarial, purple-team, red-team, refutation, and deep-iteration verification lanes."
            , doctrine =
                "Route to opus or the operator personally. Never route to fable by default. Prompt the skeptic to refute, not to agree."
            , forbidden_tasks = [ "process-control" ]
            }
          , { id = "research"
            , model_classes = [ "haiku", "sonnet", "opus" ]
            , purpose =
                "Wide research, deep Linear exploration, web sweeps, multi-repo recon."
            , doctrine =
                "Fan out wide and return dense structured reports. Escalate model class with task depth: haiku for locators, sonnet for pattern extraction, opus for the deepest research lanes."
            , forbidden_tasks = [ "final-synthesis", "process-control" ]
            }
          , { id = "mechanical"
            , model_classes = [ "haiku" ]
            , purpose =
                "Locators, inventories, shallow greps, format conversions, checklist execution."
            , doctrine =
                "Cheapest lane. Keep prompts narrow and mechanical; return raw structured data."
            , forbidden_tasks =
                [ "design-decisions", "final-synthesis", "process-control" ]
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
          , { route = "mythos.delegate.review"
            , persona = "pi"
            , purpose =
                "Technical review delegated to a harness seat: GitHub PR review and review commentary, returned as adversarial_review_pass evidence for the fable synthesis seat. Kimi rides pi-k3 on the identical envelope. Never the operator WORD leg."
            }
          , { route = "mythos.delegate.mechanical"
            , persona = "pi"
            , purpose =
                "Mechanical code-quality output delegated to a harness seat: lint-shaped findings, structural nits, and checklist sweeps over a diff. Evidence only; the ruling stays with the operator and the fable seat."
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
          , { id = "harness-delegation-envelope"
            , severity = "error"
            , rule =
                "A harness seat may only receive tasks listed in its own delegable_tasks. It never owns the operator WORD/ratification leg and never owns the adversarial-refutation-of-record; harness output is evidence for the fable synthesis seat, never the ruling. A lane routed to a harness seat therefore carries evidentiary work only."
            }
          , { id = "harness-dispatch-bounded-only"
            , severity = "error"
            , rule =
                "Harness dispatch goes through the lab-managed bounded launchers named in the seat's dispatch_surface (the pi-* wrappers). Never execute a raw agent binary as a dispatch or probe surface."
            }
          , { id = "serial-workflow-on-fan-out"
            , severity = "warn"
            , rule =
                "When the operator asks to fan out, decompose the work into parallel concurrent workflow lanes, never one serial workflow. Each lane return is a Fable-operator interview checkpoint — live re-planning plus HITL ratification of merges and priorities — which a serial handoff suppresses."
            }
          , { id = "process-control-absolute"
            , severity = "error"
            , rule =
                "No seat, harness seat, or persona may signal a process in any form -- kill, pkill, killall, tmux kill-server/kill-session/kill-pane/kill-window, systemctl stop/kill, launchctl kill/bootout, a literal PID, or any other process signal. This is absolute and has no exception; it binds every seat and persona even where forbidden_tasks has no slot (the seats record: fable, opus, sonnet, haiku). Ratified operator interview 2026-09-20 (R-N11), TIN-3692: an agent walked process ancestry to PID 1, found the operator's tmux server, and killed it after a guard hook had already refused the command twice; critical work was lost."
            }
          , { id = "hook-refusal-is-stop"
            , severity = "error"
            , rule =
                "A guard-hook refusal is a stop, not friction to route around. Quote the refusal verbatim to the operator and propose at most one materially different alternative -- never a reworded or re-encoded form of the same refused command -- before asking. Reformulating a refused command to evade the pattern is itself a ruling violation, independent of what the refused command was trying to do. Ratified operator interview 2026-09-20 (R-N12), TIN-3692."
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
