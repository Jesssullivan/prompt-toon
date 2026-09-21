---
name: mythos-delegation
description: Route agent work across model classes per the Toon of Mythos delegation policy. Use when orchestrating subagents or workflows, choosing a model for a lane, deciding whether a fable-class model should run a task, or attributing a session to a lane. Core rule - never route adversarial, purple-team, red-team, or deep-iteration hammering work to fable-class models.
when_to_use: Spawning subagents/workflows, picking model classes for lanes, session attribution, or reviewing whether scarce fable tokens and operator attention are being spent where they shine.
---

# Mythos Delegation

Two scarce resources mirror each other: the operator's SWE attention and
fable-class model tokens (expensive, not always available, can be slow,
compute costly). Everything cheaper — haiku/sonnet/opus bandwidth and
deterministic infra — is spent freely to protect them.

The machine-readable SSOT is `policy/delegation.json` **in
`Jesssullivan/prompt-toon`** (typed Dhall source in `policy/dhall/`, document
shape in `policy/delegation.schema.json`). Read it rather than restating it:

```sh
# inside a Jesssullivan/prompt-toon checkout
jq '.seats[] | {id, cost_tier, default_personas}' policy/delegation.json
jq '.harness_seats[] | {id, harness, model_lane, posture, delegable_tasks}' policy/delegation.json
jq '.personas[] | {id, model_classes, forbidden_tasks}' policy/delegation.json
jq -r '.lanes[] | "\(.route) -> \(.persona): \(.purpose)"' policy/delegation.json

# no checkout handy
curl -fsSL https://raw.githubusercontent.com/Jesssullivan/prompt-toon/main/policy/delegation.json
```

> **Do not confuse repos.** `tinyland-inc/lab` also has a
> `policy/delegation.json`; it is the interview-HITL delegation policy with a
> different schema and is not this routing SSOT.

## Routing rules

1. **fable** takes the synthesis seat only: umbrella orchestration,
   architecture, engineering judgment, review alongside the operator. It
   never runs adversarial-analysis, purple-team, red-team,
   deep-iteration-hammering, or bulk-execution.
2. **Adversarial / purple-team / refutation lanes** go to opus or the
   operator personally. Prompt the skeptic to refute, not to agree.
3. **Research lanes** (wide recon, deep Linear exploration, web sweeps) go to
   haiku/sonnet/opus, escalating model class with task depth.
4. **Mechanical lanes** (locators, inventories, conversions) go to haiku.
5. Research and mechanical lanes escalating to fable is a warn-class
   violation: it needs explicit operator justification.
6. Leave harness default model selection provider-managed; never pin a model
   globally without a host-level justification.

## Seats

`fable`, `opus`, `sonnet`, and `haiku` are first-class seat records under
`.seats[]`, each carrying a cost tier, the personas it serves by default, and
the personas it must never serve. `operator` is not a seat: it means the
operator takes the lane personally.

`pi` and `pi-k3` are **harness** seats under `.harness_seats[]` — dispatch
identities, not model classes. See "Harness delegation" below.

## Fan-out doctrine

When the operator asks to fan out, that means **parallel ultracode lanes** —
separate concurrent Workflow invocations — not one serial workflow that walks
the same work end to end.

The reason is orchestration, not throughput. Parallel lanes return
independently, and **each return is a Fable↔operator interview checkpoint**:
plans, todos, and assertions update live, the dialog continues, and HITL stays
active for review and ratification of merges, `gh api` merges, and priorities.
A serial mega-workflow hands off for extended periods and suppresses exactly
that live orchestration — the operator gets one arrival at the end instead of a
checkpoint per lane.

Decomposing to serial when fan-out was asked for is the warn-class
`serial-workflow-on-fan-out` violation.

## Harness delegation

Fable may delegate technical and code-quality work — GitHub PR review, review
commentary, mechanical code-quality output — to the **pi harness seats** under
`.harness_seats[]`.

```sh
jq '.harness_seats[] | {id, harness, model_lane, posture, delegable_tasks, dispatch_surface}' policy/delegation.json
```

