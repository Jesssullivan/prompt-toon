import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "delegation.json"
SCHEMA_PATH = ROOT / "policy" / "delegation.schema.json"
DHALL_TYPE_PATH = ROOT / "policy" / "dhall" / "DelegationPolicy.dhall"
DHALL_SOURCE_PATH = ROOT / "policy" / "dhall" / "delegation.dhall"
SKILL_PATH = ROOT / ".agents" / "skills" / "mythos-delegation" / "SKILL.md"
ENVELOPE_SCHEMA_PATH = ROOT / "policy" / "cade-dispatch.schema.json"
RECEIPT_SCHEMA_PATH = ROOT / "policy" / "cade-dispatch-receipt.schema.json"

FABLE_FORBIDDEN = {
    "adversarial-analysis",
    "purple-team",
    "red-team",
    "deep-iteration-hammering",
    "bulk-execution",
}

# First-class seat names. Not model_classes strings: adding or renaming a seat
# is a policy change that has to move the Dhall, the JSON, and this set.
SEAT_IDS = {"fable", "opus", "sonnet", "haiku", "kimi"}

# Harness seats are dispatch identities, not model classes. Same rule: adding
# or renaming one moves the Dhall, the JSON, the schema enum, and this set.
HARNESS_SEAT_IDS = {"pi", "pi-k3", "claude-code@cade"}

# The legs a harness seat can never own, whatever else it is trusted with.
# Ratification is the operator's WORD. Refutation-of-record is the adversarial
# persona's. The merge gate is Opus's (J81(a)). A harness seat only ever
# produces evidence for the fable seat or the Opus gate.
HARNESS_FORBIDDEN = {
    "word-leg-ratification",
    "adversarial-refutation-of-record",
    "merge-gating-review",
}

HARNESS_POSTURE = "evidentiary-only"
# The posture of each harness seat. The review seats are evidentiary-only.
# Cade's job seat is a gated worker: its output is work product whose
# external acts wait for a GO (TIN-7125).
HARNESS_POSTURES = {
    "pi": HARNESS_POSTURE,
    "pi-k3": HARNESS_POSTURE,
    "claude-code@cade": "gated-worker",
}

# J81 (Jess, 2026-10-23): exact model IDs per seat. Fable stays
# provider-managed and carries no model_id. TIN-7125: for Cade jobs the Fable
# id is a config value read from the cade@xoxd.ai account after login, never a
# value in the policy, so the seat's model_id_source is account-config.
MODEL_IDS = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}

# TIN-7760: the provider allow-list. The 5.5-family check is not loosened: a
# seat passes only when its provider is listed here and its model_id is on
# that provider's list. Adding a provider or a model id moves the Dhall, the
# JSON, the schema and this table together.
PROVIDER_ALLOW_LIST = {
    "anthropic": set(MODEL_IDS.values()),
    "kimi": {"k3.1-pro"},
}
# Model ids are keyed by endpoint (design P16). Each endpoint maps to its ids,
# whether it is admitted and its status. kimi-coding stays "unmeasured" until a
# live turn is measured (TIN-7125); flip it here and in the Dhall together. The
# Platform endpoint is listed only so consumers can refuse it (SP-34, SPR-143).
PROVIDER_ENDPOINTS = {
    "anthropic": {
        "anthropic": (set(MODEL_IDS.values()), True, "in-service"),
    },
    "kimi": {
        "kimi-coding": ({"k3.1-pro"}, True, "unmeasured"),
        "moonshot-platform": ({"kimi-k3.1-pro"}, False, "forbidden"),
    },
}
# Seats whose provider keys model ids by endpoint, and the endpoint each uses.
# Every other seat names no endpoint.
SEAT_ENDPOINTS = {"kimi": "kimi-coding"}
SEAT_PROVIDERS = {
    "fable": "anthropic",
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "kimi": "kimi",
}
SEAT_MODEL_IDS = {**MODEL_IDS, "kimi": "k3.1-pro"}
SEAT_MODEL_ID_SOURCES = {
    "fable": "account-config",
    "opus": "pinned",
    "sonnet": "pinned",
    "haiku": "pinned",
    "kimi": "pinned",
}
# Any Fable-class model id string. None may appear in the policy, the
# provider allow-list or the skill: the id is read from the account.
FABLE_ID_RE = re.compile(r"claude-fable-[0-9a-z][0-9a-z.-]*")
# What the Kimi seat may and may never serve (SPR-142). Every persona here is
# used only by cade.* lanes: Kimi has its own cade-research and cade-drafting
# personas so that no mythos.* lane can resolve to it.
KIMI_ALLOWED_PERSONAS = {
    "cade-research",
    "cade-drafting",
    "business",
    "marketing",
    "ceo-ops",
    "evidentiary-review",
}
KIMI_FORBIDDEN_PERSONAS = {"fable", "fable-orchestrator", "critical", "adversarial"}
# The only personas fable serves: the synthesis seat and the orchestrator root
# of cade.delegate.* jobs (TIN-7125 0a1924b3, "Fable leads, others work").
FABLE_PERSONAS = {"fable", "fable-orchestrator"}

# Cade lanes (SPR-142). Closed world like the mythos routes: a new cade.*
# route fails until it is added here on purpose.
CADE_ROUTES = {
    "cade.research",
    "cade.draft",
    "cade.business",
    "cade.marketing",
    "cade.ceo-ops",
    "cade.review",
    "cade.critical",
    "cade.client",
}
# Custody classes and the only seats each may use. A child's custody class
# follows its parent home. Confidential is Opus or on-prem only; health is
# never hosted, so no seat serves it.
CUSTODY_SEATS = {
    "confidential": {"opus"},
    "health": set(),
    "ops": {"opus", "kimi"},
    "public": {"opus", "kimi"},
}
# SPR-182: every Kimi seat gets the ops class, the pi-k3 harness seat
# included. Harness seats sit beside the model seats in a custody class, and
# child seat resolution never reads them.
CUSTODY_HARNESS_SEATS = {
    "confidential": set(),
    "health": set(),
    "ops": {"pi-k3", "claude-code@cade"},
    "public": {"pi-k3", "claude-code@cade"},
}
# SPR-144: Cade's children run auto-level, and these standing rules bind them.
CHILD_BINDINGS = {
    "batch-confirm-for-external-acts",
    "health-never-hosted",
    "client-exports-never-kimi",
    "ownership-and-live-session-checks",
    "hooks-advisory",
}
# How a child's seats resolve and what it does when none remain (SPR-144).
CHILD_SEAT_RESOLUTION = "lane-seats-within-parent-custody"
CHILD_ON_NO_SEAT = "wait"
CADE_TABLE_HEADER = ["Cade route", "Persona", "Custody", "Default seat", "Alternates"]
CADE_ROUTE_RE = re.compile(r"cade(?:\.[a-z0-9*-]+)+")

# Cade Dispatch v1 (TIN-7125). The shared contract the lab launcher, the cade
# resolver and this policy all use. Closed world: a field, lane, tag, kind or
# status moves here, in the Dhall, the JSON and the schemas together.
DISPATCH_ROUTES = {
    "cade.delegate.research": ("fable-orchestrator", "fable"),
    "cade.delegate.draft": ("fable-orchestrator", "fable"),
    "cade.delegate.business": ("fable-orchestrator", "fable"),
    "cade.delegate.marketing": ("fable-orchestrator", "fable"),
    "cade.delegate.develop": ("critical", "opus"),
}
DEVELOP_ROUTE = "cade.delegate.develop"
ENVELOPE_SCHEMA_ID = "cade.dispatch/v1"
RECEIPT_SCHEMA_ID = "cade.dispatch.receipt/v1"
ENVELOPE_FIELDS = (
    "job_id", "parent_job_id", "depth", "root_id", "lane", "tags", "custody",
    "goal", "inputs", "allowed_effects", "budget_envelope", "requested_by",
    "created_at",
)
ENVELOPE_OPTIONAL = {"budget_envelope"}
RECEIPT_FIELDS = (
    "job_id", "parent_job_id", "depth", "lane", "resolved_root_model",
    "observed_models", "children", "peak_concurrency", "observed_depth",
    "tokens", "wall_seconds", "effects", "go_refs", "result", "status",
    "policy_rev",
)
RECIPIENT = "claude:sting:cade-jobs"
DISPATCH_SURFACE = f"swb:{RECIPIENT}"
DISPATCH_HARNESS_SEAT = "claude-code@cade"
REPLY_KINDS = ("reply", "needs-go", "dispatch-request")
RECEIPT_STATUSES = ("done", "failed", "needs-go", "dispatch-request")
CARVE_OUT_TAGS = ("adversarial", "red-team", "purple-team", "deep-iteration")
CUSTODY_ORDER = ("public", "ops", "confidential", "health")
# Who runs each unit inside a job. Fable never runs one and Kimi never runs
# inside a Claude Code job.
UNIT_SEATS = {
    "research": ["sonnet", "haiku"],
    "bulk": ["haiku", "sonnet"],
    "code": ["opus"],
    "gate": ["opus"],
    "carve-out": ["opus"],
}
OPUS_ONLY_UNITS = {"code", "gate", "carve-out"}
ENVELOPE_EFFECTS = {"none", "result-doc", "linear-comment", "github-pr"}
GO_EFFECTS = {"spend", "external-send", "host-act", "merge", "credential-change"}
BROKER_MAY = {"route", "author-envelope"}
BROKER_WRITES = {"lane", "tags", "goal", "inputs", "allowed_effects", "budget_envelope"}
JOB_SEAT = {
    "host": "sting",
    "unix_user": "jess",
    "account": "cade@xoxd.ai",
    "config_dir": "$HOME/.claude-cade",
    "operator_login": "never",
    "key_reach": "accepted",
}
CLAUDE_CODE_CADE_FORBIDDEN = HARNESS_FORBIDDEN | {"review-own-pr", "credential-minting"}
# Key fragments that would put a cap on a job. Dispatch adds accounting and
# visibility, never containment ("Harnesses run unconstrained").
CAP_KEY_RE = re.compile(r"(^max|_max$|^min_|cap$|_cap_|limit|ceiling|quota)")
DISPATCH_TABLE_HEADER = ["Dispatch route", "Root persona", "Root seat", "Custody"]
DISPATCH_ROUTE_RE = re.compile(r"cade\.delegate(?:\.[a-z0-9*-]+)+")

# Personas that own mission-critical work. Their lanes resolve to opus (or the
# operator personally) and nothing else.
CRITICAL_PERSONAS = {"critical", "adversarial"}

# J81(a): every mission-critical route and the persona it must sit on. These
# routes must all exist. Removing or moving one is a policy change.
CRITICAL_ROUTES = {
    "mythos.implement.merge": "critical",
    "mythos.gate.review": "critical",
    "mythos.gate.security": "critical",
    "mythos.act.host": "critical",
    "mythos.adversarial": "adversarial",
}

# Closed world: every route that is allowed off a critical persona. A new lane
# that is neither here nor on a critical persona fails, whatever it is named,
# so a host act or a merge cannot slip in under a fresh route.
NON_CRITICAL_ROUTES = {
    "mythos.synthesis",
    "mythos.review",
    "mythos.draft",
    "mythos.review.first-pass",
    "mythos.research.wide",
    "mythos.research.linear",
    "mythos.research.web",
    "mythos.mechanical.locate",
    "mythos.mechanical.poll",
    "mythos.delegate.review",
    "mythos.delegate.mechanical",
}

# Route words that mean a merge, a gate or a host act. A lane carrying one must
# sit on a critical persona even if it is added to NON_CRITICAL_ROUTES.
CRITICAL_ROUTE_WORDS = {
    "merge",
    "gate",
    "act",
    "host",
    "deploy",
    "release",
    "security",
    "adversarial",
    "migrate",
    "infra",
}

FALLBACK_ID = "opus-quota-exhausted"
FALLBACK_OBLIGATIONS = {
    "merge-waits-for-opus-review",
    "no-host-or-credential-act",
    "downgrade-recorded-in-receipt",
}
# J81(e) as narrowed by this policy: the only lanes Sonnet may continue.
FALLBACK_LANES = {"mythos.implement.merge", "mythos.gate.security"}
# Lanes no fallback may ever reach. J81(a) keeps the merge gate on Opus. Host
# acts and refutation-of-record wait for Opus or go to the operator.
NEVER_FALLBACK_LANES = {"mythos.gate.review", "mythos.act.host", "mythos.adversarial"}

# Load-bearing sentences. A rewrite that weakens one fails here.
RULE_SENTENCES = {
    "critical-lanes-default-opus": (
        "A merge-gating review runs on Opus.",
        "mythos.review and harness reviews never gate a merge.",
    ),
    "opus-budget-fallback": (
        "Merges from those lanes wait for an Opus review.",
        "The downgraded lane never runs a host act or a credential change.",
        "The downgrade is recorded in the receipt.",
        "The fallback never reaches mythos.gate.review, mythos.act.host or the "
        "adversarial persona: that work waits for Opus or goes to the operator.",
        "It never reaches fable or haiku.",
    ),
    "no-adversarial-on-fable": (
        "A lane whose persona is adversarial must never resolve to a persona that "
        "includes fable in its model classes.",
    ),
    "provider-allow-list": (
        "Every seat names a provider listed in providers.",
        "A seat's model_id must be on its provider's allow-list.",
        "Anthropic seats stay on the 5.5 family and fable stays provider-managed.",
        "A seat that names an endpoint runs only on an admitted endpoint whose "
        "model_ids hold its model_id.",
        "The kimi seat runs k3.1-pro on the kimi-coding endpoint through the Kimi "
        "Code subscription.",
        "The Platform endpoint and any Platform key are forbidden.",
        "A provider, endpoint or model id outside the allow-list fails closed.",
    ),
    "kimi-never-gates": (
        "The kimi seat never gates a merge.",
        "It never owns the refutation-of-record, never carries the operator WORD leg "
        "and never runs a critical host act.",
        "The critical and adversarial personas never list kimi.",
        "Kimi authors only on cade.* lanes that name it as a default or alternate seat.",
        "No mythos.* lane resolves to a persona that lists kimi.",
    ),
    "cade-custody-follows-parent": (
        "A child's custody class follows its parent home.",
        "A child's effective seats are its lane's default and alternate seats that "
        "also sit in its parent home's custody class.",
        "When none remain, the child waits.",
        "Confidential work runs on Opus or on-prem only and never routes to Kimi.",
        "Public and ops work runs on Opus or Kimi.",
        "Health data is never hosted.",
        "Every seat a cade.* lane names sits in its custody class and in its "
        "persona's model classes.",
        "Fable serves only cade.delegate.*, as orchestrator: no route in cade.lanes "
        "names it.",
    ),
    "cade-children-auto-level": (
        "Cade's children are auto-level workers, not read-only.",
        "Standing rules still bind them: external acts go through the daily batch "
        "confirm or Jess's confirm, health data is never hosted, client raw exports "
        "never reach Kimi, ownership and live-session checks precede any signal and "
        "hook rulings stay advisory.",
        "A child never reroutes to another provider when its seat is unavailable: "
        "it waits.",
    ),
    "cross-provider-review-advisory": (
        "Cross-provider review is advisory evidence.",
        "It never blocks, never gates a merge and never stands in for Jess's "
        "approval or a batch confirm.",
    ),
    "fable-leads-others-work": (
        "A Fable root plans, picks each unit's model and synthesizes.",
        "It never runs a unit itself.",
        "Workers are Sonnet or Haiku for research and bulk work, and Opus for code, "
        "gates and every carve-out unit at any depth.",
        "Bulk execution stays forbidden to fable.",
    ),
    "cade-dispatch-root-resolution": (
        "A fixed resolver, not the broker, picks a dispatched job's root seat from "
        "its lane and tags.",
        "cade.delegate.develop gets an Opus root.",
        "Any carve-out tag on any lane reroutes the root to Opus.",
        "Uncertain or missing tags on cade.delegate.develop resolve to Opus.",
        "A lane that does not admit the job's custody gets no root, and the job "
        "does not run.",
    ),
    "cade-dispatch-carve-out-inheritance": (
        "Carve-out tags pass to every child, at any depth.",
        "A unit or child job that carries a carve-out tag runs on Opus.",
    ),
    "cade-dispatch-custody-narrows": (
        "Custody comes from the source item's label, never from the broker.",
        "A child's custody stays the same as its parent's or narrows, in the order "
        "public, ops, confidential, health.",
        "It never widens.",
    ),
    "cade-broker-routes-only": (
        "New policy (TIN-7125).",
        "It gates nothing and never names a model: the envelope has no model field "
        "and the resolver picks the root.",
        "The broker never sets custody.",
    ),
    "fable-id-from-account": (
        "The Fable model id is a config value read from the cade@xoxd.ai account "
        "after Jess's attended login.",
        "This policy never carries it.",
        "A Fable-root job waits until the id is read.",
    ),
    "cade-never-reviews-own-pr": (
        "A Cade job never reviews a pull request that Cade opened.",
        "The merge gate for a Cade PR is an independent Opus review on "
        "mythos.gate.review.",
    ),
    "cade-dispatch-accounting-not-caps": (
        "The budget envelope is report-only.",
        "Nothing in dispatch caps subagent count, depth or cost: a cap is "
        "containment and needs a ruled interview.",
    ),
}

SKILL_SENTENCES = (
    "A merge-gating review runs on Opus.",
    "The fallback never covers `mythos.gate.review`, `mythos.act.host` or the "
    "`adversarial` persona.",
    "Their merges wait for an Opus review.",
    "Never run a host act or touch live credentials on Sonnet.",
    "**Opus 5.5** (`claude-opus-5-5`) is the default for every mission-critical lane",
    "**Sonnet 5.5** (`claude-sonnet-5-5`) is the default for research sweeps",
    "**Haiku 5.5** (`claude-haiku-5-5`) is the default for mechanical lanes",
    "The kimi seat never gates a merge.",
    "It never owns the refutation-of-record, never carries the operator WORD leg "
    "and never runs a critical host act.",
    "Kimi never sees confidential, client or health data.",
    "Kimi authors only on `cade.*` lanes that name it.",
    "No `mythos.*` lane resolves to a persona that lists kimi.",
    "The Platform endpoint and any Platform key are forbidden.",
    "Cade is an orchestrator, not a seat and not the operator.",
    "Cade's code work follows CORE-5, and J81 governs the `mythos.*` lanes.",
    "A child's custody class follows its parent home.",
    "A child's effective seats are its lane's default and alternate seats that "
    "also sit in its parent home's custody class.",
    "When none remain, the child waits.",
    "Confidential work runs on Opus or on-prem only and never routes to Kimi.",
    "Public and ops work runs on Opus or Kimi.",
    "Health data is never hosted",
    "Cade's children are auto-level workers, not read-only.",
    "A child never reroutes to another provider when its seat is unavailable: it waits.",
    "Cross-provider review is advisory evidence.",
    "It never blocks, never gates a merge and never stands in for Jess's approval "
    "or a batch confirm.",
    "Any other provider or model ID fails closed.",
    "Fable serves only `cade.delegate.*`, as orchestrator.",
    "Fable leads, others work.",
    "It never runs a unit itself, and bulk execution stays forbidden to fable.",
    "It gates nothing and never names a model.",
    "Custody comes from the source item's label, never from Kimi.",
    "any carve-out tag (`adversarial`, `red-team`, `purple-team`, "
    "`deep-iteration`) on any lane reroutes the root to Opus;",
    "uncertain or missing tags on `cade.delegate.develop` resolve to Opus;",
    "Custody never widens.",
    "Every adversarial, red-team, purple-team or deep-iteration unit runs on Opus, "
    "inside the job and in every child.",
    "Code, gates and every carve-out unit run on Opus 5.5 (`claude-opus-5-5`).",
    "Cade never reviews her own PR.",
    "Nothing caps subagent count, depth or cost.",
    "Jess's own `~/.claude` login is never used for a job.",
    "The policy never names it.",
)

