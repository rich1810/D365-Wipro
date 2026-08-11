# D365 project agent routing

This repository uses D365 Solution Craft Agent Skills for reusable delivery
methods and artifact authoring contracts. This file binds project issue types,
paths, automation, and human gates to those skills.

## Required setup

- Read `conventions.yml` before authoring or changing a lifecycle artifact.
- Treat schemas under `specs/_schema/` and scripts under `scripts/` as the
  project-owned deterministic contract.
- Validate `.d365/authoring-targets.yml` before Design, Development Planning,
  or Development Execution. It contains non-secret Dataverse environment URLs,
  immutable environment IDs, interactive authentication mode, optional
  connection aliases, custom solutions, targets, and routing. Never add
  credentials, tokens, client secrets, auth profiles, secret-bearing URLs, or
  promotion environments.
- Validate `.d365/development-resources.yml` before Design, Development
  Planning, or Development Execution. It globally defines approved MCPs,
  endpoints, CLIs, SDKs, portals, versions, deterministic component routing,
  and safe per-resource preflight procedures. Never invent or substitute an
  unregistered resource, command, installation, authentication, or manual
  procedure.
- Validate `.d365/dataverse-web-api-capabilities.yml` against its schema before
  Design, Development Planning, or Development Execution. It covers all
  registered component types and maps create/update/delete/verify/publish or
  solution actions to controlled methods, endpoint templates, explicit
  solution context, verification, fallback, rationale, and official references.
  Never invent a Web API payload or route that the matrix does not allow.
- Load skills from the registered D365 Solution Craft locations.
- If a required skill is unavailable, stop and report the missing skill. Do not
  reconstruct or improvise its procedure.
- `.github/workflows/copilot-setup-steps.yml` installs MarkItDown for a cloud
  runner; it does not install the externally registered Craft skills.
- Treat `.d365/spec-kit/compatibility.yml` as an exact runtime contract. Do not
  upgrade or substitute the pinned official `specify-cli` release.

## Global project safeguards

- Never edit, move, or delete raw evidence under `intake/`.
- Never invent facts, decisions, platform capabilities, or component types.
- Never hand-edit compiler-owned indexes, context projections, or hash fields.
- Never hand-edit compiler-owned implementation scope, execution host, or
  authoring-target fields in plan context, task context, `tasks.md`, or DEV
  artifacts.
- Address solution components only by compiler-validated `schema_name` and
  environment records only by `record_name`; display labels never select an
  implementation target.
- Never merge a lifecycle pull request. The named human gate owns the merge.
- Preserve the traceability chain:
  `INTK-#### -> INTK-####-GRP-## -> INTK-####-REQ-### -> FEAT-## -> DES-## -> DEV-####`.
  `GRP` is non-governing intake navigation and never replaces REQ provenance or
  dictates feature boundaries.

## Intake route

**Trigger:** an Intake issue with label `intake`.

**Required skills:**

1. `intake-conversion`

Before an Intake issue exists, `/d365.intake --create` invokes
`intake-creation` to collect source files, open the source-evidence PR, and let
merge automation create the issue from immutable default-branch content.

**Project bindings:**

- Input evidence: exact bytes downloaded to a fresh temporary directory from
  the issue's commit-pinned GitHub links under
  `intake/<customer>/<date>/`; never use workspace copies for conversion.
- Output: a registered `INTK-####` row in `intake/_index.md` and converted
  evidence.
- Submission: `/d365.intake #<issue-number> --submit` validates the reviewed
  intake and opens a focused pull request without approving or merging it.
- Human gate: intake acceptance; the agent does not merge.
- Next handoff: a Requirement issue linked to the accepted `INTK-####`.

## Requirement route

**Trigger:** a Requirement issue with label `requirement`.

**Required skills, in execution order:**

1. `intake-traceability` to stamp and verify the accepted intake relationship.
2. `atomic-requirement-authoring`.

**Project bindings:**

- Input: an accepted `INTK-####` and its converted evidence.
- Intake registry: `intake/_index.md`.
- Output: intake-scoped
  `specs/intakes/INTK-####/requirement-groups.yml` and
  `specs/intakes/INTK-####/requirements/INTK-####-REQ-###.md`.
- Contract: `specs/_schema/requirement-groups.schema.json` and
  `specs/_schema/req.schema.json`.
- Validation: `python scripts/validate_reqs.py`.
- Submission: `/d365.requirements #<issue-number> --submit` revalidates reviewed
  requirements and opens a focused pull request without approving or merging it.
- Pull request: `Requirement: <customer> <date>`.
- Human gate: requirement review approves the proposed grouping and atomic
  REQs together; the agent does not merge.

