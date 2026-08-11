#!/usr/bin/env python3
"""Incrementally generate tasks.md, DEV artifacts, and task-context.json."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

import pipeline_common as P

LEGACY_SECTION = {
    "component-payload": re.compile(
        r"(?ms)^## Component payload\s*\n+```ya?ml\s*\n.*?\n```\s*"
    ),
    "execution-profile": re.compile(
        r"(?ms)^## Execution profile\s*\n+```ya?ml\s*\n.*?\n```\s*"
    ),
}
AUTHOR_SECTIONS = ("implementation-notes", "checkpoints", "evidence")


def dev_yaml(body: str, zone: str, heading: str) -> dict:
    content = P.compiler_zone(body, zone)
    if content is None:
        if zone == "development-resources":
            return {}
        match = LEGACY_SECTION[zone].search(body)
        if not match:
            raise P.PipelineError(f"DEV artifact is missing generated {heading.lower()}")
        fenced = re.search(r"```ya?ml\s*\n(.*?)\n```", match.group(0), re.DOTALL)
        content = f"```yaml\n{fenced.group(1)}\n```" if fenced else ""
    return P.parse_yaml_fence(content, heading)


def scan_existing_devs() -> tuple[dict[str, dict], int]:
    by_component: dict[str, dict] = {}
    seen_ids: dict[str, Path] = {}
    maximum = 0
    for path in P.development_files():
        rel = path.relative_to(P.ROOT).as_posix()
        front, body, text = P.read_markdown(path)
        dev_id = front.get("id")
        match = P.DEV_ID_RE.fullmatch(dev_id) if isinstance(dev_id, str) else None
        if not match or path.name != f"{dev_id}.md":
            raise P.PipelineError(f"{rel}: filename and id must be DEV-####")
        maximum = max(maximum, int(match.group(1)))
        if dev_id in seen_ids:
            raise P.PipelineError(
                f"duplicate DEV id {dev_id}: {seen_ids[dev_id].relative_to(P.ROOT).as_posix()} and {rel}"
            )
        seen_ids[dev_id] = path
        component = front.get("component")
        if not isinstance(component, str) or not component:
            raise P.PipelineError(f"{rel}: missing component id")
        if component in by_component:
            other = by_component[component]["path"].relative_to(P.ROOT).as_posix()
            raise P.PipelineError(f"component {component} maps to multiple DEV artifacts: {other} and {rel}")
        by_component[component] = {
            "path": path,
            "front": front,
            "body": body,
            "text": text,
            "payload": dev_yaml(body, "component-payload", "Component payload"),
            "profile": dev_yaml(body, "execution-profile", "Execution profile"),
            "resources": dev_yaml(
                body, "development-resources", "Development resources"
            ),
            "preflight": P.compiler_zone(body, "developer-preflight"),
        }
    return by_component, maximum


def task_material(plan_context: dict, conv: dict, existing: dict[str, dict], maximum: int) -> list[dict]:
    rows = []
    resource_registry = P.load_development_resources()
    seen_components = set()
    next_id = maximum + 1
    for plan in sorted(plan_context.get("plans") or [], key=lambda item: item["workspace"]):
        for component in sorted(plan.get("components") or [], key=lambda item: item["id"]):
            component_id = component["id"]
            if component_id in seen_components:
                raise P.PipelineError(f"current plans contain duplicate component id {component_id}")
            seen_components.add(component_id)
            skill = component["build_skill"]
            profile = (conv.get("component_task_profiles") or {}).get(skill)
            if not isinstance(profile, dict):
                raise P.PipelineError(f"build skill '{skill}' has no task profile")
            record = existing.get(component_id)
            if record:
                executor = record["front"].get("executor")
                owner = record["front"].get("owner")
                status = record["front"].get("status")
                dev_id = record["front"]["id"]
            else:
                if next_id > 9999:
                    raise P.PipelineError("DEV id space exhausted at DEV-9999")
                executor = component.get("executor", profile.get("executor_default"))
                owner = component.get("owner")
                status = "draft"
                dev_id = f"DEV-{next_id:04d}"
                next_id += 1
            if executor not in (conv.get("dev_executor_types") or []):
                raise P.PipelineError(f"component {component_id} has invalid executor '{executor}'")
            if status not in (conv.get("dev_status_flow") or []):
                raise P.PipelineError(f"component {component_id} has invalid DEV status '{status}'")
            payload = {
                key: value
                for key, value in component.items()
                if key not in {
                    "id", "component_type", "executor", "owner", "depends_on",
                    "build_skill", "task_profile", "implementation_scope",
                    "execution_host", "authoring_target",
                    "development_resources",
                }
            }
            development_resources = component["development_resources"]
            developer_preflight = P.resolve_developer_preflight(
                development_resources,
                resource_registry,
                component["implementation_scope"],
                component["execution_host"],
                component["authoring_target"],
                payload,
            )
            rows.append(
                {
                    "id": dev_id,
                    "feature": plan["feature"],
                    "workspace": plan["workspace"],
                    "plan": plan["plan"],
                    "source_plan_hash": plan["plan_hash"],
                    "component": component_id,
                    "component_type": component["component_type"],
                    "executor": executor,
                    "owner": owner,
                    "build_skill": skill,
                    "implementation_scope": component["implementation_scope"],
                    "execution_host": component["execution_host"],
                    "authoring_target": component["authoring_target"],
                    "development_resources": development_resources,
                    "authentication_policy": resource_registry["authentication_policy"],
                    "developer_preflight": developer_preflight,
                    "satisfies": component["satisfies"],
                    "component_dependencies": component.get("depends_on") or [],
                    "status": status,
                    "payload": payload,
                    "task_profile": profile,
                }
            )
    mapping = {row["component"]: row["id"] for row in rows}
    for row in rows:
        try:
            row["depends_on"] = [mapping[item] for item in row.pop("component_dependencies")]
        except KeyError as exc:
            raise P.PipelineError(
                f"component {row['component']} depends on non-current component {exc.args[0]}"
            ) from exc
    return rows


def immutable_delta(record: dict, row: dict) -> list[str]:
    front = record["front"]
    changed = []
    for field in ("component_type", "build_skill"):
        if front.get(field) != row[field]:
            changed.append(field)
    for field in ("implementation_scope", "execution_host", "authoring_target"):
        if field in front and front.get(field) != row[field]:
            changed.append(field)
    if sorted(front.get("satisfies") or []) != sorted(row["satisfies"]):
        changed.append("satisfies")
    old_payload = dict(record["payload"])
    new_payload = dict(row["payload"])
    if isinstance(old_payload.get("satisfies"), list):
        old_payload["satisfies"] = sorted(old_payload["satisfies"])
    if isinstance(new_payload.get("satisfies"), list):
        new_payload["satisfies"] = sorted(new_payload["satisfies"])
    if old_payload != new_payload:
        changed.append("payload")
    return changed


def render_tasks(plan: dict, rows: list[dict], repo_hash: str, task_hash: str) -> str:
    front = {
        "feature": plan["feature"],
        "plan": plan["plan"],
        "source_plan_hash": plan["plan_hash"],
        "repository_context_hash": repo_hash,
        "task_context_hash": task_hash,
        "status": "draft",
    }
    lines = [
        "# Development work index",
        "",
        "Generated from the current plan.md. Historical DEV artifacts are retained but omitted.",
        "",
        "| DEV | Component | Type | Scope | Execution host | Authoring target | Authentication policy | Required resources | Executor | Depends on | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| [{row['id']}](development/{row['id']}.md) | {row['component']} | "
            f"{row['component_type']} | {row['implementation_scope']} | "
            f"{row['execution_host']} | "
            f"{(row['authoring_target'] or {}).get('name') or 'repository only'} | "
            f"{row['authentication_policy']} | "
            f"{', '.join(item['id'] for item in row['development_resources'].get('required', [])) or '—'} | "
            f"{row['executor']} | {', '.join(row['depends_on']) or '—'} | "
            f"{row['status']} |"
        )
    return P.render_markdown(front, "\n".join(lines) + "\n")


def generated_section(heading: str, zone: str, value: dict) -> str:
    content = yaml.safe_dump(value, sort_keys=True, allow_unicode=True).rstrip()
    return (
        f"## {heading}\n\n"
        f"<!-- COMPILER:BEGIN {zone} -->\n"
        f"```yaml\n{content}\n```\n"
        f"<!-- COMPILER:END {zone} -->"
    )


def developer_preflight_section(value: dict) -> str:
    return (
        "## Developer preflight\n\n"
        "<!-- COMPILER:BEGIN developer-preflight -->\n"
        f"{P.render_developer_preflight(value)}\n"
        "<!-- COMPILER:END developer-preflight -->"
    )


def ensure_author_sections(body: str) -> str:
    additions = []
    for name in AUTHOR_SECTIONS:
        if f"<!-- AUTHOR:BEGIN {name} -->" not in body:
            heading = name.replace("-", " ").title()
            additions.append(
                f"## {heading}\n\n"
                f"<!-- AUTHOR:BEGIN {name} -->\n"
                f"<!-- AUTHOR:END {name} -->"
            )
    if additions:
        body = body.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
    return body


def render_dev(row: dict, task_hash: str, record: dict | None) -> str:
    existing_front = record["front"] if record else {}
    front = {
        "id": row["id"],
        "feature": row["feature"],
        "plan": row["plan"],
        "component": row["component"],
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
        "task_context_hash": task_hash,
    }
    if "supersedes" in existing_front:
        front["supersedes"] = existing_front["supersedes"]
    payload_section = generated_section("Component payload", "component-payload", row["payload"])
    profile_section = generated_section("Execution profile", "execution-profile", row["task_profile"])
    resources_section = generated_section(
        "Development resources",
        "development-resources",
        row["development_resources"],
    )
    preflight_section = developer_preflight_section(row["developer_preflight"])
    if not record:
        body = (
            f"# {row['id']} — {row['component']}\n\n"
            "Generated fields are compiler-owned. Edit only AUTHOR sections.\n\n"
            f"{payload_section}\n\n{profile_section}\n\n{resources_section}\n\n"
            f"{preflight_section}\n"
        )
    else:
        body = record["body"]
        for zone, section in (
            ("component-payload", payload_section),
            ("execution-profile", profile_section),
            ("development-resources", resources_section),
            ("developer-preflight", preflight_section),
        ):
            if P.compiler_zone(body, zone) is not None:
                content = P.compiler_zone(section, zone)
                body = P.replace_compiler_zone(body, zone, content or "")
            else:
                match = LEGACY_SECTION.get(zone)
                match = match.search(body) if match else None
                if not match:
                    if zone in {"development-resources", "developer-preflight"}:
                        body = body.rstrip() + "\n\n" + section + "\n"
                        continue
                    raise P.PipelineError(
                        f"{record['path'].relative_to(P.ROOT).as_posix()}: missing generated {zone}"
                    )
                body = body[: match.start()] + section + "\n\n" + body[match.end() :]
    return P.render_markdown(front, ensure_author_sections(body))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        repo = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        plan_context = P.read_context(P.PLAN_CONTEXT_PATH)
        conv = P.conventions()
        existing, maximum = scan_existing_devs()
        rows = task_material(plan_context, conv, existing, maximum)
        for row in rows:
            record = existing.get(row["component"])
            if record and record["path"].parent.parent != P.ROOT / row["workspace"]:
                raise P.PipelineError(
                    f"component {row['component']} maps to "
                    f"{record['path'].relative_to(P.ROOT).as_posix()} outside current workspace "
                    f"{row['workspace']}"
                )
            if record and record["front"].get("status") in P.TERMINAL_DEV_STATUSES:
                changed = immutable_delta(record, row)
                if changed:
                    raise P.PipelineError(
                        f"{record['path'].relative_to(P.ROOT).as_posix()}: immutable "
                        f"{record['front']['status']} DEV {row['id']} cannot change "
                        f"{', '.join(changed)}; use a new component ID and DEV delta"
                    )
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    context = {
        "schema_version": 1,
        "repository_context_hash": repo["context_hash"],
        "plan_context_hash": plan_context["context_hash"],
        "tasks": rows,
    }
    context["context_hash"] = P.context_hash(context)
    drifted = []
    if P.write_json(P.TASK_CONTEXT_PATH, context, args.check):
        drifted.append(P.TASK_CONTEXT_PATH.relative_to(P.ROOT).as_posix())
    by_workspace: dict[str, list[dict]] = {}
    for row in rows:
        by_workspace.setdefault(row["workspace"], []).append(row)
    for plan in plan_context.get("plans") or []:
        workspace = P.ROOT / plan["workspace"]
        plan_rows = by_workspace.get(plan["workspace"], [])
        tasks_path = workspace / "tasks.md"
        if P.write_text(
            tasks_path,
            render_tasks(plan, plan_rows, repo["context_hash"], context["context_hash"]),
            args.check,
        ):
            drifted.append(tasks_path.relative_to(P.ROOT).as_posix())
        for row in plan_rows:
            record = existing.get(row["component"])
            if record and record["front"].get("status") in P.TERMINAL_DEV_STATUSES:
                continue
            path = record["path"] if record else workspace / "development" / f"{row['id']}.md"
            if P.write_text(path, render_dev(row, context["context_hash"], record), args.check):
                drifted.append(path.relative_to(P.ROOT).as_posix())
    if args.check and drifted:
        for rel in sorted(set(drifted)):
            print(P.error(rel, "generated task artifact is stale; run compile_tasks.py"))
        return 1
    print(f"{'Checked' if args.check else 'Generated'} {len(rows)} current DEV mapping(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
