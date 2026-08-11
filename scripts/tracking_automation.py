#!/usr/bin/env python3
"""D365 Solution Craft tracking automation.

Implements the automation-boundary-safe rules from
`.d365/tracking/project-definition.yml` (`automation.rules`) for a consuming
implementation project:

  init-issue        Add a newly opened/labeled lifecycle issue to the
                    configured Project and initialize Lifecycle Stage,
                    Status, and Stage Status. Idempotent: Status and Stage
                    Status are only ever initialized (never reset) once
                    they already hold a value, so re-running this on a
                    later labeled event (for example a subsequent lifecycle
                    stage label) never discards progressed work.
  sync-validation   Reflect a definitive, stage-applicable validation
                    workflow conclusion (Passed/Failed) onto the Validation
                    field of every lifecycle issue the associated pull
                    request closes.
  detect-overdue    Flag Project items whose Planned Completion is in the
                    past and whose Status is not Done/Cancelled by setting
                    Health to "Off Track". Enumerates every item in the
                    Project via GraphQL pageInfo/endCursor pagination, so a
                    Project with more than 100 items is never silently
                    truncated to its first page.
  count-measurable  Report deterministic, generically computable counts
                    (open/closed issues per lifecycle stage label) to the
                    job summary. Stage-specific artifact counts (for example
                    requirements approved or tests passed) are project-owned
                    and are not fabricated here.
  check-readiness   Read one live issue and its configured GitHub Project V2
                    item, then fail closed unless the issue state, lifecycle
                    label, Lifecycle Stage, Status, and Stage Status exactly
                    match the requested activation gate. Issue-body status
                    snapshots are never used as workflow state.
  link-traceability Record the Parent Artifact reference found in an issue
                    body, and mark issues closed as not planned as Cancelled.
                    Done remains manual because it requires merged output,
                    passed validation, an approved gate, and a handoff.

This script never approves a gate, closes an open question, or merges a pull
request. Every human gate stays manual. It calls the `gh` CLI, which must be
authenticated with a token that can read and write the target Project
(`GH_TOKEN`, typically the `D365_PROJECT_TOKEN` repository secret described in
docs/04 - Configure Project Tracking.md). Every gh/GraphQL failure raises an
explicit error; nothing is silently swallowed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"::error::missing dependency: {exc}. Run: pip install pyyaml")
    sys.exit(2)

ROOT = Path.cwd()
DEFINITION_PATH = ROOT / ".d365" / "tracking" / "project-definition.yml"

ARTIFACT_ID_RE = re.compile(
    r"\b(?:INTK-\d{4}-REQ-\d{3}|(?:INTK|FEAT|DES|DEV|TEST|REL|OPS)-\d{2,4})\b"
)
VALIDATION_WORKFLOW_STAGES = {
    "Validate requirements (T1)": {"Requirement"},
    "Validate features (T1)": {"Feature"},
    "Validate design (T1)": {"Design"},
    "Validate development planning (T1)": {"Development"},
}
VALIDATION_WORKFLOW_PATHS = {
    "Validate requirements (T1)": re.compile(
        r"^(?:conventions\.yml|\.d365/spec-kit/compatibility\.yml|specs/_schema/|"
        r"intake/|specs/intakes/INTK-\d{4}/requirements/)"
    ),
    "Validate features (T1)": re.compile(
        r"^(?:conventions\.yml|\.d365/spec-kit/compatibility\.yml|specs/_schema/|"
        r"specs/intakes/|specs/\d{3}-[^/]+/spec\.md|specs/_index/repository-context\.json|"
        r"\.specify/context/spec-context\.json)"
    ),
    "Validate design (T1)": re.compile(
        r"^(?:conventions\.yml|\.d365/spec-kit/|specs/_schema/|"
        r"specs/\d{3}-[^/]+/(?:spec|plan)\.md|specs/_index/repository-context\.json|"
        r"\.specify/context/(?:spec|plan)-context\.json)"
    ),
    "Validate development planning (T1)": re.compile(
        r"^(?:conventions\.yml|\.d365/spec-kit/|specs/_schema/|"
        r"specs/_index/repository-context\.json|specs/\d{3}-[^/]+/(?:plan|tasks)\.md|"
        r"specs/\d{3}-[^/]+/development/DEV-\d{4}\.md|"
        r"\.specify/context/(?:plan|task)-context\.json)"
    ),
}
PARENT_PREFIXES_BY_STAGE = {
    "Requirement": {"INTK"},
    "Feature": {"REQ"},
    "Design": {"FEAT"},
    "Development": {"DES"},
    "Test": {"DEV"},
    "Release": {"TEST"},
    "Operate": {"REL"},
}


class TrackingAutomationError(RuntimeError):
    """Raised for any unrecoverable automation failure. Never swallowed."""


def run_gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise TrackingAutomationError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def graphql(query: str, **variables: object) -> dict:
    # Build the full {query, variables} request body as real JSON and pipe it
    # to `gh api graphql --input -`. Passing complex variables through -f/-F
    # (gh's magic type conversion only handles scalars) would flatten lists
    # and dicts to strings, silently corrupting inputs such as a paging
    # cursor alongside array/object variables.
    request_body = json.dumps({"query": query, "variables": variables})
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=request_body,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise TrackingAutomationError(f"gh api graphql failed:\n{result.stderr.strip()}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TrackingAutomationError(
            "gh api graphql returned invalid JSON."
        ) from exc
    if parsed.get("errors"):
        messages = "; ".join(e["message"] for e in parsed["errors"])
        raise TrackingAutomationError(f"GraphQL request returned errors: {messages}")
    return parsed["data"]


def load_definition() -> dict:
    if not DEFINITION_PATH.exists():
        raise TrackingAutomationError(
            f"Tracking definition not found: {DEFINITION_PATH}. "
            "Run the D365 Solution Craft initializer to install the tracking package."
        )
    return yaml.safe_load(DEFINITION_PATH.read_text(encoding="utf-8"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise TrackingAutomationError(f"required environment variable '{name}' is not set.")
    return value


def resolve_repository(explicit_repo: str = "") -> str:
    repo = explicit_repo.strip() or os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo:
        if repo.count("/") != 1:
            raise TrackingAutomationError(
                "repository must use the 'owner/repository' format."
            )
        return repo
    try:
        payload = json.loads(run_gh(["repo", "view", "--json", "nameWithOwner"]))
    except (TrackingAutomationError, json.JSONDecodeError) as exc:
        raise TrackingAutomationError(
            "repository identity is unavailable. Pass --repo owner/repository, "
            "set GITHUB_REPOSITORY, or run from an authenticated GitHub repository."
        ) from exc
    repo = str(payload.get("nameWithOwner") or "").strip()
    if repo.count("/") != 1:
        raise TrackingAutomationError(
            "gh repo view did not return a valid owner/repository identity."
        )
    return repo


def repository_variables(repo: str) -> dict[str, str]:
    try:
        payload = json.loads(
            run_gh(["variable", "list", "--repo", repo, "--json", "name,value"])
        )
    except (TrackingAutomationError, json.JSONDecodeError) as exc:
        raise TrackingAutomationError(
            f"repository Actions variables for '{repo}' could not be read with "
            "the current gh authentication."
        ) from exc
    if not isinstance(payload, list):
        raise TrackingAutomationError(
            f"gh variable list returned an invalid response for '{repo}'."
        )
    return {
        str(item.get("name")): str(item.get("value") or "")
        for item in payload
        if item.get("name")
    }


def resolve_project_configuration(
    *,
    repo: str,
    project_owner: str = "",
    project_number: int | str | None = None,
) -> dict[str, object]:
    owner = str(project_owner or "").strip()
    number_value = "" if project_number is None else str(project_number).strip()
    owner_source = "cli" if owner else ""
    number_source = "cli" if number_value else ""

    if not owner:
        owner = os.environ.get("D365_PROJECT_OWNER", "").strip()
        if owner:
            owner_source = "environment"
    if not number_value:
        number_value = os.environ.get("D365_PROJECT_NUMBER", "").strip()
        if number_value:
            number_source = "environment"

    if not owner or not number_value:
        variables = repository_variables(repo)
        if not owner:
            owner = variables.get("D365_PROJECT_OWNER", "").strip()
            if owner:
                owner_source = "repository_variable"
        if not number_value:
            number_value = variables.get("D365_PROJECT_NUMBER", "").strip()
            if number_value:
                number_source = "repository_variable"

    missing = []
    if not owner:
        missing.append("D365_PROJECT_OWNER")
    if not number_value:
        missing.append("D365_PROJECT_NUMBER")
    if missing:
        raise TrackingAutomationError(
            "live Project configuration is unavailable: "
            + ", ".join(missing)
            + " was not supplied by CLI, environment, or readable repository "
            "Actions variables."
        )
    try:
        number = int(number_value)
    except ValueError as exc:
        raise TrackingAutomationError(
            f"D365_PROJECT_NUMBER must be a positive integer; got '{number_value}'."
        ) from exc
    if number < 1:
        raise TrackingAutomationError(
            f"D365_PROJECT_NUMBER must be a positive integer; got '{number_value}'."
        )
    return {
        "owner": owner,
        "number": number,
        "owner_source": owner_source,
        "number_source": number_source,
    }


PROJECT_FIELDS_QUERY = """
query($login: String!, $number: Int!) {
  repositoryOwner(login: $login) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        number
        title
        url
        fields(first: 100) {
          nodes {
            ... on ProjectV2FieldCommon { id name dataType }
            ... on ProjectV2SingleSelectField { id name dataType options { id name } }
          }
        }
      }
    }
  }
}
"""

# Shared between the paginated project-items query and the single-item
# lookup below so both report field values identically.
ITEM_FIELD_VALUES_FRAGMENT = """
    id
    content {
      ... on Issue {
        id
        number
        title
        url
        state
        repository { nameWithOwner }
      }
    }
    fieldValues(first: 50) {
      nodes {
        __typename
        ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
      }
    }
