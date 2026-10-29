---
name: mythos-delegation
description: Route agent work across model classes per the Toon of Mythos delegation policy. Use when orchestrating subagents or workflows, choosing a model per subagent or per lane, deciding whether a fable-class model should run a task or attributing a session to a lane. Core rules - rely on Opus 5.5 (claude-opus-5-5) for mission-critical work such as implementation that merges, merge-gating review, security-sensitive code and host acts; use Sonnet 5.5 and Haiku 5.5 freely for research, drafting and mechanical lanes; never route adversarial, purple-team, red-team or deep-iteration hammering work to fable-class models; Kimi K3.1 Pro (k3.1-pro on the Kimi Code endpoint) serves only the cade.* lanes that name it and never gates a merge; Cade orchestrates cade.* business, marketing and CEO-ops lanes under custody rules.
when_to_use: Spawning subagents or workflows, picking a model for each lane, routing Cade's children, session attribution or reviewing whether scarce fable tokens, Opus quota and operator attention are spent where they shine.
---

# Mythos Delegation

Two scarce resources mirror each other: the operator's SWE attention and
fable-class model tokens. Fable tokens are expensive, not always available,
can be slow and cost compute. Sonnet and Haiku bandwidth and deterministic
infra are spent freely to protect them. Opus 5.5 is spent freely on
mission-critical lanes and rationed elsewhere. It is the one budget to watch
below fable.

The machine-readable SSOT is `policy/delegation.json` **in
`Jesssullivan/prompt-toon`**. Its typed Dhall source is in `policy/dhall/` and
its document shape is `policy/delegation.schema.json`. Read it rather than
restating it:

```sh
# inside a Jesssullivan/prompt-toon checkout
jq '.providers[] | {id, model_ids, access, endpoints}' policy/delegation.json
jq '.seats[] | {id, provider, endpoint, model_id, cost_tier, default_personas}' policy/delegation.json
jq '.harness_seats[] | {id, harness, model_lane, posture, delegable_tasks}' policy/delegation.json
jq '.personas[] | {id, model_classes, forbidden_tasks}' policy/delegation.json
jq -r '.lanes[] | "\(.route) -> \(.persona): \(.purpose)"' policy/delegation.json
jq '.budget_fallbacks[] | {id, from_seat, to_seat, lanes, obligations}' policy/delegation.json
jq '.cade.lanes[] | {route, persona, custody, default_seat, alternate_seats, gate}' policy/delegation.json
jq '.cade.custody_classes[], .cade.children' policy/delegation.json

# no checkout handy
curl -fsSL https://raw.githubusercontent.com/Jesssullivan/prompt-toon/main/policy/delegation.json
```

> **Do not confuse repos.** `tinyland-inc/lab` also has a
> `policy/delegation.json`. It is the interview-HITL delegation policy with a
> different schema and is not this routing SSOT.

## Lane to model

| Route | Persona | Default model | When Opus quota is out |
| --- | --- | --- | --- |
| `mythos.implement.merge` | critical | `claude-opus-5-5` | Sonnet; merge waits for Opus |
| `mythos.gate.security` | critical | `claude-opus-5-5` | Sonnet; merge waits for Opus |
| `mythos.gate.review` | critical | `claude-opus-5-5` | Wait for Opus or the operator |
| `mythos.act.host` | critical | `claude-opus-5-5` | Wait for Opus or the operator |
| `mythos.adversarial` | adversarial | `claude-opus-5-5` | Wait for Opus or the operator |
| `mythos.draft`, `mythos.review.first-pass` | drafting | `claude-sonnet-5-5` | No change |
| `mythos.research.*` | research | haiku, sonnet or opus by depth | No change |
| `mythos.mechanical.*` | mechanical | `claude-haiku-5-5` | No change |
| `mythos.synthesis`, `mythos.review` | fable | provider-managed | No change |
| `mythos.delegate.*` | pi, pi-k3 | harness lane | No change |

## Routing rules

Ruling J81 (Jess, 2026-10-23): rely on Opus 5.5 for mission-critical work.
Use Sonnet 5.5 and Haiku 5.5 freely where they fit.

1. **Opus 5.5** (`claude-opus-5-5`) is the default for every mission-critical
   lane: implementation that merges, merge-gating review, adversarial review
   and refutation-of-record, security-sensitive code and cluster or host
   acts. A merge-gating review runs on Opus. The `critical` and
   `adversarial` personas route to opus or the operator personally.
2. **Sonnet 5.5** (`claude-sonnet-5-5`) is the default for research sweeps,
   drafting, broad recon, non-merging prototypes and first-pass review. Use
   it freely in these lanes. A Sonnet first pass never stands in for the
   Opus merge gate.
3. **Haiku 5.5** (`claude-haiku-5-5`) is the default for mechanical lanes:
   locators, inventories, conversions, polling and log summaries. Use it
   freely there.