## Feature route

**Trigger:** a Feature issue with label `feature`.

**Required skills, in execution order:**

1. `requirement-refinement`
2. `open-question-handling`
3. `gherkin-authoring`
4. `feature-grouping`
5. `spec-compilation`

**Project bindings:**

- Input: reviewed intake-scoped requirement files named by the issue.
- Index: `specs/_index/features.md`.
- Output: native `specs/<sequence>-<feature-slug>/spec.md` workspaces.
- Artifact and FILL-zone contract: the `spec-compilation` skill.
- Structural contract: `specs/_schema/spec.schema.json`.
- Classification: `/d365.specify #<issue-number>` proposes and obtains human
  confirmation before changing feature membership or authoring `spec.md`.
- Intake requirement groups are contextual navigation only. A feature may
  contain REQs from multiple groups, and one group may feed multiple features.
- Compile and validate:

  ```text
  python scripts/compile_specs.py
  python scripts/compile_specs.py --check
  python scripts/validate_specs.py
  ```

- Submission: `/d365.specify #<issue-number> --submit` revalidates reviewed
  specs and opens a focused Feature PR without approving or merging it.
- Pull request: `Feature-spec: baseline vN`.
- Human gate: customer and architect Gate 2; the agent does not merge.

Stage 2 is solution-agnostic. Platform feasibility belongs to Design.

## Design route

**Trigger:** a Design issue with label `design`.

**Required skills:**

1. `decision-axes`
2. `grounded-architecture`
3. `component-decomposition`
4. `observability-design`
5. `design-compilation`
6. `open-question-handling` for recorded Stage-3 decisions

**Project bindings:**

- Input: one approved feature workspace `spec.md`.
- Output: the same workspace's authoritative `plan.md`; UX behavior belongs in
  `spec.md` and technical UX decisions belong in `plan.md`.
- FILL-zone and artifact contract: the `design-compilation` skill.
- Structural contract: `specs/_schema/plan.schema.json`.
- Component contract: `specs/_schema/component-types.md` plus
  `conventions.yml` `component_types`, `component_type_skills`, and
  `component_type_payloads`.
- Design sources: validate and load `.d365/design-sources.yml`. Microsoft Learn
  MCP and the current repository are mandatory; any additional MCP, knowledge
  source, or reference repository must be registered there.
- Authoring targets: validate `.d365/authoring-targets.yml`. Scope comes from
  Craft-owned `component_implementation_scopes`; target routing comes from the
  customer-owned manifest. Missing, ambiguous, or incompatible routing fails.
- Ground implementation decisions in Microsoft Learn and available live
  environment tools.
- Preparation: `/d365.plan #<issue-number>` authors and validates only the
  linked draft `plan.md`, then stops for architect review without prompting for
  submission.
- Submission: `/d365.plan #<issue-number> --submit` revalidates the reviewed
  plan and opens a focused Design PR without approving or merging it.
- Compile and validate:

  ```text
  python scripts/validate_design_sources.py
  python scripts/validate_authoring_targets.py
  python scripts/compile_design.py
  python scripts/compile_design.py --check
  python scripts/validate_design.py
  ```

- Pull request: `Design: FEAT-## <name>`.
- Human gate: architect Gate A for `plan.md`; the agent does not merge.

## Development route

**Trigger:** either a Development Planning or Development Execution issue with
label `development`.

**Planning:** `/d365.tasks #<issue-number>` generates `tasks.md`, task context,
and exactly one authoritative `development/DEV-####.md` per approved logical
`plan.md` component. The DEV artifact includes all subordinate changes to that
component; a table and its columns do not become separate work items. For every
component, resolve `component_type_skills`, invoke the
exact mapped skill from the registered Craft
`.github/skills/build/<build-skill>/SKILL.md` location, and read its
component-specific reference before defining payload, targets, validations,
acceptance, evidence, or checkpoints. A task profile alone is insufficient;
The compiler also resolves `development_resources` from the global registry;
every DEV artifact receives an immutable required/preferred/allowed/fallback
resource snapshot and a compiler-owned `Developer preflight` describing the
minimal safety gate, direct scoped action, and post-action verification. A task
profile alone is insufficient;
the compiler resolves `execution_host` from scope plus required-resource host
capabilities. Dataverse-bound tasks are `local_interactive`; repository-only
tasks are `cloud_or_local` only when all required resources support cloud.
`executor` is a separate agent/hybrid/human decision.
stop if the mapped Craft skill or reference is unavailable. Then stop for
Development Lead review. Explicit
`/d365.tasks #<issue-number> --submit` revalidates and opens the planning PR. Run
`python scripts/compile_tasks.py --check` and
`python scripts/validate_tasks.py`. A human Development Lead reviews the
planning PR before automation creates execution issues. The lead may request
an exceptional split for deployment, risk, ownership, sequencing, or review
reasons; record the reason, revise and review the authoritative plan boundary,
then regenerate before the final planning PR.