"""

PROJECT_ITEMS_PAGE_QUERY = f"""
query($login: String!, $number: Int!, $after: String) {{
  repositoryOwner(login: $login) {{
    ... on ProjectV2Owner {{
      projectV2(number: $number) {{
        items(first: 100, after: $after) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{
{ITEM_FIELD_VALUES_FRAGMENT}
          }}
        }}
      }}
    }}
  }}
}}
"""

ISSUE_COUNTS_QUERY = """
query($owner: String!, $name: String!, $label: String!) {
  repository(owner: $owner, name: $name) {
    open: issues(states: OPEN, labels: [$label]) { totalCount }
    closed: issues(states: CLOSED, labels: [$label]) { totalCount }
  }
}
"""

ITEM_FIELD_VALUES_QUERY = f"""
query($id: ID!) {{
  node(id: $id) {{
    ... on ProjectV2Item {{
{ITEM_FIELD_VALUES_FRAGMENT}
    }}
  }}
}}
"""


def get_project(owner: str, number: int) -> dict:
    data = graphql(PROJECT_FIELDS_QUERY, login=owner, number=number)
    project = (data.get("repositoryOwner") or {}).get("projectV2")
    if not project:
        raise TrackingAutomationError(
            f"Project #{number} was not found for owner '{owner}', or the token cannot access it."
        )
    return project


def get_all_project_items(owner: str, number: int) -> list[dict]:
    """Return every item in the project.

    Pages through items(first: 100, after: $cursor) using pageInfo/endCursor
    until hasNextPage is false, so a project with more than 100 items (for
    example, overdue detection against a large backlog) is never silently
    truncated to its first page.
    """
    items: list[dict] = []
    cursor: str | None = None
    while True:
        data = graphql(PROJECT_ITEMS_PAGE_QUERY, login=owner, number=number, after=cursor)
        project = (data.get("repositoryOwner") or {}).get("projectV2")
        if not project:
            raise TrackingAutomationError(
                f"Project #{number} was not found for owner '{owner}', or the token cannot access it."
            )
        connection = project["items"]
        items.extend(connection["nodes"])
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return items


def get_item_field_values(item_id: str) -> dict:
    """Return the current {field name: value} map for a single project item.

    Looked up directly by item id (a single GraphQL node query) rather than
    by scanning every project item, so callers such as init-issue's
    idempotency check stay cheap regardless of how many items the project
    holds.
    """
    data = graphql(ITEM_FIELD_VALUES_QUERY, id=item_id)
    node = data.get("node") or {}
    return project_item_field_values(node)


def project_item_field_values(item: dict) -> dict[str, object]:
    """Parse supported Project field value types from an item snapshot."""
    values: dict[str, object] = {}
    for fv in (item.get("fieldValues") or {}).get("nodes", []):
        fname = (fv.get("field") or {}).get("name")
        if not fname:
            continue
        if fv.get("name") is not None:
            values[fname] = fv["name"]
        elif fv.get("text") is not None:
            values[fname] = fv["text"]
        elif fv.get("date") is not None:
            values[fname] = fv["date"]
    return values


def field_map(project: dict) -> dict:
    return {f["name"]: f for f in project["fields"]["nodes"]}


def add_item(project_id: str, content_id: str) -> str:
    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    data = graphql(mutation, projectId=project_id, contentId=content_id)
    return data["addProjectV2ItemById"]["item"]["id"]


def set_single_select(project_id: str, item_id: str, field: dict, option_name: str) -> None:
    option = next((o for o in field.get("options", []) if o["name"] == option_name), None)
    if not option:
        raise TrackingAutomationError(f"field '{field['name']}' has no option named '{option_name}'.")
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
        value: { singleSelectOptionId: $optionId }
      }) { projectV2Item { id } }
    }
    """
    graphql(mutation, projectId=project_id, itemId=item_id, fieldId=field["id"], optionId=option["id"])


