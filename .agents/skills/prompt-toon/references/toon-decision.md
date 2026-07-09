# TOON Decision

Use TOON as a measured leaf encoding, not as the default interchange format.

Current July 2026 evidence:

- The canonical spec is TOON v3.3, a working draft dated 2026-05-21. Official
  TOON already requires LF line endings; no separate LF TOON spec was found.
- TOON is strongest for large uniform arrays of primitive object rows.
- Compact JSON often wins for nested, ragged, semi-uniform, or arbitrary tool
  data.
- Independent July 2026 research found TOON can impose a prompt tax and can
  reduce accuracy or parse stability in multi-turn/tool-call workflows.
- Linear TIN-2554 records the local constraint: TOON/CSV is declined as a
  fleetwide default and may only be tried as a flag-gated uniform-row
  experiment.

Policy:

- Default to compact JSON/JSONL for arbitrary data.
- Emit TOON only when:
  - rows are flat, uniform, and scalar;
  - row order and field order are stable;
  - strict shape checks pass;
  - estimated token savings meet the configured threshold;
  - downstream consumers do not need mature JSON Schema tooling.
- Never encode authority-bearing instructions, tool schemas, code diffs,
  nested configs, or model-generated tool-call output as TOON by default.
