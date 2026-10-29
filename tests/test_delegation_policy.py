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
HARNESS_SEAT_IDS = {"pi", "pi-k3"}

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

# J81 (Jess, 2026-10-23): exact model IDs per seat. Fable stays
# provider-managed and carries no model_id.
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
KIMI_FORBIDDEN_PERSONAS = {"fable", "critical", "adversarial"}

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
        "Fable never serves a cade.* lane.",
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
    flat = " ".join(skill_text.split())
    for sentence in SKILL_SENTENCES:
        if sentence not in flat:
            errors.append(f"skill lost: {sentence}")
    stray = model_ids_in(skill_text) - set(MODEL_IDS.values())
    if stray:
        errors.append(f"skill names model IDs outside the seat registry: {sorted(stray)}")
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
    lowered = " ".join(" ".join(prose).split()).lower()
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
    for line in (before + "\n" + rest).splitlines():
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
SKILL_SHA256 = "1ce503077c70b20059a1f1d80adc2c88430283ecffbff3243025e8717749d3f3"
POLICY_CANONICAL_SHA256 = "2491888633f1ffa3353dd4a239b627cc07318b138c07b9f45df5da0622f2c5bb"
AGENTS_DELEGATION_SHA256 = "53ea4c364f813afbbcc4894047529775e4efa67ebc7a40bbd50cf288c3b202e0"
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

    Covers only what the delegation schema uses: type, const, enum, required,
    additionalProperties=false, properties, items, minItems, minLength,
    pattern, uniqueItems, allOf, if/then, not, contains and local $ref into
    $defs. Unknown constructs are ignored rather than passed
    silently as valid, which is why jsonschema stays the preferred path.
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
        if expected == "integer" and not isinstance(value, int):
            return [f"{where}: expected integer"]
        if "const" in node and value != node["const"]:
            errors.append(f"{where}: expected const {node['const']!r}")
        if "enum" in node and value not in node["enum"]:
            errors.append(f"{where}: {value!r} not in {node['enum']}")
        if isinstance(value, str) and len(value) < node.get("minLength", 0):
            errors.append(f"{where}: shorter than minLength")
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
        self.assertEqual(self.policy["schema_version"], 4)

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

    def test_seat_model_ids_pin_the_5_5_family(self):
        # TIN-7760: the 5.5 pin is kept, and the only other admitted seat is
        # kimi / k3.1-pro on the kimi-coding endpoint.
        for seat_id, model_id in MODEL_IDS.items():
            self.assertEqual(self.seats[seat_id].get("model_id"), model_id, seat_id)
            self.assertEqual(self.seats[seat_id]["provider"], "anthropic", seat_id)
            self.assertNotIn("endpoint", self.seats[seat_id], seat_id)
        self.assertNotIn("model_id", self.seats["fable"], "fable stays provider-managed")
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
        }
        for name, fn in cases.items():
            self.assertTrue(provider_allow_list_violations(mutate(fn)), name)
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

    def test_harness_seats_are_evidentiary_only(self):
        for seat in self.policy["harness_seats"]:
            self.assertEqual(seat["posture"], HARNESS_POSTURE, seat["id"])

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
        for seat in self.policy["harness_seats"]:
            surface = seat["dispatch_surface"]
            self.assertIn("launcher", surface.lower(), seat["id"])
            self.assertIn(f"{seat['harness']}-*", surface, seat["id"])

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
        for lane in self.policy["lanes"] + self.policy["cade"]["lanes"]:
            self.assertIn(f'route = "{lane["route"]}"', dhall_text, lane["route"])
        for provider in self.policy["providers"]:
            self.assertIn(f'id = "{provider["id"]}"', dhall_text, provider["id"])
        for rule in self.policy["enforcement"]:
            self.assertIn(f'id = "{rule["id"]}"', dhall_text, rule["id"])
        owner = self.policy["metadata"]["owner_linear_issue"]
        self.assertIn(f'owner_linear_issue = "{owner}"', dhall_text)


if __name__ == "__main__":
    unittest.main()
