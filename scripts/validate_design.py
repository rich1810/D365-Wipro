#!/usr/bin/env python3
"""Validate plan.md typed component contracts and plan context."""
from __future__ import annotations

import json
import re
import sys

from jsonschema import Draft202012Validator

import pipeline_common as P

COMPONENT_ID = re.compile(r"^(DES-[0-9]{2})-CMP-([0-9]{3})$")
REQUIRED_FILL = ("decisions", "components", "observability", "open-questions")
REQUIRED_ZONES = ("coverage", "skills", "provenance")


def present(value) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def main() -> int:
    errors = []
    try:
        validator = Draft202012Validator(
            json.loads((P.SCHEMA_DIR / "plan.schema.json").read_text(encoding="utf-8"))
        )
        repo = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        spec_context = P.read_context(P.SPEC_CONTEXT_PATH)
        plan_context = P.read_context(P.PLAN_CONTEXT_PATH)
        conv = P.conventions()
        authoring_targets = P.load_authoring_targets(repo.get("authoring_targets"))
        development_resources = P.load_development_resources(
            repo.get("development_resources")
        )
    except (OSError, json.JSONDecodeError, P.PipelineError) as exc:
        print(f"::error::{exc}")
        return 1
    plan_ids = set()
    component_ids = set()
    observed_plans = set()
    component_sequences = {}
    for workspace in P.feature_workspaces():
        spec_path = workspace / "spec.md"
        plan_path = workspace / "plan.md"
        if not plan_path.exists():
            continue
        rel = plan_path.relative_to(P.ROOT).as_posix()
        try:
            spec_front, _, _ = P.read_markdown(spec_path)
            front, body, text = P.read_markdown(plan_path)
            components = P.parse_components(body)
        except P.PipelineError as exc:
            errors.append(P.error(rel, str(exc)))
            continue
        for issue in sorted(validator.iter_errors(front), key=lambda item: list(item.path)):
            location = "/".join(str(part) for part in issue.path) or "(root)"
            errors.append(P.error(rel, f"schema: {location}: {issue.message}"))
        plan_id = front.get("id")
        observed_plans.add(plan_id)
        if plan_id in plan_ids:
            errors.append(P.error(rel, f"duplicate plan id '{plan_id}'"))
        plan_ids.add(plan_id)
        if front.get("implements_feature") != spec_front.get("id"):
            errors.append(P.error(rel, "implements_feature must match workspace spec.md"))
        if front.get("source_spec_hash") != spec_front.get("spec_hash"):
            errors.append(P.error(rel, "source_spec_hash is stale"))
        if front.get("repository_context_hash") != repo["context_hash"]:
            errors.append(P.error(rel, "repository_context_hash is stale"))
        if front.get("status") not in (conv.get("status_flow") or []):
            errors.append(P.error(rel, f"status '{front.get('status')}' is not in conventions.yml status_flow"))
        if front.get("plan_hash") != P.authoritative_hash(text, "plan_hash"):
            errors.append(P.error(rel, "plan_hash is stale; run compile_design.py"))
        for zone in REQUIRED_FILL:
            try:
                if not P.fill_zone(body, zone):
                    errors.append(P.error(rel, f"FILL zone '{zone}' is empty"))
            except P.PipelineError as exc:
                errors.append(P.error(rel, str(exc)))
        for zone in REQUIRED_ZONES:
            if f"<!-- COMPILER:BEGIN {zone} -->" not in body:
                errors.append(P.error(rel, f"missing COMPILER zone '{zone}'"))
        member_reqs = set(spec_front.get("member_reqs") or [])
        covered = set()
        local_ids = set()
        for component in components:
            component_id = component.get("id")
            forbidden = {
                "implementation_scope",
                "execution_host",
                "authoring_target",
                "development_resources",
            } & component.keys()
            if forbidden:
                errors.append(
                    P.error(
                        rel,
                        f"component {component_id} authors compiler-owned field(s): "
                        f"{', '.join(sorted(forbidden))}",
                    )
                )
            match = COMPONENT_ID.fullmatch(component_id) if isinstance(component_id, str) else None
            if not match or match.group(1) != plan_id:
                errors.append(P.error(rel, f"component id '{component_id}' must match {plan_id}-CMP-###"))
            elif component_id in component_ids:
                errors.append(P.error(rel, f"duplicate component id '{component_id}'"))
            elif match:
                component_sequences.setdefault(plan_id, []).append(int(match.group(2)))
            component_ids.add(component_id)
            local_ids.add(component_id)
            component_type = component.get("component_type")
            if not isinstance(component_type, str) or not P.component_type_allowed(component_type, conv):
                errors.append(P.error(rel, f"unknown component_type '{component_type}'"))
                continue
            try:
                P.resolve_component_skill(component_type, conv)
                required = P.required_component_fields(component_type, conv)
                scope, _ = P.resolve_component_authoring(component, conv, authoring_targets)
                skill = P.resolve_component_skill(component_type, conv)
                resolved_resources = P.resolve_development_resources(
                    str(component_id), component_type, skill, development_resources
                )
                P.resolve_execution_host(scope, resolved_resources)
            except P.PipelineError as exc:
                errors.append(P.error(rel, str(exc)))
                continue
            identity_field = P.required_component_identity_field(scope, conv)
            if identity_field and not present(component.get(identity_field)):
                errors.append(
                    P.error(
                        rel,
                        f"component {component_id} ({component_type}) missing required "
                        f"{scope} identity field '{identity_field}'",
                    )
                )
            for field in required:
                if not present(component.get(field)):
                    errors.append(
                        P.error(rel, f"component {component_id} ({component_type}) missing required field '{field}'")
                    )
            satisfies = component.get("satisfies")
            if not isinstance(satisfies, list) or not satisfies:
                errors.append(P.error(rel, f"component {component_id} must satisfy one or more requirements"))
            else:
                unknown = set(satisfies) - member_reqs
                if unknown:
                    errors.append(P.error(rel, f"component {component_id} satisfies non-member REQs: {sorted(unknown)}"))
                covered.update(satisfies)
            executor = component.get("executor")
            if executor is not None and executor not in (conv.get("dev_executor_types") or []):
                errors.append(P.error(rel, f"component {component_id} has invalid executor '{executor}'"))
        for component in components:
            unknown_dependencies = set(component.get("depends_on") or []) - local_ids
            if unknown_dependencies:
                errors.append(
                    P.error(rel, f"component {component.get('id')} has unknown dependencies: {sorted(unknown_dependencies)}")
                )
        if covered != member_reqs:
            errors.append(P.error(rel, f"component satisfies coverage must equal FEAT membership; missing {sorted(member_reqs-covered)}"))
    for plan_id, numbers in component_sequences.items():
        if numbers != sorted(numbers):
            errors.append(
                f"::error::{plan_id} component IDs must be in strictly increasing order; "
                f"found {numbers}"
            )
    if plan_context.get("repository_context_hash") != repo["context_hash"]:
        errors.append(P.error(".specify/context/plan-context.json", "repository context link is stale"))
    if plan_context.get("spec_context_hash") != spec_context["context_hash"]:
        errors.append(P.error(".specify/context/plan-context.json", "spec context link is stale"))
    if {item.get("plan") for item in plan_context.get("plans") or []} != observed_plans:
        errors.append(P.error(".specify/context/plan-context.json", "plan set does not match workspaces"))
    for message in errors:
        print(message)
    if errors:
        print(f"\nPlan validation FAILED: {len(errors)} error(s).")
        return 1
    print(f"Plan validation passed: {len(plan_ids)} plan(s), {len(component_ids)} component(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