def set_text(project_id: str, item_id: str, field: dict, text: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $text: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
        value: { text: $text }
      }) { projectV2Item { id } }
    }
    """
    graphql(mutation, projectId=project_id, itemId=item_id, fieldId=field["id"], text=text)


def clear_field(project_id: str, item_id: str, field: dict) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!) {
      clearProjectV2ItemFieldValue(input: {
        projectId: $projectId, itemId: $itemId, fieldId: $fieldId
      }) { projectV2Item { id } }
    }
    """
    graphql(mutation, projectId=project_id, itemId=item_id, fieldId=field["id"])


def get_issue(number: int, repo: str = "") -> dict:
    args = [
        "issue", "view", str(number),
        "--json", "id,number,title,body,labels,state,stateReason,url",
    ]
    if repo:
        args.extend(["--repo", repo])
    output = run_gh(args)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise TrackingAutomationError(
            f"gh issue view returned invalid JSON for issue #{number}."
        ) from exc


def get_pr_files(number: str | int) -> list[str]:
    output = run_gh([
        "api",
        f"repos/{require_env('GITHUB_REPOSITORY')}/pulls/{number}/files",
        "--paginate",
        "--slurp",
    ])
    pages = json.loads(output)
    return [
        str(item["filename"])
        for page in pages
        if isinstance(page, list)
        for item in page
    ]


