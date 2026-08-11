#!/usr/bin/env python3
"""Create automation-boundary-safe lifecycle handoff issues after PR merge."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"::error::missing dependency: {exc}. Run: pip install pyyaml")
    sys.exit(2)

import pipeline_common as P


class HandoffError(RuntimeError):
    """Raised when a required handoff cannot be created safely."""


def run_gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise HandoffError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandoffError(f"required environment variable '{name}' is not set.")
    return value


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        raise HandoffError(f"{path.as_posix()} is missing YAML front-matter.")
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise HandoffError(f"{path.as_posix()} front-matter must be a mapping.")
    return data


def pull_file_records(repo: str, pr_number: int) -> list[dict]:
    output = run_gh([
        "api",
        f"repos/{repo}/pulls/{pr_number}/files",
        "--paginate",
        "--slurp",
    ])
    pages = json.loads(output)
    records = []
    for page in pages:
        if isinstance(page, list):
            records.extend(page)
    return records


def changed_files(repo: str, pr_number: int) -> list[str]:
    return sorted({str(item["filename"]) for item in pull_file_records(repo, pr_number)})


def pull_request(repo: str, pr_number: int) -> dict:
    return json.loads(run_gh(["api", f"repos/{repo}/pulls/{pr_number}"]))


def intake_evidence_metadata(body: str) -> dict | None:
    match = re.search(
        r"<!--\s*d365-intake-evidence\s*\n(.*?)\n\s*-->",
        body,
        re.DOTALL,
    )
    if not match:
        return None
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise HandoffError("d365-intake-evidence metadata must be a YAML mapping.")
    required = {"customer", "intake_date", "intake_path"}
    missing = sorted(required - set(data))
    if missing:
        raise HandoffError(
            "d365-intake-evidence metadata is missing: " + ", ".join(missing)
        )
    return data


def create_intake_from_evidence(
    repo: str,
    pr_number: int,
    merge_sha: str,
    records: list[dict],
    metadata: dict,
) -> list[str]:
    customer = str(metadata["customer"]).strip()
    intake_date = str(metadata["intake_date"]).strip()
    intake_path = str(metadata["intake_path"]).strip().replace("\\", "/")
    if not customer:
        raise HandoffError("intake evidence customer cannot be empty.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", intake_date):
        raise HandoffError("intake_date must use yyyy-mm-dd.")
    if not re.fullmatch(r"intake/[a-z0-9]+(?:-[a-z0-9]+)*/\d{4}-\d{2}-\d{2}/", intake_path):
        raise HandoffError(
            "intake_path must match intake/<customer-slug>/<yyyy-mm-dd>/."
        )
    if not intake_path.endswith(f"/{intake_date}/"):
        raise HandoffError("intake_path date must match intake_date.")

    paths = sorted({str(item["filename"]) for item in records})
    if not paths:
        raise HandoffError("intake evidence PR contains no files.")
    invalid = [
        str(item["filename"])
        for item in records
        if item.get("status") != "added"
        or not str(item["filename"]).startswith(intake_path)
        or str(item["filename"]).endswith("/")
    ]
    if invalid:
        raise HandoffError(
            "intake evidence PR must add only new files under "
            f"{intake_path}: {', '.join(sorted(invalid))}"
        )

    assignee = require_env("D365_BA_ASSIGNEE")
    links = "\n".join(
        f"- [{Path(path).name}]({artifact_url(repo, merge_sha, path)})"
        for path in paths
    )
    ambiguities = str(metadata.get("known_ambiguities") or "None recorded.").strip()
    planned = str(metadata.get("planned_completion") or "").strip()
    marker = f"d365-intake-source:{merge_sha}"
    body = f"""### Customer / Workstream

{customer}

### Intake path

{intake_path}

### Source documents and links

{links}

### Artifact ID



### Known ambiguities, priorities, or exclusions

{ambiguities}

### Planned completion

{planned}

### Stage Status

Received

### Gate

Intake acceptance

### Downstream handoff

Not yet created. It will be generated after the accepted intake PR merges.

### Completion checklist