- **Posture is evidentiary-only.** A harness review is `adversarial_review_pass`
  evidence for the synthesis seat. It is never the operator WORD leg, and it
  never owns the adversarial-refutation-of-record. Each seat's
  `forbidden_tasks` names both prohibitions explicitly.
- **Kimi rides `pi-k3`.** The native kimi CLI has no dispatch envelope, so the
  Kimi reviewer runs on the pi harness as the `pi-k3` seat, on the identical
  task envelope and posture as `pi`.
- **Dispatch is exclusively via lab's bounded `pi-*` launchers.** Never execute
  a raw agent binary as a dispatch or probe surface — that is the error-class
  `harness-dispatch-bounded-only` violation. (Lab's GUI-launch guard hook
  denies some raw binaries, `codex` included, but not `pi` — the prohibition
  here is the policy, not the hook; the lab AGENTS.md sanction line names the
  bounded launchers as the only dispatch path.)
- A harness seat may only receive tasks in its own `delegable_tasks`
  (`harness-delegation-envelope`, error class). Harness seats are dispatch
  identities, not model classes: they never appear in a persona's
  `model_classes`.

The `mythos.delegate.review` and `mythos.delegate.mechanical` lanes route to
harness seats rather than to model-class personas.

## Process control (R-N11) and hook refusals (R-N12)

Ratified operator interview 2026-09-20, TIN-3692 (`lab` AGENTS.md Hard Rules
"R-N11" / "R-N12"; the same incident is also the reason `lab`'s
`process_signal_boundary` house rule was broadened that day). Both are
encoded in `.enforcement[]` (`process-control-absolute`,
`hook-refusal-is-stop`, both error-severity) and `process-control` is a
`forbidden_tasks` entry on every persona and every harness seat.

- **R-N11 — no seat, harness seat, or persona ever signals a process, on any
  host, in any form.** No `kill`, `pkill`, `killall`, `tmux
  kill-server`/`kill-session`/`kill-pane`/`kill-window`, `systemctl
  stop`/`kill`, or `launchctl kill`/`bootout` — not a name pattern, not a
  literal PID, regardless of what the target looks like. This is absolute
  and binds `.seats[]` (`fable`/`opus`/`sonnet`/`haiku`) too, even though
  that record's schema has no `forbidden_tasks` slot — the
  `process-control-absolute` enforcement rule is the schema-appropriate
  global carrier for those four. Ask the operator; do not act.
- **R-N12 — a guard-hook refusal is a stop, not friction to route around.**
  Quote the refusal verbatim to the operator and propose at most one
  materially different alternative — never a reworded or re-encoded form of
  the same refused command — before asking. Reformulating a refused command
  to evade the pattern is itself a violation. This governs conduct, not
  routing, so there is nothing else in the schema to attach it to beyond the
  `enforcement` rule above and this prose.

The incident: an agent walked a preview server's process ancestry to PID 1,
found the operator's own tmux server, and killed it with a literal PID after
the guard hook had already refused the command twice. Critical work was
lost.

## Attribution

Attribute every synthesis/recon session to a lane in session notes (lab
`docs/agent-notes/INDEX.md` style), e.g. `fable ultracode (workflow)`,
`research dispatch (fable)`, `lab hygiene (fable/opus)`, `opus-ultracode`.

## Enforcement

`tests/test_delegation_policy.py` in `Jesssullivan/prompt-toon` fails closed
on: adversarial lanes resolving to fable, missing fable task prohibitions,
missing purpose strings, JSON that does not validate against
`policy/delegation.schema.json`, seats that serve a persona excluding them,
harness seats that claim a ratification or refutation-of-record task, or drop
the evidentiary-only posture, and Dhall/JSON drift on load-bearing IDs. It runs
in that repo's `just check`.

Ratified 2026-09-20 (TIN-3692): `process-control-absolute` (R-N11) and
`hook-refusal-is-stop` (R-N12) join the error-class enforcement rules. See
"Process control (R-N11) and hook refusals (R-N12)" above.