**Execution skill:** `/d365.implement #<issue-number>` resolves exactly one DEV
artifact and its `component_type` build skill through `conventions.yml`
`component_type_skills`, implements and validates within declared target roots,
then stops for inspection. `/d365.implement #<issue-number> --submit`
automatically re-proves the current session and opens the focused implementation
PR.
Stop if the mapped skill or task context is unavailable.
Resolved implementation scope and authoring target are compiler-owned and
cannot be adjusted during task review.
Before the live GitHub Project gate, resolve the issue's DEV reference and run
`validate_development_environment.py DEV-#### --host-requirements --json`,
automatically identify VS Code, Copilot CLI, or GitHub Copilot cloud from
authoritative runtime context, and inspect the current agent tool surface
against every compiler-required MCP server and tool. In VS Code the dynamic
tools attached to the current Chat/Agent session are authoritative; in Copilot
CLI use the current `/env` tool surface and `/mcp` state; in cloud use cloud
runtime identity. Cloud identity always wins over local hints. If names are
unscoped, map them only when exact ownership is unique; duplicate or colliding
tool names block. When needed, use only an actual safe read-only tools-list,
capability, describe, or metadata probe with minimal non-sensitive inputs.
Never fabricate a probe or use a Dataverse write.
Pass the ephemeral structured evidence through `--host-proof-stdin` or
`--host-proof-json` to the mandatory `--host-check-only` check. A
`local_interactive` DEV must automatically prove Copilot CLI or VS Code plus
all required MCP tools. GitHub Copilot cloud coding agent must stop immediately
for Dataverse-bound DEV work and hand off to local Copilot CLI or VS Code;
repository Settings MCP configuration cannot overcome the current
OAuth-authenticated remote MCP limitation. Repository-only `cloud_or_local`
tasks are eligible for cloud execution.
`--execution-host` and repeatable `--mcp-server-callable` are optional
diagnostic overrides only when automatic evidence is genuinely inaccessible;
they are never required in the normal path and cannot override contradictory
automatic evidence. All session evidence is ephemeral and is never written to
DEV artifacts, Git, issue bodies, environment variables, or persistent
configuration.
Before loading a DEV artifact, run the read-only live GitHub Project gate in
`scripts/tracking_automation.py check-readiness`. Require an open issue with
the `development` label, exactly one configured Project item, and live
`Lifecycle Stage=Development`, `Status=Ready`, and
`Stage Status=Ready for Build`. Issue-body status values are creation
snapshots only and are never a fallback. Separately require compiler-owned DEV
front-matter `status: ready`, current compiler hashes, completed dependencies,
valid resource/target routing, and the mapped build skill. Before implementation, run
`python scripts/validate_development_environment.py DEV-####` with the optional
per-run `--authentication-policy reuse_if_valid|always_prompt` override. The
CLI override takes precedence over the project default in
`.d365/development-resources.yml`. Safe automatic
probes may run noninteractively; installation never runs unless the registry
explicitly declares `automatic_safe` and the executor deliberately enables it.
`reuse_if_valid` reuses a valid exact-endpoint session, otherwise it requires
interactive authentication. `always_prompt` requires fresh interactive
authentication for every DEV execution. Launch only the snapshot's approved
interactive authentication, wait for the human browser/device flow, resume
validation, and block the first action on unavailable tools, version drift,
unresolved configuration, or absent exact-endpoint authentication. If an MCP or
host cannot force fresh authentication, give the exact targeted
sign-out/disconnect/reconnect instructions and use `waiting_for_human`;
never claim freshness, delete local profiles, or sign out unrelated sessions
automatically.

A live validator `ready` result means ready to attempt the exact scoped action,
not proof that every platform prerequisite exists. Do not export/unpack a
solution or enumerate environments, publishers, solutions, components,
permissions, component absence, or cross-solution membership before the first
action. Directly invoke only the DEV/build-skill action against the exact YAML
endpoint, solution/record target, and canonical `schema_name`/`record_name`.
Never fall back to an inferred environment or solution and never create or
repair environment, publisher, or solution prerequisites.