# Load-bearing sentences in the policy's own prose, keyed by (collection,
# id, field). Agents read these through the skill's jq lines, so a rewrite
# that contradicts the structured fallback must fail (enforcement re-review
# on PR #49, mutations D11 to D13).
PROSE_SENTENCES = {
    ("personas", "critical", "doctrine"): (
        "A merge-gating review runs on Opus.",
        "Their merges wait for an Opus review.",
        "The downgraded lane never runs a host act or a credential change.",
        "mythos.gate.review and mythos.act.host never fall back: they wait for "
        "Opus or go to the operator.",
        "Never fable or haiku.",
    ),
    ("personas", "adversarial", "doctrine"): (
        "The budget fallback does not cover this persona",
    ),
    ("seats", "sonnet", "notes"): (
        "It is not the default for mission-critical lanes.",
        "It may continue mythos.implement.merge or mythos.gate.security only under "
        "the opus-quota-exhausted budget fallback.",
        "It never gates a merge.",
    ),
    ("lanes", "mythos.gate.review", "purpose"): (
        "Runs on Opus 5.5 and never falls back to Sonnet.",
    ),
    ("lanes", "mythos.gate.security", "purpose"): (
        "the merge waits for an Opus review and live credentials wait for Opus or "
        "the operator.",
    ),
    ("lanes", "mythos.act.host", "purpose"): (
        "Never falls back to Sonnet: it waits for Opus or goes to the operator.",
    ),
    ("seats", "kimi", "notes"): (
        "on the kimi-coding endpoint through the Kimi Code subscription.",
        "The Platform endpoint and any Platform key are forbidden.",
        "No mythos.* lane resolves to a persona that lists kimi.",
        "It never gates a merge.",
        "It never owns the refutation-of-record, never carries the operator WORD leg "
        "and never runs a critical host act.",
        "It never serves confidential or health custody.",
    ),
    ("personas", "evidentiary-review", "doctrine"): (
        "Review is evidence, not authority.",
        "It never blocks, never gates a merge and never stands in for Jess's "
        "approval or a batch confirm.",
    ),
    ("personas", "research", "doctrine"): (
        "Kimi K3.1 Pro never takes this persona: Cade research runs on cade-research.",
    ),
    ("personas", "drafting", "doctrine"): (
        "It never stands in for a merge-gating review and never merges on its own.",
        "Kimi K3.1 Pro never takes this persona: Cade drafts run on cade-drafting.",
    ),
}

# Phrases that would grant a downgraded or non-Opus lane the gate, a host act
# or live credentials. Pinned sentences catch a rewrite. These catch an added
# contradicting sentence. Checked case-insensitively over the skill and every
# policy prose field.
FORBIDDEN_GRANTS = (
    "may run a host act",
    "may touch live credentials",
    "rotate live credentials",
    "may run on sonnet",
    "may gate a merge",
    "sonnet with a receipt",
    "kimi may gate",
    "kimi may merge",
    "kimi may handle client",
    "confidential work may run on kimi",
    "children are read-only",
    "blocking review",
    # Design premise 3 and SP-8: pi-k3 is not a rail Kimi review is dispatched to.
    "engineering review rides the pi-k3",
    # No ruling extends J81 to Cade's own code work (SPR-142 keeps CORE-5).
    "code work cade delegates runs on the mythos",
    # TIN-7125: Fable leads and never works; the broker never names a model;
    # custody never widens; Cade never reviews her own PR.
    "fable may run a unit",
    "fable runs the units",
    "fable may execute",
    "carve-out work may run on fable",
    "kimi may name a model",
    "the broker may gate",
    "custody may widen",
    "may review her own pr",
)

# The skill's lane-to-model table cells (J81).
TABLE_FALLBACK_CELL = "Sonnet; merge waits for Opus"
TABLE_WAIT_CELL = "Wait for Opus or the operator"
TABLE_NO_CHANGE_CELL = "No change"
# Personas whose lanes have one default model: the first model class that
# carries a seat model_id.
SINGLE_DEFAULT_PERSONAS = {"critical", "adversarial", "drafting", "mechanical"}
# Any Claude model ID, old style (claude-3-opus) or new (claude-opus-5-5).
MODEL_ID_RE = re.compile(r"claude-(?=[a-z0-9.-]*(?:opus|sonnet|haiku|instant|\d))[a-z0-9][a-z0-9.-]*")
TABLE_HEADER = ["Route", "Persona", "Default model", "When Opus quota is out"]
ROUTE_RE = re.compile(r"mythos(?:\.[a-z0-9*-]+)+")


def model_ids_in(text):
    return {m.rstrip(".-") for m in MODEL_ID_RE.findall(text)}


def skill_table_rows(skill_text):
    """Parse the skill's "Lane to model" table.

    Returns (rows, errors). Every pipe line in the section must be the exact
    header, the separator or a row naming at least one route, so a row the
    parser cannot read fails instead of being skipped. A pipe line naming a
    route anywhere else in the skill is a second, unchecked table and fails.
    """
    errors = []
    marker = "\n## Lane to model\n"
    if skill_text.count(marker) != 1:
        return None, ["skill needs exactly one Lane to model section"]
    before, after = skill_text.split(marker, 1)
    body, _, rest = after.partition("\n## ")
    for line in (before + "\n" + rest).splitlines():
        if line.strip().startswith("|") and ROUTE_RE.search(line):
            errors.append(f"lane table row outside Lane to model: {line.strip()}")
    rows = []
    header_seen = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_seen:
            if cells != TABLE_HEADER:
                errors.append(f"table header is {cells}, want {TABLE_HEADER}")
            header_seen = True
            continue
        if set(stripped) <= set("|-: "):
            continue
        routes = ROUTE_RE.findall(cells[0])
        if not routes or line != stripped:
            errors.append(f"unparseable table row: {line!r}")
            continue
        rows.append((routes, cells))
    if not header_seen:
        errors.append("Lane to model section has no table")
    return rows, errors


def skill_table_violations(policy, skill_text):
    """The skill's lane-to-model table agrees with the policy (enforcement
    re-reviews on PR #49)."""
    rows, errors = skill_table_rows(skill_text)
    if rows is None:
        return errors
    if not rows:
        return errors + ["skill Lane to model table has no rows"]
    personas = {p["id"]: p for p in policy["personas"]}
    model_ids = {s["id"]: s.get("model_id") for s in policy["seats"]}
    lanes = {lane["route"]: lane["persona"] for lane in policy["lanes"]}
    fallback_lanes = {
        route for f in policy.get("budget_fallbacks", []) for route in f.get("lanes", [])
    }

    def default_model(persona):
        if persona not in SINGLE_DEFAULT_PERSONAS or persona not in personas:
            return None
        return next(
            (model_ids[c] for c in personas[persona]["model_classes"] if model_ids.get(c)),
            None,
        )

    covered = set()
    for routes, cells in rows:
        if len(cells) != 4:
            errors.append(f"table row {cells[0]} has {len(cells)} cells, not 4")
            continue
        _, persona_cell, default_cell, fallback_cell = cells
        row_personas = {p.strip() for p in persona_cell.split(",")}
        matched = set()
        for route in routes:
            if route.endswith(".*"):
                hits = {r for r in lanes if r.startswith(route[:-1])}
            else:
                hits = {route} & set(lanes)
            if not hits:
                errors.append(f"table route {route} matches no policy lane")
            dup = hits & covered
            if dup:
                errors.append(f"table lists {sorted(dup)} twice")
            matched |= hits
        covered |= matched
        for route in matched:
            if lanes[route] not in row_personas:
                errors.append(f"table row for {route} names {persona_cell}, policy says {lanes[route]}")
        if row_personas & SINGLE_DEFAULT_PERSONAS:
            expected = {default_model(p) for p in row_personas}
            if len(expected) != 1 or None in expected:
                errors.append(f"table row {cells[0]} mixes personas with different default models")
            elif default_cell != f"`{expected.pop()}`":
                errors.append(f"table row {cells[0]} default {default_cell} disagrees with the policy")
        elif model_ids_in(default_cell):
            errors.append(f"table row {cells[0]} pins a model for a multi-model persona")
        in_fallback = matched & fallback_lanes
        if in_fallback and in_fallback != matched:
            errors.append(f"table row {cells[0]} mixes fallback and non-fallback lanes")
            continue
        if in_fallback:
            want = TABLE_FALLBACK_CELL
        elif row_personas & CRITICAL_PERSONAS:
            want = TABLE_WAIT_CELL
        else:
            want = TABLE_NO_CHANGE_CELL
        if fallback_cell != want:
            errors.append(f"table row {cells[0]} fallback {fallback_cell!r}, want {want!r}")
    missing = set(lanes) - covered
    if missing:
        errors.append(f"table omits lanes {sorted(missing)}")
    return errors


def skill_text_violations(policy, skill_text):
    """Pinned skill sentences, the lane table, model IDs and grant phrases."""
    errors = skill_table_violations(policy, skill_text)
    errors.extend(cade_skill_table_violations(policy, skill_text))
    errors.extend(dispatch_skill_table_violations(policy, skill_text))
    flat = " ".join(skill_text.split())
    for sentence in SKILL_SENTENCES:
        if sentence not in flat:
            errors.append(f"skill lost: {sentence}")
    stray = model_ids_in(skill_text) - set(MODEL_IDS.values())
    if stray:
        errors.append(f"skill names model IDs outside the seat registry: {sorted(stray)}")
    if FABLE_ID_RE.search(skill_text):
        errors.append("skill names a Fable model id; it is read from the account")
    lowered = flat.lower()
    for phrase in FORBIDDEN_GRANTS:
        if phrase in lowered:
            errors.append(f"skill grants: {phrase}")
    return errors


def prose_violations(policy):
    """Pinned policy prose sentences and grant phrases (mutations D11 to D13)."""
    errors = []
    index = {
        "personas": {p["id"]: p for p in policy["personas"]},
        "seats": {s["id"]: s for s in policy["seats"]},
        "lanes": {lane["route"]: lane for lane in policy["lanes"]},
    }
    for (collection, key, field), sentences in PROSE_SENTENCES.items():
        record = index[collection].get(key)
        if record is None:
            errors.append(f"missing {collection} record {key}")
            continue
        text = " ".join(record.get(field, "").split())
        for sentence in sentences:
            if sentence not in text:
                errors.append(f"{collection}.{key}.{field} lost: {sentence}")
    prose = []
    for collection in ("personas", "seats", "lanes"):
        for record in policy[collection]:
            prose.extend(record.get(f, "") for f in ("purpose", "doctrine", "notes"))
    prose.extend(r["rule"] for r in policy.get("enforcement", []))
    cade = policy.get("cade", {})
    prose.extend(cade.get(f, "") for f in ("orchestrator", "cross_provider_review"))
    prose.append(cade.get("children", {}).get("notes", ""))
    for lane in cade.get("lanes", []):
        prose.extend(lane.get(f, "") for f in ("purpose", "gate"))
    prose.append(cade.get("broker", {}).get("notes", ""))
    dispatch = cade.get("dispatch", {})
    prose.append(dispatch.get("notes", ""))
    for key in ("fable_model_id", "job_seat", "accounting"):
        prose.append(dispatch.get(key, {}).get("notes", ""))
    for lane in dispatch.get("lanes", []):
        prose.extend(lane.get(f, "") for f in ("purpose", "gate"))
    for key in ("units", "effects", "credentials"):
        prose.extend(record.get("notes", "") for record in dispatch.get(key, []))
    for seat in policy.get("harness_seats", []):
        prose.extend(seat.get(f, "") for f in ("purpose", "model_lane", "launcher"))
    lowered = " ".join(" ".join(str(p) for p in prose if p).split()).lower()
    for phrase in FORBIDDEN_GRANTS:
        if phrase in lowered:
            errors.append(f"policy prose grants: {phrase}")
    return errors


def critical_lane_violations(policy):
    """Mission-critical lanes that do not resolve to opus (J81(a))."""
    personas = {p["id"]: p for p in policy["personas"]}
    harness = {s["id"] for s in policy["harness_seats"]}
    seats = {s["id"]: s for s in policy["seats"]}
    errors = []
    lanes = {lane["route"]: lane for lane in policy["lanes"]}
    for route, persona in CRITICAL_ROUTES.items():
        if route not in lanes:
            errors.append(f"missing critical lane {route}")
        elif lanes[route]["persona"] != persona:
            errors.append(f"{route}: routes to {lanes[route]['persona']}, not {persona}")
    for lane in policy["lanes"]:
        route = lane["route"]
        words = set(re.split(r"[.-]", route)) - {"mythos"}
        gating = (
            route in CRITICAL_ROUTES
            or route not in NON_CRITICAL_ROUTES
            or bool(words & CRITICAL_ROUTE_WORDS)
        )
        if not gating and lane["persona"] not in CRITICAL_PERSONAS:
            continue
        if lane["persona"] in harness or lane["persona"] not in CRITICAL_PERSONAS:
            errors.append(f"{route}: routes to {lane['persona']}, not a critical persona")
            continue
        classes = set(personas[lane["persona"]]["model_classes"]) - {"operator"}
        if classes != {"opus"}:
            errors.append(f"{route}: resolves to {sorted(classes)}, not opus only")
    for seat_id, seat in seats.items():
        leaked = set(seat["default_personas"]) & CRITICAL_PERSONAS
        if seat_id != "opus" and leaked:
            errors.append(f"seat {seat_id} defaults to {sorted(leaked)}")
    if not CRITICAL_PERSONAS.issubset(set(seats.get("opus", {}).get("default_personas", []))):
        errors.append("opus seat does not default to every critical persona")
    return errors


def rule_sentence_violations(policy):
    """Load-bearing rule sentences are present verbatim and error-class."""
    errors = []
    rules = {r["id"]: r for r in policy.get("enforcement", [])}
    for rule_id, sentences in RULE_SENTENCES.items():
        rule = rules.get(rule_id)
        if rule is None or rule["severity"] != "error":
            errors.append(f"missing error-class {rule_id} rule")
            continue
        for sentence in sentences:
            if sentence not in rule["rule"]:
                errors.append(f"{rule_id} lost: {sentence}")
    return errors


def fallback_violations(policy):
    """J81(e): the opus budget fallback exists, is lane-scoped and keeps its
    obligations. Every fallback is checked, not only the named one, so a
    second unconditioned downgrade path cannot sit beside it."""
    errors = rule_sentence_violations(policy)
    fallback_list = policy.get("budget_fallbacks", [])
    fallbacks = {f["id"]: f for f in fallback_list}
    if len(fallbacks) != len(fallback_list):
        errors.append("duplicate budget fallback ids")
    if set(fallbacks) != {FALLBACK_ID}:
        errors.append(f"budget fallbacks must be exactly [{FALLBACK_ID}], got {sorted(fallbacks)}")
    if FALLBACK_ID not in fallbacks:
        errors.append(f"missing budget fallback {FALLBACK_ID}")
    persona_ids = {p["id"] for p in policy["personas"]}
    lanes = {lane["route"]: lane for lane in policy["lanes"]}
    seats = {s["id"]: s for s in policy["seats"]}
    for f in fallback_list:
        fid = f["id"]
        if (f["from_seat"], f["to_seat"]) != ("opus", "sonnet"):
            errors.append(f"{fid} must run opus -> sonnet")
        if f["to_seat"] in {"fable", "haiku"}:
            errors.append(f"{fid} falls back to {f['to_seat']}")
        missing = FALLBACK_OBLIGATIONS - set(f["obligations"])
        if missing:
            errors.append(f"{fid} drops obligations {sorted(missing)}")
        if set(f["personas"]) != {"critical"}:
            errors.append(f"{fid} must cover exactly the critical persona")
        unknown = set(f["personas"]) - persona_ids
        if unknown:
            errors.append(f"{fid} names unknown personas {sorted(unknown)}")
        if "adversarial" in f["personas"]:
            errors.append(f"{fid} must not cover the adversarial persona")
        f_lanes = set(f.get("lanes", []))
        if f_lanes != FALLBACK_LANES:
            errors.append(f"{fid} lanes must be {sorted(FALLBACK_LANES)}, got {sorted(f_lanes)}")
        reached = f_lanes & NEVER_FALLBACK_LANES
        if reached:
            errors.append(f"{fid} reaches {sorted(reached)}")
        for route in f_lanes:
            if route not in lanes:
                errors.append(f"{fid} names unknown lane {route}")
            elif lanes[route]["persona"] not in f["personas"]:
                errors.append(f"{fid} lane {route} is off its personas")
        target = seats.get(f["to_seat"], {})
        clash = set(f["personas"]) & set(target.get("forbidden_personas", []))
        if clash:
            errors.append(f"{fid} hands {sorted(clash)} to a seat that forbids it")
    return errors


def provider_allow_list_violations(policy):
    """TIN-7760: every seat sits on the provider allow-list. The Anthropic
    seats keep the exact 5.5 IDs, fable stays provider-managed and the only
    other admitted pair is kimi / k3.1-pro on the kimi-coding endpoint."""
    errors = []
    providers = {}
    endpoints = {}
    for entry in policy.get("providers", []):
        if entry.get("id") in providers:
            errors.append(f"duplicate provider {entry.get('id')}")
        providers[entry.get("id")] = set(entry.get("model_ids", []))
        found = {}
        for ep in entry.get("endpoints", []):
            if ep.get("id") in found:
                errors.append(f"provider {entry.get('id')} lists endpoint {ep.get('id')} twice")
            found[ep.get("id")] = (set(ep.get("model_ids", [])), ep.get("admitted"), ep.get("status"))
        endpoints[entry.get("id")] = found
        want = PROVIDER_ENDPOINTS.get(entry.get("id"))
        if want is not None and found != want:
            errors.append(f"provider {entry.get('id')} endpoints {found}, want {want}")
        admitted_ids = set().union(*[ids for ids, ok, _ in found.values() if ok is True])
        if providers[entry.get("id")] != admitted_ids:
            errors.append(
                f"provider {entry.get('id')} model_ids must equal its admitted endpoints' ids"
            )
    if set(providers) != set(PROVIDER_ALLOW_LIST):
        errors.append(
            f"providers must be exactly {sorted(PROVIDER_ALLOW_LIST)}, got {sorted(map(str, providers))}"
        )
    for pid, allowed in providers.items():
        extra = allowed - PROVIDER_ALLOW_LIST.get(pid, set())
        if extra:
            errors.append(f"provider {pid} admits model ids off the allow-list: {sorted(extra)}")
    for seat in policy.get("seats", []):
        sid = seat.get("id")
        provider = seat.get("provider")
        model_id = seat.get("model_id")
        if provider not in PROVIDER_ALLOW_LIST:
            errors.append(f"seat {sid} names provider {provider!r} outside the allow-list")
            continue
        if provider not in providers:
            errors.append(f"seat {sid} names provider {provider} missing from providers")
        if SEAT_PROVIDERS.get(sid) != provider:
            errors.append(f"seat {sid} names provider {provider}, want {SEAT_PROVIDERS.get(sid)}")
        endpoint = seat.get("endpoint")
        if SEAT_ENDPOINTS.get(sid) != endpoint:
            errors.append(f"seat {sid} names endpoint {endpoint!r}, want {SEAT_ENDPOINTS.get(sid)!r}")
        if endpoint is not None:
            ids, admitted, status = endpoints.get(provider, {}).get(endpoint, (set(), None, None))
            if admitted is not True or status == "forbidden":
                errors.append(f"seat {sid} runs on endpoint {endpoint!r}, which is not admitted")
            if model_id not in ids:
                errors.append(f"seat {sid} runs {model_id!r}, not an id on endpoint {endpoint!r}")
        if sid == "fable":
            if model_id is not None:
                errors.append("fable stays provider-managed and carries no model_id")
            continue
        if model_id not in PROVIDER_ALLOW_LIST[provider] or model_id not in providers.get(provider, set()):
            errors.append(f"seat {sid} runs {model_id!r}, not on the {provider} allow-list")
        if SEAT_MODEL_IDS.get(sid) != model_id:
            errors.append(f"seat {sid} runs {model_id!r}, want {SEAT_MODEL_IDS.get(sid)}")
    return errors


def kimi_violations(policy):
    """SPR-142: the Kimi seat serves its allowed personas only and never
    reaches a merge gate, the refutation-of-record, the WORD leg, a critical
    host act or a budget fallback."""
    errors = []
    seats = {s["id"]: s for s in policy.get("seats", [])}
    kimi = seats.get("kimi")
    if kimi is None:
        return ["missing kimi seat"]
    defaults = set(kimi.get("default_personas", []))
    forbidden = set(kimi.get("forbidden_personas", []))
    if defaults != KIMI_ALLOWED_PERSONAS:
        errors.append(f"kimi default personas {sorted(defaults)}, want {sorted(KIMI_ALLOWED_PERSONAS)}")
    if not KIMI_FORBIDDEN_PERSONAS <= forbidden:
        errors.append(f"kimi must forbid {sorted(KIMI_FORBIDDEN_PERSONAS - forbidden)}")
    for persona in policy.get("personas", []):
        listed = "kimi" in persona.get("model_classes", [])
        if persona["id"] in KIMI_FORBIDDEN_PERSONAS and listed:
            errors.append(f"persona {persona['id']} lists kimi")
        elif listed and persona["id"] not in KIMI_ALLOWED_PERSONAS:
            errors.append(f"persona {persona['id']} lists kimi outside the allowed personas")
    for f in policy.get("budget_fallbacks", []):
        if "kimi" in (f.get("from_seat"), f.get("to_seat")):
            errors.append(f"budget fallback {f.get('id')} touches kimi")
    errors.extend(mythos_kimi_violations(policy))
    review = next((p for p in policy.get("personas", []) if p["id"] == "evidentiary-review"), None)
    if review is None:
        errors.append("missing persona evidentiary-review")
    elif not HARNESS_FORBIDDEN <= set(review.get("forbidden_tasks", [])):
        errors.append("persona evidentiary-review must forbid the gate, refutation and WORD legs")
    for persona in ("business", "marketing", "ceo-ops"):
        record = next((p for p in policy.get("personas", []) if p["id"] == persona), None)
        if record is None:
            errors.append(f"missing persona {persona}")
            continue
        tasks = set(record.get("forbidden_tasks", []))
        for task in ("merge", "merge-gating-review", "external-send-without-confirm"):
            if task not in tasks:
                errors.append(f"persona {persona} must forbid {task}")
    return errors