- [x] Source documents are committed under the intake path.
- [x] The document list above is complete.
- [ ] Scanned, image-only, empty, or garbled files are identified for escalation.
- [ ] The intake batch is registered in intake/_index.md with a sequential INTK-#### id.
- [ ] Intake acceptance gate is approved and recorded on this issue.
- [ ] The downstream Requirement issue exists and is linked above.
"""
    url = create_issue(
        repo,
        title=f"Intake: {customer} {intake_date}",
        label="intake",
        assignee=assignee,
        body=body,
        marker=marker,
    )
    return [url] if url else []


def added_intake_ids(repo: str, pr_number: int) -> list[str]:
    ids = set()
    for item in pull_file_records(repo, pr_number):
        if item.get("filename") != "intake/_index.md":
            continue
        for line in str(item.get("patch") or "").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                ids.update(re.findall(r"\bINTK-\d{4}\b", line))
    if not ids:
        raise HandoffError(
            "the merged intake PR changed intake/_index.md but no added INTK-#### "
            "registry entry was found in the PR patch"
        )
    return sorted(ids)


def artifact_url(repo: str, merge_sha: str, path: str) -> str:
    return f"https://github.com/{repo}/blob/{merge_sha}/{quote(path, safe='/')}"


def issue_exists(repo: str, marker: str) -> bool:
    query = f'repo:{repo} "{marker}" in:body'
    result = json.loads(run_gh([
        "api",
        "--method",
        "GET",
        "search/issues",
        "-f",
        f"q={query}",
        "-f",
        "per_page=1",
    ]))
    return bool(result.get("total_count"))


def create_issue(
    repo: str,
    *,
    title: str,
    label: str,
    assignee: str,
    body: str,
    marker: str,
) -> str | None:
    if issue_exists(repo, marker):
        print(f"::notice::handoff '{marker}' already exists; skipped.")
        return None
    args = [
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--label",
        label,
        "--assignee",
        assignee,
        "--body",
        body.rstrip() + f"\n\n<!-- {marker} -->\n",
    ]
    return run_gh(args).strip()


def handoff_body(
    *,
    purpose: str,
    parent: str,
    artifacts: list[tuple[str, str]],
    pr_number: int,
    marker: str,
    instructions: str,
) -> str:
    links = "\n".join(f"- [{path}]({url})" for path, url in artifacts)
    return f"""## Automated handoff draft

**Purpose:** {purpose}
**Parent artifact:** {parent}
**Merged pull request:** #{pr_number}
**Initial Project Status (creation snapshot):** Backlog
**Initial Stage Status (creation snapshot):** Handoff Draft

These issue-body values are immutable handoff context, not current workflow
state. The live fields on the linked GitHub Project govern activation and may
later differ. Do not keep this snapshot synchronized or use it as a readiness
fallback.

### Permanent artifact links

{links}

### Human activation required

{instructions}

Automation created this draft from merged, immutable content. It did not
approve a gate, infer readiness, or move the issue to Ready.
"""


PREFLIGHT_ISSUE_BLOCK = re.compile(
    r"<!-- D365:BEGIN DEVELOPER-PREFLIGHT -->.*?"
    r"<!-- D365:END DEVELOPER-PREFLIGHT -->",
    re.DOTALL,
)


def execution_host_label(value: str) -> str:
    labels = {
        "local_interactive": "Local interactive (Copilot CLI or VS Code)",
        "cloud_or_local": "Cloud or local",
    }
    return labels.get(value, value or "Missing")


def developer_preflight_issue_block(
    repo: str,
    merge_sha: str,
    row: dict,
    task_context_hash: str,
) -> str:
    path = f"{row['workspace']}/development/{row['id']}.md"
    return f"""<!-- D365:BEGIN DEVELOPER-PREFLIGHT -->
### Compiler-owned Developer preflight

- DEV artifact: [{path}]({artifact_url(repo, merge_sha, path)})
- Task context hash: `{task_context_hash}`
- Registry SHA-256: `{row['developer_preflight']['registry_hash']}`
- Capability matrix SHA-256: `{row['developer_preflight']['capability_matrix_hash']}`
- Execution host: **{execution_host_label(str(row.get('execution_host') or ''))}**
- Authentication policy: `{row['developer_preflight']['authentication_policy']}`

{"Do not delegate Dataverse execution to GitHub Copilot cloud coding agent. Run `/d365.implement` locally from this Works repository." if row.get("execution_host") == "local_interactive" else "This DEV may run in GitHub Copilot cloud coding agent only while every required resource remains cloud-compatible."}

