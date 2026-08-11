#!/usr/bin/env python3
"""Validate current task mappings while accepting immutable DEV history."""
from __future__ import annotations

import json
import sys

from jsonschema import Draft202012Validator

import compile_tasks as C
import pipeline_common as P


def main() -> int:
    errors = []
    try:
        validator = Draft202012Validator(
            json.loads((P.SCHEMA_DIR / "dev.schema.json").read_text(encoding="utf-8"))
        )
        repo = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        plans = P.read_context(P.PLAN_CONTEXT_PATH)
        tasks = P.read_context(P.TASK_CONTEXT_PATH)
        conv = P.conventions()
        development_resources = P.load_development_resources()
        records, _ = C.scan_existing_devs()
    except (OSError, json.JSONDecodeError, P.PipelineError) as exc:
        print(f"::error::{exc}")
        return 1
    if tasks.get("repository_context_hash") != repo["context_hash"]:
        errors.append(P.error(".specify/context/task-context.json", "repository context link is stale"))
    if tasks.get("plan_context_hash") != plans["context_hash"]:
        errors.append(P.error(".specify/context/task-context.json", "plan context link is stale"))
    rows = tasks.get("tasks") or []
    expected_by_id = {row["id"]: row for row in rows}
    if len(expected_by_id) != len(rows):
        errors.append(P.error(".specify/context/task-context.json", "duplicate current DEV id"))
    expected_by_component = {row["component"]: row for row in rows}
    if len(expected_by_component) != len(rows):
        errors.append(P.error(".specify/context/task-context.json", "duplicate current component mapping"))
    plan_by_workspace = {item["workspace"]: item for item in plans.get("plans") or []}

    for workspace in P.feature_workspaces():
        plan = plan_by_workspace.get(workspace.relative_to(P.ROOT).as_posix())
        if plan is None:
            continue
        tasks_path = workspace / "tasks.md"
        rel = tasks_path.relative_to(P.ROOT).as_posix()
        if not tasks_path.exists():
            errors.append(P.error(rel, "generated tasks.md is missing"))
            continue
        try:
            tasks_front, _, _ = P.read_markdown(tasks_path)
            expected_front = {
                "feature": plan.get("feature"),
                "plan": plan.get("plan"),
                "source_plan_hash": plan.get("plan_hash"),
                "repository_context_hash": repo["context_hash"],
                "task_context_hash": tasks["context_hash"],
            }
            for key, value in expected_front.items():
                if tasks_front.get(key) != value:
                    errors.append(P.error(rel, f"{key} does not match generated context"))
            if tasks_front.get("status") not in (conv.get("status_flow") or []):
                errors.append(
                    P.error(rel, f"status '{tasks_front.get('status')}' is not in conventions.yml status_flow")
                )
        except P.PipelineError as exc:
            errors.append(P.error(rel, str(exc)))

    observed_ids = set()
    for component, record in records.items():
        path = record["path"]
        rel = path.relative_to(P.ROOT).as_posix()
        front = record["front"]
        for issue in sorted(validator.iter_errors(front), key=lambda item: list(item.path)):
            location = "/".join(str(part) for part in issue.path) or "(root)"
            errors.append(P.error(rel, f"schema: {location}: {issue.message}"))
        dev_id = front.get("id")
        if dev_id in observed_ids:
            errors.append(P.error(rel, f"duplicate DEV id '{dev_id}'"))
        observed_ids.add(dev_id)
        if front.get("status") not in (conv.get("dev_status_flow") or []):
            errors.append(
                P.error(rel, f"status '{front.get('status')}' is not in conventions.yml dev_status_flow")
            )
        row = expected_by_component.get(component)
        if row is None:
            if front.get("status") not in P.TERMINAL_DEV_STATUSES:
                errors.append(
                    P.error(
                        rel,
                        "nonterminal DEV has no current plan component; restore the component "
                        "or explicitly cancel/supersede the DEV artifact",
                    )
                )
            continue
        if row["id"] != dev_id:
            errors.append(P.error(rel, f"current component {component} maps to {row['id']}, not {dev_id}"))
            continue
        terminal = front.get("status") in P.TERMINAL_DEV_STATUSES
        if terminal:
            changed = C.immutable_delta(record, row)
            if changed:
                errors.append(
                    P.error(
                        rel,
                        f"immutable {front.get('status')} DEV cannot change {', '.join(changed)}; "
                        "use a new component ID and DEV delta",
                    )
                )
            for field in ("executor", "owner", "status"):
                if front.get(field) != row.get(field):
                    errors.append(P.error(rel, f"{field} does not match retained immutable mapping"))
            continue
        comparisons = {
            "feature": row["feature"],
            "plan": row["plan"],
            "component_type": row["component_type"],
            "executor": row["executor"],
            "owner": row["owner"],
            "build_skill": row["build_skill"],
            "implementation_scope": row["implementation_scope"],
            "execution_host": row["execution_host"],
            "authoring_target": row["authoring_target"],
            "authentication_policy": row["authentication_policy"],
            "satisfies": row["satisfies"],
            "depends_on": row["depends_on"],
            "status": row["status"],
            "source_plan_hash": row["source_plan_hash"],
            "task_context_hash": tasks["context_hash"],
        }
        for key, value in comparisons.items():
            if front.get(key) != value:
                errors.append(P.error(rel, f"{key} does not match current task context"))
        if record["payload"] != row["payload"]:
            errors.append(P.error(rel, "compiler-owned component payload is stale"))
        if record["profile"] != row["task_profile"]:
            errors.append(P.error(rel, "compiler-owned execution profile is stale"))
        if record["resources"] != row["development_resources"]:
            errors.append(P.error(rel, "compiler-owned development resources are stale"))
        expected_preflight = P.render_developer_preflight(row["developer_preflight"])
        if record["preflight"] != expected_preflight:
            errors.append(
                P.error(
                    rel,
                    "compiler-owned Developer preflight is missing, stale, or authored",
                )
            )
        current_resources = P.resolve_development_resources(
            row["component"],
            row["component_type"],
            row["build_skill"],
            development_resources,
            row["authoring_target"],
        )
        if row["development_resources"] != current_resources:
            errors.append(P.error(rel, "task context development_resources is stale"))
        current_execution_host = P.resolve_execution_host(
            row["implementation_scope"], current_resources
        )
        if row.get("execution_host") != current_execution_host:
            errors.append(P.error(rel, "task context execution_host is stale"))
        if row.get("authentication_policy") != development_resources["authentication_policy"]:
            errors.append(P.error(rel, "task context authentication_policy is stale"))
        current_preflight = P.resolve_developer_preflight(
            current_resources,
            development_resources,
            row["implementation_scope"],
            row["execution_host"],
            row["authoring_target"],
            row["payload"],
        )
        if row["developer_preflight"] != current_preflight:
            errors.append(P.error(rel, "task context developer_preflight is stale"))

    missing_components = sorted(set(expected_by_component) - set(records))
    if missing_components:
        errors.append(f"::error::current DEV component mappings are missing: {missing_components}")
    for message in errors:
        print(message)
    if errors:
        print(f"\nTask validation FAILED: {len(errors)} error(s).")
        return 1
    historical = len(records) - len(expected_by_component)
    print(f"Task validation passed: {len(expected_by_component)} current, {historical} historical DEV artifact(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