def mythos_kimi_violations(policy):
    """No mythos.* lane may resolve to a persona that lists kimi, and every
    persona that lists kimi is used only by cade.* lanes. This is the
    structural fence; the prose sentence alone is not enough."""
    errors = []
    personas = {p["id"]: p for p in policy.get("personas", [])}
    kimi_personas = {pid for pid, p in personas.items() if "kimi" in p.get("model_classes", [])}
    for lane in policy.get("lanes", []):
        if lane.get("persona") in kimi_personas:
            errors.append(f"{lane.get('route')}: mythos lane resolves to persona {lane['persona']}, which lists kimi")
    cade_personas = {lane.get("persona") for lane in policy.get("cade", {}).get("lanes", [])}
    mythos_personas = {lane.get("persona") for lane in policy.get("lanes", [])}
    for pid in sorted(kimi_personas & mythos_personas):
        errors.append(f"persona {pid} lists kimi and serves a mythos lane")
    for pid in sorted(kimi_personas - cade_personas):
        errors.append(f"persona {pid} lists kimi but no cade lane uses it")
    return errors


def resolve_child_seats(policy, route, parent_custody):
    """A Cade child's effective seats: its lane's default then alternate seats
    that also sit in its parent home's custody class. An unknown route or
    custody class, or an empty result, means the child waits: []."""
    cade = policy.get("cade", {})
    children = cade.get("children", {})
    if children.get("seat_resolution") != CHILD_SEAT_RESOLUTION:
        return []
    lane = next((l for l in cade.get("lanes", []) if l.get("route") == route), None)
    custody = {c.get("id"): c.get("seats", []) for c in cade.get("custody_classes", [])}
    if lane is None or parent_custody not in custody:
        return []
    allowed = set(custody[parent_custody])
    ordered = [lane.get("default_seat"), *lane.get("alternate_seats", [])]
    return [seat for seat in ordered if seat in allowed]


def child_resolution_violations(policy):
    """Fail closed when any parent custody resolves a child to a seat its
    custody forbids: confidential never reaches Kimi, health reaches nothing
    and a child never gets a seat its lane does not name."""
    errors = []
    children = policy.get("cade", {}).get("children", {})
    if children.get("seat_resolution") != CHILD_SEAT_RESOLUTION:
        errors.append(f"cade children seat_resolution must be {CHILD_SEAT_RESOLUTION}")
    if children.get("on_no_seat") != CHILD_ON_NO_SEAT:
        errors.append(f"cade children on_no_seat must be {CHILD_ON_NO_SEAT}")
    lanes = {l.get("route"): l for l in policy.get("cade", {}).get("lanes", [])}
    for route, lane in sorted(lanes.items()):
        named = {lane.get("default_seat"), *lane.get("alternate_seats", [])}
        for parent, allowed in CUSTODY_SEATS.items():
            seats = resolve_child_seats(policy, route, parent)
            if not set(seats) <= allowed:
                errors.append(f"{parent} parent on {route} resolves to {seats}, outside {sorted(allowed)}")
            if not set(seats) <= named:
                errors.append(f"{parent} parent on {route} resolves to seats the lane does not name: {seats}")
            if parent in ("confidential", "health") and "kimi" in seats:
                errors.append(f"{parent} parent on {route} resolves to Kimi")
            if parent == "health" and seats:
                errors.append(f"health parent on {route} resolves to {seats}; health is never hosted")
    return errors


def kimi_seats(policy):
    """Every Kimi seat: a model seat on the kimi provider, or a harness seat
    whose model lane runs on a Kimi endpoint."""
    seats = {s.get("id") for s in policy.get("seats", []) if s.get("provider") == "kimi"}
    seats |= {
        h.get("id")
        for h in policy.get("harness_seats", [])
        if str(h.get("model_lane", "")).startswith("kimi")
    }
    return seats


def kimi_custody_violations(policy):
    """SPR-182: every Kimi seat, pi-k3 included, sits in the ops class and
    never in the confidential or health class."""
    errors = []
    members = {}
    for entry in policy.get("cade", {}).get("custody_classes", []):
        for seat in [*entry.get("seats", []), *entry.get("harness_seats", [])]:
            members.setdefault(seat, set()).add(entry.get("id"))
    for seat in sorted(map(str, kimi_seats(policy))):
        classes = members.get(seat, set())
        if "ops" not in classes:
            errors.append(f"Kimi seat {seat} lacks the ops class (SPR-182)")
        for klass in sorted(classes & {"confidential", "health"}):
            errors.append(f"Kimi seat {seat} sits in the {klass} class")
    return errors


def cade_violations(policy):
    """SPR-142 to SPR-145: cade.* lanes, custody classes, the auto-level child
    rule and advisory cross-provider review. Fails closed on Kimi in a
    confidential or critical lane, fable anywhere in Cade, a seat outside its
    custody class or persona, an unknown route and a weakened child rule."""
    errors = []
    cade = policy.get("cade")
    if not isinstance(cade, dict):
        return ["missing cade section"]
    personas = {p["id"]: p for p in policy.get("personas", [])}
    seat_ids = {s["id"] for s in policy.get("seats", [])}
    custody = {}
    for entry in cade.get("custody_classes", []):
        custody[entry.get("id")] = set(entry.get("seats", []))
    if custody != CUSTODY_SEATS:
        errors.append(f"custody classes {custody} disagree with {CUSTODY_SEATS}")
    harness_custody = {
        entry.get("id"): set(entry.get("harness_seats", [])) for entry in cade.get("custody_classes", [])
    }
    if harness_custody != CUSTODY_HARNESS_SEATS:
        errors.append(f"custody harness seats {harness_custody} disagree with {CUSTODY_HARNESS_SEATS}")
    errors.extend(kimi_custody_violations(policy))
    children = cade.get("children", {})
    if children.get("level") != "auto":
        errors.append("cade children must run auto-level (SPR-144)")
    if children.get("custody") != "follows-parent-home":
        errors.append("cade child custody must follow the parent home")
    errors.extend(child_resolution_violations(policy))
    orchestrator = " ".join(cade.get("orchestrator", "").split())
    if "Cade's code work follows CORE-5, and J81 governs the mythos.* lanes." not in orchestrator:
        errors.append("cade.orchestrator must keep Cade's code work on CORE-5")
    if children.get("provider_fallback") != "none":
        errors.append("cade children never reroute to another provider")
    missing = CHILD_BINDINGS - set(children.get("bindings", []))
    if missing:
        errors.append(f"cade children lost standing-rule bindings {sorted(missing)}")
    if "not read-only" not in children.get("notes", ""):
        errors.append("cade children notes must say they are not read-only")
    review = " ".join(cade.get("cross_provider_review", "").split())
    for sentence in RULE_SENTENCES["cross-provider-review-advisory"]:
        if sentence not in review:
            errors.append(f"cade.cross_provider_review lost: {sentence}")
    lanes = cade.get("lanes", [])
    routes = [lane.get("route") for lane in lanes]
    if len(routes) != len(set(routes)):
        errors.append("duplicate cade routes")
    if set(routes) != CADE_ROUTES:
        errors.append(f"cade routes {sorted(map(str, routes))}, want {sorted(CADE_ROUTES)}")
    for lane in lanes:
        route = lane.get("route")
        persona_id = lane.get("persona")
        persona = personas.get(persona_id)
        if persona is None:
            errors.append(f"{route}: unknown persona {persona_id}")
            continue
        seats = {lane.get("default_seat"), *lane.get("alternate_seats", [])}
        if lane.get("default_seat") in lane.get("alternate_seats", []):
            errors.append(f"{route}: default seat repeated as an alternate")
        unknown = seats - seat_ids
        if unknown:
            errors.append(f"{route}: unknown seats {sorted(map(str, unknown))}")
        if "fable" in seats or "fable" in persona["model_classes"]:
            errors.append(f"{route}: fable never serves a cade lane")
        off_persona = seats - set(persona["model_classes"])
        if off_persona:
            errors.append(f"{route}: seats {sorted(map(str, off_persona))} are off persona {persona_id}")
        klass = lane.get("custody")
        if klass not in custody or klass == "health":
            errors.append(f"{route}: custody {klass!r} has no hosted seat")
            continue
        off_custody = seats - CUSTODY_SEATS.get(klass, set())
        if off_custody:
            errors.append(f"{route}: seats {sorted(map(str, off_custody))} break {klass} custody")
        if klass == "confidential" and "kimi" in seats:
            errors.append(f"{route}: Kimi in a confidential lane")
        words = set(re.split(r"[.-]", route or "")) - {"cade"}
        critical = persona_id in CRITICAL_PERSONAS or bool(words & CRITICAL_ROUTE_WORDS)
        if critical and persona_id not in CRITICAL_PERSONAS:
            errors.append(f"{route}: carries a critical word off a critical persona")
        if critical and seats != {"opus"}:
            errors.append(f"{route}: critical cade lane resolves to {sorted(map(str, seats))}, not opus only")
        if not (lane.get("gate") or "").strip() or not (lane.get("purpose") or "").strip():
            errors.append(f"{route}: gate and purpose are required")
    return errors


def cade_skill_table_violations(policy, skill_text):
    """The skill's Cade lanes table agrees with .cade.lanes[] row for row."""
    errors = []
    marker = "\n### Cade lanes\n"
    if skill_text.count(marker) != 1:
        return ["skill needs exactly one Cade lanes section"]
    before, after = skill_text.split(marker, 1)
    body, _, rest = after.partition("\n#")
    outside = strip_section(before + "\n#" + rest, DISPATCH_TABLE_MARKER)
    for line in outside.splitlines():
        if line.strip().startswith("|") and CADE_ROUTE_RE.search(line):
            errors.append(f"cade table row outside Cade lanes: {line.strip()}")
    lanes = {lane["route"]: lane for lane in policy.get("cade", {}).get("lanes", [])}
    seen = set()
    header_seen = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_seen:
            if cells != CADE_TABLE_HEADER:
                errors.append(f"cade table header is {cells}, want {CADE_TABLE_HEADER}")
            header_seen = True
            continue
        if set(stripped) <= set("|-: "):
            continue
        if len(cells) != len(CADE_TABLE_HEADER) or line != stripped:
            errors.append(f"unparseable cade table row: {line!r}")
            continue
        routes = CADE_ROUTE_RE.findall(cells[0])
        if len(routes) != 1 or cells[0] != f"`{routes[0]}`":
            errors.append(f"unparseable cade table row: {line!r}")
            continue
        route = routes[0]
        if route in seen:
            errors.append(f"cade table lists {route} twice")
        seen.add(route)
        lane = lanes.get(route)
        if lane is None:
            errors.append(f"cade table route {route} matches no policy lane")
            continue
        alternates = ", ".join(lane["alternate_seats"]) or "none"
        want = [f"`{route}`", lane["persona"], lane["custody"], lane["default_seat"], alternates]
        if cells != want:
            errors.append(f"cade table row {cells} disagrees with the policy {want}")
    if not header_seen:
        errors.append("Cade lanes section has no table")
    missing = set(lanes) - seen
    if missing:
        errors.append(f"cade table omits lanes {sorted(missing)}")
    return errors


DISPATCH_TABLE_MARKER = "\n### Dispatch lanes\n"


def strip_section(text, marker):
    """text without the section that starts at marker, up to the next heading."""
    if text.count(marker) != 1:
        return text
    before, after = text.split(marker, 1)
    _, sep, rest = after.partition("\n#")
    return before + sep + rest


def dispatch_skill_table_violations(policy, skill_text):
    """The skill's Dispatch lanes table agrees with .cade.dispatch.lanes[]."""
    errors = []
    if skill_text.count(DISPATCH_TABLE_MARKER) != 1:
        return ["skill needs exactly one Dispatch lanes section"]
    before, after = skill_text.split(DISPATCH_TABLE_MARKER, 1)
    body, _, rest = after.partition("\n#")
    for line in (before + "\n#" + rest).splitlines():
        if line.strip().startswith("|") and DISPATCH_ROUTE_RE.search(line):
            errors.append(f"dispatch table row outside Dispatch lanes: {line.strip()}")
    lanes = {l["route"]: l for l in policy.get("cade", {}).get("dispatch", {}).get("lanes", [])}
    seen = set()
    header_seen = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_seen:
            if cells != DISPATCH_TABLE_HEADER:
                errors.append(f"dispatch table header is {cells}, want {DISPATCH_TABLE_HEADER}")
            header_seen = True
            continue
        if set(stripped) <= set("|-: "):
            continue
        routes = DISPATCH_ROUTE_RE.findall(cells[0])
        if len(cells) != len(DISPATCH_TABLE_HEADER) or line != stripped or len(routes) != 1 or cells[0] != f"`{routes[0]}`":
            errors.append(f"unparseable dispatch table row: {line!r}")
            continue
        route = routes[0]
        if route in seen:
            errors.append(f"dispatch table lists {route} twice")
        seen.add(route)
        lane = lanes.get(route)
        if lane is None:
            errors.append(f"dispatch table route {route} matches no policy lane")
            continue
        want = [f"`{route}`", lane["parent_persona"], lane["root_seat"], ", ".join(lane["custody"])]
        if cells != want:
            errors.append(f"dispatch table row {cells} disagrees with the policy {want}")
    if not header_seen:
        errors.append("Dispatch lanes section has no table")
    missing = set(lanes) - seen
    if missing:
        errors.append(f"dispatch table omits lanes {sorted(missing)}")
    return errors


def normalize_tag(tag):
    return re.sub(r"[^a-z0-9]", "", str(tag).lower())


def carve_out_hits(policy, tags):
    """The carve-out tags a tag list carries. A tag matches after lowercasing
    and dropping everything outside a-z and 0-9, and a tag that starts with a
    carve-out tag matches too (normalized-prefix)."""
    carve = policy.get("cade", {}).get("dispatch", {}).get("carve_out_tags", [])
    hits = set()
    for tag in tags if isinstance(tags, list) else []:
        norm = normalize_tag(tag)
        for raw in carve:
            target = normalize_tag(raw)
            if target and norm.startswith(target):
                hits.add(raw)
    return hits


def admit_fable_id(policy, read_id):
    """A Fable id read from the account, or None when it fails the pattern."""
    pattern = policy.get("cade", {}).get("dispatch", {}).get("fable_model_id", {}).get("id_pattern")
    if not pattern or not isinstance(read_id, str) or not re.fullmatch(pattern, read_id):
        return None
    return read_id


def resolve_root(policy, envelope, read_fable_id=None):
    """Reference root resolver for a cade.dispatch/v1 envelope.

    Returns (seat, model_id) for the job root. A Fable root whose id has not
    been read from the account returns ("fable", None): the job waits. A job
    whose lane is unknown, whose custody the lane does not admit or whose tags
    are malformed off the develop lane gets None: no root, the job does not
    run. Anything uncertain resolves toward Opus, never toward Fable.
    """
    dispatch = policy.get("cade", {}).get("dispatch", {})
    seats = {s.get("id"): s for s in policy.get("seats", [])}
    lanes = {l.get("route"): l for l in dispatch.get("lanes", [])}
    lane = lanes.get(envelope.get("lane"))
    if lane is None or envelope.get("custody") not in lane.get("custody", []):
        return None
    carve_seat = dispatch.get("carve_out_seat")
    if carve_seat != "opus":
        return None
    opus = ("opus", seats.get("opus", {}).get("model_id"))
    tags = envelope.get("tags")
    tags_ok = isinstance(tags, list) and all(isinstance(tag, str) and tag.strip() for tag in tags)
    if lane.get("route") == DEVELOP_ROUTE and dispatch.get("develop_uncertain_tags") == "opus":
        if not tags_ok:
            return opus
    if lane.get("root_seat") == "opus":
        return opus
    if not tags_ok:
        return None
    if dispatch.get("carve_out_match") != "normalized-prefix" or carve_out_hits(policy, tags):
        return opus
    if lane.get("root_seat") == "fable" and lane.get("parent_persona") == "fable-orchestrator":
        return ("fable", admit_fable_id(policy, read_fable_id))
    return None


def child_envelope_violations(policy, parent, child):
    """A dispatch-request child keeps the parent's root, adds one to its
    depth, carries every carve-out tag the parent carried and keeps or
    narrows its custody."""
    errors = []
    order = list(policy.get("cade", {}).get("dispatch", {}).get("custody_order", []))
    if child.get("root_id") != parent.get("root_id"):
        errors.append("child root_id differs from the parent's")
    if child.get("parent_job_id") != parent.get("job_id"):
        errors.append("child parent_job_id is not the parent's job_id")
    if child.get("job_id") == parent.get("job_id"):
        errors.append("child reuses the parent's job_id")
    if child.get("depth") != parent.get("depth", 0) + 1:
        errors.append("child depth is not the parent's depth plus one")
    dropped = carve_out_hits(policy, parent.get("tags")) - carve_out_hits(policy, child.get("tags"))
    if dropped:
        errors.append(f"child drops carve-out tags {sorted(dropped)}")
    pc, cc = parent.get("custody"), child.get("custody")
    if pc not in order or cc not in order or order.index(cc) < order.index(pc):
        errors.append(f"child custody {cc!r} widens the parent's {pc!r}")
    return errors


def walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_keys(item)


def broker_violations(policy):
    """TIN-7125, new policy: the broker routes and authors envelopes only."""
    broker = policy.get("cade", {}).get("broker")
    if not isinstance(broker, dict):
        return ["missing cade.broker"]
    errors = []
    if broker.get("seat") != "kimi":
        errors.append("cade.broker must be the kimi seat")
    if set(broker.get("may", [])) != BROKER_MAY:
        errors.append(f"cade.broker may {broker.get('may')}, want {sorted(BROKER_MAY)}")
    if broker.get("gates") != []:
        errors.append("cade.broker gates nothing")
    if broker.get("names_model") is not False:
        errors.append("cade.broker never names a model")
    writes = set(broker.get("writes", []))
    if writes != BROKER_WRITES:
        errors.append(f"cade.broker writes {sorted(writes)}, want {sorted(BROKER_WRITES)}")
    if "custody" in writes:
        errors.append("cade.broker never sets custody")
    if broker.get("new_policy") is not True:
        errors.append("cade.broker must stay marked as new policy")
    if broker.get("unattended") != "admitted":
        errors.append("cade.broker unattended brokering is admitted (TIN-7125 0a1924b3)")
    return errors


def fable_id_violations(policy):
    """TIN-7125: the Fable id is a config value read from the account. The
    policy never carries it, in a seat, the allow-list or anywhere else."""
    errors = []
    seats = {s.get("id"): s for s in policy.get("seats", [])}
    for sid, source in SEAT_MODEL_ID_SOURCES.items():
        if seats.get(sid, {}).get("model_id_source") != source:
            errors.append(f"seat {sid} model_id_source must be {source}")
    if "model_id" in seats.get("fable", {}):
        errors.append("the fable seat carries no model_id: it is read from the account")
    cfg = policy.get("cade", {}).get("dispatch", {}).get("fable_model_id", {})
    want = {
        "config_key": "fable_model_id",
        "source": "account",
        "account": JOB_SEAT["account"],
        "admitted_when": "read-from-account-after-login",
        "on_unread": "wait",
    }
    for key, value in want.items():
        if cfg.get(key) != value:
            errors.append(f"fable_model_id.{key} must be {value!r}")
    extra = set(cfg) - set(want) - {"id_pattern", "notes"}
    if extra:
        errors.append(f"fable_model_id carries {sorted(extra)}; the policy holds no id value")
    pattern = cfg.get("id_pattern", "")
    if not pattern.startswith("^claude-fable-"):
        errors.append("fable_model_id.id_pattern must admit Fable-class ids only")
    else:
        for other in SEAT_MODEL_IDS.values():
            if re.fullmatch(pattern, other):
                errors.append(f"fable_model_id.id_pattern admits {other}")
    scrubbed = json.loads(json.dumps(policy))
    scrubbed.get("cade", {}).get("dispatch", {}).get("fable_model_id", {}).pop("id_pattern", None)
    hits = sorted(set(FABLE_ID_RE.findall(json.dumps(scrubbed))))
    if hits:
        errors.append(f"policy hard-codes Fable ids {hits}")
    return errors


