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
SEAT_IDS = {"fable", "opus", "sonnet", "haiku"}

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
    ("personas", "drafting", "doctrine"): (
        "It never stands in for a merge-gating review and never merges on its own.",
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
SKILL_SHA256 = "062a3685480798c86e444f6f86809e720b766e48f12b61d586179c1c3fe80de0"
POLICY_CANONICAL_SHA256 = "1034cb4f1f40b11e998af58fef6e86d331bdc3da597b9d515a4f18010e9438ea"
AGENTS_DELEGATION_SHA256 = "f18048be57c162610455ea5e2948d586d787881027db815c4d2269d8c093cac5"
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
# section (round 4).
AGENTS_ROUTING_RE = re.compile(
    r"\b(opus|sonnet|haiku|fable|claude-[a-z]+-\d|mythos\.|j8\d|budget fallback|"
    r"model class|merge gate|merge-gating|host act)",
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
    for line in outside.splitlines():
        if AGENTS_ROUTING_RE.search(line):
            errors.append(f"AGENTS.md routing text outside ## Delegation: {line.strip()}")
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
            for line in path.read_text(encoding="utf-8").splitlines():
                if AGENTS_ROUTING_RE.search(line):
                    errors.append(f"{name} carries routing text: {line.strip()}")
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
        self.assertEqual(self.policy["schema_version"], 3)

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
        for seat_id, model_id in MODEL_IDS.items():
            self.assertEqual(self.seats[seat_id].get("model_id"), model_id, seat_id)
        self.assertNotIn("model_id", self.seats["fable"], "fable stays provider-managed")

    def test_dhall_and_json_agree_on_model_ids(self):
        dhall_ids = dhall_seat_model_ids(DHALL_SOURCE_PATH.read_text(encoding="utf-8"))
        json_ids = {s["id"]: s.get("model_id") for s in self.policy["seats"]}
        self.assertEqual(set(dhall_ids), SEAT_IDS, "every Dhall seat declares model_id")
        self.assertEqual(dhall_ids, json_ids)
        self.assertEqual({k: v for k, v in dhall_ids.items() if v}, MODEL_IDS)

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
            (root / "CLAUDE.md").write_text("Read AGENTS.md first.\n")
            self.assertEqual(instruction_surface_violations(root), [])

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
        for lane in self.policy["lanes"]:
            self.assertIn(f'route = "{lane["route"]}"', dhall_text, lane["route"])
        for rule in self.policy["enforcement"]:
            self.assertIn(f'id = "{rule["id"]}"', dhall_text, rule["id"])
        owner = self.policy["metadata"]["owner_linear_issue"]
        self.assertIn(f'owner_linear_issue = "{owner}"', dhall_text)


if __name__ == "__main__":
    unittest.main()