def stage_for_issue(definition: dict, issue: dict, preferred_label: str = "") -> dict | None:
    label_names = {label["name"] for label in issue.get("labels", [])}
    matches = [
        stage for stage in definition["stages"]
        if stage["label"]["name"] in label_names
    ]
    if len(matches) > 1:
        matched_labels = ", ".join(stage["label"]["name"] for stage in matches)
        raise TrackingAutomationError(
            f"issue #{issue['number']} carries multiple lifecycle stage labels "
            f"({matched_labels}); remove obsolete labels before initializing it."
        )
    if not matches:
        return None
    stage = matches[0]
    if preferred_label and stage["label"]["name"] != preferred_label:
        raise TrackingAutomationError(
            f"issue #{issue['number']} no longer carries the triggering lifecycle "
            f"label '{preferred_label}'."
        )
    return stage


def expected_stage(definition: dict, stage_reference: str) -> dict:
    normalized = stage_reference.strip().casefold()
    stage = next(
        (
            item
            for item in definition["stages"]
            if normalized in {
                str(item.get("id") or "").casefold(),
                str(item.get("name") or "").casefold(),
            }
        ),
        None,
    )
    if not stage:
        raise TrackingAutomationError(
            f"expected lifecycle stage '{stage_reference}' is not defined in "
            ".d365/tracking/project-definition.yml."
        )
    return stage


