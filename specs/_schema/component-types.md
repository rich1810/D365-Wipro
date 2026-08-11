# Component-type taxonomy (Phase 3 design → logical components)

A feature workspace `plan.md` must not collapse into one opaque blob. In its
components section, every entry has a stable `DES-##-CMP-###` identifier and one
**`component_type`** so the design reads as a set of cohesive,
independently-buildable logical components. All subordinate changes to one
component stay together, preventing a separate work item for every property,
field, step, or child configuration entry.

The closed vocabulary lives in `conventions.yml` under `component_types`; this file
is the human-readable reference and the **family → design-axis** mapping. Extend both
as the pattern evolves.

## Format

`<family>_<type>` (lowercase, snake_case). Some ids are parameterised:

| Parameterised id | Meaning | Examples |
|---|---|---|
| `config_ai_<type>_<type>` | Dataverse-stored AI-agent config | `config_ai_orderstatus_topic` |
| `mcs_<agentname>_<type>` | Copilot Studio agent artefact | `mcs_orderstatus_topic`, `mcs_orderstatus_action`, `mcs_orderstatus_knowledge` |
| `az_ai_<type>_<type>` | Azure AI component | `az_ai_foundry_agent`, `az_ai_openai_deployment` |
| `code_webres_<type>` | web-resource kind | `code_webres_js`, `code_webres_html`, `code_webres_css` |

## Families and types

| Family | Types | Authoring surface |
|---|---|---|
| **`schema_`** | `schema_table`, `schema_column`, `schema_relationship`, `schema_choice`, `schema_key` | Dataverse custom solution |
| **`config_`** | `config_sla`, `config_arc`, `config_bpf`, `config_business_rule`, `config_routing_rule`, `config_queue`, `config_assignment_rule`, `config_dup_detection`, `config_env_variable`, `config_audit`, `config_ai_<type>_<type>` | Dataverse / pac |
| **`uiux_`** | `uiux_form`, `uiux_view`, `uiux_dashboard`, `uiux_chart`, `uiux_sitemap`, `uiux_app` | pac |
| **`flow_`** | `flow_cloud` | pac |
| **`code_`** | `code_plugin`, `code_custom_api`, `code_pcf`, `code_webres_<type>` | pac / Azure |
| **`mcs_`** | `mcs_<agentname>_<type>` (topic, action, knowledge, …) | Copilot Studio |
| **`sec_`** | `sec_role`, `sec_field_profile`, `sec_business_unit`, `sec_team` | Dataverse / pac |
| **`az_`** | `az_apim`, `az_service_bus`, `az_key_vault`, `az_function_app`, `az_func_scheduled`, `az_app_insights`, `az_event_grid`, `az_ai_<type>_<type>` | Azure / Bicep |
| **`bi_`** | `bi_dataset`, `bi_report` | Power BI |
| **`integ_`** | `integ_connector`, `integ_connection_ref` | pac / Azure |

## Design-axis coverage

Every design axis (`conventions.yml` `decision_axes`) is realised by at least one
component family, so a fully-decomposed design leaves no axis un-buildable. The only
exception is `environment`, which is a cross-cutting Stage-6 (CI/CD ALM) concern, not
a per-component build artefact.

| Design axis | Realising component families |
|---|---|
| `logic_tier` | `config_` → `flow_` → `code_` (declarative-first ladder) |
| `data_residency` | `schema_` (Dataverse-native), `az_` / `integ_` (external) |
| `alm_boundary` | authoring-target routing; external build pipelines own export and promotion |
| `security` | `sec_` |
| `integration` | `integ_`, `az_` |
| `environment` | authoring-target routing for every Dataverse-bound component |
| `ux_surface` | `uiux_`, `code_pcf`, `code_webres_` |
| `observability` | `config_audit`, `az_app_insights` |
| `batch_processing` | `flow_cloud`, `az_func_scheduled`, `code_plugin`, `az_service_bus` |
| `reporting` | `uiux_dashboard`, `uiux_chart`, `bi_` |

## Skill library (map + required payload)

Each `component_type` is served by one **skill** — a reusable "how-to" for building
that kind of component, applied both when authoring a DES (Stage 3) and building it
(Stage 4). The skill grouping is a **domain grouping layered over the type prefix**,
so some `config_*` types are re-homed by concern (`config_audit` →
`dataverse-security`, `config_env_variable` → `alm-packaging`, `config_ai_*` →
`dataverse-ai`).

- **Map + rollout phases:** `conventions.yml` `component_type_skills` (skill → covered
  types) and `skill_phases` (A = active, B = deferred). All 44 types are mapped exactly
  once. Skills are stored in D365 Solution Craft: load the mapped skill by name from the
  registered Craft build-skill location, then use its thin `SKILL.md` router and
  matching `<component_type>.md` deep reference.
- **Required payload:** `conventions.yml` `component_type_payloads` lists the minimum
  fields each design item must declare in its `data_model` entry (falling back to
  `_default: [name, satisfies]`). This is the single source of truth read by the prompt
  files (guidance) and by `validate_design.py` (enforcement, Step validate-payload).
- **Custom connectors:** `integ_connector` = a **custom** connector you author (OpenAPI
  + auth + policies); standard/certified connectors need only an `integ_connection_ref`.