4. **fable** takes the synthesis seat only: umbrella orchestration,
   architecture, engineering judgment and advisory review alongside the
   operator. Its review never gates a merge. It never takes a critical lane.
   It never runs adversarial-analysis, purple-team, red-team,
   deep-iteration-hammering or bulk-execution.
5. **Budget fallback** (`budget_fallbacks[]`). This is J81(e) as narrowed by
   this policy to keep J81(a). When Opus quota is exhausted, Sonnet may
   continue `mythos.implement.merge` and `mythos.gate.security`. Their merges
   wait for an Opus review. Never run a host act or touch live credentials on
   Sonnet. Record the downgrade in the receipt. The fallback never covers
   `mythos.gate.review`, `mythos.act.host` or the `adversarial` persona. That
   work waits for Opus or goes to the operator. A Sonnet review in that
   window counts as a first pass only.
6. Research and mechanical lanes escalating to fable is a warn-class
   violation. It needs explicit operator justification.
7. Pick the model per agent and per lane from the seat `model_id`. Only a
   harness-wide default pin needs a host-level justification. That default
   stays provider-managed otherwise.

## Seats

`fable`, `opus`, `sonnet`, `haiku` and `kimi` are first-class seat records
under `.seats[]`. Each carries a provider, a cost tier, the personas it
serves by default and the personas it must never serve. Opus, Sonnet, Haiku
and Kimi also carry their exact `model_id`. Fable stays provider-managed.
`operator` is not a seat: it means the operator takes the lane personally.

Every seat's provider and model ID must sit on the `.providers[]`
allow-list. Model IDs are keyed by endpoint, because one model can carry a
different ID on each access path. The `anthropic` entry admits only the 5.5
family. The `kimi` entry admits only `k3.1-pro` on the `kimi-coding`
endpoint, through the Kimi Code subscription. That route is still
unmeasured (TIN-7125). The Platform endpoint and any Platform key are
forbidden. The Platform ID `kimi-k3.1-pro` is listed only under the refused
`moonshot-platform` endpoint, so a consumer can recognise and refuse it. Any
other provider or model ID fails closed.

`pi` and `pi-k3` are **harness** seats under `.harness_seats[]`. They are
dispatch identities, not model classes. See "Harness delegation" below.

## Kimi seat

The `kimi` seat runs Kimi K3.1 Pro (`k3.1-pro` on `kimi-coding`). It is a
separate record from the `pi-k3` harness seat.

- **Allowed:** research, drafting, business, marketing, CEO-ops and
  evidentiary review, on the `cade.*` lanes that name it as a default or
  alternate seat. Its research and drafting run on the `cade-research` and
  `cade-drafting` personas, which only `cade.*` lanes use.
- **Forbidden:** The kimi seat never gates a merge. It never owns the
  refutation-of-record, never carries the operator WORD leg and never runs
  a critical host act. It never takes the fable, `critical` or
  `adversarial` persona.
- **Custody:** Kimi never sees confidential, client or health data.
- **Engineering:** Kimi authors only on `cade.*` lanes that name it.
  `mythos.*` lanes keep their J81 seats. No `mythos.*` lane resolves to a
  persona that lists kimi.

## Cade orchestration

Cade is an orchestrator, not a seat and not the operator. It breaks
business, marketing and CEO-ops work into `cade.*` lanes and picks each
lane's seat from its default and permitted alternates within the lane's
custody class. It spawns children to run them and collects a receipt per
child. Cade's code work follows CORE-5, and J81 governs the `mythos.*` lanes.

### Cade lanes

| Cade route | Persona | Custody | Default seat | Alternates |
| --- | --- | --- | --- | --- |
| `cade.research` | cade-research | public | kimi | opus |
| `cade.draft` | cade-drafting | public | kimi | opus |
| `cade.business` | business | ops | opus | kimi |
| `cade.marketing` | marketing | public | kimi | opus |
| `cade.ceo-ops` | ceo-ops | ops | opus | kimi |
| `cade.review` | evidentiary-review | ops | opus | kimi |
| `cade.critical` | critical | ops | opus | none |
| `cade.client` | business | confidential | opus | none |

Each lane's `gate` field names the confirm that binds its external acts.
Outreach and posts ship only through the daily batch confirm. Other
external sends, spending and host acts wait for Jess's confirm. Pricing,
commitments, contracts and brand claims go to `cade.critical`, where Jess
confirms each act.

### Custody follows the parent

A child's custody class follows its parent home. A child's effective seats
are its lane's default and alternate seats that also sit in its parent
home's custody class. When none remain, the child waits. So a
confidential-home child on `cade.research`, `cade.draft`, `cade.marketing`
or `cade.review` runs on Opus only, and a health-home child gets no seat.
Confidential work runs on Opus or on-prem only and never routes to Kimi.
Public and ops work runs on Opus or Kimi. Health data is never hosted: no
seat serves the `health` custody class and no `cade.*` lane carries it.
Fable never serves a `cade.*` lane.

### Children are auto-level workers