For solution-bound components, the compiler-owned capability operation selects
the primary resource. Supported solution-aware operations use
`dataverse-web-api` and `scripts/dataverse_web_api_executor.py`; the executor
accepts no arbitrary target, method, or path. Generic Dataverse MCP writes are
not primary because they do not guarantee a compiler-validated explicit
custom-solution context equivalent to the documented Web API mechanism. MCP is
restricted to routed `search`, `describe`, and `read_query` discovery or
post-action verification. Maker Portal is allowed only when the exact
capability permits fallback and sanitized issue evidence names the API
failure/unsupported payload. PAC is not generic component creation.

Web API authentication is current-human interactive delegated OAuth through
the configured non-secret public-client ID/tenant and exact pinned
`msal==1.37.0`, with `<environment-url>/user_impersonation`, loopback redirect,
and an in-memory token cache only. Unconfigured registration values are a
blocking customer/admin prerequisite. Never print/store a token or claim that
PAC exports one. HTTP `DELETE` destroys the environment component/row;
`RemoveSolutionComponent` removes unmanaged-solution membership only. Both
require exact explicit approval and are never automatically substituted.

Surface sanitized 401/403/404, allowlist/permission, conflict/duplicate,
unsupported-operation, and validation errors with the owning configuration or
administrator remediation. Stop with no further writes and retry only after
correction when the operation is idempotent. After each stopped action attempt,
immediately append the sanitized request/response evidence to the exact
Development Execution issue through `scripts/execution_evidence.py`; do not
retry if evidence posting fails. After a successful action,
target-read the canonical identity and verify payload outcomes, then append the
sanitized success evidence to the same exact issue. For
solution-bound writes, verify routed-solution membership and absence from other
declared custom solutions only post-write or for narrow conflict diagnosis.
Block mismatches without copy/move/remove/repair. Never export/unpack a whole
solution for verification.

The Development issue is the auditable work-order evidence surface. Each
append-only `d365-execution-evidence:v1` comment records a unique UTC attempt,
result, DEV/component/build skill, current task/source-plan hashes, selected
resource/tool/API, compiler-owned target and canonical identity, sanitized
request parameter names/identifiers, sanitized response status/code/immutable
ID/field names/non-secret correlation ID, verification results, remediation,
and write/stop status. “Request and response output” means this sanitized
evidence, not raw payload dumping. Never post tokens, cookies, Authorization
headers, refresh data, auth profiles, secrets, passwords, connection strings,
raw headers, full request/response bodies, raw records, customer content,
environment-variable values, file contents, or sensitive field values. If safe
sanitization is uncertain, withhold details and post only category/code,
non-secret correlation ID, and remediation. Never rewrite live Project fields,
human issue status text, or compiler-owned issue snapshot blocks.

`/d365.implement --submit` must use the helper's read-only `verify` mode to
require a current succeeded comment matching the exact issue, DEV ID,
`task_context_hash`, and `source_plan_hash`. Local DEV notes do not substitute.
Evidence comments are not compiler-owned and are never regenerated.

**Project bindings:**

- Input: one approved DEV artifact, its source plan component, and task context.
- Output: scoped solution configuration, targeted source, code, infrastructure,
  and build evidence for that DEV scope only.
- Repository-only DEV work must not access Dataverse. Solution-bound DEV work
  must use the declared custom solution; environment-
  bound DEV work must not invent a solution. Custom publishers and unmanaged
  custom solutions are administrator-created authoring prerequisites recorded
  in `.d365/authoring-targets.yml`, not components or DEV tasks. Solution
  export and downstream promotion are external-pipeline work and must not
  become plan components or DEV tasks. If targeted source synchronization is
  genuinely required by an existing profile, it occurs only after a successful
  scoped write and is not broad discovery export. Treat `config_queue` and
  `config_audit` as environment-bound composite records with no custom-solution membership.
- Dataverse-bound execution requires `authentication_mode: interactive_user`
  directly against the declared endpoint and no token recording. Refuse
  service-principal or unattended authentication. Block while either URL or ID
  is `CONFIGURE_BEFORE_IMPLEMENTATION`; an optional connection alias never
  replaces compiler-owned routing.
- Human gate: code or configuration review; the agent does not merge.
- Next handoff: a Test issue linked to the built components and build PR.

## Test, Release, and Operate routes

**Triggers:** issues with labels `test`, `release`, and `operate`.

Reusable test, release, and operations skills are not yet published (see
`taxonomy/component-types.yml` `skill_phases`). Until they are, follow the
issue form's contract (input, output, validation, gate, and handoff) and stop
to report any procedure that would otherwise be improvised.

## Deterministic ownership

- Compilers own generated indexes, contexts, projections, and artifact hashes.
- Agents author only the designated authored sections of REQ, spec, plan, and
  DEV artifacts.
- Validators and GitHub workflows decide structural conformance.
- Human reviewers decide requirement acceptance, open questions, architecture,
  and user experience approval.