def artifact_ids_for_stage(issue: dict, stage: dict) -> list[str]:
    artifact_format = str(stage.get("artifact_id_format") or "")
    if not artifact_format:
        return []
    escaped = re.escape(artifact_format)
    pattern = re.sub(
        r"%0?(\d*)d",
        lambda match: rf"\d{{{match.group(1)}}}" if match.group(1) else r"\d+",
        escaped,
    )
    text = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
    return sorted(set(re.findall(rf"\b{pattern}\b", text)))


def blocked_readiness_result(message: str, issue_number: int) -> dict:
    return {
        "result": "blocked",
        "ready": False,
        "issue": {"number": issue_number},
        "project": None,
        "expected": None,
        "live": {
            "lifecycle_stage": None,
            "status": None,
            "stage_status": None,
            "artifact_id": None,
            "artifact_id_source": None,
            "project_artifact_id": None,
            "project_item_id": None,
            "project_item_url": None,
        },
        "blockers": [message],
        "authority": (
            "Only live GitHub issue and GitHub Project V2 fields govern this gate; "
            "issue-body status snapshots are non-authoritative."
        ),
    }


def evaluate_live_readiness(args: argparse.Namespace) -> dict:
    definition = load_definition()
    stage = expected_stage(definition, args.expected_stage)
    expected_status = args.expected_status.strip()
    expected_stage_status = args.expected_stage_status.strip()
    canonical_statuses = {
        str(option.get("name") or "")
        for option in (definition.get("status") or {}).get("options", [])
    }
    if expected_status not in canonical_statuses:
        raise TrackingAutomationError(
            f"expected Status '{expected_status}' is not defined in the tracking contract."
        )
    if expected_stage_status not in (stage.get("stage_status_flow") or []):
        raise TrackingAutomationError(
            f"expected Stage Status '{expected_stage_status}' is not defined for "
            f"Lifecycle Stage '{stage['name']}'."
        )

    repo = resolve_repository(args.repo)
    config = resolve_project_configuration(
        repo=repo,
        project_owner=args.project_owner,
        project_number=args.project_number,
    )
    issue = get_issue(args.issue_number, repo)
    project = get_project(str(config["owner"]), int(config["number"]))
    items = get_all_project_items(str(config["owner"]), int(config["number"]))
    matching_items = [
        item
        for item in items
        if (item.get("content") or {}).get("id") == issue.get("id")
    ]
    artifact_ids = artifact_ids_for_stage(issue, stage)
    label_names = sorted(
        str(label.get("name") or "")
        for label in issue.get("labels", [])
        if label.get("name")
    )
    required_label = str((stage.get("label") or {}).get("name") or "")
    blockers: list[str] = []

    if issue.get("state") != "OPEN":
        blockers.append(
            f"issue #{args.issue_number} is {issue.get('state') or 'unknown'}, not OPEN."
        )
    if required_label not in label_names:
        blockers.append(
            f"issue #{args.issue_number} is missing required label '{required_label}'."
        )
    if len(artifact_ids) != 1:
        blockers.append(
            f"issue #{args.issue_number} must identify exactly one "
            f"{stage['artifact_id_format']} artifact; found {len(artifact_ids)}."
        )
    if len(matching_items) != 1:
        blockers.append(
            f"issue #{args.issue_number} must resolve to exactly one item in "
            f"Project #{config['number']} owned by '{config['owner']}'; "
            f"found {len(matching_items)}."
        )

    item = matching_items[0] if len(matching_items) == 1 else {}
    values = project_item_field_values(item)
    live_stage = values.get("Lifecycle Stage")
    live_status = values.get("Status")
    live_stage_status = values.get("Stage Status")
    project_artifact_id = values.get("Artifact ID")
    issue_artifact_id = artifact_ids[0] if len(artifact_ids) == 1 else None
    if len(matching_items) == 1:
        if live_stage != stage["name"]:
            blockers.append(
                f"live Lifecycle Stage is '{live_stage}', expected '{stage['name']}'."
            )
        if live_status != expected_status:
            blockers.append(
                f"live Status is '{live_status}', expected '{expected_status}'."
            )
        if live_stage_status != expected_stage_status:
            blockers.append(
                f"live Stage Status is '{live_stage_status}', expected "
                f"'{expected_stage_status}'."
            )

    content = item.get("content") or {}
    result = {
        "result": "blocked" if blockers else "ready",
        "ready": not blockers,
        "issue": {
            "number": issue.get("number"),
            "node_id": issue.get("id"),
            "url": issue.get("url"),
            "title": issue.get("title"),
            "state": issue.get("state"),
            "labels": label_names,
            "repository": repo,
        },
        "project": {
            "owner": config["owner"],
            "number": config["number"],
            "owner_source": config["owner_source"],
            "number_source": config["number_source"],
            "node_id": project.get("id"),
            "title": project.get("title"),
            "url": project.get("url"),
            "matching_item_count": len(matching_items),
        },
        "expected": {
            "lifecycle_stage_id": stage.get("id"),
            "lifecycle_stage": stage.get("name"),
            "status": expected_status,
            "stage_status": expected_stage_status,
            "issue_state": "OPEN",
            "label": required_label,
            "project_item_count": 1,
        },
        "live": {
            "lifecycle_stage": live_stage,
            "status": live_status,
            "stage_status": live_stage_status,
            "artifact_id": project_artifact_id or issue_artifact_id,
            "artifact_id_source": (
                "project_field"
                if project_artifact_id
                else ("issue_identity" if issue_artifact_id else None)
            ),
            "project_artifact_id": project_artifact_id,
            "artifact_ids": artifact_ids,
            "project_item_id": item.get("id"),
            "project_item_url": content.get("url"),
            "project_item_repository": (
                content.get("repository") or {}
            ).get("nameWithOwner"),
        },
        "blockers": blockers,
        "authority": (
            "Only live GitHub issue and GitHub Project V2 fields govern this gate; "
            "issue-body Project Status and Stage Status values are creation snapshots "
            "and are non-authoritative."
        ),
    }
    return result