def fable_placement_violations(policy):
    """Fable serves only the synthesis seat and cade.delegate.* roots, as
    orchestrator. It never runs a unit and keeps every task prohibition."""
    errors = []
    personas = {p["id"]: p for p in policy.get("personas", [])}
    fable_personas = {pid for pid, p in personas.items() if "fable" in p.get("model_classes", [])}
    if fable_personas != FABLE_PERSONAS:
        errors.append(f"personas listing fable are {sorted(fable_personas)}, want {sorted(FABLE_PERSONAS)}")
    orch = personas.get("fable-orchestrator")
    if orch is None:
        errors.append("missing persona fable-orchestrator")
    else:
        if orch.get("model_classes") != ["fable"]:
            errors.append("fable-orchestrator routes to fable only")
        missing = FABLE_FORBIDDEN - set(orch.get("forbidden_tasks", []))
        if missing:
            errors.append(f"fable-orchestrator lost prohibitions {sorted(missing)}")
    for lane in policy.get("lanes", []):
        if lane.get("persona") == "fable-orchestrator":
            errors.append(f"{lane.get('route')}: a mythos lane uses fable-orchestrator")
    for lane in policy.get("cade", {}).get("lanes", []):
        if lane.get("persona") in FABLE_PERSONAS:
            errors.append(f"{lane.get('route')}: a cade.lanes route uses a fable persona")
    fable = next((s for s in policy.get("seats", []) if s.get("id") == "fable"), {})
    if set(fable.get("default_personas", [])) != FABLE_PERSONAS:
        errors.append("fable seat must default to exactly the fable personas")
    for lane in policy.get("cade", {}).get("dispatch", {}).get("lanes", []):
        route = str(lane.get("route"))
        if lane.get("root_seat") == "fable" or lane.get("parent_persona") in FABLE_PERSONAS:
            if not route.startswith("cade.delegate.") or lane.get("parent_persona") != "fable-orchestrator":
                errors.append(f"{route}: fable roots only cade.delegate.* as orchestrator")
    return errors


def dispatch_violations(policy):
    """TIN-7125: Cade Dispatch v1. The shared contract, the lanes and their
    roots, units, effects, credentials, the job seat, the broker, the Fable
    id and accounting without caps."""
    cade = policy.get("cade", {})
    dispatch = cade.get("dispatch")
    if not isinstance(dispatch, dict):
        return ["missing cade.dispatch"]
    errors = []
    scalars = {
        "envelope_schema": ENVELOPE_SCHEMA_ID,
        "envelope_schema_file": "policy/cade-dispatch.schema.json",
        "receipt_schema": RECEIPT_SCHEMA_ID,
        "receipt_schema_file": "policy/cade-dispatch-receipt.schema.json",
        "recipient": RECIPIENT,
        "harness_seat": DISPATCH_HARNESS_SEAT,
        "resolver": "fixed-resolver-reads-this-policy",
        "carve_out_match": "normalized-prefix",
        "carve_out_seat": "opus",
        "develop_uncertain_tags": "opus",
        "tag_inheritance": "carve-out-tags-pass-to-every-child",
        "custody_source": "source-item-label",
        "custody_rule": "narrow-only",
        "child_dispatch": "dispatch-request-through-cade",
        "unknown_unit": "opus",
    }
    for key, value in scalars.items():
        if dispatch.get(key) != value:
            errors.append(f"cade.dispatch.{key} is {dispatch.get(key)!r}, want {value!r}")
    lists = {
        "envelope_fields": list(ENVELOPE_FIELDS),
        "receipt_fields": list(RECEIPT_FIELDS),
        "reply_kinds": list(REPLY_KINDS),
        "receipt_statuses": list(RECEIPT_STATUSES),
        "carve_out_tags": list(CARVE_OUT_TAGS),
        "custody_order": list(CUSTODY_ORDER),
    }
    for key, value in lists.items():
        if dispatch.get(key) != value:
            errors.append(f"cade.dispatch.{key} is {dispatch.get(key)!r}, want {value!r}")
    personas = {p["id"]: p for p in policy.get("personas", [])}
    seats = {s["id"]: s for s in policy.get("seats", [])}
    harness_custody = {
        c.get("id"): set(c.get("harness_seats", [])) for c in cade.get("custody_classes", [])
    }
    lane_list = dispatch.get("lanes", [])
    lanes = {l.get("route"): l for l in lane_list}
    if len(lanes) != len(lane_list):
        errors.append("duplicate dispatch routes")
    if set(lanes) != set(DISPATCH_ROUTES):
        errors.append(f"dispatch routes {sorted(map(str, lanes))}, want {sorted(DISPATCH_ROUTES)}")
    for route, lane in lanes.items():
        parent, root = lane.get("parent_persona"), lane.get("root_seat")
        if DISPATCH_ROUTES.get(route, (parent, root)) != (parent, root):
            errors.append(f"{route}: root is {parent}/{root}, want {DISPATCH_ROUTES[route]}")
        persona = personas.get(parent)
        if persona is None:
            errors.append(f"{route}: unknown parent persona {parent}")
        elif root not in persona.get("model_classes", []):
            errors.append(f"{route}: root seat {root} is off persona {parent}")
        if parent in seats.get(root, {}).get("forbidden_personas", []):
            errors.append(f"{route}: seat {root} forbids persona {parent}")
        if parent in CRITICAL_PERSONAS and root != "opus":
            errors.append(f"{route}: a critical root runs on {root}, not opus")
        for klass in lane.get("custody", []):
            if klass in ("confidential", "health"):
                errors.append(f"{route}: admits {klass} custody")
            if DISPATCH_HARNESS_SEAT not in harness_custody.get(klass, set()):
                errors.append(f"{route}: {klass} custody does not admit {DISPATCH_HARNESS_SEAT}")
        if not lane.get("custody"):
            errors.append(f"{route}: admits no custody class")
        if not (lane.get("gate") or "").strip() or not (lane.get("purpose") or "").strip():
            errors.append(f"{route}: gate and purpose are required")
    units = {u.get("kind"): u.get("seats") for u in dispatch.get("units", [])}
    if units != UNIT_SEATS:
        errors.append(f"dispatch units {units}, want {UNIT_SEATS}")
    for kind, unit_seats in units.items():
        if {"fable", "kimi"} & set(unit_seats or []):
            errors.append(f"unit {kind} runs on fable or kimi")
        if kind in OPUS_ONLY_UNITS and unit_seats != ["opus"]:
            errors.append(f"unit {kind} must run on opus only")
    effects = {e.get("id"): e for e in dispatch.get("effects", [])}
    envelope_effects = {eid for eid, e in effects.items() if e.get("envelope") is True}
    go_effects = {eid for eid, e in effects.items() if e.get("needs_go") is True}
    if envelope_effects != ENVELOPE_EFFECTS:
        errors.append(f"envelope effects {sorted(envelope_effects)}, want {sorted(ENVELOPE_EFFECTS)}")
    if go_effects != GO_EFFECTS:
        errors.append(f"GO effects {sorted(go_effects)}, want {sorted(GO_EFFECTS)}")
    if envelope_effects & go_effects:
        errors.append(f"effects both in the envelope and behind a GO: {sorted(envelope_effects & go_effects)}")
    for cred in dispatch.get("credentials", []):
        if cred.get("agent_mints") is not False or not str(cred.get("minted_by", "")).startswith("jess"):
            errors.append(f"credential {cred.get('id')} is minted by Jess, never by an agent")
    accounting = dispatch.get("accounting", {})
    if accounting.get("budget_envelope") != "report-only":
        errors.append("the budget envelope is report-only")
    if accounting.get("containment") != "none":
        errors.append("dispatch adds no containment")
    capped = sorted({k for k in walk_keys([dispatch, cade.get("broker", {})]) if CAP_KEY_RE.search(k)})
    if capped:
        errors.append(f"dispatch carries cap-shaped keys {capped}; caps need a ruled interview")
    harness = {h.get("id"): h for h in policy.get("harness_seats", [])}
    seat = harness.get(DISPATCH_HARNESS_SEAT)
    if seat is None:
        errors.append(f"missing harness seat {DISPATCH_HARNESS_SEAT}")
    else:
        if seat.get("dispatch_surface") != DISPATCH_SURFACE:
            errors.append(f"{DISPATCH_HARNESS_SEAT} surface must be {DISPATCH_SURFACE}")
        if "launcher" not in str(seat.get("launcher", "")).lower():
            errors.append(f"{DISPATCH_HARNESS_SEAT} must name its fixed launcher")
        if seat.get("posture") != HARNESS_POSTURES[DISPATCH_HARNESS_SEAT]:
            errors.append(f"{DISPATCH_HARNESS_SEAT} posture must be gated-worker")
        missing = CLAUDE_CODE_CADE_FORBIDDEN - set(seat.get("forbidden_tasks", []))
        if missing:
            errors.append(f"{DISPATCH_HARNESS_SEAT} must forbid {sorted(missing)}")
        if str(seat.get("model_lane", "")).startswith("kimi"):
            errors.append(f"{DISPATCH_HARNESS_SEAT} is a Claude Code seat, not a Kimi seat")
    job_seat = dispatch.get("job_seat", {})
    for key, value in JOB_SEAT.items():
        if job_seat.get(key) != value:
            errors.append(f"job_seat.{key} is {job_seat.get(key)!r}, want {value!r}")
    if job_seat.get("config_dir") in ("$HOME/.claude", "~/.claude"):
        errors.append("a job never uses Jess's own ~/.claude login")
    errors.extend(broker_violations(policy))
    errors.extend(fable_id_violations(policy))
    errors.extend(fable_placement_violations(policy))
    return errors


def dispatch_schema_violations(policy, envelope_schema, receipt_schema):
    """The envelope and receipt schemas carry the shared contract exactly."""
    errors = []
    dispatch = policy.get("cade", {}).get("dispatch", {})
    for name, schema, fields, optional, schema_id in (
        ("envelope", envelope_schema, ENVELOPE_FIELDS, ENVELOPE_OPTIONAL, ENVELOPE_SCHEMA_ID),
        ("receipt", receipt_schema, RECEIPT_FIELDS, set(), RECEIPT_SCHEMA_ID),
    ):
        props = schema.get("properties", {})
        if set(props) - {"schema"} != set(fields):
            errors.append(f"{name} schema fields {sorted(set(props) - {'schema'})}, want {sorted(fields)}")
        if props.get("schema") != {"const": schema_id}:
            errors.append(f"{name} schema field must be const {schema_id}")
        if set(schema.get("required", [])) != set(fields) - optional:
            errors.append(f"{name} schema required fields drifted")
        if schema.get("additionalProperties") is not False:
            errors.append(f"{name} schema must close its record")
        lane_enum = set(schema.get("$defs", {}).get("lane", {}).get("enum", []))
        if lane_enum != set(DISPATCH_ROUTES):
            errors.append(f"{name} schema lane enum {sorted(lane_enum)}, want {sorted(DISPATCH_ROUTES)}")
    env_props = envelope_schema.get("properties", {})
    named = sorted(k for k in env_props if "model" in k or "seat" in k)
    if named:
        errors.append(f"envelope carries model fields {named}; the broker never names a model")
    if set(env_props.get("allowed_effects", {}).get("items", {}).get("enum", [])) != ENVELOPE_EFFECTS:
        errors.append("envelope allowed_effects enum drifted from the envelope effects")
    if set(env_props.get("custody", {}).get("enum", [])) != set(CUSTODY_ORDER):
        errors.append("envelope custody enum must name every custody class")
    status = receipt_schema.get("properties", {}).get("status", {}).get("enum", [])
    if status != list(RECEIPT_STATUSES):
        errors.append(f"receipt status enum {status}, want {list(RECEIPT_STATUSES)}")
    if envelope_schema.get("$id", "").rsplit("/", 2)[-2:] != dispatch.get("envelope_schema_file", "").split("/"):
        errors.append("envelope schema $id does not name envelope_schema_file")
    if receipt_schema.get("$id", "").rsplit("/", 2)[-2:] != dispatch.get("receipt_schema_file", "").split("/"):
        errors.append("receipt schema $id does not name receipt_schema_file")
    return errors


def dhall_seat_model_ids(dhall_text):
    """Seat model_class -> model_id (None when provider-managed) from Dhall."""
    pattern = re.compile(
        r'model_class = "(?P<seat>[a-z]+)"\s*,\s*model_id =\s*'
        r'(?:None Text|Some "(?P<model_id>[^"]+)")'
    )
    return {m["seat"]: m["model_id"] for m in pattern.finditer(dhall_text)}


# Golden digests (enforcement re-review on PR #49, second round). The checks
# above give readable errors for known weakenings, but a phrase list can never
# catch every paraphrase an agent would act on. So the surfaces an orchestrator
# reads to pick a model are pinned whole: the shipped skill, the policy
# document and the whole AGENTS.md "## Delegation" section (a bullet alone can
# be extended by a lazy continuation line or a sibling bullet). Any edit to them fails here until the
# digest is updated in the same commit, which puts the change in front of the
# Opus merge gate as a deliberate policy edit (J81(a)). Packaging digests do not
# do this: `just manifest` regenerates them.
#
# To update after a reviewed edit: run this module and copy the digest the
# failure prints.
SKILL_SHA256 = "2105fc6976fd929a88c3990d0833d95208e1096a866afef0a798c3ac20cf168b"
POLICY_CANONICAL_SHA256 = "e7be691884bc6882cec805b16430394689f07414620a8aedf0b39067a4767f56"
AGENTS_DELEGATION_SHA256 = "490d1560e5854a96dc900a54c438e4878bbed04aeceb997989906d2b1452295c"
AGENTS_PATH = ROOT / "AGENTS.md"


def canonical_policy_sha256(policy):
    text = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def agents_delegation_section(agents_text):
    """The AGENTS.md "## Delegation" section, heading included, up to the
    next "## " heading."""
    marker = "\n## Delegation\n"
    if agents_text.count(marker) != 1:
        return None
    body = agents_text.split(marker, 1)[1]
    return marker + body.split("\n## ", 1)[0]


# Routing words that may only appear inside the pinned "## Delegation"
# section of AGENTS.md, so routing guidance cannot sit in an unpinned sibling
# section (rounds 4 and 5). Includes the policy's own fallback vocabulary and
# lane IDs without the mythos. prefix. Scanned over whitespace-joined text too,
# so a line wrap cannot split a phrase.
AGENTS_ROUTING_RE = re.compile(
    r"\b(opus|sonnet|haiku|fable|claude|mythos\.|j8\d|quota|fall(s|ing)? ?back|"
    r"downgrad|(implement|gate|act|review)\.[a-z]|merge.gat|host act|model class)",
    re.IGNORECASE,
)
DELEGATION_HEADING_RE = re.compile(r"^#{1,6}\s*delegation\b", re.IGNORECASE | re.MULTILINE)


def agents_routing_outside_violations(agents_text):
    """Exactly one Delegation heading of any level or spelling, and no routing
    words outside the pinned Delegation section."""
    errors = []
    headings = DELEGATION_HEADING_RE.findall(agents_text)
    if len(headings) != 1:
        errors.append(f"AGENTS.md has {len(headings)} Delegation headings, want 1")
    section = agents_delegation_section(agents_text)
    if section is None:
        return errors + ["AGENTS.md needs exactly one '## Delegation' section"]
    outside = agents_text.replace(section, "\n", 1)
    return errors + [f"AGENTS.md outside ## Delegation: {hit}" for hit in routing_hits(outside)]


def routing_hits(text):
    """Routing words in text, per line and across line wraps."""
    hits = [m.group(0) for line in text.splitlines() for m in AGENTS_ROUTING_RE.finditer(line)]
    joined = [m.group(0) for m in AGENTS_ROUTING_RE.finditer(" ".join(text.split()))]
    return hits if len(hits) >= len(joined) else joined


# The prompt-toon skill is a condensation skill that Claude Code also loads.
# Its two existing routing-word lines are allowed verbatim. Any other routing
# text in it fails (round 5, B08).
PROMPT_TOON_SKILL_PATH = ROOT / ".agents" / "skills" / "prompt-toon" / "SKILL.md"
PROMPT_TOON_ALLOWED_ROUTING_LINES = (
    "--mythos-route mythos.synthesis \\",
    'description: Condense wide/deep agent research, subagent outputs, logs, JSON/JSONL records, or staged Codex/Claude handoffs into safe source cards and a minimized prompt package using the local prompt-toon CLI. Use when asked to synthesize research, queue/store agent outputs, reduce prompt tokens, preserve provenance, or evaluate optional TOON encoding for flat uniform rows.',
)


def prompt_toon_skill_violations(skill_text):
    errors = []
    allowed_hits = 0
    rest = []
    for line in skill_text.splitlines():
        stripped = line.strip()
        if stripped in PROMPT_TOON_ALLOWED_ROUTING_LINES:
            allowed_hits += len(AGENTS_ROUTING_RE.findall(stripped))
        else:
            rest.append(line)
    hits = routing_hits("\n".join(rest))
    if hits:
        errors.append(f"prompt-toon SKILL.md carries routing text: {hits}")
    total = len(AGENTS_ROUTING_RE.findall(" ".join(skill_text.split())))
    if total != allowed_hits:
        errors.append("prompt-toon SKILL.md routing words span lines outside the allowance")
    return errors


def claude_skill_link_violations(root):
    """Every .claude/skills entry is a symlink to the same-named .agents
    skill, so the project skill Claude Code loads is the pinned one."""
    errors = []
    claude_root = root / ".claude" / "skills"
    agents_root = (root / ".agents" / "skills").resolve()
    if not (claude_root / "mythos-delegation").is_symlink():
        errors.append(".claude/skills/mythos-delegation is not a symlink")
    if claude_root.is_dir():
        for entry in sorted(claude_root.iterdir()):
            if not entry.is_symlink():
                errors.append(f"{entry.name}: .claude skill is not a symlink")
            elif entry.resolve() != agents_root / entry.name:
                errors.append(f"{entry.name}: .claude skill points at {entry.resolve()}")
    return errors


# The only project skills and the only .claude content (round 4, nit 2). A new
# skill, agent definition or root CLAUDE.md that carries routing guidance
# outside the pinned surfaces fails here instead of slipping in beside them.
PROJECT_SKILLS = {"mythos-delegation", "prompt-toon"}


def instruction_surface_violations(root):
    errors = []
    skills = {p.name for p in (root / ".agents" / "skills").iterdir()}
    if skills != PROJECT_SKILLS:
        errors.append(f".agents/skills holds {sorted(skills)}, want {sorted(PROJECT_SKILLS)}")
    claude = root / ".claude"
    if claude.exists():
        extra = {p.name for p in claude.iterdir()} - {"skills"}
        if extra:
            errors.append(f".claude holds {sorted(extra)} beyond skills/")
    for name in ("CLAUDE.md", "CLAUDE.local.md"):
        path = root / name
        if path.exists():
            hits = routing_hits(path.read_text(encoding="utf-8"))
            if hits:
                errors.append(f"{name} carries routing text: {hits}")
    pt_skill = root / ".agents" / "skills" / "prompt-toon" / "SKILL.md"
    if pt_skill.exists():
        errors.extend(prompt_toon_skill_violations(pt_skill.read_text(encoding="utf-8")))
    return errors


def reject_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    if dups:
        raise ValueError(f"duplicate JSON keys: {dups}")
    return dict(pairs)


def load_json_strict(text):
    """json.loads keeps the last duplicate key silently; a raw reader sees
    the first. Reject duplicates so the canonical digest covers the text."""
    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


