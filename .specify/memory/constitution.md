# D365 Solution Craft Constitution

- Raw `intake/` evidence is immutable.
- Preserve `INTK -> intake-scoped REQ -> FEAT -> plan component -> DEV` traceability.
- `spec.md` is solution-agnostic business behavior.
- `plan.md` is the authoritative D365, Power Platform, and Azure technical design.
- Use only component types, payloads, and skill mappings from `conventions.yml`.
- Prefer configuration before low-code, pro-code, or Azure; record escalation rationale.
- Generate one `DEV-####.md` per approved typed component; `tasks.md` is the index.
- A DEV executor is `agent`, `human`, or `hybrid`.
- Compile repository context before specify, plan, tasks, clarify, or analyze.
- Agents never approve gates, close questions without human decisions, or merge PRs.
- Craft and Spec Kit versions are pinned; do not auto-upgrade.