def cmd_check_readiness(args: argparse.Namespace) -> int:
    try:
        result = evaluate_live_readiness(args)
    except TrackingAutomationError as exc:
        result = blocked_readiness_result(str(exc), args.issue_number)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 1


def cmd_init_issue(args: argparse.Namespace) -> None:
    owner = require_env("D365_PROJECT_OWNER")
    number = int(require_env("D365_PROJECT_NUMBER"))
    definition = load_definition()

    issue = get_issue(args.issue_number)
    stage = stage_for_issue(definition, issue, args.stage_label)
    if not stage:
        print(f"::notice::issue #{args.issue_number} carries no lifecycle stage label; skipped.")
        return

    project = get_project(owner, number)
    fields = field_map(project)
    # addProjectV2ItemById is idempotent: adding an issue already in the
    # project returns its existing item id rather than creating a duplicate.
    # This command therefore runs safely on every labeled event, including a
    # later stage label landing on an issue that has already progressed.
    item_id = add_item(project["id"], issue["id"])

    # Status and Stage Status must only ever be *initialized*, never reset:
    # unconditionally setting them here would silently discard progressed
    # work (for example Status=In Progress, Stage Status=Converting
    # Evidence) every time any label event re-triggers this command. GitHub
    # may assign a built-in default such as "Todo" when adding an item; that
    # noncanonical value is initialization noise rather than lifecycle
    # progress and must be replaced with Backlog.
    existing_values = get_item_field_values(item_id)

    set_single_select(project["id"], item_id, fields["Lifecycle Stage"], stage["name"])

    existing_status = existing_values.get("Status")
    canonical_statuses = {option["name"] for option in definition["status"]["options"]}
    if existing_status in canonical_statuses:
        status_note = f"unchanged ({existing_status})"
    else:
        set_single_select(project["id"], item_id, fields["Status"], "Backlog")
        status_note = "Backlog" if not existing_status else f"Backlog (replaced noncanonical {existing_status})"

    if existing_values.get("Stage Status"):
        stage_status_note = f"unchanged ({existing_values['Stage Status']})"
    else:
        set_text(project["id"], item_id, fields["Stage Status"], stage["stage_status_flow"][0])
        stage_status_note = stage["stage_status_flow"][0]

    print(
        f"Initialized issue #{args.issue_number}: Lifecycle Stage={stage['name']}, "
        f"Status={status_note}, Stage Status={stage_status_note}."
    )