def golden_digest_violations(skill_bytes, policy, agents_text):
    errors = []
    actual = hashlib.sha256(skill_bytes).hexdigest()
    if actual != SKILL_SHA256:
        errors.append(f"SKILL.md changed: SKILL_SHA256 is {SKILL_SHA256}, file is {actual}")
    actual = canonical_policy_sha256(policy)
    if actual != POLICY_CANONICAL_SHA256:
        errors.append(
            f"policy changed: POLICY_CANONICAL_SHA256 is {POLICY_CANONICAL_SHA256}, policy is {actual}"
        )
    errors.extend(agents_routing_outside_violations(agents_text))
    section = agents_delegation_section(agents_text)
    if section is not None:
        actual = hashlib.sha256(section.encode("utf-8")).hexdigest()
        if actual != AGENTS_DELEGATION_SHA256:
            errors.append(
                f"AGENTS.md Delegation section changed: AGENTS_DELEGATION_SHA256 is "
                f"{AGENTS_DELEGATION_SHA256}, section is {actual}"
            )
    return errors


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def structural_schema_violations(instance, schema, path="$"):
    """Minimal fail-closed subset validator for degraded envs without jsonschema.

    Covers only what the delegation and dispatch schemas use: type (object,
    array, string, integer, number, boolean, null), const, enum, required,
    additionalProperties=false, properties, items, minItems, maxItems,
    minLength, minimum, pattern, uniqueItems, allOf, anyOf, if/then, not,
    contains and local $ref into $defs. Unknown constructs are ignored rather
    than passed silently as valid, which is why jsonschema stays the
    preferred path.
    """
    defs = schema.get("$defs", {})

    def resolve(node):
        ref = node.get("$ref")
        if not ref:
            return node
        if not ref.startswith("#/$defs/"):
            return node
        merged = dict(defs[ref[len("#/$defs/") :]])
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        return merged

    def walk(value, node, where):
        node = resolve(node)
        errors = []
        expected = node.get("type")
        if expected == "object" and not isinstance(value, dict):
            return [f"{where}: expected object"]
        if expected == "array" and not isinstance(value, list):
            return [f"{where}: expected array"]
        if expected == "string" and not isinstance(value, str):
            return [f"{where}: expected string"]
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return [f"{where}: expected integer"]
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return [f"{where}: expected number"]
        if expected == "boolean" and not isinstance(value, bool):
            return [f"{where}: expected boolean"]
        if expected == "null" and value is not None:
            return [f"{where}: expected null"]
        if "const" in node and (value != node["const"] or type(value) is not type(node["const"])):
            errors.append(f"{where}: expected const {node['const']!r}")
        if "enum" in node and value not in node["enum"]:
            errors.append(f"{where}: {value!r} not in {node['enum']}")
        if isinstance(value, str) and len(value) < node.get("minLength", 0):
            errors.append(f"{where}: shorter than minLength")
        if (
            "minimum" in node
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value < node["minimum"]
        ):
            errors.append(f"{where}: below minimum")
        if isinstance(value, str) and "pattern" in node:
            if not re.search(node["pattern"], value):
                errors.append(f"{where}: {value!r} fails {node['pattern']}")
        if isinstance(value, dict):
            for key in node.get("required", []):
                if key not in value:
                    errors.append(f"{where}: missing required {key!r}")
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{where}: unexpected property {key!r}")
            for key, subschema in properties.items():
                if key in value:
                    errors.extend(walk(value[key], subschema, f"{where}.{key}"))
        if isinstance(value, list):
            if len(value) < node.get("minItems", 0):
                errors.append(f"{where}: fewer than minItems")
            if "maxItems" in node and len(value) > node["maxItems"]:
                errors.append(f"{where}: more than maxItems")
            if node.get("uniqueItems") and len(
                {json.dumps(item, sort_keys=True) for item in value}
            ) != len(value):
                errors.append(f"{where}: duplicate items")
            item_schema = node.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    errors.extend(walk(item, item_schema, f"{where}[{index}]"))
            if "contains" in node and not any(
                not walk(item, node["contains"], where) for item in value
            ):
                errors.append(f"{where}: no item matches contains")
        for subschema in node.get("allOf", []):
            errors.extend(walk(value, subschema, where))
        if "anyOf" in node and all(walk(value, sub, where) for sub in node["anyOf"]):
            errors.append(f"{where}: matches no anyOf branch")
        if "if" in node and not walk(value, node["if"], where):
            errors.extend(walk(value, node.get("then", {}), where))
        if "not" in node and not walk(value, node["not"], where):
            errors.append(f"{where}: matches a forbidden shape")
        return errors

    return walk(instance, schema, path)


class DelegationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()
        cls.schema = load_schema()
        cls.personas = {p["id"]: p for p in cls.policy["personas"]}
        cls.seats = {s["id"]: s for s in cls.policy["seats"]}
        cls.harness_seats = {s["id"]: s for s in cls.policy["harness_seats"]}

    def test_schema_version(self):
        self.assertEqual(self.policy["schema_version"], 5)

    def test_owner_linear_issue_shape(self):
        self.assertRegex(self.policy["metadata"]["owner_linear_issue"], r"^[A-Z]+-\d+$")

    def test_founding_docs_exist(self):
        for doc in self.policy["metadata"]["founding_docs"]:
            self.assertTrue((ROOT / doc).is_file(), f"missing founding doc: {doc}")

    def test_dhall_source_of_truth_exists(self):
        self.assertTrue(DHALL_TYPE_PATH.is_file())
        self.assertTrue(DHALL_SOURCE_PATH.is_file())
        type_text = DHALL_TYPE_PATH.read_text(encoding="utf-8")
        self.assertIn("seats : List Seat", type_text)
        self.assertIn("harness_seats : List HarnessSeat", type_text)

    def test_json_declares_dhall_regeneration_target(self):
        self.assertIn("dhall-to-json", self.policy["$comment"])

    def test_json_declares_schema_document(self):
        self.assertTrue(SCHEMA_PATH.is_file())
        self.assertIn("policy/delegation.schema.json", self.policy["$comment"])
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )

    def test_policy_validates_against_schema(self):
        try:
            import jsonschema
        except ImportError:
            violations = structural_schema_violations(self.policy, self.schema)
            self.assertEqual(violations, [], "structural schema violations")
            return
        jsonschema.validate(instance=self.policy, schema=self.schema)

    def test_structural_validator_rejects_a_known_bad_document(self):
        # Guards the degraded-mode path itself: it must fail closed, not
        # silently pass every document handed to it.
        broken = json.loads(json.dumps(self.policy))
        broken["seats"][0]["cost_tier"] = "free"
        broken["seats"][0]["unexpected_key"] = True
        del broken["attribution"]
        self.assertTrue(structural_schema_violations(broken, self.schema))

    def test_seat_ids_are_first_class(self):
        self.assertEqual(set(self.seats), SEAT_IDS)
        for seat in self.policy["seats"]:
            self.assertEqual(seat["id"], seat["model_class"], seat["id"])
            self.assertTrue(seat["notes"].strip(), seat["id"])

    def test_every_routed_model_class_has_a_seat(self):
        routed = {
            model_class
            for persona in self.policy["personas"]
            for model_class in persona["model_classes"]
            if model_class != "operator"
        }
        self.assertTrue(routed.issubset(set(self.seats)), routed - set(self.seats))

    def test_seat_default_personas_accept_that_seat(self):
        for seat in self.policy["seats"]:
            for persona_id in seat["default_personas"]:
                self.assertIn(persona_id, self.personas, seat["id"])
                self.assertIn(
                    seat["model_class"],
                    self.personas[persona_id]["model_classes"],
                    f"{seat['id']} defaults to {persona_id} which excludes it",
                )

    def test_seat_forbidden_personas_are_consistent(self):
        for seat in self.policy["seats"]:
            for persona_id in seat["forbidden_personas"]:
                self.assertIn(persona_id, self.personas, seat["id"])
                self.assertNotIn(
                    seat["model_class"],
                    self.personas[persona_id]["model_classes"],
                    f"{seat['id']} forbids {persona_id} but is listed in its model classes",
                )
                self.assertNotIn(persona_id, seat["default_personas"], seat["id"])

    def test_fable_seat_never_serves_adversarial(self):
        self.assertIn("adversarial", self.seats["fable"]["forbidden_personas"])
        self.assertEqual(self.seats["fable"]["cost_tier"], "scarce")

    def test_fable_and_haiku_forbid_critical(self):
        for seat_id in ("fable", "haiku"):
            self.assertIn("critical", self.seats[seat_id]["forbidden_personas"], seat_id)
        self.assertIn("adversarial", self.seats["sonnet"]["forbidden_personas"])
        self.assertNotIn("critical", self.seats["sonnet"]["default_personas"])

    def test_pinned_seat_model_ids_keep_the_5_5_family_and_fable_reads_the_account(self):
        # TIN-7760: the 5.5 pin is kept for every pinned seat, and the only
        # other pinned seat is kimi / k3.1-pro on the kimi-coding endpoint.
        # TIN-7125: the Fable id is not pinned. It is a config value read from
        # the cade@xoxd.ai account after login, so the seat carries no id, its
        # source is account-config and no Fable id sits anywhere in the policy.
        for seat_id, model_id in MODEL_IDS.items():
            self.assertEqual(self.seats[seat_id].get("model_id"), model_id, seat_id)
            self.assertEqual(self.seats[seat_id]["model_id_source"], "pinned", seat_id)
            self.assertEqual(self.seats[seat_id]["provider"], "anthropic", seat_id)
            self.assertNotIn("endpoint", self.seats[seat_id], seat_id)
        self.assertNotIn("model_id", self.seats["fable"], "fable stays provider-managed")
        self.assertEqual(self.seats["fable"]["model_id_source"], "account-config")
        self.assertEqual(fable_id_violations(self.policy), [])
        anthropic = next(p for p in self.policy["providers"] if p["id"] == "anthropic")
        self.assertEqual(set(anthropic["model_ids"]), set(MODEL_IDS.values()))
        self.assertEqual(self.seats["kimi"]["model_id"], "k3.1-pro")
        self.assertEqual(self.seats["kimi"]["provider"], "kimi")
        self.assertEqual(self.seats["kimi"]["endpoint"], "kimi-coding")
        self.assertEqual(provider_allow_list_violations(self.policy), [])

    def test_provider_allow_list_fails_closed(self):
        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        seat = lambda doc, sid: next(s for s in doc["seats"] if s["id"] == sid)
        provider = lambda doc, pid: next(p for p in doc["providers"] if p["id"] == pid)
        endpoint = lambda doc, pid, eid: next(
            e for e in provider(doc, pid)["endpoints"] if e["id"] == eid
        )
        cases = {
            "non-allow-listed provider": lambda d: seat(d, "kimi").update(provider="openai"),
            "provider entry added": lambda d: d["providers"].append(
                {"id": "openai", "model_ids": ["gpt-5"], "access": "x", "endpoints": [], "notes": "x"}
            ),
            "kimi off its model": lambda d: seat(d, "kimi").update(model_id="kimi-k3-pro"),
            "kimi on the platform id": lambda d: seat(d, "kimi").update(model_id="kimi-k3.1-pro"),
            "kimi on the platform endpoint": lambda d: seat(d, "kimi").update(
                endpoint="moonshot-platform", model_id="kimi-k3.1-pro"
            ),
            "kimi names no endpoint": lambda d: seat(d, "kimi").pop("endpoint"),
            "platform admitted": lambda d: endpoint(d, "kimi", "moonshot-platform").update(
                admitted=True, status="unmeasured"
            ),
            "platform id on the allow-list": lambda d: provider(d, "kimi")["model_ids"].append(
                "kimi-k3.1-pro"
            ),
            "platform endpoint dropped": lambda d: provider(d, "kimi")["endpoints"].pop(),
            "kimi-coding marked measured": lambda d: endpoint(d, "kimi", "kimi-coding").update(
                status="in-service"
            ),
            "kimi-coding refused": lambda d: endpoint(d, "kimi", "kimi-coding").update(
                admitted=False, status="forbidden"
            ),
            "opus names an endpoint": lambda d: seat(d, "opus").update(endpoint="kimi-coding"),
            "kimi runs claude": lambda d: seat(d, "kimi").update(model_id="claude-opus-5-5"),
            "opus on kimi": lambda d: seat(d, "opus").update(provider="kimi"),
            "sonnet leaves 5.5": lambda d: seat(d, "sonnet").update(model_id="claude-sonnet-4-5"),
            "allow-list widened": lambda d: provider(d, "kimi")["model_ids"].append("kimi-k3-pro"),
            "anthropic widened": lambda d: provider(d, "anthropic")["model_ids"].append(
                "claude-opus-4-1"
            ),
            "fable pinned": lambda d: seat(d, "fable").update(model_id="claude-opus-5-5"),
            "provider dropped": lambda d: seat(d, "haiku").pop("provider"),
            "fable id hard-coded on the seat": lambda d: seat(d, "fable").update(
                model_id="claude-fable-5-2"
            ),
            "fable id on the allow-list": lambda d: provider(d, "anthropic")["model_ids"].append(
                "claude-fable-5-2"
            ),
        }
        for name, fn in cases.items():
            self.assertTrue(
                provider_allow_list_violations(mutate(fn)) + fable_id_violations(mutate(fn)), name
            )
        schema_cases = (
            "non-allow-listed provider",
            "kimi off its model",
            "kimi on the platform id",
            "kimi on the platform endpoint",
            "kimi names no endpoint",
            "platform admitted",
            "platform id on the allow-list",
            "platform endpoint dropped",
            "kimi-coding refused",
            "opus names an endpoint",
            "kimi runs claude",
            "opus on kimi",
            "allow-list widened",
            "anthropic widened",
            "fable pinned",
            "provider dropped",
            "fable id hard-coded on the seat",
            "fable id on the allow-list",
        )
        for name in schema_cases:
            self.assertTrue(
                structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}"
            )

    def test_kimi_seat_scope(self):
        self.assertEqual(kimi_violations(self.policy), [])
        kimi = self.seats["kimi"]
        self.assertEqual(set(kimi["default_personas"]), KIMI_ALLOWED_PERSONAS)
        self.assertTrue(KIMI_FORBIDDEN_PERSONAS <= set(kimi["forbidden_personas"]))
        self.assertEqual(self.harness_seats["pi-k3"]["harness"], "pi", "pi-k3 stays beside kimi")

    def test_no_mythos_lane_resolves_to_kimi(self):
        # Finding: kimi on the research and drafting personas let every
        # mythos.research.*, mythos.draft and mythos.review.first-pass lane
        # resolve to Kimi. Resolve every mythos lane through its persona.
        self.assertEqual(mythos_kimi_violations(self.policy), [])
        for lane in self.policy["lanes"]:
            persona = self.personas.get(lane["persona"])
            if persona is not None:
                self.assertNotIn("kimi", persona["model_classes"], lane["route"])
        for route, persona in (("cade.research", "cade-research"), ("cade.draft", "cade-drafting")):
            lane = next(l for l in self.policy["cade"]["lanes"] if l["route"] == route)
            self.assertEqual(lane["persona"], persona, route)

        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        persona = lambda doc, pid: next(p for p in doc["personas"] if p["id"] == pid)
        seat = lambda doc, sid: next(s for s in doc["seats"] if s["id"] == sid)
        lane = lambda doc, route: next(l for l in doc["lanes"] if l["route"] == route)
        cade_lane = lambda doc, route: next(l for l in doc["cade"]["lanes"] if l["route"] == route)
        cases = {
            "kimi back on research": lambda d: persona(d, "research")["model_classes"].append("kimi"),
            "kimi back on drafting": lambda d: persona(d, "drafting")["model_classes"].append("kimi"),
            "kimi defaults to research": lambda d: seat(d, "kimi")["default_personas"].append("research"),
            "mythos.draft on cade-drafting": lambda d: lane(d, "mythos.draft").update(persona="cade-drafting"),
            "first pass on cade-drafting": lambda d: lane(d, "mythos.review.first-pass").update(
                persona="cade-drafting"
            ),
            "mythos.research.web on cade-research": lambda d: lane(d, "mythos.research.web").update(
                persona="cade-research"
            ),
            "mythos.research.linear on business": lambda d: lane(d, "mythos.research.linear").update(
                persona="business"
            ),
            "cade.research back on research": lambda d: cade_lane(d, "cade.research").update(persona="research"),
            "cade.draft back on drafting": lambda d: cade_lane(d, "cade.draft").update(persona="drafting"),
        }
        for name, fn in cases.items():
            broken = mutate(fn)
            found = kimi_violations(broken) + mythos_kimi_violations(broken) + cade_violations(broken)
            self.assertTrue(found, name)

    def test_child_seats_resolve_within_parent_custody(self):
        # Finding: lane custody and parent-home custody both bind a child.
        # Effective seats are the lane's seats within the parent's class; an
        # empty set means the child waits.
        self.assertEqual(child_resolution_violations(self.policy), [])
        children = self.policy["cade"]["children"]
        self.assertEqual(children["seat_resolution"], CHILD_SEAT_RESOLUTION)
        self.assertEqual(children["on_no_seat"], CHILD_ON_NO_SEAT)
        for route in ("cade.research", "cade.draft", "cade.marketing", "cade.review"):
            self.assertEqual(resolve_child_seats(self.policy, route, "confidential"), ["opus"], route)
        for route in CADE_ROUTES:
            self.assertEqual(resolve_child_seats(self.policy, route, "health"), [], route)
            self.assertEqual(resolve_child_seats(self.policy, route, "unknown-home"), [], route)
            self.assertNotIn("kimi", resolve_child_seats(self.policy, route, "confidential"), route)
        self.assertEqual(resolve_child_seats(self.policy, "cade.research", "public"), ["kimi", "opus"])
        self.assertEqual(resolve_child_seats(self.policy, "cade.client", "public"), ["opus"])
        self.assertEqual(resolve_child_seats(self.policy, "cade.critical", "ops"), ["opus"])
        self.assertEqual(resolve_child_seats(self.policy, "cade.unknown", "public"), [])

        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        custody = lambda doc, cid: next(c for c in doc["cade"]["custody_classes"] if c["id"] == cid)
        children_of = lambda doc: doc["cade"]["children"]
        cases = {
            "kimi admitted to confidential": lambda d: custody(d, "confidential")["seats"].append("kimi"),
            "health hosted on opus": lambda d: custody(d, "health")["seats"].append("opus"),
            "health hosted on kimi": lambda d: custody(d, "health")["seats"].append("kimi"),
            "resolution dropped": lambda d: children_of(d).pop("seat_resolution"),
            "lane custody wins": lambda d: children_of(d).update(seat_resolution="lane-custody-only"),
            "empty set falls back": lambda d: children_of(d).update(on_no_seat="fallback"),
        }
        for name, fn in cases.items():
            self.assertTrue(child_resolution_violations(mutate(fn)) + cade_violations(mutate(fn)), name)
        for name in cases:
            self.assertTrue(structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}")

    def test_kimi_never_gates_a_merge(self):
        # Fail closed on every path that would let Kimi gate a merge, own the
        # refutation-of-record or reach a critical lane.
        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        persona = lambda doc, pid: next(p for p in doc["personas"] if p["id"] == pid)
        seat = lambda doc, sid: next(s for s in doc["seats"] if s["id"] == sid)
        cade_lane = lambda doc, route: next(l for l in doc["cade"]["lanes"] if l["route"] == route)
        cases = {
            "kimi on the critical persona": lambda d: persona(d, "critical")["model_classes"].append("kimi"),
            "kimi on the adversarial persona": lambda d: persona(d, "adversarial")["model_classes"].append("kimi"),
            "kimi defaults to critical": lambda d: seat(d, "kimi")["default_personas"].append("critical"),
            "kimi stops forbidding critical": lambda d: seat(d, "kimi")["forbidden_personas"].remove("critical"),
            "kimi as budget fallback": lambda d: d["budget_fallbacks"][0].update(to_seat="kimi"),
            "kimi alternate on cade.critical": lambda d: cade_lane(d, "cade.critical")["alternate_seats"].append("kimi"),
            "kimi on mechanical": lambda d: persona(d, "mechanical")["model_classes"].append("kimi"),
            "review may gate": lambda d: persona(d, "evidentiary-review")["forbidden_tasks"].remove(
                "merge-gating-review"
            ),
        }
        for name, fn in cases.items():
            broken = mutate(fn)
            found = (
                kimi_violations(broken)
                + critical_lane_violations(broken)
                + fallback_violations(broken)
                + cade_violations(broken)
            )
            self.assertTrue(found, name)
        for name in (
            "kimi defaults to critical",
            "kimi stops forbidding critical",
            "kimi as budget fallback",
            "kimi alternate on cade.critical",
        ):
            self.assertTrue(structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}")

    def test_fable_never_takes_adversarial_work(self):
        # J81 and the never-fable-for-adversarial rule stand unchanged.
        broken = json.loads(json.dumps(self.policy))
        next(p for p in broken["personas"] if p["id"] == "adversarial")["model_classes"].append("fable")
        self.assertTrue(critical_lane_violations(broken))
        broken = json.loads(json.dumps(self.policy))
        next(l for l in broken["lanes"] if l["route"] == "mythos.adversarial")["persona"] = "fable"
        self.assertTrue(critical_lane_violations(broken))
        broken = json.loads(json.dumps(self.policy))
        lane = next(l for l in broken["cade"]["lanes"] if l["route"] == "cade.review")
        lane["alternate_seats"].append("fable")
        self.assertTrue(cade_violations(broken))
        self.assertTrue(structural_schema_violations(broken, self.schema))

    def test_dhall_and_json_agree_on_model_ids(self):
        dhall_ids = dhall_seat_model_ids(DHALL_SOURCE_PATH.read_text(encoding="utf-8"))
        json_ids = {s["id"]: s.get("model_id") for s in self.policy["seats"]}
        self.assertEqual(set(dhall_ids), SEAT_IDS, "every Dhall seat declares model_id")
        self.assertEqual(dhall_ids, json_ids)
        self.assertEqual({k: v for k, v in dhall_ids.items() if v}, SEAT_MODEL_IDS)

    def test_dhall_model_id_parser_detects_drift(self):
        drifted = DHALL_SOURCE_PATH.read_text(encoding="utf-8").replace(
            '"claude-opus-5-5"', '"claude-opus-4-1"'
        )
        self.assertNotEqual(dhall_seat_model_ids(drifted)["opus"], MODEL_IDS["opus"])

    def test_merge_gating_and_adversarial_lanes_resolve_to_opus(self):
        self.assertEqual(critical_lane_violations(self.policy), [])
        routes = {lane["route"] for lane in self.policy["lanes"]}
        self.assertTrue(set(CRITICAL_ROUTES).issubset(routes))
        self.assertEqual(routes, set(CRITICAL_ROUTES) | NON_CRITICAL_ROUTES)

    def test_critical_lane_check_closes_renamed_and_missing_lanes(self):
        # Enforcement review on PR #49: a lane outside the old prefixes, or a
        # deleted critical lane, used to pass.
        for route, persona in (
            ("mythos.deploy.host", "drafting"),
            ("mythos.release.cut", "research"),
            ("mythos.ship", "drafting"),
        ):
            broken = json.loads(json.dumps(self.policy))
            broken["lanes"].append({"route": route, "persona": persona, "purpose": "x"})
            self.assertTrue(critical_lane_violations(broken), route)
        for route in ("mythos.gate.security", "mythos.act.host"):
            broken = json.loads(json.dumps(self.policy))
            broken["lanes"] = [l for l in broken["lanes"] if l["route"] != route]
            self.assertTrue(critical_lane_violations(broken), route)
            broken = json.loads(json.dumps(self.policy))
            next(l for l in broken["lanes"] if l["route"] == route)["persona"] = "adversarial"
            self.assertTrue(critical_lane_violations(broken), route)

    def test_critical_lane_check_fails_closed(self):
        for persona, classes in (("drafting", None), ("critical", ["sonnet", "opus"])):
            broken = json.loads(json.dumps(self.policy))
            gate = next(l for l in broken["lanes"] if l["route"] == "mythos.gate.review")
            if classes is None:
                gate["persona"] = persona
            else:
                next(p for p in broken["personas"] if p["id"] == persona)[
                    "model_classes"
                ] = classes
            self.assertTrue(critical_lane_violations(broken), persona)
        broken = json.loads(json.dumps(self.policy))
        next(s for s in broken["seats"] if s["id"] == "sonnet")["default_personas"].append(
            "critical"
        )
        self.assertTrue(critical_lane_violations(broken))

    def test_drafting_never_gates_a_merge(self):
        self.assertTrue(
            {"merge", "merge-gating-review"}.issubset(self.personas["drafting"]["forbidden_tasks"])
        )
        self.assertNotIn("drafting", self.seats["haiku"]["default_personas"])

    def test_advisory_review_lanes_never_gate_a_merge(self):
        lanes = {lane["route"]: lane for lane in self.policy["lanes"]}
        self.assertIn("never gates a merge", lanes["mythos.review"]["purpose"])
        self.assertIn("mythos.gate.review", lanes["mythos.review"]["purpose"])
        self.assertIn("mythos.gate.review", lanes["mythos.delegate.review"]["purpose"])
        self.assertIn("never falls back", lanes["mythos.gate.review"]["purpose"])
        self.assertIn("never falls back", lanes["mythos.act.host"]["purpose"].lower())

    def test_opus_budget_fallback_present(self):
        self.assertEqual(fallback_violations(self.policy), [])

    def test_fallback_check_fails_closed(self):
        broken = json.loads(json.dumps(self.policy))
        broken["budget_fallbacks"] = []
        self.assertTrue(fallback_violations(broken))
        broken = json.loads(json.dumps(self.policy))
        broken["enforcement"] = [
            r for r in broken["enforcement"] if r["id"] != "opus-budget-fallback"
        ]
        self.assertTrue(fallback_violations(broken))
        broken = json.loads(json.dumps(self.policy))
        broken["budget_fallbacks"][0]["obligations"].remove("merge-waits-for-opus-review")
        broken["budget_fallbacks"][0]["personas"].append("adversarial")
        self.assertGreaterEqual(len(fallback_violations(broken)), 2)

    def test_fallback_never_reaches_the_gate_or_a_host_act(self):
        # Policy-consistency review on PR #49: a persona-wide fallback let
        # Sonnet run the merge gate and mutate shared infra.
        for route in sorted(NEVER_FALLBACK_LANES):
            broken = json.loads(json.dumps(self.policy))
            broken["budget_fallbacks"][0]["lanes"].append(route)
            self.assertTrue(fallback_violations(broken), route)
        broken = json.loads(json.dumps(self.policy))
        del broken["budget_fallbacks"][0]["lanes"]
        self.assertTrue(fallback_violations(broken))

    def test_fallback_checks_every_fallback(self):
        # Enforcement review on PR #49, findings 3 and 4.
        broken = json.loads(json.dumps(self.policy))
        extra = json.loads(json.dumps(broken["budget_fallbacks"][0]))
        extra["id"] = "opus-weekend"
        extra["obligations"] = ["none"]
        broken["budget_fallbacks"].append(extra)
        self.assertTrue(fallback_violations(broken))
        for obligation in sorted(FALLBACK_OBLIGATIONS):
            broken = json.loads(json.dumps(self.policy))
            broken["budget_fallbacks"][0]["obligations"].remove(obligation)
            self.assertTrue(fallback_violations(broken), obligation)
        for personas in (["drafting"], ["critical", "nonexistent"]):
            broken = json.loads(json.dumps(self.policy))
            broken["budget_fallbacks"][0]["personas"] = personas
            self.assertTrue(fallback_violations(broken), personas)

    def test_rule_sentences_are_pinned(self):
        self.assertEqual(rule_sentence_violations(self.policy), [])
        broken = json.loads(json.dumps(self.policy))
        rule = next(r for r in broken["enforcement"] if r["id"] == "opus-budget-fallback")
        rule["rule"] = (
            "Sonnet may merge critical lanes; an Opus review and a receipt are optional."
        )
        self.assertTrue(rule_sentence_violations(broken))
        for rule_id, weaker in (
            ("kimi-never-gates", "The kimi seat may gate a merge when Opus is busy."),
            ("cade-custody-follows-parent", "Confidential work may run on Kimi."),
            ("cross-provider-review-advisory", "Cross-provider review must pass before a merge."),
            ("cade-children-auto-level", "Cade's children are read-only."),
            ("provider-allow-list", "Any provider may serve a seat."),
            ("no-adversarial-on-fable", "Fable may take adversarial lanes."),
        ):
            broken = json.loads(json.dumps(self.policy))
            next(r for r in broken["enforcement"] if r["id"] == rule_id)["rule"] = weaker
            self.assertTrue(rule_sentence_violations(broken), rule_id)

    def test_schema_rejects_known_bad_seats_and_fallbacks(self):
        # Policy-consistency review on PR #49, finding 6. Runs through the
        # degraded validator so CI enforces it without jsonschema.
        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return structural_schema_violations(broken, self.schema)

        seat = lambda doc, sid: next(s for s in doc["seats"] if s["id"] == sid)
        fb = lambda doc: doc["budget_fallbacks"][0]
        cases = {
            "fable to_seat": lambda d: fb(d).update(to_seat="fable"),
            "haiku to_seat": lambda d: fb(d).update(to_seat="haiku"),
            "adversarial persona": lambda d: fb(d)["personas"].append("adversarial"),
            "gate lane": lambda d: fb(d)["lanes"].append("mythos.gate.review"),
            "host lane": lambda d: fb(d)["lanes"].append("mythos.act.host"),
            "no obligations": lambda d: fb(d).update(obligations=["none"]),
            "opus runs haiku": lambda d: seat(d, "opus").update(model_id="claude-haiku-5-5"),
            "sonnet unpinned": lambda d: seat(d, "sonnet").pop("model_id"),
            "fable pinned": lambda d: seat(d, "fable").update(model_id="claude-opus-5-5"),
        }
        self.assertEqual(structural_schema_violations(self.policy, self.schema), [])
        for name, fn in cases.items():
            self.assertTrue(mutate(fn), name)

    def test_harness_seat_ids_are_first_class(self):
        self.assertEqual(set(self.harness_seats), HARNESS_SEAT_IDS)
        for seat in self.policy["harness_seats"]:
            self.assertTrue(seat["purpose"].strip(), seat["id"])
            self.assertTrue(seat["model_lane"].strip(), seat["id"])
            self.assertTrue(seat["dispatch_surface"].strip(), seat["id"])

    def test_harness_seat_postures(self):
        # The review seats are evidentiary-only. Cade's job seat is a gated
        # worker: work product whose external acts wait for a GO (TIN-7125).
        for seat in self.policy["harness_seats"]:
            self.assertEqual(seat["posture"], HARNESS_POSTURES[seat["id"]], seat["id"])

    def test_harness_seats_never_own_the_word_or_refutation_legs(self):
        for seat in self.policy["harness_seats"]:
            forbidden = set(seat["forbidden_tasks"])
            self.assertTrue(
                HARNESS_FORBIDDEN.issubset(forbidden),
                f"{seat['id']} must forbid {sorted(HARNESS_FORBIDDEN - forbidden)}",
            )

    def test_harness_delegable_and_forbidden_are_disjoint(self):
        for seat in self.policy["harness_seats"]:
            delegable = set(seat["delegable_tasks"])
            forbidden = set(seat["forbidden_tasks"])
            self.assertTrue(delegable, seat["id"])
            self.assertEqual(
                delegable & forbidden,
                set(),
                f"{seat['id']} both delegates and forbids "
                f"{sorted(delegable & forbidden)}",
            )

    def test_harness_dispatch_is_bounded_launchers_not_raw_binaries(self):
        # A launcher surface names the bounded wrappers. A transport surface
        # (an swb recipient) names the fixed launcher behind it in launcher.
        for seat in self.policy["harness_seats"]:
            surface = seat["dispatch_surface"]
            if surface.startswith("swb:"):
                self.assertIn("launcher", seat.get("launcher", "").lower(), seat["id"])
                self.assertIn("never a raw binary", seat["launcher"], seat["id"])
                continue
            self.assertNotIn("launcher", seat, seat["id"])
            self.assertIn("launcher", surface.lower(), seat["id"])
            self.assertIn(f"{seat['harness']}-*", surface, seat["id"])
        self.assertEqual(self.harness_seats[DISPATCH_HARNESS_SEAT]["dispatch_surface"], DISPATCH_SURFACE)

    def test_kimi_reviewer_rides_the_pi_harness(self):
        # The native kimi CLI has no dispatch envelope, so the Kimi reviewer is
        # a pi-harness seat. If that ever stops being true the seat record has
        # to change, not the doctrine quietly.
        k3 = self.harness_seats["pi-k3"]
        self.assertEqual(k3["harness"], "pi")
        self.assertIn("kimi", k3["model_lane"].lower())
        self.assertEqual(
            set(k3["delegable_tasks"]),
            set(self.harness_seats["pi"]["delegable_tasks"]),
            "pi-k3 must ride the identical technical-review envelope as pi",
        )

    def test_harness_seats_are_not_model_classes(self):
        # A harness seat is a dispatch identity. Leaking one into a persona's
        # model_classes or the model-class seat registry would let it inherit a
        # routing authority it must never have.
        self.assertEqual(HARNESS_SEAT_IDS & set(self.seats), set())
        for persona in self.policy["personas"]:
            self.assertEqual(
                HARNESS_SEAT_IDS & set(persona["model_classes"]),
                set(),
                persona["id"],
            )

    def test_every_kimi_seat_gets_the_ops_class(self):
        # SPR-182 (TIN-7125 38d94764): all Kimi seats get the ops class,
        # including the pi-k3 harness seat.
        self.assertEqual(kimi_seats(self.policy), {"kimi", "pi-k3"})
        self.assertEqual(kimi_custody_violations(self.policy), [])

        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        klass = lambda doc, cid: next(c for c in doc["cade"]["custody_classes"] if c["id"] == cid)
        cases = {
            "pi-k3 dropped from ops": lambda d: klass(d, "ops")["harness_seats"].remove("pi-k3"),
            "kimi dropped from ops": lambda d: klass(d, "ops")["seats"].remove("kimi"),
            "pi-k3 in confidential": lambda d: klass(d, "confidential")["harness_seats"].append("pi-k3"),
            "pi-k3 in health": lambda d: klass(d, "health")["harness_seats"].append("pi-k3"),
            "harness seats dropped": lambda d: klass(d, "ops").pop("harness_seats"),
        }
        for name, fn in cases.items():
            self.assertTrue(cade_violations(mutate(fn)), name)
            self.assertTrue(structural_schema_violations(mutate(fn), self.schema), f"schema: {name}")
        new_kimi_harness = lambda d: d["harness_seats"].append(
            dict(next(h for h in d["harness_seats"] if h["id"] == "pi"), id="pi-k4", model_lane="kimi-coding/k4")
        )
        self.assertTrue(kimi_custody_violations(mutate(new_kimi_harness)), "unclassed Kimi harness seat")

    def test_cade_lanes_custody_and_children(self):
        self.assertEqual(cade_violations(self.policy), [])
        lanes = {l["route"]: l for l in self.policy["cade"]["lanes"]}
        self.assertEqual(set(lanes), CADE_ROUTES)
        for route in ("cade.business", "cade.marketing", "cade.ceo-ops"):
            lane = lanes[route]
            self.assertIn("kimi", {lane["default_seat"], *lane["alternate_seats"]}, route)
            self.assertIn(lane["custody"], {"public", "ops"}, route)
        self.assertEqual(lanes["cade.client"]["custody"], "confidential")
        self.assertEqual(lanes["cade.critical"]["persona"], "critical")

    def test_cade_check_fails_closed(self):
        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        lane = lambda doc, route: next(l for l in doc["cade"]["lanes"] if l["route"] == route)
        klass = lambda doc, cid: next(c for c in doc["cade"]["custody_classes"] if c["id"] == cid)
        children = lambda doc: doc["cade"]["children"]
        cases = {
            "kimi alternate in confidential lane": lambda d: lane(d, "cade.client")["alternate_seats"].append("kimi"),
            "kimi default in confidential lane": lambda d: lane(d, "cade.client").update(default_seat="kimi"),
            "kimi lane moved to confidential": lambda d: lane(d, "cade.research").update(custody="confidential"),
            "confidential class admits kimi": lambda d: klass(d, "confidential")["seats"].append("kimi"),
            "health class hosted": lambda d: klass(d, "health")["seats"].append("opus"),
            "lane on health custody": lambda d: lane(d, "cade.ceo-ops").update(custody="health"),
            "seat off persona": lambda d: lane(d, "cade.critical")["alternate_seats"].append("kimi"),
            "sonnet in cade": lambda d: lane(d, "cade.draft")["alternate_seats"].append("sonnet"),
            "unknown route": lambda d: d["cade"]["lanes"].append(dict(lane(d, "cade.draft"), route="cade.send")),
            "critical word off critical": lambda d: d["cade"]["lanes"].append(
                dict(lane(d, "cade.draft"), route="cade.deploy")
            ),
            "children read-only": lambda d: children(d).update(level="read-only"),
            "child custody detached": lambda d: children(d).update(custody="any"),
            "child provider fallback": lambda d: children(d).update(provider_fallback="kimi"),
            "child binding dropped": lambda d: children(d)["bindings"].remove("client-exports-never-kimi"),
            "review made blocking": lambda d: d["cade"].update(
                cross_provider_review="Cross-provider review blocks the merge until it passes."
            ),
            "lane dropped": lambda d: d["cade"]["lanes"].remove(lane(d, "cade.client")),
        }
        for name, fn in cases.items():
            self.assertTrue(cade_violations(mutate(fn)), name)
        for name in (
            "kimi alternate in confidential lane",
            "kimi default in confidential lane",
            "kimi lane moved to confidential",
            "confidential class admits kimi",
            "health class hosted",
            "lane on health custody",
            "sonnet in cade",
            "children read-only",
            "child custody detached",
            "child provider fallback",
            "child binding dropped",
        ):
            self.assertTrue(structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}")

    def test_cade_skill_table_fails_closed(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertEqual(cade_skill_table_violations(self.policy, skill_text), [])
        client = "| `cade.client` | business | confidential | opus | none |"
        research = "| `cade.research` | cade-research | public | kimi | opus |"
        cases = {
            "client on kimi": (client, client.replace("| none |", "| kimi |")),
            "research back on the mythos persona": (
                research,
                research.replace("| cade-research |", "| research |"),
            ),
            "row dropped": (client + "\n", ""),
            "custody drift": (research, research.replace("public", "ops")),
            "second table": ("### Custody follows the parent", "| `cade.client` | business | public | kimi | none |\n\n### Custody follows the parent"),
            "duplicate row": (research, research + "\n" + research),
        }
        for name, (old, new) in cases.items():
            self.assertIn(old, skill_text, name)
            broken = skill_text.replace(old, new, 1)
            self.assertTrue(skill_text_violations(self.policy, broken), name)

    def test_every_persona_has_purpose_and_doctrine(self):
        for persona in self.policy["personas"]:
            self.assertTrue(persona["purpose"].strip(), persona["id"])
            self.assertTrue(persona["doctrine"].strip(), persona["id"])

    def test_every_lane_has_purpose(self):
        for lane in self.policy["lanes"]:
            self.assertTrue(lane["purpose"].strip(), lane["route"])

    def test_lane_personas_resolve(self):
        # A lane routes to a model-class persona or, for mythos.delegate.*, to
        # a harness seat. Nothing else resolves.
        for lane in self.policy["lanes"]:
            self.assertIn(
                lane["persona"],
                set(self.personas) | HARNESS_SEAT_IDS,
                lane["route"],
            )

    def test_harness_lanes_and_harness_personas_imply_each_other(self):
        # The mythos.delegate.* namespace and harness-seat routing are the same
        # fact stated twice; drift either way is a routing lie.
        for lane in self.policy["lanes"]:
            delegate_route = lane["route"].startswith("mythos.delegate.")
            harness_persona = lane["persona"] in self.harness_seats
            self.assertEqual(delegate_route, harness_persona, lane["route"])

    def test_harness_lanes_carry_only_evidentiary_work(self):
        for lane in self.policy["lanes"]:
            if lane["persona"] not in self.harness_seats:
                continue
            seat = self.harness_seats[lane["persona"]]
            self.assertEqual(seat["posture"], HARNESS_POSTURE, lane["route"])
            self.assertTrue(lane["purpose"].strip(), lane["route"])

    def test_fable_forbids_adversarial_work(self):
        fable = self.personas["fable"]
        self.assertTrue(FABLE_FORBIDDEN.issubset(set(fable["forbidden_tasks"])))

    def test_adversarial_persona_excludes_fable(self):
        adversarial = self.personas["adversarial"]
        self.assertNotIn("fable", adversarial["model_classes"])

    def test_adversarial_lanes_never_route_to_fable(self):
        for lane in self.policy["lanes"]:
            if lane["persona"] in self.harness_seats:
                # A harness seat has no model_classes to leak fable through,
                # and is barred from the refutation-of-record leg outright.
                self.assertIn(
                    "adversarial-refutation-of-record",
                    self.harness_seats[lane["persona"]]["forbidden_tasks"],
                    lane["route"],
                )
                continue
            persona = self.personas[lane["persona"]]
            if lane["persona"] == "adversarial" or "adversarial" in lane["route"]:
                self.assertNotIn("fable", persona["model_classes"], lane["route"])

    def test_error_rules_present(self):
        errors = {r["id"] for r in self.policy["enforcement"] if r["severity"] == "error"}
        self.assertTrue(
            {
                "no-adversarial-on-fable",
                "fable-forbidden-tasks",
                "purpose-required",
                "seat-registry-complete",
                "harness-delegation-envelope",
                "harness-dispatch-bounded-only",
                "critical-lanes-default-opus",
                "opus-budget-fallback",
                "provider-allow-list",
                "kimi-never-gates",
                "cade-custody-follows-parent",
                "cade-children-auto-level",
                "cross-provider-review-advisory",
                "fable-leads-others-work",
                "cade-dispatch-root-resolution",
                "cade-dispatch-carve-out-inheritance",
                "cade-dispatch-custody-narrows",
                "cade-broker-routes-only",
                "fable-id-from-account",
                "cade-never-reviews-own-pr",
                "cade-dispatch-accounting-not-caps",
            }.issubset(errors)
        )

    def test_warn_rules_present(self):
        warns = {r["id"] for r in self.policy["enforcement"] if r["severity"] == "warn"}
        self.assertTrue(
            {"cheap-lane-escalation", "serial-workflow-on-fan-out"}.issubset(warns)
        )

    def test_fan_out_rule_demands_parallel_lanes_and_names_the_checkpoint(self):
        # The rule exists to stop a serial mega-workflow from swallowing the
        # per-lane operator interview checkpoint, so it has to say both halves.
        rule = next(
            r for r in self.policy["enforcement"] if r["id"] == "serial-workflow-on-fan-out"
        )
        text = rule["rule"].lower()
        self.assertIn("parallel", text)
        self.assertIn("never one serial workflow", text)
        self.assertIn("interview checkpoint", text)

    def test_harness_envelope_rule_names_both_withheld_legs(self):
        rule = next(
            r for r in self.policy["enforcement"] if r["id"] == "harness-delegation-envelope"
        )
        text = rule["rule"].lower()
        self.assertIn("delegable_tasks", text)
        self.assertIn("word", text)
        self.assertIn("adversarial-refutation-of-record", text)

    def test_rule_severities_are_known(self):
        for rule in self.policy["enforcement"]:
            self.assertIn(rule["severity"], {"error", "warn"}, rule["id"])

    def test_attribution_values_nonempty(self):
        self.assertTrue(self.policy["attribution"]["values"])
        for value in self.policy["attribution"]["values"]:
            self.assertTrue(value.strip())

    def test_scarce_resources_name_both_realities(self):
        kinds = {r["kind"] for r in self.policy["scarce_resources"]}
        self.assertEqual(kinds, {"human", "model"})

    def test_skill_references_policy_artifact(self):
        self.assertTrue(SKILL_PATH.is_file())
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("policy/delegation.json", skill_text)

    def test_skill_documents_harness_delegation_and_fan_out(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("harness_seats", skill_text)
        self.assertIn("## Harness delegation", skill_text)
        self.assertIn("## Fan-out doctrine", skill_text)
        for seat_id in HARNESS_SEAT_IDS:
            self.assertIn(seat_id, skill_text, seat_id)
        # The cross-repo warning is load-bearing; a rewrite must not drop it.
        self.assertIn("Do not confuse repos", skill_text)

    def test_skill_promotes_opus_and_names_the_5_5_family(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        for model_id in MODEL_IDS.values():
            self.assertIn(model_id, skill_text, model_id)
        for needle in ("J81", "budget_fallbacks", "merge-gating review", "receipt"):
            self.assertIn(needle, skill_text, needle)
        # Pin the rule sentences, not just needles: a skill edit that lets
        # Sonnet gate a merge or cover adversarial work must fail.
        flat = " ".join(skill_text.split())
        for sentence in SKILL_SENTENCES:
            self.assertIn(sentence, flat, sentence)
        for route in FALLBACK_LANES | NEVER_FALLBACK_LANES - {"mythos.adversarial"}:
            self.assertIn(route, skill_text, route)
        self.assertNotIn("may run on Sonnet", skill_text)
        self.assertNotIn("\u2014", skill_text, "house style: no em dash")
        # Per-agent model choice follows the policy; only a global pin needs a
        # host justification. The old blanket line contradicted J81.
        self.assertNotIn("Leave harness default model selection provider-managed", skill_text)

    def test_skill_table_ids_and_grants_agree_with_policy(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertEqual(skill_text_violations(self.policy, skill_text), [])

    def test_skill_text_check_fails_closed(self):
        # Enforcement re-review on PR #49: these skill edits used to pass.
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        gate = "| `mythos.gate.review` | critical | `claude-opus-5-5` | Wait for Opus or the operator |"
        host = "| `mythos.act.host` | critical | `claude-opus-5-5` | Wait for Opus or the operator |"
        merge = "| `mythos.implement.merge` | critical | `claude-opus-5-5` | Sonnet; merge waits for Opus |"
        draft = "| `mythos.draft`, `mythos.review.first-pass` | drafting | `claude-sonnet-5-5` | No change |"
        for row in (gate, host, merge, draft):
            self.assertIn(row, skill_text, row)
        cases = {
            "A10 gate on sonnet": (gate, "| `mythos.gate.review` | critical | `claude-sonnet-5-5` | Sonnet |"),
            "D10 host falls back": (host, host.replace(TABLE_WAIT_CELL, "Sonnet with a receipt")),
            "host falls back plain": (host, host.replace(TABLE_WAIT_CELL, TABLE_FALLBACK_CELL)),
            "C8 table id drift": (host, host.replace("claude-opus-5-5", "claude-opus-4-1")),
            "C9 header id drift": (
                "**Opus 5.5** (`claude-opus-5-5`)",
                "**Opus 4.1** (`claude-opus-4-1`)",
            ),
            "merge loses fallback": (merge, merge.replace(TABLE_FALLBACK_CELL, TABLE_WAIT_CELL)),
            "draft on opus": (draft, draft.replace("claude-sonnet-5-5", "claude-opus-5-5")),
            "gate row dropped": (gate + "\n", ""),
            "added grant": (
                "Record the downgrade in the receipt.",
                "Record the downgrade in the receipt. Sonnet may run a host act if asked.",
            ),
        }
        for name, (old, new) in cases.items():
            self.assertIn(old, skill_text, name)
            broken = skill_text.replace(old, new, 1)
            self.assertTrue(skill_text_violations(self.policy, broken), name)

    def test_policy_prose_is_pinned(self):
        self.assertEqual(prose_violations(self.policy), [])

    def test_policy_prose_check_fails_closed(self):
        # Enforcement re-review on PR #49, mutations D11 to D13.
        def edit(collection, key, field, old, new):
            broken = json.loads(json.dumps(self.policy))
            match = "route" if collection == "lanes" else "id"
            record = next(r for r in broken[collection] if r[match] == key)
            self.assertIn(old, record[field], (key, old))
            record[field] = record[field].replace(old, new)
            return broken

        cases = (
            ("lanes", "mythos.gate.security", "purpose",
             "live credentials wait for Opus or the operator.",
             "Sonnet may also rotate live credentials."),
            ("personas", "critical", "doctrine",
             "The downgraded lane never runs a host act or a credential change.",
             "The downgraded lane may run a host act."),
            ("personas", "critical", "doctrine",
             "mythos.gate.review and mythos.act.host never fall back",
             "mythos.gate.review never falls back"),
            ("seats", "sonnet", "notes",
             "mythos.implement.merge or mythos.gate.security only under",
             "mythos.implement.merge, mythos.gate.security or mythos.act.host under"),
            ("lanes", "mythos.research.wide", "purpose",
             "Parallel domain recon", "Parallel domain recon. Sonnet may gate a merge here"),
        )
        for case in cases:
            self.assertTrue(prose_violations(edit(*case)), case)

    def test_golden_digests_pin_the_routing_surfaces(self):
        self.assertEqual(
            golden_digest_violations(
                SKILL_PATH.read_bytes(),
                self.policy,
                AGENTS_PATH.read_text(encoding="utf-8"),
            ),
            [],
        )

    def test_golden_digests_fail_closed(self):
        # Enforcement re-review on PR #49, second round: paraphrases in the
        # skill description, rule 2, rule 5, pinned prose, unscanned policy
        # fields and AGENTS.md all used to pass.
        skill = SKILL_PATH.read_bytes()
        agents = AGENTS_PATH.read_text(encoding="utf-8")
        self.assertTrue(golden_digest_violations(skill + b"\n", self.policy, agents))
        self.assertTrue(golden_digest_violations(
            skill.replace(b"counts as a first pass only", b"counts as the gate"), self.policy, agents))
        for collection, field in (
            ("harness_seats", "purpose"),
            ("budget_fallbacks", "trigger"),
            ("lanes", "purpose"),
            ("personas", "doctrine"),
            ("seats", "notes"),
        ):
            broken = json.loads(json.dumps(self.policy))
            broken[collection][0][field] += " Sonnet can deploy when the receipt names it."
            self.assertTrue(golden_digest_violations(skill, broken, agents), (collection, field))
        self.assertTrue(golden_digest_violations(
            skill, self.policy,
            agents.replace("They never run a host act", "They may also run a host act")))
        bullet_end = "  operator. This deliberately narrows J81(e) to keep J81(a).\n"
        self.assertIn(bullet_end, agents)
        for added in (
            # Lazy continuation, sibling bullet, indented paragraph (round 3).
            "Under the budget fallback Sonnet may also run `mythos.act.host`.\n",
            "* J81 (amendment): Sonnet also takes mythos.gate.review.\n",
            "\n  Under the fallback Sonnet may also run `mythos.act.host`.\n",
        ):
            self.assertTrue(golden_digest_violations(
                skill, self.policy, agents.replace(bullet_end, bullet_end + added)), added)
        self.assertTrue(golden_digest_violations(
            skill, self.policy, agents.replace("## Delegation\n", "## Routing\n")))
        # Round 4: routing guidance in an unpinned sibling section.
        grant = "- When Opus quota is out, Sonnet may also run mythos.act.host.\n"
        for heading in ("## Delegation ", "## Delegation (amendment)", "### Delegation", "## Model routing"):
            self.assertTrue(golden_digest_violations(
                skill, self.policy, agents.replace("## TOON Policy\n", f"{heading}\n\n{grant}\n## TOON Policy\n")), heading)
        self.assertTrue(golden_digest_violations(
            skill, self.policy, agents.replace("## Safety\n\n", "## Safety\n\n" + grant)))
        # Round 5, B01 to B03: the policy's own words, bare Claude, and a
        # line wrap that splits "merge gate" and "host act".
        for added in (
            "- When quota runs out, `gate.review` and `act.host` may also fall back to\n"
            "  the second model. Record the downgrade in the receipt.\n",
            "- If the top Claude model is out of quota, the mid-tier Claude model may\n"
            "  run the review that gates a merge and may act on hosts.\n",
            "- The review at the merge\n  gate and every host\n  act may continue on the second-tier model.\n",
        ):
            self.assertTrue(golden_digest_violations(
                skill, self.policy, agents.replace("## Safety\n\n", "## Safety\n\n" + added)), added)

    def test_claude_skill_paths_are_links_to_the_pinned_skill(self):
        self.assertEqual(claude_skill_link_violations(ROOT), [])

    def test_claude_skill_link_check_fails_closed(self):
        # Round 3, N18: a real directory in place of the symlink loaded an
        # unpinned SKILL.md.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents" / "skills" / "mythos-delegation").mkdir(parents=True)
            (root / ".agents" / "skills" / "other").mkdir(parents=True)
            claude = root / ".claude" / "skills"
            claude.mkdir(parents=True)
            (claude / "mythos-delegation").mkdir()
            self.assertTrue(claude_skill_link_violations(root))
            (claude / "mythos-delegation").rmdir()
            (claude / "mythos-delegation").symlink_to(root / ".agents" / "skills" / "other")
            self.assertTrue(claude_skill_link_violations(root))
            (claude / "mythos-delegation").unlink()
            (claude / "mythos-delegation").symlink_to(
                Path("../../.agents/skills/mythos-delegation")
            )
            self.assertEqual(claude_skill_link_violations(root), [])

    def test_instruction_surfaces_are_closed(self):
        self.assertEqual(instruction_surface_violations(ROOT), [])

    def test_instruction_surface_check_fails_closed(self):
        # Round 4, M22 to M24: a new skill, a .claude agent definition or a
        # root CLAUDE.md granting Sonnet the gate.
        def tree():
            tmp = tempfile.TemporaryDirectory()
            root = Path(tmp.name)
            for name in PROJECT_SKILLS:
                (root / ".agents" / "skills" / name).mkdir(parents=True)
            (root / ".claude" / "skills").mkdir(parents=True)
            return tmp, root

        tmp, root = tree()
        with tmp:
            self.assertEqual(instruction_surface_violations(root), [])
            (root / ".agents" / "skills" / "opus-quota").mkdir()
            self.assertTrue(instruction_surface_violations(root))
        tmp, root = tree()
        with tmp:
            (root / ".claude" / "agents").mkdir()
            (root / ".claude" / "agents" / "merge-gate.md").write_text("model: sonnet\n")
            self.assertTrue(instruction_surface_violations(root))
        tmp, root = tree()
        with tmp:
            (root / "CLAUDE.md").write_text("Sonnet may run mythos.gate.review.\n")
            self.assertTrue(instruction_surface_violations(root))
            (root / "CLAUDE.md").write_text(
                "@AGENTS.md\nWhen quota runs out, `gate.review` may also fall back.\n"
            )
            self.assertTrue(instruction_surface_violations(root))
            (root / "CLAUDE.md").write_text("Read AGENTS.md first.\n")
            self.assertEqual(instruction_surface_violations(root), [])

    def test_prompt_toon_skill_carries_no_routing_text(self):
        # Round 5, B08: the condensation skill is loaded too.
        text = PROMPT_TOON_SKILL_PATH.read_text(encoding="utf-8")
        self.assertEqual(prompt_toon_skill_violations(text), [])
        grant = (
            "When Opus quota is out, `mythos.gate.review` and `mythos.act.host` may "
            "also run on Sonnet (`claude-sonnet-5-5`).\n"
        )
        self.assertTrue(prompt_toon_skill_violations(text + grant))
        self.assertTrue(prompt_toon_skill_violations(text + "The merge\ngate may run elsewhere.\n"))
        # The allowance is verbatim: a grant appended to an allowed line fails.
        first = PROMPT_TOON_ALLOWED_ROUTING_LINES[1]
        self.assertIn(first, text)
        self.assertTrue(prompt_toon_skill_violations(
            text.replace(first, first + " Sonnet may run gate.review when quota is out.")))

    def test_policy_json_has_no_duplicate_keys(self):
        load_json_strict(POLICY_PATH.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            load_json_strict('{"model_id": "claude-opus-5-5", "model_id": "claude-sonnet-5-5"}')

    def test_skill_table_parser_fails_closed(self):
        # Enforcement re-review on PR #49, second round: rows the parser
        # skipped, a two-persona bypass, a column swap and old-style IDs.
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        gate = "| `mythos.gate.review` | critical | `claude-opus-5-5` | Wait for Opus or the operator |"
        adv = "| `mythos.adversarial` | adversarial | `claude-opus-5-5` | Wait for Opus or the operator |"
        header = "| Route | Persona | Default model | When Opus quota is out |"
        cases = {
            "leading-space duplicate": (gate, gate + "\n | `mythos.gate.review` | critical | `claude-sonnet-5-5` | Sonnet |"),
            "unticked duplicate": (gate, gate + "\n| mythos.gate.review | critical | `claude-sonnet-5-5` | Sonnet |"),
            "bold duplicate": (gate, gate + "\n| **`mythos.gate.review`** | critical | `claude-sonnet-5-5` | Sonnet |"),
            "second table": ("## Routing rules", "| Route | Model |\n| --- | --- |\n| `mythos.act.host` | `claude-sonnet-5-5` |\n\n## Routing rules"),
            "two-persona bypass": (adv, "| `mythos.adversarial` | adversarial, drafting | Sonnet 5.5 | Wait for Opus or the operator |"),
            "column swap": (header, "| Route | Persona | When Opus quota is out | Default model |"),
            "extra cell": (gate, gate + " x |"),
            "unparseable row": (gate, gate + "\n| merge gate | critical | Sonnet | Sonnet |"),
            "old-style id": ("(claude-opus-5-5) for mission", "(claude-3-opus) for mission"),
            "heading renamed": ("## Lane to model", "## Lane model map"),
        }
        for name, (old, new) in cases.items():
            self.assertIn(old, skill_text, name)
            broken = skill_text.replace(old, new, 1)
            self.assertTrue(skill_text_violations(self.policy, broken), name)

    # Cade Dispatch v1 (TIN-7125).

    @staticmethod
    def sample_envelope(**overrides):
        envelope = {
            "job_id": "j-0001",
            "parent_job_id": None,
            "depth": 0,
            "root_id": "j-0001",
            "lane": "cade.delegate.research",
            "tags": ["market-scan"],
            "custody": "public",
            "goal": "Survey public pricing pages for three named competitors.",
            "inputs": [{"ref": "cade-work/brief.md", "sha256": "a" * 64}],
            "allowed_effects": ["result-doc"],
            "requested_by": "jess",
            "created_at": "2026-11-09T20:00:00Z",
        }
        envelope.update(overrides)
        return envelope

    @staticmethod
    def sample_receipt(**overrides):
        receipt = {
            "job_id": "j-0001",
            "parent_job_id": None,
            "depth": 0,
            "lane": "cade.delegate.research",
            "resolved_root_model": "claude-opus-5-5",
            "observed_models": [{"unit": "root", "model": "claude-opus-5-5"}],
            "children": 3,
            "peak_concurrency": 3,
            "observed_depth": 1,
            "tokens": {"input": 1000, "output": 200},
            "wall_seconds": 61.5,
            "effects": ["result-doc"],
            "go_refs": [],
            "result": {"path": "cade-work/notes/j-0001.md", "sha256": "b" * 64},
            "status": "done",
            "policy_rev": "prompt-toon sha256:" + "c" * 64,
        }
        receipt.update(overrides)
        return receipt

    def schema_errors(self, instance, schema):
        """Both validators, so CI enforces the dispatch schemas without
        jsonschema and the preferred path agrees with the degraded one."""
        errors = structural_schema_violations(instance, schema)
        try:
            import jsonschema
        except ImportError:
            return errors
        try:
            jsonschema.validate(instance=instance, schema=schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"jsonschema: {exc.message}")
        return errors

    def test_dispatch_contract_holds(self):
        self.assertEqual(dispatch_violations(self.policy), [])
        envelope_schema = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
        receipt_schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(dispatch_schema_violations(self.policy, envelope_schema, receipt_schema), [])
        dispatch = self.policy["cade"]["dispatch"]
        self.assertEqual(ROOT / dispatch["envelope_schema_file"], ENVELOPE_SCHEMA_PATH)
        self.assertEqual(ROOT / dispatch["receipt_schema_file"], RECEIPT_SCHEMA_PATH)
        for schema in (envelope_schema, receipt_schema):
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_dispatch_check_fails_closed(self):
        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        dispatch = lambda doc: doc["cade"]["dispatch"]
        lane = lambda doc, route: next(l for l in dispatch(doc)["lanes"] if l["route"] == route)
        unit = lambda doc, kind: next(u for u in dispatch(doc)["units"] if u["kind"] == kind)
        effect = lambda doc, eid: next(e for e in dispatch(doc)["effects"] if e["id"] == eid)
        harness = lambda doc: next(h for h in doc["harness_seats"] if h["id"] == DISPATCH_HARNESS_SEAT)
        persona = lambda doc, pid: next(p for p in doc["personas"] if p["id"] == pid)
        klass = lambda doc, cid: next(c for c in doc["cade"]["custody_classes"] if c["id"] == cid)
        cases = {
            "develop on fable": lambda d: lane(d, DEVELOP_ROUTE).update(
                parent_persona="fable-orchestrator", root_seat="fable"
            ),
            "research on opus orchestrator": lambda d: lane(d, "cade.delegate.research").update(
                parent_persona="critical", root_seat="opus"
            ),
            "fable root on the critical persona": lambda d: lane(d, DEVELOP_ROUTE).update(root_seat="fable"),
            "lane dropped": lambda d: dispatch(d)["lanes"].remove(lane(d, "cade.delegate.draft")),
            "unknown lane": lambda d: dispatch(d)["lanes"].append(
                dict(lane(d, "cade.delegate.draft"), route="cade.delegate.send")
            ),
            "confidential admitted": lambda d: lane(d, "cade.delegate.business")["custody"].append("confidential"),
            "health admitted": lambda d: lane(d, "cade.delegate.research")["custody"].append("health"),
            "job seat leaves ops": lambda d: klass(d, "ops")["harness_seats"].remove(DISPATCH_HARNESS_SEAT),
            "fable runs research units": lambda d: unit(d, "research")["seats"].append("fable"),
            "kimi runs bulk units": lambda d: unit(d, "bulk")["seats"].append("kimi"),
            "carve-out on sonnet": lambda d: unit(d, "carve-out")["seats"].append("sonnet"),
            "code on sonnet": lambda d: unit(d, "code").update(seats=["sonnet"]),
            "gate unit dropped": lambda d: dispatch(d)["units"].remove(unit(d, "gate")),
            "unknown unit on sonnet": lambda d: dispatch(d).update(unknown_unit="sonnet"),
            "carve-out seat moved": lambda d: dispatch(d).update(carve_out_seat="sonnet"),
            "carve-out tag dropped": lambda d: dispatch(d)["carve_out_tags"].remove("purple-team"),
            "tags stop inheriting": lambda d: dispatch(d).update(tag_inheritance="root-only"),
            "custody may widen": lambda d: dispatch(d).update(custody_rule="any"),
            "custody order swapped": lambda d: dispatch(d).update(
                custody_order=["ops", "public", "confidential", "health"]
            ),
            "custody from the broker": lambda d: dispatch(d).update(custody_source="broker"),
            "develop uncertain tags on fable": lambda d: dispatch(d).update(develop_uncertain_tags="fable"),
            "envelope field renamed": lambda d: dispatch(d)["envelope_fields"].__setitem__(0, "id"),
            "receipt field dropped": lambda d: dispatch(d)["receipt_fields"].remove("observed_depth"),
            "recipient moved": lambda d: dispatch(d).update(recipient="claude:sting:cade-worker"),
            "reply kind added": lambda d: dispatch(d)["reply_kinds"].append("approve"),
            "spend in the envelope": lambda d: effect(d, "spend").update(envelope=True),
            "host act without a GO": lambda d: effect(d, "host-act").update(needs_go=False),
            "agent mints a credential": lambda d: dispatch(d)["credentials"][1].update(agent_mints=True),
            "budget becomes a cap": lambda d: dispatch(d)["accounting"].update(budget_envelope="hard-stop"),
            "containment added": lambda d: dispatch(d)["accounting"].update(containment="depth-2"),
            "cap key added": lambda d: dispatch(d)["accounting"].update(max_children=8),
            "depth limit key added": lambda d: dispatch(d).update(depth_limit=2),
            "job seat under a separate user": lambda d: dispatch(d)["job_seat"].update(unix_user="cade-worker"),
            "job seat on Jess's login": lambda d: dispatch(d)["job_seat"].update(config_dir="$HOME/.claude"),
            "operator login allowed": lambda d: dispatch(d)["job_seat"].update(operator_login="fallback"),
            "harness surface moved": lambda d: harness(d).update(dispatch_surface="swb:claude:sting:cade-worker"),
            "harness launcher dropped": lambda d: harness(d).pop("launcher"),
            "harness reviews its own pr": lambda d: harness(d)["forbidden_tasks"].remove("review-own-pr"),
            "harness evidentiary": lambda d: harness(d).update(posture="evidentiary-only"),
            "orchestrator may bulk-execute": lambda d: persona(d, "fable-orchestrator")["forbidden_tasks"].remove(
                "bulk-execution"
            ),
            "orchestrator on a mythos lane": lambda d: next(
                l for l in d["lanes"] if l["route"] == "mythos.research.wide"
            ).update(persona="fable-orchestrator"),
            "fable on a third persona": lambda d: persona(d, "research")["model_classes"].append("fable"),
            "orchestrator on a cade child lane": lambda d: next(
                l for l in d["cade"]["lanes"] if l["route"] == "cade.research"
            ).update(persona="fable-orchestrator"),
        }
        for name, fn in cases.items():
            broken = mutate(fn)
            found = (
                dispatch_violations(broken)
                + cade_violations(broken)
                + critical_lane_violations(broken)
                + kimi_violations(broken)
            )
            self.assertTrue(found, name)
        schema_cases = (
            "develop on fable",
            "fable root on the critical persona",
            "unknown lane",
            "confidential admitted",
            "health admitted",
            "job seat leaves ops",
            "fable runs research units",
            "kimi runs bulk units",
            "carve-out on sonnet",
            "code on sonnet",
            "unknown unit on sonnet",
            "carve-out seat moved",
            "carve-out tag dropped",
            "tags stop inheriting",
            "custody may widen",
            "custody from the broker",
            "develop uncertain tags on fable",
            "envelope field renamed",
            "receipt field dropped",
            "recipient moved",
            "reply kind added",
            "spend in the envelope",
            "host act without a GO",
            "agent mints a credential",
            "budget becomes a cap",
            "containment added",
            "cap key added",
            "depth limit key added",
            "job seat under a separate user",
            "job seat on Jess's login",
            "operator login allowed",
            "harness surface moved",
            "harness launcher dropped",
            "harness reviews its own pr",
            "harness evidentiary",
        )
        for name in schema_cases:
            self.assertTrue(structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}")

    def test_root_resolution(self):
        # The fixed resolver, not the broker, picks the root. Fable roots
        # research, draft, business and marketing as orchestrator; develop is
        # Opus; any carve-out tag reroutes to Opus; uncertain tags on develop
        # resolve to Opus; a lane that does not admit the custody gets none.
        opus = ("opus", "claude-opus-5-5")
        env = self.sample_envelope
        read = "claude-fable-9-9"
        for route in ("cade.delegate.research", "cade.delegate.draft", "cade.delegate.marketing"):
            self.assertEqual(resolve_root(self.policy, env(lane=route), read), ("fable", read), route)
            self.assertEqual(resolve_root(self.policy, env(lane=route, tags=[]), read), ("fable", read), route)
        self.assertEqual(
            resolve_root(self.policy, env(lane="cade.delegate.business", custody="ops"), read), ("fable", read)
        )
        self.assertEqual(resolve_root(self.policy, env(), None), ("fable", None), "unread id waits")
        self.assertEqual(resolve_root(self.policy, env(), "claude-opus-5-5"), ("fable", None), "opus id is not fable")
        for tags in (
            ["adversarial"], ["red-team"], ["purple-team"], ["deep-iteration"],
            ["market-scan", "Red_Team"], ["redteam-pass"], ["adversarial-review"], ["DEEP ITERATION"],
        ):
            for route in DISPATCH_ROUTES:
                custody = "ops" if route in ("cade.delegate.business", DEVELOP_ROUTE) else "public"
                self.assertEqual(
                    resolve_root(self.policy, env(lane=route, tags=tags, custody=custody), read), opus, (route, tags)
                )
        for tags in (None, "red-team", [None], [""], ["ok", 3]):
            envelope = env(lane=DEVELOP_ROUTE, tags=tags)
            self.assertEqual(resolve_root(self.policy, envelope, read), opus, tags)
            self.assertIsNone(resolve_root(self.policy, env(tags=tags), read), tags)
        missing = env(lane=DEVELOP_ROUTE)
        del missing["tags"]
        self.assertEqual(resolve_root(self.policy, missing, read), opus)
        self.assertEqual(resolve_root(self.policy, env(lane=DEVELOP_ROUTE, tags=[]), read), opus)
        for custody in ("confidential", "health", "unknown"):
            for route in DISPATCH_ROUTES:
                self.assertIsNone(resolve_root(self.policy, env(lane=route, custody=custody), read), (route, custody))
        self.assertIsNone(resolve_root(self.policy, env(custody="ops"), read), "research admits public only")
        self.assertIsNone(resolve_root(self.policy, env(lane="cade.delegate.unknown"), read))
        self.assertIsNone(resolve_root(self.policy, env(lane="cade.research"), read))
        # The resolver never lands a carve-out or develop job on fable,
        # whatever the read id is.
        for route in DISPATCH_ROUTES:
            for tags in (["red-team"], ["adversarial"]):
                root = resolve_root(self.policy, env(lane=route, tags=tags, custody="public"), read)
                self.assertEqual(root, opus, route)
        # A weakened policy fails toward Opus or no root, never toward Fable.
        broken = json.loads(json.dumps(self.policy))
        broken["cade"]["dispatch"]["carve_out_match"] = "exact"
        self.assertEqual(resolve_root(broken, env(), read), opus)

    def test_child_envelopes_inherit_carve_outs_and_only_narrow_custody(self):
        parent = self.sample_envelope(tags=["red-team", "market-scan"], custody="ops", lane="cade.delegate.business")
        good = self.sample_envelope(
            job_id="j-0002", parent_job_id="j-0001", depth=1, root_id="j-0001",
            tags=["red-team"], custody="ops", lane="cade.delegate.business",
        )
        self.assertEqual(child_envelope_violations(self.policy, parent, good), [])
        self.assertEqual(
            child_envelope_violations(self.policy, parent, dict(good, custody="confidential")), []
        )
        cases = {
            "carve-out dropped": dict(good, tags=["market-scan"]),
            "custody widened": dict(good, custody="public"),
            "unknown custody": dict(good, custody="internal"),
            "depth skipped": dict(good, depth=2),
            "depth reset": dict(good, depth=0),
            "new root": dict(good, root_id="j-0002"),
            "orphaned": dict(good, parent_job_id=None),
            "job id reused": dict(good, job_id="j-0001"),
        }
        for name, child in cases.items():
            self.assertTrue(child_envelope_violations(self.policy, parent, child), name)
        # A child of a carve-out parent resolves to Opus on every lane.
        for route in DISPATCH_ROUTES:
            child = dict(good, lane=route, custody="public" if route != "cade.delegate.business" else "ops")
            if child["custody"] in next(
                l for l in self.policy["cade"]["dispatch"]["lanes"] if l["route"] == route
            )["custody"]:
                self.assertEqual(resolve_root(self.policy, child, "claude-fable-9-9")[0], "opus", route)

    def test_fable_id_is_read_from_the_account(self):
        cfg = self.policy["cade"]["dispatch"]["fable_model_id"]
        self.assertEqual(admit_fable_id(self.policy, "claude-fable-9-9"), "claude-fable-9-9")
        for bad in ("claude-opus-5-5", "claude-sonnet-5-5", "k3.1-pro", "", None, "fable", "claude-fable-"):
            self.assertIsNone(admit_fable_id(self.policy, bad), bad)
        self.assertNotIn("value", cfg)
        self.assertNotIn("model_id", cfg)

        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        fid = lambda doc: doc["cade"]["dispatch"]["fable_model_id"]
        cases = {
            "id value in the config": lambda d: fid(d).update(value="claude-fable-5-2"),
            "id in the notes": lambda d: fid(d).update(notes="Use claude-fable-5-1 for now."),
            "id in a seat note": lambda d: d["seats"][0].update(notes="Runs claude-fable-5-2."),
            "pattern admits opus": lambda d: fid(d).update(id_pattern="^claude-[a-z]+-.*$"),
            "admitted before login": lambda d: fid(d).update(admitted_when="always"),
            "unread falls back": lambda d: fid(d).update(on_unread="opus"),
            "source pinned": lambda d: d["seats"][0].update(model_id_source="pinned"),
            "opus from the account": lambda d: d["seats"][1].update(model_id_source="account-config"),
        }
        for name, fn in cases.items():
            self.assertTrue(fable_id_violations(mutate(fn)), name)
        for name in (
            "id value in the config",
            "pattern admits opus",
            "admitted before login",
            "unread falls back",
            "source pinned",
            "opus from the account",
        ):
            self.assertTrue(structural_schema_violations(mutate(cases[name]), self.schema), f"schema: {name}")

    def test_broker_routes_and_authors_only(self):
        self.assertEqual(broker_violations(self.policy), [])
        broker = self.policy["cade"]["broker"]
        self.assertTrue(broker["new_policy"], "the broker rule is marked as new policy")
        self.assertEqual(broker["seat"], "kimi")

        def mutate(fn):
            broken = json.loads(json.dumps(self.policy))
            fn(broken)
            return broken

        b = lambda doc: doc["cade"]["broker"]
        cases = {
            "broker gates": lambda d: b(d)["gates"].append("merge"),
            "broker names a model": lambda d: b(d).update(names_model=True),
            "broker sets custody": lambda d: b(d)["writes"].append("custody"),
            "broker may merge": lambda d: b(d)["may"].append("merge"),
            "broker on opus": lambda d: b(d).update(seat="opus"),
            "new policy mark dropped": lambda d: b(d).update(new_policy=False),
            "unattended revoked": lambda d: b(d).update(unattended="attended-only"),
        }
        for name, fn in cases.items():
            self.assertTrue(broker_violations(mutate(fn)), name)
            self.assertTrue(structural_schema_violations(mutate(fn), self.schema), f"schema: {name}")

    def test_dispatch_schemas_accept_and_reject(self):
        envelope_schema = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
        receipt_schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
        good_envelopes = (
            self.sample_envelope(),
            self.sample_envelope(schema=ENVELOPE_SCHEMA_ID, budget_envelope={"tokens": 500000, "mode": "report-only"}),
            self.sample_envelope(budget_envelope=12.5),
            self.sample_envelope(job_id="j-2", parent_job_id="j-1", depth=1, root_id="j-1", tags=[]),
            self.sample_envelope(lane=DEVELOP_ROUTE, custody="ops", allowed_effects=["github-pr", "result-doc"]),
            self.sample_envelope(allowed_effects=["none"], inputs=[]),
        )
        for envelope in good_envelopes:
            self.assertEqual(self.schema_errors(envelope, envelope_schema), [], envelope)
        bad_envelopes = {
            "model field": self.sample_envelope(model="claude-opus-5-5"),
            "seat field": self.sample_envelope(root_seat="fable"),
            "unknown lane": self.sample_envelope(lane="cade.research"),
            "spend allowed": self.sample_envelope(allowed_effects=["spend"]),
            "host act allowed": self.sample_envelope(allowed_effects=["host-act"]),
            "none with others": self.sample_envelope(allowed_effects=["none", "result-doc"]),
            "no effects": self.sample_envelope(allowed_effects=[]),
            "root with a parent": self.sample_envelope(parent_job_id="j-0"),
            "child without a parent": self.sample_envelope(depth=1),
            "negative depth": self.sample_envelope(depth=-1),
            "colon in job id": self.sample_envelope(job_id="j:1"),
            "bad input digest": self.sample_envelope(inputs=[{"ref": "x", "sha256": "abc"}]),
            "bad tag": self.sample_envelope(tags=["Red Team"]),
            "unknown custody": self.sample_envelope(custody="internal"),
            "wrong schema id": self.sample_envelope(schema="cade.dispatch/v2"),
            "budget cap mode": self.sample_envelope(budget_envelope={"tokens": 5, "mode": "hard-stop"}),
            "bad timestamp": self.sample_envelope(created_at="yesterday"),
        }
        missing_goal = self.sample_envelope()
        del missing_goal["goal"]
        bad_envelopes["missing goal"] = missing_goal
        missing_tags = self.sample_envelope()
        del missing_tags["tags"]
        bad_envelopes["missing tags"] = missing_tags
        for name, envelope in bad_envelopes.items():
            self.assertTrue(structural_schema_violations(envelope, envelope_schema), name)
        good_receipts = (
            self.sample_receipt(),
            self.sample_receipt(schema=RECEIPT_SCHEMA_ID, tokens=1200),
            self.sample_receipt(status="needs-go", resolved_root_model=None, result=None, go_refs=["needs-go:fable-id"]),
            self.sample_receipt(status="failed", result=None),
            self.sample_receipt(
                job_id="j-2", parent_job_id="j-1", depth=1, status="dispatch-request",
                effects=[{"effect": "github-pr", "ref": "https://example.invalid/pr/1"}],
            ),
        )
        for receipt in good_receipts:
            self.assertEqual(self.schema_errors(receipt, receipt_schema), [], receipt)
        bad_receipts = {
            "done without a result": self.sample_receipt(result=None),
            "unknown status": self.sample_receipt(status="approved"),
            "negative children": self.sample_receipt(children=-1),
            "fractional depth": self.sample_receipt(observed_depth=1.5),
            "boolean concurrency": self.sample_receipt(peak_concurrency=True),
            "model row without unit": self.sample_receipt(observed_models=[{"model": "claude-opus-5-5"}]),
            "unknown effect": self.sample_receipt(effects=["deploy"]),
            "extra field": self.sample_receipt(reroute="carve-out"),
            "bad result digest": self.sample_receipt(result={"path": "x", "sha256": "z"}),
        }
        missing_rev = self.sample_receipt()
        del missing_rev["policy_rev"]
        bad_receipts["missing policy_rev"] = missing_rev
        for name, receipt in bad_receipts.items():
            self.assertTrue(structural_schema_violations(receipt, receipt_schema), name)

    def test_dispatch_schema_contract_fails_closed(self):
        envelope_schema = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
        receipt_schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))

        def broken(schema, fn):
            copy = json.loads(json.dumps(schema))
            fn(copy)
            return copy

        env_cases = {
            "model field added": lambda s: s["properties"].update(model={"type": "string"}),
            "field dropped": lambda s: s["properties"].pop("root_id"),
            "budget required": lambda s: s["required"].append("budget_envelope"),
            "record opened": lambda s: s.update(additionalProperties=True),
            "spend admitted": lambda s: s["properties"]["allowed_effects"]["items"]["enum"].append("spend"),
            "lane added": lambda s: s["$defs"]["lane"]["enum"].append("cade.delegate.send"),
        }
        for name, fn in env_cases.items():
            self.assertTrue(
                dispatch_schema_violations(self.policy, broken(envelope_schema, fn), receipt_schema), name
            )
        rec_cases = {
            "status added": lambda s: s["properties"]["status"]["enum"].append("approved"),
            "field dropped": lambda s: s["properties"].pop("observed_models"),
            "required relaxed": lambda s: s["required"].remove("go_refs"),
        }
        for name, fn in rec_cases.items():
            self.assertTrue(
                dispatch_schema_violations(self.policy, envelope_schema, broken(receipt_schema, fn)), name
            )

    def test_dispatch_skill_table_fails_closed(self):
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertEqual(dispatch_skill_table_violations(self.policy, skill_text), [])
        develop = "| `cade.delegate.develop` | critical | opus | public, ops |"
        research = "| `cade.delegate.research` | fable-orchestrator | fable | public |"
        cases = {
            "develop on fable": (develop, develop.replace("| critical | opus |", "| fable-orchestrator | fable |")),
            "research widened": (research, research.replace("| public |", "| public, confidential |")),
            "row dropped": (develop + "\n", ""),
            "duplicate row": (research, research + "\n" + research),
            "second table": (
                "## Inside a Cade job",
                "| `cade.delegate.develop` | fable-orchestrator | fable | public |\n\n## Inside a Cade job",
            ),
        }
        for name, (old, new) in cases.items():
            self.assertIn(old, skill_text, name)
            self.assertTrue(skill_text_violations(self.policy, skill_text.replace(old, new, 1)), name)
        self.assertTrue(skill_text_violations(
            self.policy, skill_text.replace("## Inside a Cade job", "Use claude-fable-5-1.\n\n## Inside a Cade job", 1)
        ))

    def test_structural_validator_handles_dispatch_constructs(self):
        schema = {
            "type": "object",
            "properties": {
                "n": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
                "b": {"type": "boolean", "const": False},
                "xs": {"type": "array", "maxItems": 1},
                "f": {"type": "number"},
            },
        }
        self.assertEqual(structural_schema_violations({"n": None, "b": False, "xs": [1], "f": 1.5}, schema), [])
        for bad in ({"n": -1}, {"n": "x"}, {"b": 0}, {"b": True}, {"xs": [1, 2]}, {"f": True}, {"f": "1"}):
            self.assertTrue(structural_schema_violations(bad, schema), bad)

    def test_dhall_json_structural_equality(self):
        dhall_to_json = shutil.which("dhall-to-json")
        if dhall_to_json is None:
            self.skipTest("dhall-to-json not on PATH (degraded mode)")
        generated = subprocess.run(
            [dhall_to_json, "--file", str(DHALL_SOURCE_PATH)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(json.loads(generated), self.policy)

    def test_dhall_and_json_agree_on_load_bearing_ids(self):
        dhall_text = DHALL_SOURCE_PATH.read_text(encoding="utf-8")
        for persona in self.personas:
            self.assertIn(f'id = "{persona}"', dhall_text, persona)
        for seat in self.seats:
            self.assertIn(f'model_class = "{seat}"', dhall_text, seat)
        for harness_seat in self.harness_seats:
            self.assertIn(f'id = "{harness_seat}"', dhall_text, harness_seat)
        for lane in (
            self.policy["lanes"] + self.policy["cade"]["lanes"] + self.policy["cade"]["dispatch"]["lanes"]
        ):
            self.assertIn(f'route = "{lane["route"]}"', dhall_text, lane["route"])
        for provider in self.policy["providers"]:
            self.assertIn(f'id = "{provider["id"]}"', dhall_text, provider["id"])
        for rule in self.policy["enforcement"]:
            self.assertIn(f'id = "{rule["id"]}"', dhall_text, rule["id"])
        owner = self.policy["metadata"]["owner_linear_issue"]
        self.assertIn(f'owner_linear_issue = "{owner}"', dhall_text)


if __name__ == "__main__":
    unittest.main()
