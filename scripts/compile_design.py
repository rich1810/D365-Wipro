#!/usr/bin/env python3
"""Compile plan.md generated zones and plan-context.json."""
from __future__ import annotations

import argparse
import sys

import pipeline_common as P


def compile_plan(workspace, repo, spec_context, conv, check):
    spec_front, _, spec_text = P.read_markdown(workspace / "spec.md")
    path = workspace / "plan.md"
    front, body, _ = P.read_markdown(path)
    components = P.parse_components(body)
    front["implements_feature"] = spec_front["id"]
    front["source_spec_hash"] = spec_front["spec_hash"]
    front["repository_context_hash"] = repo["context_hash"]
    enriched = []
    authoring_targets = P.load_authoring_targets(repo.get("authoring_targets"))
    development_resources = P.load_development_resources(
        repo.get("development_resources")
    )
    for component in components:
        forbidden = {
            "implementation_scope",
            "execution_host",
            "authoring_target",
            "development_resources",
        } & component.keys()
        if forbidden:
            raise P.PipelineError(
                f"component {component.get('id')} authors compiler-owned field(s): "
                f"{', '.join(sorted(forbidden))}"
            )
        component_type = str(component.get("component_type", ""))
        skill = P.resolve_component_skill(component_type, conv)
        profile = (conv.get("component_task_profiles") or {}).get(skill)
        if not isinstance(profile, dict):
            raise P.PipelineError(f"build skill '{skill}' has no component_task_profiles entry")
        item = dict(component)
        item["build_skill"] = skill
        item["task_profile"] = profile
        scope, target = P.resolve_component_authoring(item, conv, authoring_targets)
        item["implementation_scope"] = scope
        item["authoring_target"] = target
        item["development_resources"] = P.resolve_development_resources(
            str(item.get("id")),
            component_type,
            skill,
            development_resources,
            target,
        )
        item["execution_host"] = P.resolve_execution_host(
            scope, item["development_resources"]
        )
        enriched.append(item)
    coverage = ["| REQ | Components |", "| --- | --- |"]
    for req_id in spec_front["member_reqs"]:
        owners = [str(item.get("id")) for item in enriched if req_id in (item.get("satisfies") or [])]
        coverage.append(f"| {req_id} | {', '.join(owners) or '—'} |")
    skills = [
        "| Component | Type | Build skill | Implementation scope | Execution host | Authoring target |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in enriched:
        target_name = (item.get("authoring_target") or {}).get("name") or "repository only"
        skills.append(
            f"| {item.get('id')} | {item.get('component_type')} | {item['build_skill']} | "
            f"{item['implementation_scope']} | {item['execution_host']} | {target_name} |"
        )
    provenance = "\n".join(
        [
            "| Plan | Feature | Source spec SHA-256 | Repository context |",
            "| --- | --- | --- | --- |",
            f"| {front['id']} | {spec_front['id']} | `{spec_front['spec_hash']}` | `{repo['context_hash']}` |",
        ]
    )
    provisional = P.render_markdown(front, body)
    provisional = P.replace_compiler_zone(provisional, "coverage", "\n".join(coverage))
    provisional = P.replace_compiler_zone(provisional, "skills", "\n".join(skills))
    provisional = P.replace_compiler_zone(provisional, "provenance", provenance)
    front, body = P.split_markdown(provisional)
    front["plan_hash"] = P.authoritative_hash(P.render_markdown(front, body), "plan_hash")
    compiled = P.render_markdown(front, body)
    changed = P.write_text(path, compiled, check)
    return {
        "feature": spec_front["id"],
        "workspace": workspace.relative_to(P.ROOT).as_posix(),
        "plan": front["id"],
        "source_spec_hash": spec_front["spec_hash"],
        "plan_hash": front["plan_hash"],
        "status": front["status"],
        "components": enriched,
    }, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        repo = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        spec_context = P.read_context(P.SPEC_CONTEXT_PATH)
        conv = P.conventions()
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    entries = []
    errors = []
    drifted = []
    for workspace in P.feature_workspaces():
        if not (workspace / "plan.md").exists():
            continue
        rel = (workspace / "plan.md").relative_to(P.ROOT).as_posix()
        try:
            entry, changed = compile_plan(workspace, repo, spec_context, conv, args.check)
            entries.append(entry)
            if changed:
                drifted.append(rel)
        except (P.PipelineError, KeyError, TypeError) as exc:
            errors.append(P.error(rel, str(exc)))
    context = {
        "schema_version": 1,
        "repository_context_hash": repo["context_hash"],
        "spec_context_hash": spec_context["context_hash"],
        "plans": sorted(entries, key=lambda item: item["workspace"]),
    }
    context["context_hash"] = P.context_hash(context)
    if P.write_json(P.PLAN_CONTEXT_PATH, context, args.check):
        drifted.append(P.PLAN_CONTEXT_PATH.relative_to(P.ROOT).as_posix())
    for message in errors:
        print(message)
    if errors:
        return 1
    if args.check and drifted:
        for rel in drifted:
            print(P.error(rel, "generated content is stale; run compile_design.py"))
        return 1
    print(f"{'Checked' if args.check else 'Compiled'} {len(entries)} technical plan(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