Cade's children are auto-level workers, not read-only. They run like a
fresh harness with bypass permissions and subagents. Standing rules still
bind them:

- external acts go through the daily batch confirm or Jess's confirm;
- health data is never hosted;
- client raw exports stay in the confidential home or on Opus and never
  reach Kimi;
- ownership and live-session checks precede any signal;
- hook rulings stay advisory.

A child never reroutes to another provider when its seat is unavailable:
it waits.

### Cross-provider review

Cross-provider review is advisory evidence. It never blocks, never gates a
merge and never stands in for Jess's approval or a batch confirm. The
`cade.review` lane runs the review on the provider other than the
author's. A same-provider review is recorded as same-provider.
Confidential output is reviewed only on Opus inside its own lane.

## Fan-out doctrine

When the operator asks to fan out, that means **parallel ultracode lanes**:
separate concurrent Workflow invocations. It does not mean one serial
workflow that walks the same work end to end.

The reason is orchestration, not throughput. Parallel lanes return
independently, and **each return is a Fable↔operator interview checkpoint**.
Plans, todos and assertions update live. The dialog continues. HITL stays
active for review and ratification of merges, `gh api` merges and
priorities. A serial mega-workflow hands off for long stretches and
suppresses exactly that live orchestration. The operator gets one arrival at
the end instead of a checkpoint per lane.

Decomposing to serial when fan-out was asked for is the warn-class
`serial-workflow-on-fan-out` violation.

## Harness delegation

Fable may delegate technical and code-quality work to the **pi harness
seats** under `.harness_seats[]`: GitHub PR review, review commentary and
mechanical code-quality output.

```sh
jq '.harness_seats[] | {id, harness, model_lane, posture, delegable_tasks, dispatch_surface}' policy/delegation.json
```

- **Posture is evidentiary-only.** A harness review is
  `adversarial_review_pass` evidence for the synthesis seat or for the Opus
  merge gate. It never gates a merge. It is never the operator WORD leg and
  never owns the adversarial-refutation-of-record. Each seat's
  `forbidden_tasks` names all three prohibitions.
- **Kimi rides `pi-k3`.** The native kimi CLI has no dispatch envelope, so the
  Kimi reviewer runs on the pi harness as the `pi-k3` seat. It has the same
  task envelope and posture as `pi`.
- **Dispatch is exclusively via lab's bounded `pi-*` launchers.** Never
  execute a raw agent binary as a dispatch or probe surface. That is the
  error-class `harness-dispatch-bounded-only` violation. Lab's GUI-launch
  guard hook denies some raw binaries, `codex` included, but not `pi`. The
  prohibition here is the policy, not the hook. The lab AGENTS.md sanction
  line names the bounded launchers as the only dispatch path.
- A harness seat may only receive tasks in its own `delegable_tasks`
  (`harness-delegation-envelope`, error class). Harness seats are dispatch
  identities, not model classes. They never appear in a persona's
  `model_classes`.

The `mythos.delegate.review` and `mythos.delegate.mechanical` lanes route to
harness seats rather than to model-class personas.

## Attribution

Attribute every synthesis or recon session to a lane in session notes, in
lab `docs/agent-notes/INDEX.md` style. Examples: `fable ultracode (workflow)`,
`research dispatch (fable)`, `lab hygiene (fable/opus)`, `opus-ultracode`.

## Enforcement

`tests/test_delegation_policy.py` in `Jesssullivan/prompt-toon` runs in that
repo's `just check`. It fails closed when:

- a critical lane goes missing;
- a critical or adversarial lane resolves to anything but opus or the
  operator;
- a new lane is neither on the known non-critical list nor on a critical
  persona;
- a budget fallback drops an obligation;
- a budget fallback reaches the merge gate, a host act, the adversarial
  persona, fable or haiku;
- a pinned rule sentence in the policy or in this skill is weakened;
- a seat model ID leaves the 5.5 family or drifts between Dhall and JSON;
- fable loses a task prohibition;
- fable or haiku stops forbidding critical work;
- a harness seat claims ratification, refutation-of-record or the merge
  gate;
- a harness seat drops the evidentiary-only posture;
- a seat names a provider or model ID outside the `.providers[]`
  allow-list;
- a seat runs on an endpoint that is not admitted, or the Platform endpoint
  is admitted;
- the kimi seat reaches the `critical`, `adversarial` or fable persona, a
  merge gate or a budget fallback;
- a `mythos.*` lane resolves to a persona that lists kimi;
- a `cade.*` lane names Kimi under confidential custody, a seat outside its
  custody class or persona, or a route outside the known set;
- the Cade children lose a standing-rule binding, gain a provider fallback
  or stop following the parent home's custody;
- a child under a confidential parent resolves to Kimi, or one under a
  health parent resolves to any seat;
- the Cade lanes table in this skill disagrees with `.cade.lanes[]`;
- a purpose string is missing, the JSON fails `policy/delegation.schema.json`
  or the Dhall and JSON disagree.
