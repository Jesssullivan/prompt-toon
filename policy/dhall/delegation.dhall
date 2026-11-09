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
      , schema_version = 5
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
                "Fable-class model tokens: expensive, not always available, can be slow, compute costly, token-limited. Spend on the synthesis seat and on the orchestrator root of cade.delegate.* jobs only."
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
            , endpoints =
              [ { id = "anthropic"
                , model_ids =
                  [ "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5" ]
                , admitted = True
                , status = "in-service"
                , notes =
                    "Anthropic subscription or API. The model ids are the same on every Anthropic access path, so Anthropic seats name no endpoint."
                }
              ]
            , notes =
                "Anthropic seats stay pinned to the 5.5 family (J81). A new Claude model id is a policy change, not a string edit."
            }
          , { id = "kimi"
            , model_ids = [ "k3.1-pro" ]
            , access =
                "Kimi Code subscription on the kimi-coding endpoint only. The Platform endpoint and any Platform key are forbidden."
            , endpoints =
              [ { id = "kimi-coding"
                , model_ids = [ "k3.1-pro" ]
                , admitted = True
                , status = "unmeasured"
                , notes =
                    "Kimi Code subscription endpoint, model id k3.1-pro. Unattended Kimi children run here under the extended risk acceptance (SPR-143). No live turn on this route has been measured yet (TIN-7125)."
                }
              , { id = "moonshot-platform"
                , model_ids = [ "kimi-k3.1-pro" ]
                , admitted = False
                , status = "forbidden"
                , notes =
                    "The Platform endpoint names the model kimi-k3.1-pro. It is forbidden: no seat runs here and no Platform key is used (SP-34, SPR-143). It is listed so a consumer can recognise and refuse it."
                }
              ]
            , notes =
                "Kimi K3.1 Pro for the cade.* lanes that name it. Model ids are keyed by endpoint. The pi-k3 harness seat is separate and this entry does not change it."
            }
          ]
      , seats =
          [ { id = "fable"
            , model_class = "fable"
            , model_id = None Text
            , model_id_source = "account-config"
            , provider = "anthropic"
            , endpoint = None Text
            , cost_tier = "scarce"
            , default_personas = [ "fable", "fable-orchestrator" ]
            , forbidden_personas = [ "adversarial", "critical" ]
            , notes =
                "Fable-class synthesis seat: umbrella orchestration, architecture, engineering judgment and advisory review alongside the operator. Never the adversarial or critical seat. Its review never gates a merge. It also roots cade.delegate.* jobs as orchestrator on the fable-orchestrator persona (TIN-7125). For those jobs its model id is a config value read from the cade@xoxd.ai account after login, never a value in this policy."
            }
          , { id = "opus"
            , model_class = "opus"
            , model_id = Some "claude-opus-5-5"
            , model_id_source = "pinned"
            , provider = "anthropic"
            , endpoint = None Text
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
            , model_id_source = "pinned"
            , provider = "anthropic"
            , endpoint = None Text
            , cost_tier = "medium"
            , default_personas = [ "research", "drafting" ]
            , forbidden_personas = [ "adversarial" ]
            , notes =
                "Sonnet 5.5 is used freely for research sweeps, drafting, broad recon, non-merging prototypes and first-pass review. It is not the default for mission-critical lanes. It may continue mythos.implement.merge or mythos.gate.security only under the opus-quota-exhausted budget fallback. It never gates a merge."
            }
          , { id = "haiku"
            , model_class = "haiku"
            , model_id = Some "claude-haiku-5-5"
            , model_id_source = "pinned"
            , provider = "anthropic"
            , endpoint = None Text
            , cost_tier = "low"
            , default_personas = [ "mechanical", "research" ]
            , forbidden_personas = [ "adversarial", "critical" ]
            , notes =
                "Haiku 5.5 is the cheapest seat and is used freely for mechanical lanes: locators, inventories, shallow greps, format conversions, polling, log summaries and checklist execution."
            }
          , { id = "kimi"
            , model_class = "kimi"
            , model_id = Some "k3.1-pro"
            , model_id_source = "pinned"
            , provider = "kimi"
            , endpoint = Some "kimi-coding"
            , cost_tier = "high"
            , default_personas =
              [ "cade-research"
              , "cade-drafting"
              , "business"
              , "marketing"
              , "ceo-ops"
              , "evidentiary-review"
              ]
            , forbidden_personas =
              [ "fable", "fable-orchestrator", "critical", "adversarial" ]
            , notes =
                "Kimi K3.1 Pro (k3.1-pro) on the kimi-coding endpoint through the Kimi Code subscription. The Platform endpoint and any Platform key are forbidden. It serves research, drafting, business, marketing, CEO-ops and evidentiary review on the cade.* lanes that name it, through personas that only cade.* lanes use. No mythos.* lane resolves to a persona that lists kimi. It never gates a merge. It never owns the refutation-of-record, never carries the operator WORD leg and never runs a critical host act. It never serves confidential or health custody. As the Cade broker it routes requests to cade.* lanes and authors cade.dispatch/v1 envelopes. It gates nothing and never names a model (cade.broker)."
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
            , launcher = None Text
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
            , launcher = None Text
            , posture = "evidentiary-only"
            }
          , { id = "claude-code@cade"
            , harness = "claude-code"
            , model_lane =
                "anthropic/cade@xoxd.ai: the root seat comes from the cade.dispatch resolver and each unit's seat from cade.dispatch.units"
            , purpose =
                "Cade's job seat (TIN-7125). Cade sends a whole cade.delegate.* job here as a cade.dispatch/v1 envelope. A Claude Code session signed in to cade@xoxd.ai runs it under its own config dir, fans out subagents per unit and returns a cade.dispatch.receipt/v1 receipt."
            , delegable_tasks =
              [ "cade-research"
              , "cade-drafting"
              , "cade-business"
              , "cade-marketing"
              , "cade-development"
              ]
            , forbidden_tasks =
              [ "word-leg-ratification"
              , "adversarial-refutation-of-record"
              , "merge-gating-review"
              , "review-own-pr"
              , "merge"
              , "credential-minting"
              , "secret-mutation"
              ]
            , dispatch_surface = "swb:claude:sting:1:cade-jobs"
            , launcher = Some
                "cade-job: the lab-managed fixed launcher the swb receiver calls (never a raw binary)"
            , posture = "gated-worker"
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
          , { id = "fable-orchestrator"
            , model_classes = [ "fable" ]
            , purpose =
                "Root of a Cade delegated job: plan the job, pick each unit's model and synthesize the result. Only cade.delegate.* lanes use this persona."
            , doctrine =
                "Fable leads, others work (TIN-7125 0a1924b3). Fable plans, fans out and synthesizes. It never runs a unit itself. Workers are Sonnet or Haiku for research and bulk work, and Opus for code, gates and every adversarial, red-team, purple-team or deep-iteration unit at any depth. Bulk execution stays forbidden to fable. No mythos.* lane uses this persona."
            , forbidden_tasks =
              [ "adversarial-analysis"
              , "purple-team"
              , "red-team"
              , "deep-iteration-hammering"
              , "bulk-execution"
              , "merge"
              , "merge-gating-review"
              , "host-act"
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
            , model_classes = [ "haiku", "sonnet", "opus" ]
            , purpose =
                "Wide research, deep Linear exploration, web sweeps, multi-repo recon."
            , doctrine =
                "Fan out wide and return dense structured reports. Escalate model class with task depth: haiku for locators, sonnet for pattern extraction and broad recon, opus for the deepest research lanes. Use haiku and sonnet freely here. Kimi K3.1 Pro never takes this persona: Cade research runs on cade-research."
            , forbidden_tasks = [ "final-synthesis" ]
            }
          , { id = "drafting"
            , model_classes = [ "sonnet", "opus" ]
            , purpose =
                "Drafting, non-merging prototypes and first-pass review ahead of an Opus gate."
            , doctrine =
                "Sonnet 5.5 by default; use it freely. Output is a draft or a first pass. It never stands in for a merge-gating review and never merges on its own. Kimi K3.1 Pro never takes this persona: Cade drafts run on cade-drafting."
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
          , { id = "cade-research"
            , model_classes = [ "kimi", "opus" ]
            , purpose =
                "Cade's public market, competitor and lead research. Only cade.* lanes use this persona."
            , doctrine =
                "Kimi K3.1 Pro by default, with Opus 5.5 as the alternate, within the custody the child's parent home admits. Research returns evidence to the orchestrator. No mythos.* lane uses this persona: engineering research stays on the research persona."
            , forbidden_tasks =
              [ "merge", "merge-gating-review", "final-synthesis" ]
            }
          , { id = "cade-drafting"
            , model_classes = [ "kimi", "opus" ]
            , purpose =
                "Cade's proposals, briefs and other business drafts. Only cade.* lanes use this persona."
            , doctrine =
                "Kimi K3.1 Pro by default, with Opus 5.5 as the alternate, within the custody the child's parent home admits. Drafts only: anything sent goes through the daily batch confirm or Jess's confirm. No mythos.* lane uses this persona: engineering drafts stay on the drafting persona."
            , forbidden_tasks =
              [ "merge"
              , "merge-gating-review"
              , "final-synthesis"
              , "external-send-without-confirm"
              ]
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
              "Cade is an orchestrator, not a seat and not the operator. It decomposes business, marketing and CEO-ops work into cade.* lanes, picks each lane's seat from its default and permitted alternates within the lane's custody class, spawns children to run them and collects a receipt per child. Cade's code work follows CORE-5, and J81 governs the mythos.* lanes."
          , cross_provider_review =
              "Advisory (SPR-145). Consequential Cade output may get a review on cade.review from the provider other than its author's. Cross-provider review is advisory evidence. It never blocks, never gates a merge and never stands in for Jess's approval or a batch confirm. A same-provider review is recorded as same-provider."
          , children =
            { level = "auto"
            , custody = "follows-parent-home"
            , seat_resolution = "lane-seats-within-parent-custody"
            , on_no_seat = "wait"
            , provider_fallback = "none"
            , bindings =
              [ "batch-confirm-for-external-acts"
              , "health-never-hosted"
              , "client-exports-never-kimi"
              , "ownership-and-live-session-checks"
              , "hooks-advisory"
              ]
            , notes =
                "Cade's children are auto-level workers, like a fresh harness with bypass permissions and subagents (SPR-144). They are not read-only. Standing rules still bind them: external acts go through the daily batch confirm (BIZ-5) or Jess's confirm (CORE-5), health data is never hosted (SP-4), client raw exports stay in the confidential home or on Opus and never reach Kimi (SPR-102), ownership and live-session checks precede any signal (R-N11) and hook rulings stay advisory (R-N12). A child's custody class follows its parent home, so confidential-home children never route to Kimi. A child's effective seats are its lane's default and alternate seats that also sit in its parent home's custody class. When none remain, the child waits. A child never reroutes to another provider when its seat is unavailable: it waits."
            }
          , custody_classes =
            [ { id = "confidential"
              , seats = [ "opus" ]
              , harness_seats = [] : List Text
              , notes =
                  "Confidential-home work, including client raw exports and confidential papers. Opus 5.5 or on-prem only. Never Kimi."
              }
            , { id = "health"
              , seats = [] : List Text
              , harness_seats = [] : List Text
              , notes =
                  "Health data is never hosted (SP-4). It stays on-prem, no seat in this policy serves it and no cade.* lane carries it."
              }
            , { id = "ops"
              , seats = [ "opus", "kimi" ]
              , harness_seats = [ "pi-k3", "claude-code@cade" ]
              , notes =
                  "Non-confidential operating work. Opus 5.5 or Kimi K3.1 Pro. Every Kimi seat gets this class, the pi-k3 harness seat included (SPR-182). The claude-code@cade job seat also sees ops data."
              }
            , { id = "public"
              , seats = [ "opus", "kimi" ]
              , harness_seats = [ "pi-k3", "claude-code@cade" ]
              , notes =
                  "Public-home work over public or synthetic material. Opus 5.5 or Kimi K3.1 Pro. The claude-code@cade job seat also sees public data."
              }
            ]
          , lanes =
            [ { route = "cade.research"
              , persona = "cade-research"
              , custody = "public"
              , default_seat = "kimi"
              , alternate_seats = [ "opus" ]
              , gate = "None. Research returns evidence to the orchestrator."
              , purpose =
                  "Public market, competitor and lead research. Confidential papers and client material stay off this lane."
              }
            , { route = "cade.draft"
              , persona = "cade-drafting"
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
          , broker =
            { seat = "kimi"
            , may = [ "route", "author-envelope" ]
            , gates = [] : List Text
            , names_model = False
            , writes =
              [ "lane", "tags", "goal", "inputs", "allowed_effects", "budget_envelope" ]
            , unattended = "admitted"
            , new_policy = True
            , notes =
                "New policy in TIN-7125: the v0.4.0 kimi-never-gates rule had no clause about naming a model. Kimi k3.1-pro on Cade's broker seat routes a request to a cade.* lane and authors the cade.dispatch/v1 envelope. It gates nothing. It never names a model: the envelope has no model field, and the dispatch resolver picks the root from the lane and tags. Cade's own code stamps job_id, parent_job_id, depth, root_id, custody, requested_by and created_at. Custody comes from the source item's label, never from Kimi. Kimi may broker unattended: Jess ruled \"all accounts are checked and suitable for Cade.\" (TIN-7125 0a1924b3)."
            }
          , dispatch =
            { envelope_schema = "cade.dispatch/v1"
            , envelope_schema_file = "policy/cade-dispatch.schema.json"
            , envelope_fields =
              [ "job_id"
              , "parent_job_id"
              , "depth"
              , "root_id"
              , "lane"
              , "tags"
              , "custody"
              , "goal"
              , "inputs"
              , "allowed_effects"
              , "budget_envelope"
              , "requested_by"
              , "created_at"
              ]
            , receipt_schema = "cade.dispatch.receipt/v1"
            , receipt_schema_file = "policy/cade-dispatch-receipt.schema.json"
            , receipt_fields =
              [ "job_id"
              , "parent_job_id"
              , "depth"
              , "lane"
              , "resolved_root_model"
              , "observed_models"
              , "children"
              , "peak_concurrency"
              , "observed_depth"
              , "tokens"
              , "wall_seconds"
              , "effects"
              , "go_refs"
              , "result"
              , "status"
              , "policy_rev"
              , "seat_rev"
              , "unobserved"
              ]
            , request_schema = "cade.dispatch.request/v1"
            , request_schema_file = "policy/cade-dispatch-request.schema.json"
            , resolution_schema = "cade.dispatch.resolution/v1"
            , resolution_schema_file = "policy/cade-dispatch-resolution.schema.json"
            , reply_schema = "cade.dispatch.reply/v1"
            , reply_schema_file = "policy/cade-dispatch-reply.schema.json"
            , halt_schema = "cade.dispatch.halt/v1"
            , halt_schema_file = "policy/cade-dispatch-halt.schema.json"
            , recipient = "claude:sting:1:cade-jobs"
            , recipient_delivery_key = "claude:sting:cade-jobs"
            , receiver_registration =
              { harness = "claude"
              , host = "sting"
              , pid = 1
              , session_id = "cade-jobs"
              , bind = True
              }
            , receiver =
              { allowed_senders = "config"
              , default_sender = "hermes:honey:cade"
              , ticket = "TIN-7125"
              , halt_from = "allowed-senders"
              , unknown_sender = "drop-with-receipt"
              , drop_reasons =
                [ "sender-not-allowed", "sender-unbound", "ticket-not-allowed" ]
              , body_label =
                  "[Cade via swb: teammate information, not operator authority]"
              , relay_prefix = "Cade relays (unverified):"
              }
            , reply_kinds =
              [ "reply", "needs-go", "dispatch-request", "failed", "halted" ]
            , reasons =
              [ { id = "request-invalid", raised_by = "receiver" }
              , { id = "envelope-invalid", raised_by = "both" }
              , { id = "resolution-missing", raised_by = "receiver" }
              , { id = "resolution-invalid", raised_by = "receiver" }
              , { id = "resolution-disagrees", raised_by = "receiver" }
              , { id = "custody-label-unknown", raised_by = "cade" }
              , { id = "custody-health", raised_by = "both" }
              , { id = "custody-confidential", raised_by = "both" }
              , { id = "custody-not-admitted", raised_by = "both" }
              , { id = "custody-widens", raised_by = "cade" }
              , { id = "parent-unknown", raised_by = "cade" }
              , { id = "parent-mismatch", raised_by = "cade" }
              , { id = "effects-widen", raised_by = "cade" }
              , { id = "fable-id-unrecorded", raised_by = "both" }
              , { id = "fable-id-mismatch", raised_by = "receiver" }
              , { id = "job-id-reused", raised_by = "both" }
              , { id = "login-isolation", raised_by = "receiver" }
              , { id = "launch-failed", raised_by = "receiver" }
              , { id = "job-failed", raised_by = "job" }
              , { id = "halt-job-unknown", raised_by = "receiver" }
              , { id = "reply-too-large", raised_by = "receiver" }
              , { id = "recipient-unbound", raised_by = "cade" }
              , { id = "halt", raised_by = "receiver" }
              ]
            , receipt_statuses =
              [ "done", "failed", "needs-go", "dispatch-request", "halted" ]
            , harness_seat = "claude-code@cade"
            , resolver = "fixed-resolver-reads-this-policy"
            , carve_out_tags =
              [ "adversarial", "red-team", "purple-team", "deep-iteration" ]
            , carve_out_match = "normalized-substring"
            , carve_out_goal_match = "normalized-substring"
            , carve_out_normalize = "nfkc"
            , carve_out_stems =
              [ { stem = "adversar", canonical = "adversarial" }
              , { stem = "red-team", canonical = "red-team" }
              , { stem = "redteam", canonical = "red-team" }
              , { stem = "team-red", canonical = "red-team" }
              , { stem = "purple-team", canonical = "purple-team" }
              , { stem = "purpleteam", canonical = "purple-team" }
              , { stem = "deep-iteration", canonical = "deep-iteration" }
              , { stem = "deep-iterat", canonical = "deep-iteration" }
              ]
            , carve_out_non_ascii =
              { tag = "request-invalid", goal = "carve-out-uncertain" }
            , carve_out_seat = "opus"
            , develop_uncertain_tags = "opus"
            , tag_inheritance = "carve-out-tags-pass-to-every-child"
            , custody_source = "source-item-label"
            , custody_labels =
              { normalize = "trim-lowercase-underscore-to-hyphen"
              , classes =
                [ { custody = "public"
                  , labels = [ "public", "hosted-ok" ]
                  , v1_result = "admitted-where-lane-admits"
                  }
                , { custody = "ops"
                  , labels = [ "ops", "private" ]
                  , v1_result = "admitted-where-lane-admits"
                  }
                , { custody = "confidential"
                  , labels = [ "confidential", "client", "client-raw" ]
                  , v1_result = "custody-confidential"
                  }
                , { custody = "health"
                  , labels = [ "health" ]
                  , v1_result = "custody-health"
                  }
                ]
              , unknown = "custody-label-unknown"
              , not_admitted = "custody-not-admitted"
              , widens = "custody-widens"
              }
            , custody_order = [ "public", "ops", "confidential", "health" ]
            , custody_rule = "narrow-only"
            , child_dispatch = "dispatch-request-through-cade"
            , fable_model_id =
              { config_key = "fable_model_id"
              , source = "account"
              , account = "cade@xoxd.ai"
              , admitted_when = "read-from-account-after-login"
              , id_pattern = "^claude-fable-[0-9a-z][0-9a-z.-]*$"
              , on_unread = "refuse"
              , on_unread_reason = "fable-id-unrecorded"
              , notes =
                  "Jess said \"fable 5.2\" and that id is not verified. After Jess's attended login under the job seat's config dir, the id is read from the account's model list, written to the job seat's dispatch config as fable_model_id and recorded on TIN-7125. Until then a Fable-root job is refused with reply kind failed and reason fable-id-unrecorded. It never runs on Opus instead. The id never lands in this policy, the provider allow-list or a test fixture. A read id must match id_pattern, so an Opus or Sonnet id cannot stand in for Fable."
              }
            , job_seat =
              { host = "sting"
              , unix_user = "jess"
              , account = "cade@xoxd.ai"
              , config_dir = "$HOME/.claude-cade"
              , operator_login = "never"
              , key_reach = "accepted"
              , login_isolation =
                { compare = "realpath-and-inode"
                , refuse_reason = "login-isolation"
                , paths =
                  [ { path = "~/.claude-cade"
                    , refused_when =
                        "It is missing, it is a symlink, or its real path is realpath(~/.claude) or inside it."
                    }
                  , { path = "~/.claude-cade/.claude.json"
                    , refused_when =
                        "Its real path is realpath(~/.claude.json) or inside realpath(~/.claude), or it is the same inode as ~/.claude.json."
                    }
                  , { path = "~/.claude-cade/.credentials.json"
                    , refused_when =
                        "Its real path is inside realpath(~/.claude), or it is the same inode as ~/.claude/.credentials.json."
                    }
                  ]
                , refused_env = [ "ANTHROPIC_*", "CLAUDE_CODE_OAUTH_TOKEN" ]
                , inherited_env = "dropped"
                }
              , notes =
                  "Jess ruled \"Under your own user on sting\" (TIN-7125 0a1924b3). Jobs run as jess on sting with CLAUDE_CONFIG_DIR=$HOME/.claude-cade, signed in to cade@xoxd.ai. There is no separate Unix user for jobs. Jess's own ~/.claude login is never used for a job. Jobs can reach Jess's keys, become file and gh login, and Jess accepted that. A job acts under Cade's own credentials, never under Jess's."
              }
            , lanes =
              [ { route = "cade.delegate.research"
                , parent_persona = "fable-orchestrator"
                , root_seat = "fable"
                , custody = [ "public" ]
                , gate = "None. The result doc returns to Cade as {path, sha256}."
                , purpose =
                    "Public research jobs: market, competitor, lead and literature research that needs a fan-out wider than one Cade turn."
                }
              , { route = "cade.delegate.draft"
                , parent_persona = "fable-orchestrator"
                , root_seat = "fable"
                , custody = [ "public" ]
                , gate =
                    "Drafts only. Anything sent goes through the daily batch confirm or Jess's confirm."
                , purpose =
                    "Drafting jobs: proposals, briefs and long documents that a later lane or Jess finishes."
                }
              , { route = "cade.delegate.business"
                , parent_persona = "fable-orchestrator"
                , root_seat = "fable"
                , custody = [ "public", "ops" ]
                , gate =
                    "External acts wait for Jess's confirm (CORE-5). Pricing and commitments move to cade.critical."
                , purpose =
                    "Business jobs: offers, partnership and pipeline analysis and operating plans short of a commitment."
                }
              , { route = "cade.delegate.marketing"
                , parent_persona = "fable-orchestrator"
                , root_seat = "fable"
                , custody = [ "public" ]
                , gate =
                    "Outreach and posts ship only through the daily batch confirm (BIZ-5). Other external sends wait for Jess's confirm (CORE-5)."
                , purpose = "Marketing jobs: campaigns, copy sets and outreach drafts."
                }
              , { route = "cade.delegate.develop"
                , parent_persona = "critical"
                , root_seat = "opus"
                , custody = [ "public", "ops" ]
                , gate =
                    "Pull requests open under Cade's own GitHub identity, and Cade never reviews her own PR. The merge waits for an independent Opus review on mythos.gate.review. Each host act waits for its own GO from Jess (R-C754)."
                , purpose =
                    "Development jobs: code, tests and pull requests. An Opus root plans them, and Opus runs every code, gate and carve-out unit."
                }
              ]
            , units =
              [ { kind = "research"
                , seats = [ "sonnet", "haiku" ]
                , notes = "Research, survey and extraction units."
                }
              , { kind = "bulk"
                , seats = [ "haiku", "sonnet" ]
                , notes =
                    "Bulk and mechanical execution: locators, conversions, inventories and checklists."
                }
              , { kind = "code"
                , seats = [ "opus" ]
                , notes = "Code that may merge, tests and security-sensitive changes."
                }
              , { kind = "gate"
                , seats = [ "opus" ]
                , notes = "Gates and reviews inside the job. None of them gates a merge."
                }
              , { kind = "carve-out"
                , seats = [ "opus" ]
                , notes =
                    "Every adversarial, red-team, purple-team or deep-iteration unit, at any depth."
                }
              ]
            , unknown_unit = "opus"
            , effects =
              [ { id = "none"
                , envelope = True
                , needs_go = False
                , identity = "none"
                , notes =
                    "The job writes only its result file and receipt under its own state dir."
                }
              , { id = "result-doc"
                , envelope = True
                , needs_go = False
                , identity = "job-seat"
                , notes =
                    "The result doc is also committed, signed, to the git-tracked cade-work notes path. Nothing is pushed. Its {path, sha256} returns over swb."
                }
              , { id = "linear-comment"
                , envelope = True
                , needs_go = False
                , identity = "cade-linear"
                , notes = "A Linear comment under Cade's own Linear identity."
                }
              , { id = "linear-issue"
                , envelope = True
                , needs_go = False
                , identity = "cade-linear"
                , notes = "A new Linear issue under Cade's own Linear identity."
                }
              , { id = "github-branch"
                , envelope = True
                , needs_go = False
                , identity = "cade-github"
                , notes =
                    "A branch pushed to a fork under Cade's own GitHub identity, with no pull request."
                }
              , { id = "github-pr"
                , envelope = True
                , needs_go = False
                , identity = "cade-github"
                , notes =
                    "A branch pushed to a fork and a pull request opened under Cade's own GitHub identity. It includes github-branch. Cade never reviews her own PR and never merges it."
                }
              , { id = "spend"
                , envelope = False
                , needs_go = True
                , identity = "jess-confirm"
                , notes = "Any spend waits for Jess's confirm (CORE-5)."
                }
              , { id = "external-send"
                , envelope = False
                , needs_go = True
                , identity = "jess-confirm"
                , notes =
                    "A send outside the estate waits for the daily batch confirm (BIZ-5) or Jess's confirm (CORE-5)."
                }
              , { id = "host-act"
                , envelope = False
                , needs_go = True
                , identity = "jess-confirm"
                , notes = "Each host act waits for its own GO from Jess (R-C754)."
                }
              , { id = "merge"
                , envelope = False
                , needs_go = True
                , identity = "jess-confirm"
                , notes =
                    "A merge of Cade's PR waits for an independent Opus review on mythos.gate.review and a GO."
                }
              , { id = "credential-change"
                , envelope = False
                , needs_go = True
                , identity = "jess-confirm"
                , notes = "Minting or rotating a credential is Jess's act. A job never does it."
                }
              ]
            , credentials =
              [ { id = "cade-claude"
                , provider = "anthropic"
                , holder = "cade@xoxd.ai"
                , minted_by = "jess-attended-login"
                , agent_mints = False
                , notes =
                    "Jess signs in to cade@xoxd.ai, attended, under CLAUDE_CONFIG_DIR=$HOME/.claude-cade on sting. It is a third cade@xoxd.ai sign-in, beside the two Hermes homes."
                }
              , { id = "cade-linear"
                , provider = "linear"
                , holder = "cade"
                , minted_by = "jess-in-provider-ui"
                , agent_mints = False
                , notes =
                    "Cade's own Linear identity. Jess creates it in Linear's UI, and its custody row is written with the mint. Jobs comment as Cade, never as Jess."
                }
              , { id = "cade-github"
                , provider = "github"
                , holder = "cade"
                , minted_by = "jess-in-provider-ui"
                , agent_mints = False
                , notes =
                    "A Cade GitHub App installation or token. Jess creates it in GitHub's UI, and its custody row is written with the mint. Jobs push to forks and open PRs as Cade."
                }
              ]
            , accounting =
              { budget_envelope = "report-only"
              , containment = "none"
              , queue = "fifo"
              , audit =
                [ "observed-root-matches-resolved"
                , "carve-out-units-on-opus"
                , "fable-runs-no-unit"
                , "children-and-depth-reported"
                ]
              , notes =
                  "Every receipt records lineage, the model that answered each unit, children, peak concurrency, observed depth, tokens, wall time, effects and GO refs. The budget envelope is report-only: the card posts status lines at 50, 80 and 100 percent, and nothing stops. Nothing in dispatch caps subagent count, depth or cost, because a cap is containment and needs a ruled interview. Extra jobs queue first come, first served. An audit finding is a warning on the card, never a denial (R-N12)."
              }
            , notes =
                "Cade Dispatch v1 (TIN-7125 e539eb42, 5c0eb819, 8cca9101 and 0a1924b3; wire contract contract-v1, 2026-11-09). Kimi brokers, and a fixed resolver picks the root. Jobs travel over swb to claude:sting:1:cade-jobs, where a bound receiver calls the fixed cade-job launcher. Cade sends the swb label line and one cade.dispatch.request/v1 object, and the receiver strips exactly that line. Every reply is one cade.dispatch.reply/v1 object of kind reply, needs-go, dispatch-request, failed or halted. Tags and goals match the carve-out stems by normalized substring: NFKC, then A-Z lowercased, then every character outside a-z and 0-9 dropped. A raw tag with a non-ASCII letter after NFKC, or one that slugs to empty, is refused, and a goal with one is uncertain and reroutes a Fable lane to Opus. The receiver recomputes dedupe_key as the sha256 of root_id, LF and goal, and trims observed_models from a reply over 16384 bytes, keeping root and every Fable-observed unit. The receiver re-runs the resolver and refuses on any disagreement. Inside a job the carve-out holds through named Opus agent definitions, advisory warnings and receipt audit, because hooks never deny (R-N12). No dispatch lane admits confidential or health custody in v1."
            }
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
                "Every seat names a provider listed in providers. A seat's model_id must be on its provider's allow-list. Anthropic seats stay on the 5.5 family and fable stays provider-managed. A seat that names an endpoint runs only on an admitted endpoint whose model_ids hold its model_id. The kimi seat runs k3.1-pro on the kimi-coding endpoint through the Kimi Code subscription. The Platform endpoint and any Platform key are forbidden. A provider, endpoint or model id outside the allow-list fails closed."
            }
          , { id = "kimi-never-gates"
            , severity = "error"
            , rule =
                "The kimi seat never gates a merge. It never owns the refutation-of-record, never carries the operator WORD leg and never runs a critical host act. The critical and adversarial personas never list kimi. Kimi authors only on cade.* lanes that name it as a default or alternate seat. No mythos.* lane resolves to a persona that lists kimi."
            }
          , { id = "cade-custody-follows-parent"
            , severity = "error"
            , rule =
                "A child's custody class follows its parent home. A child's effective seats are its lane's default and alternate seats that also sit in its parent home's custody class. When none remain, the child waits. Confidential work runs on Opus or on-prem only and never routes to Kimi. Public and ops work runs on Opus or Kimi. Health data is never hosted. Every seat a cade.* lane names sits in its custody class and in its persona's model classes. Fable serves only cade.delegate.*, as orchestrator: no route in cade.lanes names it."
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
          , { id = "fable-leads-others-work"
            , severity = "error"
            , rule =
                "Fable leads, others work (TIN-7125 0a1924b3). A Fable root plans, picks each unit's model and synthesizes. It never runs a unit itself. Workers are Sonnet or Haiku for research and bulk work, and Opus for code, gates and every carve-out unit at any depth. Bulk execution stays forbidden to fable."
            }
          , { id = "cade-dispatch-root-resolution"
            , severity = "error"
            , rule =
                "A fixed resolver, not the broker, picks a dispatched job's root seat from its lane and tags. cade.delegate.research, cade.delegate.draft, cade.delegate.business and cade.delegate.marketing get a Fable root as orchestrator. cade.delegate.develop gets an Opus root. Any carve-out tag on any lane reroutes the root to Opus. A tag or a goal whose NFKC-normalized form contains a carve-out stem reroutes the root to Opus too. A goal with a non-ASCII letter after NFKC makes the carve-out status uncertain, and an uncertain goal on a Fable lane reroutes the root to Opus. Uncertain or missing tags on cade.delegate.develop resolve to Opus. A lane that does not admit the job's custody gets no root, and the job does not run."
            }
          , { id = "cade-dispatch-carve-out-inheritance"
            , severity = "error"
            , rule =
                "Carve-out tags pass to every child, at any depth. A unit or child job that carries a carve-out tag runs on Opus. Inside a job the carve-out holds through named Opus agent definitions, advisory warnings and receipt audit, because hooks never deny."
            }
          , { id = "cade-dispatch-custody-narrows"
            , severity = "error"
            , rule =
                "Custody comes from the source item's label, never from the broker. A job's custody keeps or narrows its label's class. A child's custody stays the same as its parent's or narrows, in the order public, ops, confidential, health. It never widens."
            }
          , { id = "cade-broker-routes-only"
            , severity = "error"
            , rule =
                "New policy (TIN-7125). Kimi on the broker seat may route a request to a cade.* lane and author cade.dispatch/v1 envelopes. It gates nothing and never names a model: the envelope has no model field and the resolver picks the root. The broker never sets custody. Kimi may broker unattended (TIN-7125 0a1924b3)."
            }
          , { id = "fable-id-from-account"
            , severity = "error"
            , rule =
                "The Fable model id is a config value read from the cade@xoxd.ai account after Jess's attended login. This policy never carries it. A Fable-root job is refused with fable-id-unrecorded until the id is read, and it never runs on Opus instead. A receipt whose observed root model differs from its resolved root model is an audit finding."
            }
          , { id = "cade-never-reviews-own-pr"
            , severity = "error"
            , rule =
                "A Cade job never reviews a pull request that Cade opened. The merge gate for a Cade PR is an independent Opus review on mythos.gate.review."
            }
          , { id = "cade-dispatch-accounting-not-caps"
            , severity = "error"
            , rule =
                "Every receipt records lineage, observed models, children, peak concurrency, observed depth, tokens, wall time, effects and GO refs. The budget envelope is report-only. Nothing in dispatch caps subagent count, depth or cost: a cap is containment and needs a ruled interview."
            }
          , { id = "cheap-lane-escalation"
            , severity = "warn"
            , rule =
                "A research or mechanical lane routed to fable warns; escalation requires explicit operator justification. The cade.delegate.* roots carry that justification (TIN-7125 0a1924b3): Fable orchestrates those jobs and never runs their units."
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
                "A harness seat may only receive tasks listed in its own delegable_tasks. It never owns the operator WORD/ratification leg and never owns the adversarial-refutation-of-record; harness output is evidence for the fable synthesis seat or the Opus merge gate. The gated-worker seat's output is work product whose external acts wait for a GO. It is never the ruling and never gates a merge. A lane routed to a harness seat therefore carries evidentiary work only."
            }
          , { id = "harness-dispatch-bounded-only"
            , severity = "error"
            , rule =
                "Harness dispatch goes through the lab-managed bounded launchers named in the seat's dispatch_surface (the pi-* wrappers). A seat whose surface is a transport, such as an swb recipient, names the fixed launcher behind it in launcher, and the receiver calls only that launcher. Never execute a raw agent binary as a dispatch or probe surface."
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
              , "cade dispatch job"
              ]
          }
      }
    : T.Policy