def cmd_sync_validation(args: argparse.Namespace) -> None:
    owner = require_env("D365_PROJECT_OWNER")
    number = int(require_env("D365_PROJECT_NUMBER"))

    if args.conclusion not in {"success", "failure"}:
        print(
            f"::notice::workflow conclusion '{args.conclusion}' is not definitive; "
            "Validation was not changed."
        )
        return
    if not args.pr_number:
        print("::notice::workflow_run has no associated pull request; nothing to sync.")
        return
    applicable_stages = VALIDATION_WORKFLOW_STAGES.get(args.workflow_name)
    if not applicable_stages:
        raise TrackingAutomationError(
            f"workflow '{args.workflow_name}' is not a configured lifecycle validator."
        )
    path_pattern = VALIDATION_WORKFLOW_PATHS.get(args.workflow_name)
    if not path_pattern:
        raise TrackingAutomationError(
            f"workflow '{args.workflow_name}' has no configured path applicability contract."
        )
    changed_paths = get_pr_files(args.pr_number)
    if not any(path_pattern.search(path) for path in changed_paths):
        print(
            f"::notice::workflow '{args.workflow_name}' completed successfully but its "
            "validator scope did not apply to this pull request; Validation was not changed."
        )
        return

    pr = json.loads(run_gh(["pr", "view", args.pr_number, "--json", "body,number"]))
    issue_numbers = {int(n) for n in re.findall(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", pr.get("body") or "", re.IGNORECASE)}
    if not issue_numbers:
        print(f"::notice::PR #{pr['number']} does not close any issue; nothing to sync.")
        return

    result = "Passed" if args.conclusion == "success" else "Failed"
    definition = load_definition()
    project = get_project(owner, number)
    fields = field_map(project)

    for issue_number in sorted(issue_numbers):
        issue = get_issue(issue_number)
        stage = stage_for_issue(definition, issue)
        if not stage:
            print(f"::notice::issue #{issue_number} has no lifecycle stage label; skipped.")
            continue
        if stage["name"] not in applicable_stages:
            print(
                f"::notice::workflow '{args.workflow_name}' does not apply to "
                f"{stage['name']} issue #{issue_number}; skipped."
            )
            continue
        item_id = add_item(project["id"], issue["id"])
        set_single_select(project["id"], item_id, fields["Validation"], result)
        print(f"Set Validation={result} on issue #{issue_number} from workflow '{args.workflow_name}' ({args.conclusion}).")


def cmd_detect_overdue(_args: argparse.Namespace) -> None:
    owner = require_env("D365_PROJECT_OWNER")
    number = int(require_env("D365_PROJECT_NUMBER"))

    project = get_project(owner, number)
    fields = field_map(project)
    items = get_all_project_items(owner, number)
    today = date.today()
    flagged = 0

    for item in items:
        values = project_item_field_values(item)

        status = values.get("Status")
        planned = values.get("Planned Completion")
        health = values.get("Health")
        if status in (None, "Done", "Cancelled") or not planned:
            continue
        try:
            planned_date = datetime.strptime(planned, "%Y-%m-%d").date()
        except ValueError:
            continue
        if planned_date < today and health != "Off Track":
            set_single_select(project["id"], item["id"], fields["Health"], "Off Track")
            issue_number = (item.get("content") or {}).get("number", "?")
            print(f"Flagged issue #{issue_number} Off Track: Planned Completion {planned} has passed.")
            flagged += 1

    print(f"Overdue detection complete: {flagged} item(s) flagged across {len(items)} project item(s).")


def cmd_count_measurable(_args: argparse.Namespace) -> None:
    definition = load_definition()
    repo = require_env("GITHUB_REPOSITORY")
    try:
        owner, name = repo.split("/", 1)
    except ValueError as exc:
        raise TrackingAutomationError(
            "GITHUB_REPOSITORY must use the 'owner/repository' format."
        ) from exc
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    lines = ["# Tracking measurable counts", "", "| Stage | Open | Closed |", "| --- | ---: | ---: |"]
    for stage in definition["stages"]:
        label = stage["label"]["name"]
        data = graphql(ISSUE_COUNTS_QUERY, owner=owner, name=name, label=label)
        repository = data.get("repository")
        if not repository:
            raise TrackingAutomationError(
                f"repository '{repo}' was not found, or the token cannot access it."
            )
        open_count = repository["open"]["totalCount"]
        closed_count = repository["closed"]["totalCount"]
        lines.append(f"| {stage['name']} | {open_count} | {closed_count} |")

    lines.append("")
    lines.append(
        "Stage-specific artifact counts (requirements approved, tests passed, and "
        "similar measures) are project-owned and are not computed generically here."
    )
    report = "\n".join(lines)
    print(report)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


def cmd_link_traceability(args: argparse.Namespace) -> None:
    owner = require_env("D365_PROJECT_OWNER")
    number = int(require_env("D365_PROJECT_NUMBER"))
    issue = get_issue(args.issue_number)
    definition = load_definition()
    stage = stage_for_issue(definition, issue)
    if not stage:
        print(f"::notice::issue #{args.issue_number} has no lifecycle stage label; skipped.")
        return

    project = get_project(owner, number)
    fields = field_map(project)
    item_id = add_item(project["id"], issue["id"])

    allowed_prefixes = PARENT_PREFIXES_BY_STAGE.get(stage["name"], set())
    parent_reference = next(
        (
            match.group(0)
            for match in ARTIFACT_ID_RE.finditer(issue.get("body") or "")
            if (
                ("REQ" if "-REQ-" in match.group(0) else match.group(0).split("-", 1)[0])
                in allowed_prefixes
            )
        ),
        None,
    )
    if parent_reference:
        set_text(project["id"], item_id, fields["Parent Artifact"], parent_reference)
        print(f"Set Parent Artifact={parent_reference} on issue #{args.issue_number}.")
    elif stage["name"] == "Intake":
        clear_field(project["id"], item_id, fields["Parent Artifact"])
        print(f"::notice::Intake issue #{args.issue_number} has no upstream Parent Artifact.")
    else:
        clear_field(project["id"], item_id, fields["Parent Artifact"])
        expected = "/".join(sorted(allowed_prefixes))
        print(
            f"::notice::no {expected} upstream artifact reference found in "
            f"{stage['name']} issue #{args.issue_number} body."
        )

    if issue.get("state") == "CLOSED":
        if issue.get("stateReason") == "NOT_PLANNED":
            set_single_select(project["id"], item_id, fields["Status"], "Cancelled")
            print(f"Set Status=Cancelled on closed issue #{args.issue_number}.")
        else:
            print(
                f"::notice::issue #{args.issue_number} was closed as completed; "
                "Status remains unchanged because Done requires manual gate verification."
            )


COMMANDS = {
    "init-issue": cmd_init_issue,
    "check-readiness": cmd_check_readiness,
    "sync-validation": cmd_sync_validation,
    "detect-overdue": cmd_detect_overdue,
    "count-measurable": cmd_count_measurable,
    "link-traceability": cmd_link_traceability,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_p = subparsers.add_parser("init-issue")
    init_p.add_argument("--issue-number", type=int, required=True)
    init_p.add_argument("--stage-label", required=False, default="")

    readiness_p = subparsers.add_parser("check-readiness")
    readiness_p.add_argument("--issue-number", type=int, required=True)
    readiness_p.add_argument("--expected-stage", required=True)
    readiness_p.add_argument("--expected-status", required=True)
    readiness_p.add_argument("--expected-stage-status", required=True)
    readiness_p.add_argument("--project-owner", default="")
    readiness_p.add_argument("--project-number", type=int)
    readiness_p.add_argument("--repo", default="")

    sync_p = subparsers.add_parser("sync-validation")
    sync_p.add_argument("--workflow-name", required=True)
    sync_p.add_argument("--conclusion", required=True)
    sync_p.add_argument("--pr-number", required=False, default="")

    subparsers.add_parser("detect-overdue")
    subparsers.add_parser("count-measurable")

    link_p = subparsers.add_parser("link-traceability")
    link_p.add_argument("--issue-number", type=int, required=True)

    args = parser.parse_args()
    try:
        exit_code = COMMANDS[args.command](args)
    except TrackingAutomationError as exc:
        print(f"::error::{exc}")
        return 1
    return exit_code if isinstance(exit_code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
