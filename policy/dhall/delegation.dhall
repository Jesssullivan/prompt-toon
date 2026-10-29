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
      , schema_version = 4
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
          , { id = "opus-quota"
            , kind = "model"
            , notes =
                "Opus 5.5 subagent quota. Spend it on mission-critical lanes first. When it runs out the opus-quota-exhausted budget fallback applies (J81(e))."
            }
          ]
      , providers =
          [ { id = "anthropic"
            , model_ids =
              [ "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5" ]
            , access =
                "Anthropic subscription or API, per seat. The fable seat stays provider-managed and carries no model_id."
            , notes =
                "Anthropic seats stay pinned to the 5.5 family (J81). A new Claude model id is a policy change, not a string edit."
            }
          , { id = "kimi"
            , model_ids = [ "kimi-k3.1-pro" ]
            , access =
                "Kimi Code subscription only. Never a Platform key."
            , notes =
                "Kimi K3.1 Pro for the cade.* lanes that name it. Unattended Kimi children run on the Kimi Code subscription under the extended risk acceptance (SPR-143). The engineering pi-k3 harness seat is a separate dispatch identity."
            }
          ]
      , seats =
          [ { id = "fable"
            , model_class = "fable"
            , model_id = None Text
            , provider = "anthropic"
            , cost_tier = "scarce"
            , default_personas = [ "fable" ]
            , forbidden_personas = [ "adversarial", "critical" ]
            , notes =
                "Fable-class synthesis seat: umbrella orchestration, architecture, engineering judgment and advisory review alongside the operator. Never the adversarial or critical seat. Its review never gates a merge."
            }
          , { id = "opus"
            , model_class = "opus"
            , model_id = Some "claude-opus-5-5"
            , provider = "anthropic"
            , cost_tier = "high"
            , default_personas =
              [ "critical"
              , "adversarial"
              , "research"
              , "business"
              , "ceo-ops"
              , "evidentiary-review"
              ]
            , forbidden_personas = [] : List Text
            , notes =
                "Opus 5.5 is the primary execution and verification seat (J81). It is the default for mission-critical lanes: implementation that merges, merge-gating and adversarial review, refutation-of-record, security-sensitive work plus cluster and host acts. It is also the deepest-research seat and the default escalation target whenever fable tokens must be protected."
            }
          , { id = "sonnet"
            , model_class = "sonnet"
            , model_id = Some "claude-sonnet-5-5"
            , provider = "anthropic"
            , cost_tier = "medium"
            , default_personas = [ "research", "drafting" ]
            , forbidden_personas = [ "adversarial" ]
            , notes =
                "Sonnet 5.5 is used freely for research sweeps, drafting, broad recon, non-merging prototypes and first-pass review. It is not the default for mission-critical lanes. It may continue mythos.implement.merge or mythos.gate.security only under the opus-quota-exhausted budget fallback. It never gates a merge."
            }
          , { id = "haiku"
            , model_class = "haiku"
            , model_id = Some "claude-haiku-5-5"
            , provider = "anthropic"
            , cost_tier = "low"
            , default_personas = [ "mechanical", "research" ]
            , forbidden_personas = [ "adversarial", "critical" ]
            , notes =
                "Haiku 5.5 is the cheapest seat and is used freely for mechanical lanes: locators, inventories, shallow greps, format conversions, polling, log summaries and checklist execution."
            }
          , { id = "kimi"
            , model_class = "kimi"
            , model_id = Some "kimi-k3.1-pro"
            , provider = "kimi"
            , cost_tier = "high"
            , default_personas =
              [ "research"
              , "drafting"
              , "business"
              , "marketing"
              , "ceo-ops"
              , "evidentiary-review"
              ]
            , forbidden_personas = [ "fable", "critical", "adversarial" ]
            , notes =
                "Kimi K3.1 Pro (kimi-k3.1-pro) through the Kimi Code subscription, never a Platform key. It serves research, drafting, business, marketing, CEO-ops and evidentiary review on the cade.* lanes that name it. It never gates a merge. It never owns the refutation-of-record, never carries the operator WORD leg and never runs a critical host act. It never serves confidential or health custody. Kimi engineering review rides the pi-k3 harness seat as evidence."
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
                , "merge-gating-review"
                , "final-synthesis"
                , "secret-mutation"
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
                , "merge-gating-review"
                , "final-synthesis"
                , "secret-mutation"
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
                ]
            }
          , { id = "critical"
            , model_classes = [ "opus", "operator" ]
            , purpose =
                "Mission-critical execution and verification: implementation that merges, merge-gating review, security-sensitive code plus cluster and host acts."
            , doctrine =
                "Rely on Opus 5.5 or the operator personally. A merge-gating review runs on Opus. When Opus quota is exhausted the opus-quota-exhausted fallback lets Sonnet continue mythos.implement.merge and mythos.gate.security only. Their merges wait for an Opus review. The downgraded lane never runs a host act or a credential change. The downgrade is recorded in the receipt. mythos.gate.review and mythos.act.host never fall back: they wait for Opus or go to the operator. Never fable or haiku."
            , forbidden_tasks = [] : List Text
            }
          , { id = "adversarial"
            , model_classes = [ "opus", "operator" ]
            , purpose =
                "Adversarial, purple-team, red-team, refutation, and deep-iteration verification lanes."
            , doctrine =
                "Route to Opus 5.5 or the operator personally. Never route to fable. Prompt the skeptic to refute, not to agree. The budget fallback does not cover this persona: when Opus quota is exhausted the refutation-of-record waits or goes to the operator."
            , forbidden_tasks = [] : List Text
            }
          , { id = "research"
            , model_classes = [ "haiku", "sonnet", "opus", "kimi" ]
            , purpose =
                "Wide research, deep Linear exploration, web sweeps, multi-repo recon."
            , doctrine =
                "Fan out wide and return dense structured reports. Escalate model class with task depth: haiku for locators, sonnet for pattern extraction and broad recon, opus for the deepest research lanes. Use haiku and sonnet freely here. Kimi K3.1 Pro researches only on the cade.* lanes that name it."
            , forbidden_tasks = [ "final-synthesis" ]
            }
          , { id = "drafting"
            , model_classes = [ "sonnet", "opus", "kimi" ]
            , purpose =
                "Drafting, non-merging prototypes and first-pass review ahead of an Opus gate."
            , doctrine =
                "Sonnet 5.5 by default; use it freely. Output is a draft or a first pass. It never stands in for a merge-gating review and never merges on its own. Kimi K3.1 Pro drafts only on the cade.* lanes that name it."
            , forbidden_tasks = [ "merge", "merge-gating-review", "final-synthesis" ]
            }
          , { id = "mechanical"
            , model_classes = [ "haiku" ]
            , purpose =
                "Locators, inventories, shallow greps, format conversions, polling, log summaries, checklist execution."
            , doctrine =
                "Cheapest lane. Keep prompts narrow and mechanical; return raw structured data."
            , forbidden_tasks = [ "design-decisions", "final-synthesis" ]
            }
          , { id = "business"
            , model_classes = [ "opus", "kimi" ]
            , purpose =
                "Business work for Cade: offers, partnerships, pipeline analysis and operating plans short of a commitment."
            , doctrine =
                "Opus 5.5 by default. Kimi K3.1 Pro is a permitted alternate where the lane's custody admits it. Output is a draft or a recommendation. Pricing, commitments, contracts and brand claims move to cade.critical. External acts wait for Jess's confirm."
            , forbidden_tasks =
              [ "merge"
              , "merge-gating-review"
              , "commitment-of-record"
              , "external-send-without-confirm"
              ]
            }
          , { id = "marketing"
            , model_classes = [ "kimi", "opus" ]
            , purpose =
                "Marketing for Cade: copy, posts, campaign drafts and outreach drafts."
            , doctrine =
                "Kimi K3.1 Pro by default on public custody, with Opus 5.5 as the alternate. Drafts only: outreach and posts ship through the daily batch confirm, and other external sends wait for Jess's confirm."
            , forbidden_tasks =
              [ "merge"
              , "merge-gating-review"
              , "brand-claim-of-record"
              , "external-send-without-confirm"
              ]
            }
          , { id = "ceo-ops"
            , model_classes = [ "opus", "kimi" ]
            , purpose =
                "CEO operations for Cade: obligations, digests, scheduling preparation and assembling the daily batch."
            , doctrine =
                "Opus 5.5 by default. Kimi K3.1 Pro is a permitted alternate on ops custody. It assembles the daily batch and never sends it. Spending, external sends and host acts wait for Jess's confirm."
            , forbidden_tasks =
              [ "merge"
              , "merge-gating-review"
              , "host-act"
              , "spend"
              , "external-send-without-confirm"
              ]
            }
          , { id = "evidentiary-review"
            , model_classes = [ "opus", "kimi" ]
            , purpose =
                "Advisory cross-provider review of consequential Cade output. The reviewer runs on the provider other than the author's."
            , doctrine =
                "Review is evidence, not authority. It never blocks, never gates a merge and never stands in for Jess's approval or a batch confirm. A same-provider review is recorded as same-provider."
            , forbidden_tasks =
              [ "merge-gating-review"
              , "adversarial-refutation-of-record"
              , "word-leg-ratification"
              , "final-synthesis"
              ]
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
            , purpose =
                "Advisory code and design review alongside the operator. It never gates a merge: the merge gate is mythos.gate.review on Opus."
            }
          , { route = "mythos.adversarial"
            , persona = "adversarial"
            , purpose =
                "Purple-team and refutation passes over synthesized findings before they reach the operator."
            }
          , { route = "mythos.implement.merge"
            , persona = "critical"
            , purpose =
                "Implementation that lands on a default branch or a release. Opus 5.5 by default."
            }
          , { route = "mythos.gate.review"
            , persona = "critical"
            , purpose =
                "Independent merge-gating review. Runs on Opus 5.5 and never falls back to Sonnet. A Sonnet first pass or a harness review is evidence for it, never the gate."
            }
          , { route = "mythos.gate.security"
            , persona = "critical"
            , purpose =
                "Security-sensitive code, credentials paths and trust-boundary changes. Under the budget fallback Sonnet may write the code; the merge waits for an Opus review and live credentials wait for Opus or the operator."
            }
          , { route = "mythos.act.host"
            , persona = "critical"
            , purpose =
                "Cluster and host acts: deploys, switches, migrations and anything that mutates shared infra. Never falls back to Sonnet: it waits for Opus or goes to the operator."
            }
          , { route = "mythos.draft"
            , persona = "drafting"
            , purpose =
                "Drafts, non-merging prototypes and write-ups that a critical lane or the operator will finish."
            }
          , { route = "mythos.review.first-pass"
            , persona = "drafting"
            , purpose =
                "First-pass review that narrows findings before the Opus merge gate."
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
          , { route = "mythos.mechanical.poll"
            , persona = "mechanical"
            , purpose = "Polling CI and queues. Summarising logs."
            }
          , { route = "mythos.delegate.review"
            , persona = "pi"
            , purpose =
                "Technical review delegated to a harness seat: GitHub PR review and review commentary, returned as adversarial_review_pass evidence for the fable synthesis seat. For a merge it is evidence for the Opus gate on mythos.gate.review, never the gate. Kimi rides pi-k3 on the identical envelope. Never the operator WORD leg."
            }
          , { route = "mythos.delegate.mechanical"
            , persona = "pi"
            , purpose =
                "Mechanical code-quality output delegated to a harness seat: lint-shaped findings, structural nits and checklist sweeps over a diff. Evidence only. The ruling stays with the operator, the fable seat and, for a merge, the Opus gate."
            }
          ]
      , budget_fallbacks =
          [ { id = "opus-quota-exhausted"
            , from_seat = "opus"
            , to_seat = "sonnet"
            , trigger =
                "Opus 5.5 subagent quota is exhausted for the current window."
            , personas = [ "critical" ]
            , lanes = [ "mythos.implement.merge", "mythos.gate.security" ]
            , obligations =
                [ "merge-waits-for-opus-review"
                , "no-host-or-credential-act"
                , "downgrade-recorded-in-receipt"
                ]
            }
          ]
      , cade =
          { orchestrator =
              "Cade is an orchestrator, not a seat and not the operator. It decomposes business, marketing and CEO-ops work into cade.* lanes, picks each lane's seat from its default and permitted alternates within the lane's custody class, spawns children to run them and collects a receipt per child. Code work Cade delegates runs on the mythos.* lanes under J81."
          , cross_provider_review =
              "Advisory (SPR-145). Consequential Cade output may get a review on cade.review from the provider other than its author's. Cross-provider review is advisory evidence. It never blocks, never gates a merge and never stands in for Jess's approval or a batch confirm. A same-provider review is recorded as same-provider."
          , children =
            { level = "auto"
            , custody = "follows-parent-home"
            , provider_fallback = "none"
            , bindings =
              [ "batch-confirm-for-external-acts"
              , "health-never-hosted"
              , "client-exports-never-kimi"
              , "ownership-and-live-session-checks"
              , "hooks-advisory"
              ]
            , notes =
                "Cade's children are auto-level workers, like a fresh harness with bypass permissions and subagents (SPR-144). They are not read-only. Standing rules still bind them: external acts go through the daily batch confirm (BIZ-5) or Jess's confirm (CORE-5), health data is never hosted (SP-4), client raw exports stay in the confidential home or on Opus and never reach Kimi (SPR-102), ownership and live-session checks precede any signal (R-N11) and hook rulings stay advisory (R-N12). A child's custody class follows its parent home, so confidential-home children never route to Kimi. A child never reroutes to another provider when its seat is unavailable: it waits."
            }
          , custody_classes =
            [ { id = "confidential"
              , seats = [ "opus" ]
              , notes =
                  "Confidential-home work, including client raw exports and confidential papers. Opus 5.5 or on-prem only. Never Kimi."
              }
            , { id = "health"
              , seats = [] : List Text
              , notes =
                  "Health data is never hosted (SP-4). It stays on-prem, no seat in this policy serves it and no cade.* lane carries it."
              }
            , { id = "ops"
              , seats = [ "opus", "kimi" ]
              , notes =
                  "Non-confidential operating work. Opus 5.5 or Kimi K3.1 Pro."
              }
            , { id = "public"
              , seats = [ "opus", "kimi" ]
              , notes =
                  "Public-home work over public or synthetic material. Opus 5.5 or Kimi K3.1 Pro."
              }
            ]
          , lanes =
            [ { route = "cade.research"
              , persona = "research"
              , custody = "public"
              , default_seat = "kimi"
              , alternate_seats = [ "opus" ]
              , gate = "None. Research returns evidence to the orchestrator."
              , purpose =
                  "Public market, competitor and lead research. Confidential papers and client material stay off this lane."
              }
            , { route = "cade.draft"
              , persona = "drafting"
              , custody = "public"
              , default_seat = "kimi"
              , alternate_seats = [ "opus" ]
              , gate =
                  "Drafts only. Anything sent goes through the daily batch confirm or Jess's confirm."
              , purpose =
                  "Proposals, briefs and other business drafts that a later lane or Jess finishes."
              }
            , { route = "cade.business"
              , persona = "business"
              , custody = "ops"
              , default_seat = "opus"
              , alternate_seats = [ "kimi" ]
              , gate =
                  "External acts wait for Jess's confirm (CORE-5). Pricing and commitments move to cade.critical."
              , purpose =
                  "Offers, partnerships, pipeline analysis and operating plans short of a commitment."
              }
            , { route = "cade.marketing"
              , persona = "marketing"
              , custody = "public"
              , default_seat = "kimi"
              , alternate_seats = [ "opus" ]
              , gate =
                  "Outreach and posts ship only through the daily batch confirm (BIZ-5). Other external sends wait for Jess's confirm (CORE-5)."
              , purpose = "Copy, posts, campaign drafts and outreach drafts."
              }
            , { route = "cade.ceo-ops"
              , persona = "ceo-ops"
              , custody = "ops"
              , default_seat = "opus"
              , alternate_seats = [ "kimi" ]
              , gate =
                  "Assembles the daily batch and never sends it. Spending, external sends and host acts wait for Jess's confirm (CORE-5)."
              , purpose =
                  "Obligations, digests, scheduling preparation and the daily batch."
              }
            , { route = "cade.review"
              , persona = "evidentiary-review"
              , custody = "ops"
              , default_seat = "opus"
              , alternate_seats = [ "kimi" ]
              , gate =
                  "Advisory only (SPR-145). It never blocks and never counts as Jess's approval."
              , purpose =
                  "Cross-provider review of consequential Cade output, run on the provider other than the author's. Confidential output is reviewed only on Opus inside its own lane."
              }
            , { route = "cade.critical"
              , persona = "critical"
              , custody = "ops"
              , default_seat = "opus"
              , alternate_seats = [] : List Text
              , gate = "Jess confirms each act (CORE-5)."
              , purpose =
                  "Pricing, commitments, contracts and brand claims. Opus 5.5 or the operator; never Kimi and never fable."
              }
            , { route = "cade.client"
              , persona = "business"
              , custody = "confidential"
              , default_seat = "opus"
              , alternate_seats = [] : List Text
              , gate =
                  "Stays in the confidential home or on Opus (SPR-102). External acts wait for Jess's confirm (CORE-5)."
              , purpose =
                  "Client raw exports and analysis over them. Never Kimi and never a public home."
              }
            ]
          }
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
          , { id = "critical-lanes-default-opus"
            , severity = "error"
            , rule =
                "A lane whose persona is critical or adversarial resolves to Opus 5.5 by default or to the operator personally. Those personas never list sonnet, haiku or fable in their model classes. A merge-gating review runs on Opus. mythos.review and harness reviews never gate a merge."
            }
          , { id = "opus-budget-fallback"
            , severity = "error"
            , rule =
                "When Opus quota is exhausted, Sonnet may continue mythos.implement.merge and mythos.gate.security under the opus-quota-exhausted fallback. Merges from those lanes wait for an Opus review. The downgraded lane never runs a host act or a credential change. The downgrade is recorded in the receipt. The fallback never reaches mythos.gate.review, mythos.act.host or the adversarial persona: that work waits for Opus or goes to the operator. It never reaches fable or haiku. This narrows J81(e) on purpose to keep J81(a)."
            }
          , { id = "provider-allow-list"
            , severity = "error"
            , rule =
                "Every seat names a provider listed in providers. A seat's model_id must be on its provider's allow-list. Anthropic seats stay on the 5.5 family and fable stays provider-managed. The kimi seat runs kimi-k3.1-pro through the Kimi Code subscription, never a Platform key. A provider or model id outside the allow-list fails closed."
            }
          , { id = "kimi-never-gates"
            , severity = "error"
            , rule =
                "The kimi seat never gates a merge. It never owns the refutation-of-record, never carries the operator WORD leg and never runs a critical host act. The critical and adversarial personas never list kimi. Kimi authors only on cade.* lanes that name it as a default or alternate seat. Kimi engineering review rides the pi-k3 harness seat as evidence."
            }
          , { id = "cade-custody-follows-parent"
            , severity = "error"
            , rule =
                "A child's custody class follows its parent home. Confidential work runs on Opus or on-prem only and never routes to Kimi. Public and ops work runs on Opus or Kimi. Health data is never hosted. Every seat a cade.* lane names sits in its custody class and in its persona's model classes. Fable never serves a cade.* lane."
            }
          , { id = "cade-children-auto-level"
            , severity = "error"
            , rule =
                "Cade's children are auto-level workers, not read-only. Standing rules still bind them: external acts go through the daily batch confirm or Jess's confirm, health data is never hosted, client raw exports never reach Kimi, ownership and live-session checks precede any signal and hook rulings stay advisory. A child never reroutes to another provider when its seat is unavailable: it waits."
            }
          , { id = "cross-provider-review-advisory"
            , severity = "error"
            , rule =
                "Cross-provider review is advisory evidence. It never blocks, never gates a merge and never stands in for Jess's approval or a batch confirm."
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
                "Per-agent and per-lane model choice follows this policy and names the seat model_id. The harness-wide default model stays provider-managed: pinning fable, opus, sonnet or haiku globally still needs an explicit host-level justification."
            }
          , { id = "harness-delegation-envelope"
            , severity = "error"
            , rule =
                "A harness seat may only receive tasks listed in its own delegable_tasks. It never owns the operator WORD/ratification leg and never owns the adversarial-refutation-of-record; harness output is evidence for the fable synthesis seat or the Opus merge gate. It is never the ruling and never gates a merge. A lane routed to a harness seat therefore carries evidentiary work only."
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
              , "cade orchestration"
              ]
          }
      }
    : T.Policy
