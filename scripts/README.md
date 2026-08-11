# Deterministic consumer pipeline

Run from the consumer repository root, in order:

```text
python scripts/validate_compatibility.py
python scripts/validate_design_sources.py
python scripts/validate_authoring_targets.py
python scripts/compile_repository_context.py
python scripts/validate_reqs.py
python scripts/compile_requirements.py
python scripts/compile_specs.py
python scripts/validate_specs.py
python scripts/compile_design.py
python scripts/validate_design.py
python scripts/compile_tasks.py
python scripts/validate_tasks.py
```

Each compiler also supports `--check`. Checks do not write and fail when a
generated index, context, compiler-owned zone, `tasks.md`, or DEV artifact
differs from its authoritative source.

Each intake with requirements owns
`specs/intakes/INTK-####/requirement-groups.yml`, validated by
`specs/_schema/requirement-groups.schema.json`. Groups use stable
`INTK-####-GRP-##` IDs and contiguous `order` values. Every atomic REQ declares
exactly one same-intake `requirement_group`; empty/orphaned groups are invalid.
The registry is non-governing navigation and does not replace REQ provenance or
control feature boundaries. `compile_requirements.py` is the only writer of the
grouped `requirements.md` index.

DEV allocation is repository-global, incremental, and append-only. Existing
component-to-DEV mappings are retained, new components receive the next ID
after the highest DEV ID on disk, and historical DEV files are never deleted.
For nonterminal DEV files, compilation preserves `owner`, `executor`, `status`,
and all `AUTHOR` sections while refreshing compiler-owned payload/profile
zones. Completed, superseded, and cancelled DEV files are immutable; plan
changes must use a new component ID and DEV delta.

`.d365/development-resources.yml` declares one project authentication default:
`reuse_if_valid` or `always_prompt`. `validate_development_environment.py`
accepts the same optional `--authentication-policy` per-run override; CLI wins
over project default. `reuse_if_valid` probes and reuses only a valid session.
`always_prompt` requires fresh interactive authentication for every DEV
execution. Both rerun the minimal safety gate for every task. `execution_host`
is compiler-owned and resolves independently
from `executor`: Dataverse-bound scopes require `local_interactive`;
repository-only work is `cloud_or_local` only when every required routed
resource supports GitHub Copilot cloud. Before the live Project gate, run
`/d365.implement #<issue-number>`. The prompt resolves the compiler-owned host,
required MCP server keys, and exact tools through
`validate_development_environment.py DEV-#### --host-requirements --json`.
It automatically identifies VS Code, Copilot CLI, or GitHub Copilot cloud from
authoritative runtime context and validates the current agent tool surface.
Cloud identity cannot be overridden by local hints. Namespaced tools must match
their exact server; unscoped names are accepted only when compiler-owned
ownership and occurrence are unique. A supported safe read-only tools-list,
capability, describe, or metadata search probe may fill missing metadata, but a
failed probe blocks and a Dataverse write is never proof.

The prompt passes schema-version-1 evidence directly through
`--host-proof-stdin` or `--host-proof-json` to the mandatory
`--host-check-only` check. Session evidence is never written to DEV artifacts,
Git, issue bodies, environment variables, or persistent configuration.
`--execution-host` and repeatable `--mcp-server-callable` are optional
diagnostic overrides only when automatic evidence is genuinely inaccessible;
they cannot override contradictory evidence. Use `--run-interactive` only for
approved commands. MCP/host flows that cannot force freshness return
`waiting-for-human` with exact
targeted reconnect instructions. The runner never deletes profiles or signs
out unrelated sessions.

For Works issue #23 in VS Code, keep `dataverse-authoring` **Running** and the
required tools enabled, then run:

```text
/d365.implement #23
```

After the live Project and compiler-integrity gates, the environment validator
checks required versions/resources, structural compiler-owned target values,
and an authenticated callable session for the exact routed endpoint. A live
`ready` result means ready to attempt the scoped action, not proof that every
platform prerequisite exists. The implementation does not export/unpack or
inventory environments, publishers, solutions, component absence, permissions,
or memberships before acting. It surfaces sanitized action errors and performs
targeted canonical-identity/payload and post-write solution-membership
verification without automatic repair.

## Sanitized Development execution evidence

`execution_evidence.py` is the deterministic append-only evidence helper for
`/d365.implement`. It accepts one schema-version-1 non-secret JSON object on
standard input and does not persist the source input:

```powershell
<sanitized-evidence-json> | python scripts/execution_evidence.py render

<sanitized-evidence-json> | python scripts/execution_evidence.py post `
  --issue-number <exact-development-issue>