- **Integrity gate:** D365 Solution Craft's skill validator keeps the map,
  `component_type_payloads`, and registered build-skill library mutually consistent —
  every type is covered by exactly one skill, every payload key is a real type, and
  every Craft build-skill folder has a `SKILL.md` (with `name`/`description`) plus
  one `<component_type>.md` reference per covered type (wildcard `x_*` → `x.md`); no
  orphan reference files. Skills not yet authored (no folder) are skipped as deferred.

## Implementation-scope classification

`conventions.yml` `component_implementation_scopes` is Craft-owned and covers
all 44 registered types and parameterized patterns. The customer-owned
`.d365/authoring-targets.yml` supplies declared environment URLs and immutable
IDs, interactive authentication mode, optional connection aliases,
custom solutions, targets, and routing.

| Types or patterns | Scope |
| --- | --- |
| `config_queue`, `config_audit` | `repository_and_dataverse_environment` composite records |
| `schema_*`, `config_*` except `config_queue`/`config_audit`, `uiux_*`, `flow_*` | `repository_and_dataverse_solution` |
| `code_plugin`, `code_custom_api`, `code_pcf`, `code_webres_*` | `repository_and_dataverse_solution` |
| `mcs_*`, `sec_role`, `sec_field_profile` | `repository_and_dataverse_solution` |
| `integ_connector`, `integ_connection_ref` | `repository_and_dataverse_solution` |
| `sec_business_unit`, `sec_team` | `repository_and_dataverse_environment` data |
| `az_apim`, `az_service_bus`, `az_key_vault`, `az_function_app`, `az_func_scheduled`, `az_app_insights`, `az_event_grid`, `az_ai_*` | `repository_only` |
| `bi_dataset`, `bi_report` | `repository_only` |

Exact type mappings take precedence over family wildcards. Repository-only
work must not access Dataverse. Solution-bound work must use and verify the
declared custom solution and publisher. Environment-bound work verifies the
environment and must not invent a solution. Exact `config_queue` and
`config_audit` mappings override the `config_*` wildcard and do not claim
custom-solution membership. Custom publishers and unmanaged custom solutions
are administrator-created authoring prerequisites declared in the manifest,
not components. The canonical setup and routing contract is in Craft guide 02.

## Rules

- Tag **every** component in `FILL:solution` with exactly one `component_type` from
  the closed vocabulary (or a valid parameterised form). `validate_design.py`
  enforces this per component bullet — an untagged primary bullet in the
  `components:` list fails the gate, and incomplete component coverage fails
  feature-membership validation.
- **Declarative-first:** prefer a `config_` / `uiux_` / `flow_` type over a `code_` /
  `az_` type; any escalation to a pro-code type must carry the same grounded rationale
  the `logic_tier` axis records.
- Choose the **most specific** type that fits; if none fits, do not invent a type
  silently — raise it as an open question so the taxonomy can be extended in
  `conventions.yml` first.

## Design-item payload contract

Tagging a component with a `component_type` is only the **routing key**. For the
mapped skill to execute and for `validate_design.py` to enforce completeness, each
component must also **declare its required payload** — the minimum fields listed in
`conventions.yml` `component_type_payloads.<type>.required` (falling back to
`_default: [name, satisfies]`).

**Format.** In `plan.md` `FILL:components`, use one YAML fenced block whose
`components` list contains one mapping per logical component:

```yaml
components:
  - id: DES-01-CMP-001
    component_type: <type>
    name: <name>
    satisfies: [INTK-0001-REQ-001]
    <required_field>: <value>
```

Rules:

- Every component has one stable `DES-##-CMP-###` ID, one `component_type`,
  `name`, and `satisfies`.
- Every required field appears using the exact field name from
  `component_type_payloads` (no synonyms, no omissions, no invented fields).
- `satisfies` is a list of intake-scoped requirement IDs and must be a subset
  of the feature's `member_reqs`.
- Multi-valued fields may use an inline list for simple values or a YAML list of
  mappings when subordinate changes require detail. All changes to the same
  logical component stay in this payload.
- Parameterised types (`config_ai_*`, `code_webres_*`, `mcs_*`, `az_ai_*`) resolve to
  their concrete form and use that type's payload, or `_default` if none is detailed.
- Optional `depends_on`, `executor`, and `owner` fields drive DEV generation.
- Every `repository_and_dataverse_solution` component declares `schema_name`,
  the exact platform schema/logical/unique name used for creation and all later
  updates. Every `repository_and_dataverse_environment` component declares
  `record_name`, the exact record name used for live lookup. Display labels are
  not implementation identities.

**Worked example** (one new table with all initial columns):

```yaml
components:
  - id: DES-01-CMP-001
    component_type: schema_table
    name: Escalation
    schema_name: cr_escalation
    table: cr_escalation
    operation: create
    satisfies: [INTK-0001-REQ-005]
    ownership: user_team
    primary_name: cr_name
    columns:
      - name: cr_escalated
        data_type: Boolean
        required_level: optional
        behavior: simple
      - name: cr_escalation_notes
        data_type: Multiline Text
        required_level: optional
```

`validate_design.py` reads the same `component_type_payloads` map (Step
validate-payload) and fails any component missing a required field — the skill
guidance and the gate share one source of truth.

A `schema_column` component is valid for one independently reviewed column
change when no cohesive table-level change exists. Several column changes to
the same table default to one `schema_table` extension component. Apply the
same parent-component rule across families. Split only when a Development Lead
records an exceptional deployment, risk, ownership, sequencing, or review
reason and the authoritative plan boundary is revised before the Development
Planning PR.