{P.render_developer_preflight(row['developer_preflight'])}
<!-- D365:END DEVELOPER-PREFLIGHT -->"""


def replace_developer_preflight_block(body: str, block: str) -> str:
    if PREFLIGHT_ISSUE_BLOCK.search(body):
        return PREFLIGHT_ISSUE_BLOCK.sub(block, body, count=1)
    return body.rstrip() + "\n\n" + block + "\n"


def find_development_issue(repo: str, dev_id: str) -> dict:
    marker = f"d365-handoff-key:execution:{dev_id}:"
    query = f'repo:{repo} is:issue is:open "{marker}" in:body'
    result = json.loads(
        run_gh(
            [
                "api",
                "--method",
                "GET",
                "search/issues",
                "-f",
                f"q={query}",
                "-f",
                "per_page=10",
            ]
        )
    )
    items = [
        item
        for item in result.get("items") or []
        if marker in str(item.get("body") or "") and "pull_request" not in item
    ]
    if len(items) != 1:
        raise HandoffError(
            f"{dev_id} must resolve to exactly one open Development issue; "
            f"found {len(items)}"
        )
    return items[0]


def refresh_development_issues(repo: str, merge_sha: str) -> int:
    context = P.read_context(P.TASK_CONTEXT_PATH)
    task_context_hash = context["context_hash"]
    count = 0
    for row in sorted(context.get("tasks") or [], key=lambda item: item["id"]):
        issue = find_development_issue(repo, row["id"])
        body = str(issue.get("body") or "")
        updated = replace_developer_preflight_block(
            body,
            developer_preflight_issue_block(
                repo,
                merge_sha,
                row,
                task_context_hash,
            ),
        )
        run_gh(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{repo}/issues/{issue['number']}",
                "-f",
                f"body={updated}",
            ]
        )
        count += 1
    return count


def create_requirement_handoff(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    intake_ids = added_intake_ids(repo, pr_number)
    assignee = require_env("D365_BA_ASSIGNEE")
    artifacts = [(path, artifact_url(repo, merge_sha, path)) for path in paths]
    marker = f"d365-handoff-key:requirement:{merge_sha}"
    url = create_issue(
        repo,
        title=f"Requirement: {', '.join(intake_ids)}",
        label="requirement",
        assignee=assignee,
        marker=marker,
        body=handoff_body(
            purpose="Author atomic, testable requirements from the accepted intake evidence.",
            parent=", ".join(intake_ids),
            artifacts=artifacts,
            pr_number=pr_number,
            marker=marker,
            instructions="The assigned Business Analyst reviews the evidence links, adjusts scope if needed, then changes Status to Ready and Stage Status to Ready for Drafting.",
        ),
    )
    return [url] if url else []


def create_feature_handoff(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    assignee = require_env("D365_BA_ASSIGNEE")
    intake_ids = sorted({path.split("/")[2] for path in paths})
    group_registries = [
        f"specs/intakes/{intake}/requirement-groups.yml"
        for intake in intake_ids
        if Path(f"specs/intakes/{intake}/requirement-groups.yml").is_file()
    ]
    artifact_paths = group_registries + paths
    artifacts = [(path, artifact_url(repo, merge_sha, path)) for path in artifact_paths]
    marker = f"d365-handoff-key:feature:{merge_sha}"
    url = create_issue(
        repo,
        title="Feature: classify approved requirements",
        label="feature",
        assignee=assignee,
        marker=marker,
        body=handoff_body(
            purpose="Classify the increment and create or extend cohesive feature workspaces; intake requirement groups are navigation only.",
            parent=", ".join(Path(path).stem for path in paths),
            artifacts=artifacts,
            pr_number=pr_number,
            marker=marker,
            instructions="The assigned Business Analyst uses requirement groups as context, derives feature boundaries independently, confirms extend-existing/create-new/split/clarify classification, then changes Status to Ready and Stage Status to Ready for Refinement.",
        ),
    )
    return [url] if url else []


def create_design_handoffs(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    assignee = require_env("D365_ARCHITECT_ASSIGNEE")
    urls: list[str] = []
    for path in paths:
        data = parse_frontmatter(Path(path))
        feature_id = str(data.get("id") or Path(path).parent.name)
        marker = f"d365-handoff-key:design:{path}:{merge_sha}"
        url = create_issue(
            repo,
            title=f"Design: {feature_id} {data.get('slug', Path(path).parent.name)}",
            label="design",
            assignee=assignee,
            marker=marker,
            body=handoff_body(
                purpose="Create the authoritative technical plan and typed component decomposition.",
                parent=feature_id,
                artifacts=[(path, artifact_url(repo, merge_sha, path))],
                pr_number=pr_number,
                marker=marker,
                instructions="The assigned Solution Architect reviews the approved specification, then changes Status to Ready and Stage Status to Ready for Design.",
            ),
        )
        if url:
            urls.append(url)
    return urls


def create_planning_handoffs(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    assignee = require_env("D365_DEV_LEAD_ASSIGNEE")
    urls: list[str] = []
    for path in paths:
        data = parse_frontmatter(Path(path))
        feature_id = str(data.get("implements_feature") or Path(path).parent.name)
        marker = f"d365-handoff-key:planning:{path}:{merge_sha}"
        url = create_issue(
            repo,
            title=f"Development Planning: {feature_id}",
            label="development",
            assignee=assignee,
            marker=marker,
            body=handoff_body(
                purpose="Generate and review tasks.md plus one DEV artifact per approved plan component.",
                parent=str(data.get("id") or feature_id),
                artifacts=[(path, artifact_url(repo, merge_sha, path))],
                pr_number=pr_number,
                marker=marker,
                instructions="The assigned Development Lead reviews task granularity and ownership, then changes Status to Ready and Stage Status to Ready for Planning.",
            ),
        )
        if url:
            urls.append(url)
    return urls


def create_execution_handoffs(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    assignee = require_env("D365_DEV_LEAD_ASSIGNEE")
    urls: list[str] = []
    for path in paths:
        data = parse_frontmatter(Path(path))
        dev_id = str(data.get("id") or Path(path).stem)
        planned_owner = str(data.get("owner") or "Not assigned")
        implementation_scope = str(data.get("implementation_scope") or "missing")
        execution_host = str(data.get("execution_host") or "missing")
        target = data.get("authoring_target")
        target_name = target.get("name") if isinstance(target, dict) else "repository only"
        marker = f"d365-handoff-key:execution:{dev_id}:{merge_sha}"
        url = create_issue(
            repo,
            title=f"Development: {dev_id} {data.get('component', data.get('component_type', 'component'))}",
            label="development",
            assignee=assignee,
            marker=marker,
            body=handoff_body(
                purpose="Implement exactly one approved DEV artifact and produce its required evidence.",
                parent=str(data.get("component") or data.get("plan") or ""),
                artifacts=[(path, artifact_url(repo, merge_sha, path))],
                pr_number=pr_number,
                marker=marker,
                instructions=(
                    f"Planned DEV owner: {planned_owner}. Implementation scope is "
                    f"{implementation_scope}; authoring target is {target_name}. "
                    f"Execution host: {execution_host_label(execution_host)}. "
                    + (
                        "Do not delegate Dataverse execution to GitHub Copilot cloud "
                        "coding agent; run /d365.implement locally from this Works "
                        "repository using Copilot CLI or VS Code. "
                        if execution_host == "local_interactive"
                        else ""
                    )
                    + "The configured Development Lead reviews ownership, dependencies, "
                    "and target roots, adjusts "
                    "the GitHub assignee if needed, then changes Status to Ready and "
                    "Stage Status to Ready for Build."
                ),
            ),
        )
        if url:
            urls.append(url)
    return urls


def create_test_handoffs(repo: str, pr_number: int, merge_sha: str, paths: list[str]) -> list[str]:
    assignee = require_env("D365_TEST_LEAD_ASSIGNEE")
    urls: list[str] = []
    for path in paths:
        data = parse_frontmatter(Path(path))
        dev_id = str(data.get("id") or Path(path).stem)
        if data.get("status") not in {"completed", "superseded"}:
            raise HandoffError(
                f"{path} changed in an implementation merge but status is "
                f"'{data.get('status')}'; set a reviewed terminal status before Test handoff"
            )
        marker = f"d365-handoff-key:test:{dev_id}:{merge_sha}"
        url = create_issue(
            repo,
            title=f"Test: {dev_id}",
            label="test",
            assignee=assignee,
            marker=marker,
            body=handoff_body(
                purpose="Verify the implemented DEV behavior and non-functional requirements.",
                parent=dev_id,
                artifacts=[(path, artifact_url(repo, merge_sha, path))],
                pr_number=pr_number,
                marker=marker,
                instructions="The assigned Test Lead reviews the approved DEV evidence, then changes Status to Ready and Stage Status to Test Planning.",
            ),
        )
        if url:
            urls.append(url)
    return urls


def classify(paths: list[str]) -> tuple[str | None, list[str]]:
    dev_paths = [p for p in paths if re.fullmatch(r"specs/[^/]+/development/DEV-\d{4}\.md", p)]
    if dev_paths:
        return "execution", dev_paths
    plan_paths = [p for p in paths if re.fullmatch(r"specs/[^/]+/plan\.md", p)]
    if plan_paths:
        return "planning", plan_paths
    spec_paths = [p for p in paths if re.fullmatch(r"specs/[^/]+/spec\.md", p)]
    if spec_paths:
        return "design", spec_paths
    req_paths = [
        p for p in paths
        if re.fullmatch(r"specs/intakes/INTK-\d{4}/requirements/INTK-\d{4}-REQ-\d{3}\.md", p)
    ]
    if req_paths:
        return "feature", req_paths
    intake_paths = [
        p for p in paths
        if p == "intake/_index.md"
        or re.fullmatch(r"intake/[^/]+/\d{4}-\d{2}-\d{2}/.+\.md", p)
        or re.fullmatch(r"intake/[^/]+/[^/]+/derived/.+", p)
    ]
    if intake_paths:
        return "requirement", intake_paths
    return None, []


def classify_records(records: list[dict]) -> tuple[str | None, list[str]]:
    added_devs = sorted({
        str(item["filename"])
        for item in records
        if item.get("status") == "added"
        and re.fullmatch(r"specs/[^/]+/development/DEV-\d{4}\.md", str(item.get("filename")))
    })
    if added_devs:
        return "execution", added_devs
    modified_devs = sorted({
        str(item["filename"])
        for item in records
        if item.get("status") != "added"
        and re.fullmatch(r"specs/[^/]+/development/DEV-\d{4}\.md", str(item.get("filename")))
    })
    if modified_devs:
        return "test", modified_devs
    return classify(sorted({str(item["filename"]) for item in records}))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pr-number", type=int)
    mode.add_argument("--refresh-development-issues", action="store_true")
    parser.add_argument("--merge-sha", required=True)
    args = parser.parse_args()

    repo = require_env("GITHUB_REPOSITORY")
    if args.refresh_development_issues:
        count = refresh_development_issues(repo, args.merge_sha)
        print(f"Refreshed {count} current Development issue(s).")
        return 0
    records = pull_file_records(repo, args.pr_number)
    pr = pull_request(repo, args.pr_number)
    intake_metadata = intake_evidence_metadata(str(pr.get("body") or ""))
    if intake_metadata:
        urls = create_intake_from_evidence(
            repo,
            args.pr_number,
            args.merge_sha,
            records,
            intake_metadata,
        )
        if urls:
            summary = "Created Intake issue:\n" + "\n".join(f"- {url}" for url in urls)
            run_gh(["pr", "comment", str(args.pr_number), "--repo", repo, "--body", summary])
            print(summary)
        return 0

    handoff_type, relevant = classify_records(records)
    if not handoff_type:
        print("::notice::merged PR does not contain a recognized lifecycle handoff; skipped.")
        return 0

    creators = {
        "requirement": create_requirement_handoff,
        "feature": create_feature_handoff,
        "design": create_design_handoffs,
        "planning": create_planning_handoffs,
        "execution": create_execution_handoffs,
        "test": create_test_handoffs,
    }
    urls = creators[handoff_type](repo, args.pr_number, args.merge_sha, relevant)
    if urls:
        summary = "Created lifecycle handoff issue(s):\n" + "\n".join(f"- {url}" for url in urls)
        run_gh(["pr", "comment", str(args.pr_number), "--repo", repo, "--body", summary])
        print(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HandoffError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