```

`post` verifies the exact Development Execution issue is open, has the
`development` label, and references the payload's DEV. It renders a stable
`d365-execution-evidence:v1` marker containing the unique UTC attempt ID,
DEV ID, `task_context_hash`, and `source_plan_hash`; checks existing comments
for that marker; and uses authenticated `gh issue comment --body-file -`.
Duplicate attempts are skipped. It never changes GitHub Project fields, issue
bodies, human status text, or compiler-owned snapshot blocks.

Each direct API/MCP/PAC or applicable repository action attempt receives one
comment. A stopped platform failure is posted immediately before retry and
states that no further writes occurred. A success is posted only after
targeted identity and payload verification plus applicable routed-solution
membership verification.

“Request and response output” means the helper's sanitized summary, not raw
payloads. The helper validates allowed keys/statuses, rejects secret-bearing
keys and solution export/unpack operations, and redacts recognized sensitive
string patterns. Never supply tokens, cookies, Authorization headers, refresh
data, auth profiles, secrets, passwords, connection strings, raw headers, full
request/response bodies, raw records, customer content, environment-variable
values, file contents, or sensitive field values. Use field names and
matched/expected results. If safe sanitization is uncertain, set
`details_withheld` and retain only category/code, non-secret correlation ID,
and remediation.

Submit mode verifies a current succeeded comment:

```powershell
python scripts/execution_evidence.py verify `
  --issue-number <exact-development-issue> `
  --dev-id DEV-#### `
  --task-context-hash <current-task-context-hash> `
  --source-plan-hash <current-source-plan-hash>
```

Missing, stale, unrelated, or blocked-only comments fail. Evidence comments are
append-only audit records and are never compiler-owned or regenerated.

## Capability-gated Dataverse Web API executor

`.d365/dataverse-web-api-capabilities.yml` and its JSON Schema cover every
registered component-type pattern and every create/update/delete/verify
operation, plus publish and solution-component actions where relevant.
Validate them with:

```powershell
python scripts/validate_dataverse_capabilities.py
```

For a matrix-supported solution-aware operation, use only:

```powershell
python scripts/dataverse_web_api_executor.py DEV-#### `
  --operation create|update|delete|verify|publish|add_solution_component|remove_solution_component `
  --issue-number <exact-development-issue>
```

`--dry-run` renders the controlled method/path family without authentication or
a Dataverse write. The executor reads the exact environment, solution,
canonical identity, payload, capability, and hashes from current compiler-owned
task/DEV context. There is no URL/method/path argument. It uses the configured
public-client ID/tenant and exact `msal==1.37.0` for current-human interactive
delegated OAuth with a memory-only token cache; no token or raw request/response
body is printed or persisted.

Destructive commands require `--approve-destructive` with the exact approval
string returned by a blocked attempt. `DELETE ...` destroys the environment
component/row. `REMOVE-SOLUTION-COMPONENT ...` changes unmanaged-solution
membership only. The executor never substitutes them.

Supported successes and sanitized platform failures are posted through
`execution_evidence.py`. Maker Portal fallback is not an executor option: the
capability must permit it and the issue evidence must name the exact API
failure or unsupported payload.

The context chain is:

`repository-context.json -> spec-context.json -> plan-context.json -> task-context.json`.

Design grounding sources are declared in `.d365/design-sources.yml` and
validated against `.d365/design-sources.schema.json`. MCP entries must also
exist in `.vscode/mcp.json`. The registry is hashed into repository context so
source changes invalidate stale downstream plans.

## Live GitHub Project execution gate

Before loading a DEV artifact for `/d365.implement`, run:

```powershell
python scripts/tracking_automation.py check-readiness `
  --issue-number <issue-number> `
  --expected-stage development `
  --expected-status Ready `
  --expected-stage-status "Ready for Build"
```

The read-only command fetches the live issue and every page of the configured
GitHub Project V2 items. It returns structured JSON containing the live
Lifecycle Stage, Status, Stage Status, artifact ID, Project item identity/URL,
and a `ready` or `blocked` result. Development Execution requires an open issue
with label `development`, exactly one Project item, and live
`Development / Ready / Ready for Build` values.

Project owner/number resolution is: explicit CLI options, then
`D365_PROJECT_OWNER` / `D365_PROJECT_NUMBER` environment variables, then
repository Actions variables read through authenticated `gh`. Missing
configuration or Project access blocks explicitly. Issue-body Project Status
and Stage Status values are creation snapshots only and are never used as a
fallback. The separate compiler-owned DEV front-matter gate requires
`status: ready`.
