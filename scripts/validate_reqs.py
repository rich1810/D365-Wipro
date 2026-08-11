#!/usr/bin/env python3
"""Validate authoritative intake-scoped requirement artifacts."""
from __future__ import annotations

import json
import hashlib
import re
import sys
from collections import defaultdict

from jsonschema import Draft202012Validator

import pipeline_common as P

REQ_ID = re.compile(r"^INTK-([0-9]{4})-REQ-([0-9]{3})$")
GROUP_ID = re.compile(r"^INTK-([0-9]{4})-GRP-([0-9]{2})$")
GHERKIN = re.compile(r"```gherkin\b.*?```", re.DOTALL)


def main() -> int:
    errors: list[str] = []
    schema_path = P.SCHEMA_DIR / "req.schema.json"
    if not schema_path.exists():
        print("::error::required schema not found: specs/_schema/req.schema.json")
        return 2
    group_schema_path = P.SCHEMA_DIR / "requirement-groups.schema.json"
    if not group_schema_path.exists():
        print("::error::required schema not found: specs/_schema/requirement-groups.schema.json")
        return 2
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    group_validator = Draft202012Validator(
        json.loads(group_schema_path.read_text(encoding="utf-8"))
    )
    allowed_statuses = P.conventions().get("status_flow") or []
    files = P.requirement_files()

    sequences: dict[str, list[int]] = defaultdict(list)
    seen: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    requirement_rows: dict[str, tuple[dict, str]] = {}
    for path in files:
        rel = path.relative_to(P.ROOT).as_posix()
        try:
            front, body, _ = P.read_markdown(path)
        except P.PipelineError as exc:
            errors.append(P.error(rel, str(exc)))
            continue
        for issue in sorted(validator.iter_errors(front), key=lambda item: list(item.path)):
            location = "/".join(str(part) for part in issue.path) or "(root)"
            errors.append(P.error(rel, f"schema: {location}: {issue.message}"))
        req_id = front.get("id")
        match = REQ_ID.fullmatch(req_id) if isinstance(req_id, str) else None
        if match:
            intake = f"INTK-{match.group(1)}"
            sequences[intake].append(int(match.group(2)))
            expected = P.ROOT / "specs" / "intakes" / intake / "requirements" / f"{req_id}.md"
            if path != expected:
                errors.append(P.error(rel, f"path does not match id '{req_id}'"))
            if front.get("intake_batch") != intake:
                errors.append(P.error(rel, f"intake_batch must be '{intake}'"))
            if req_id in seen:
                errors.append(P.error(rel, f"duplicate requirement id '{req_id}'"))
            seen.add(req_id)
            dependencies[req_id] = list(front.get("depends_on") or [])
            requirement_rows[req_id] = (front, rel)
        source = front.get("source_file")
        if isinstance(source, str):
            source_path = P.ROOT / source
            if not source_path.is_file():
                errors.append(P.error(rel, f"source_file does not exist: {source}"))
            elif hashlib.sha256(source_path.read_bytes()).hexdigest() != front.get("sha256"):
                errors.append(P.error(rel, f"sha256 does not match source_file bytes: {source}"))
        if "## Acceptance scenarios" not in body or not GHERKIN.search(body):
            errors.append(P.error(rel, "missing Acceptance scenarios section with a gherkin block"))
        if front.get("type") == "security" and "negative" not in body.lower():
            errors.append(P.error(rel, "security requirement must include a negative scenario"))
        if front.get("status") is not None and front.get("status") not in allowed_statuses:
            errors.append(P.error(rel, f"status '{front.get('status')}' is not in conventions.yml status_flow"))

    for intake, numbers in sorted(sequences.items()):
        expected = list(range(1, max(numbers) + 1))
        if sorted(numbers) != expected:
            errors.append(f"::error::{intake} requirement sequence must be contiguous from 001; found {sorted(numbers)}")
    for req_id, deps in dependencies.items():
        unknown = set(deps) - seen
        if unknown:
            errors.append(f"::error::{req_id} depends_on unknown requirements: {sorted(unknown)}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(req_id: str) -> None:
        if req_id in visiting:
            errors.append(f"::error::requirement dependency cycle includes {req_id}")
            return
        if req_id in visited:
            return
        visiting.add(req_id)
        for dependency in dependencies.get(req_id, []):
            if dependency in dependencies:
                visit(dependency)
        visiting.remove(req_id)
        visited.add(req_id)

    for req_id in sorted(dependencies):
        visit(req_id)

    registries: dict[str, tuple[dict, str]] = {}
    group_rows: dict[str, tuple[str, dict, str]] = {}
    for path in P.requirement_group_registry_files():
        rel = path.relative_to(P.ROOT).as_posix()
        intake_from_path = path.parent.name
        try:
            registry = P.load_yaml(path)
        except P.PipelineError as exc:
            errors.append(P.error(rel, str(exc)))
            continue
        registries[intake_from_path] = (registry, rel)
        for issue in sorted(
            group_validator.iter_errors(registry), key=lambda item: list(item.path)
        ):
            location = "/".join(str(part) for part in issue.path) or "(root)"
            errors.append(P.error(rel, f"schema: {location}: {issue.message}"))
        registry_intake = registry.get("intake")
        if registry_intake != intake_from_path:
            errors.append(P.error(rel, f"intake must match workspace '{intake_from_path}'"))
        groups = registry.get("groups")
        if not isinstance(groups, list):
            continue
        ids: list[str] = []
        orders: list[int] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_id = group.get("id")
            if isinstance(group_id, str):
                ids.append(group_id)
                match = GROUP_ID.fullmatch(group_id)
                if match and f"INTK-{match.group(1)}" != intake_from_path:
                    errors.append(
                        P.error(rel, f"group id '{group_id}' must belong to '{intake_from_path}'")
                    )
                if group_id in group_rows:
                    errors.append(P.error(rel, f"duplicate requirement group id '{group_id}'"))
                else:
                    group_rows[group_id] = (intake_from_path, group, rel)
            if isinstance(group.get("order"), int) and not isinstance(group.get("order"), bool):
                orders.append(group["order"])
        if len(ids) != len(set(ids)):
            errors.append(P.error(rel, "requirement group ids must be unique"))
        if len(orders) != len(set(orders)):
            errors.append(P.error(rel, "requirement group order values must be unique"))
        if orders and sorted(orders) != list(range(1, len(orders) + 1)):
            errors.append(
                P.error(
                    rel,
                    f"requirement group order must be contiguous from 1; found {sorted(orders)}",
                )
            )

    requirement_intakes = {
        str(front.get("intake_batch"))
        for front, _ in requirement_rows.values()
        if isinstance(front.get("intake_batch"), str)
    }
    for intake in sorted(requirement_intakes):
        if intake not in registries:
            path = P.requirement_group_registry_path(intake).relative_to(P.ROOT).as_posix()
            errors.append(P.error(path, f"missing requirement group registry for {intake}"))

    assignments: dict[str, list[str]] = defaultdict(list)
    for req_id, (front, rel) in sorted(requirement_rows.items()):
        intake = front.get("intake_batch")
        group_id = front.get("requirement_group")
        if not isinstance(group_id, str):
            continue
        match = GROUP_ID.fullmatch(group_id)
        if match and isinstance(intake, str) and f"INTK-{match.group(1)}" != intake:
            errors.append(
                P.error(rel, f"requirement_group '{group_id}' crosses intake boundary '{intake}'")
            )
            continue
        group = group_rows.get(group_id)
        if group is None:
            errors.append(P.error(rel, f"unknown requirement_group '{group_id}'"))
            continue
        if group[0] != intake:
            errors.append(
                P.error(rel, f"requirement_group '{group_id}' crosses intake boundary '{intake}'")
            )
            continue
        assignments[group_id].append(req_id)

    for group_id, (intake, group, rel) in sorted(group_rows.items()):
        assigned = assignments.get(group_id, [])
        if not assigned:
            errors.append(P.error(rel, f"requirement group '{group_id}' is empty/orphaned"))
            continue
        assigned_provenance = {
            (requirement_rows[req_id][0].get("source_file"), requirement_rows[req_id][0].get("location"))
            for req_id in assigned
        }
        for evidence in group.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            reference = (evidence.get("source_file"), evidence.get("location"))
            if reference not in assigned_provenance:
                errors.append(
                    P.error(
                        rel,
                        f"group '{group_id}' evidence {reference!r} does not match "
                        "an assigned atomic REQ provenance reference",
                    )
                )

    for message in errors:
        print(message)
    if errors:
        print(f"\nRequirement validation FAILED: {len(errors)} error(s).")
        return 1
    print(
        f"Requirement validation passed: {len(files)} file(s), "
        f"{len(group_rows)} group(s), {len(registries)} intake registry file(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
