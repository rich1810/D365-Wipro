#!/usr/bin/env python3
"""Upgrade consumer resources and optionally adopt Craft capability routing."""
from __future__ import annotations

import argparse
import re
import sys
from copy import deepcopy
from pathlib import Path

import yaml


def load(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} root must be a mapping")
    return value


def merge_contract(
    customer: dict,
    template: dict,
) -> tuple[bool, list[str], list[str], list[str]]:
    policy_added = False
    if "authentication_policy" not in customer:
        customer["authentication_policy"] = template["authentication_policy"]
        policy_added = True
    customer_resources = customer.get("resources")
    template_resources = template.get("resources")
    if not isinstance(customer_resources, dict) or not isinstance(template_resources, dict):
        raise ValueError("both registries must contain a resources mapping")
    added = []
    authentication_metadata_added = []
    execution_host_metadata_added = []
    for resource_id, customer_resource in customer_resources.items():
        if not isinstance(customer_resource, dict):
            raise ValueError(f"customer resource '{resource_id}' must be a mapping")
        template_resource = template_resources.get(resource_id)
        if not isinstance(template_resource, dict):
            raise ValueError(
                f"customer resource '{resource_id}' is not present in the selected Craft template"
            )
        if "supported_execution_hosts" not in customer_resource:
            customer_resource["supported_execution_hosts"] = deepcopy(
                template_resource["supported_execution_hosts"]
            )
            execution_host_metadata_added.append(resource_id)
        if "preflight" not in customer_resource:
            customer_resource["preflight"] = deepcopy(template_resource["preflight"])
            added.append(resource_id)
            continue
        customer_steps = {
            step.get("id"): step
            for step in customer_resource["preflight"].get("steps") or []
            if isinstance(step, dict)
        }
        for template_step in template_resource["preflight"].get("steps") or []:
            if template_step.get("phase") != "authentication":
                continue
            customer_step = customer_steps.get(template_step.get("id"))
            if not isinstance(customer_step, dict):
                continue
            changed = False
            for field in ("session_probe", "fresh_authentication"):
                if field not in customer_step:
                    customer_step[field] = deepcopy(template_step[field])
                    changed = True
            if changed:
                authentication_metadata_added.append(
                    f"{resource_id}.{template_step['id']}"
                )
    return (
        policy_added,
        added,
        authentication_metadata_added,
        execution_host_metadata_added,
    )


def indent_block(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in value.splitlines())


def insert_policy(text: str, policy: str) -> str:
    if re.search(r"(?m)^authentication_policy:\s*", text):
        return text
    match = re.search(r"(?m)^schema_version:\s*.*$", text)
    if not match:
        raise ValueError("registry text is missing schema_version")
    return text[: match.end()] + f"\nauthentication_policy: {policy}" + text[match.end() :]


def insert_capability_contract(text: str, contract: dict) -> str:
    if re.search(r"(?m)^capability_contract:\s*$", text):
        return text
    match = re.search(r"(?m)^authentication_policy:\s*.*$", text)
    if not match:
        raise ValueError("registry text is missing authentication_policy")
    block = yaml.safe_dump(
        {"capability_contract": contract},
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    ).rstrip()
    return text[: match.end()] + "\n" + block + text[match.end() :]


def insert_resource(text: str, resource_id: str, resource: dict) -> str:
    if re.search(rf"(?m)^  {re.escape(resource_id)}:\s*$", text):
        return text
    boundary = text.find("\nrouting:")
    if boundary < 0:
        raise ValueError("registry text is missing routing")
    block = yaml.safe_dump(
        {resource_id: resource},
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    ).rstrip()
    return (
        text[:boundary].rstrip()
        + "\n"
        + indent_block(block, 2)
        + "\n"
        + text[boundary:]
    )


def insert_resource_field(
    text: str, resource_id: str, field: str, value: dict
) -> str:
    resource_match = re.search(rf"(?m)^  {re.escape(resource_id)}:\s*$", text)
    if not resource_match:
        raise ValueError(f"registry text is missing resource '{resource_id}'")
    next_resource = re.search(
        r"(?m)^  [a-z][a-z0-9-]*:\s*$", text[resource_match.end() :]
    )
    resource_end = (
        resource_match.end() + next_resource.start()
        if next_resource
        else text.find("\nrouting:", resource_match.end())
    )
    if resource_end < 0:
        resource_end = len(text)
    segment = text[resource_match.start() : resource_end]
    if re.search(rf"(?m)^    {re.escape(field)}:\s*$", segment):
        return text
    preflight = re.search(r"(?m)^    preflight:\s*$", segment)
    if not preflight:
        raise ValueError(f"registry resource '{resource_id}' is missing preflight")
    insertion = resource_match.start() + preflight.start()
    block = yaml.safe_dump(
        {field: value},
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    ).rstrip()
    return text[:insertion] + indent_block(block, 4) + "\n" + text[insertion:]


def replace_capability_routing(text: str, template: dict) -> str:
    match = re.search(r"(?ms)^routing:\s*\n.*\Z", text)
    if not match:
        raise ValueError("registry text is missing routing")
    block = yaml.safe_dump(
        {
            "routing": template["routing"],
            "validation": template["validation"],
        },
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    )
    return text[: match.start()] + block


def add_missing_route_rationales(customer: dict, template: dict) -> list[str]:
    added: list[str] = []
    template_routes = {
        yaml.safe_dump(route.get("match"), sort_keys=True): route
        for route in template.get("routing") or []
    }
    for index, route in enumerate(customer.get("routing") or []):
        if not isinstance(route, dict):
            continue
        template_route = template_routes.get(
            yaml.safe_dump(route.get("match"), sort_keys=True)
        )
        if not str(route.get("rationale") or "").strip():
            route["rationale"] = (
                (template_route or {}).get("rationale")
                or "Customer-authored route preserved during upgrade; its resource selection remains customer-owned."
            )
            added.append(f"routing[{index}]")
        template_resources = (template_route or {}).get("resources") or {}
        for role, assignments in (route.get("resources") or {}).items():
            template_assignments = {
                item.get("resource"): item
                for item in template_resources.get(role) or []
                if isinstance(item, dict)
            }
            for assignment_index, assignment in enumerate(assignments or []):
                if not isinstance(assignment, dict) or str(
                    assignment.get("rationale") or ""
                ).strip():
                    continue
                resource_id = assignment.get("resource")
                assignment["rationale"] = (
                    template_assignments.get(resource_id, {}).get("rationale")
                    or "Customer-owned resource assignment preserved during upgrade; no new capability claim is implied."
                )
                added.append(
                    f"routing[{index}].resources.{role}[{assignment_index}]"
                )
    return added


def insert_resource_preflight(text: str, resource_id: str, preflight: dict) -> str:
    resource_match = re.search(
        rf"(?m)^  {re.escape(resource_id)}:\s*$", text
    )
    if not resource_match:
        raise ValueError(f"registry text is missing resource '{resource_id}'")
    next_resource = re.search(r"(?m)^  [a-z][a-z0-9-]*:\s*$", text[resource_match.end() :])
    boundary = (
        resource_match.end() + next_resource.start()
        if next_resource
        else text.find("\nrouting:", resource_match.end())
    )
    if boundary < 0:
        boundary = len(text)
    block = yaml.safe_dump(
        {"preflight": preflight},
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    ).rstrip()
    return text[:boundary].rstrip() + "\n" + indent_block(block, 4) + "\n" + text[boundary:].lstrip("\n")


def insert_resource_execution_hosts(
    text: str,
    resource_id: str,
    supported_execution_hosts: list[str],
) -> str:
    resource_match = re.search(rf"(?m)^  {re.escape(resource_id)}:\s*$", text)
    if not resource_match:
        raise ValueError(f"registry text is missing resource '{resource_id}'")
    next_resource = re.search(
        r"(?m)^  [a-z][a-z0-9-]*:\s*$", text[resource_match.end() :]
    )
    resource_end = (
        resource_match.end() + next_resource.start()
        if next_resource
        else text.find("\nrouting:", resource_match.end())
    )
    if resource_end < 0:
        resource_end = len(text)
    segment = text[resource_match.start() : resource_end]
    authentication = re.search(r"(?m)^    authentication:\s*.*$", segment)
    if not authentication:
        raise ValueError(
            f"registry text is missing authentication for resource '{resource_id}'"
        )
    insertion = resource_match.start() + authentication.end()
    rendered = yaml.safe_dump(
        supported_execution_hosts,
        allow_unicode=True,
        default_flow_style=True,
    ).strip()
    return (
        text[:insertion]
        + "\n    supported_execution_hosts: "
        + rendered
        + text[insertion:]
    )


def insert_authentication_metadata(
    text: str,
    resource_id: str,
    step_id: str,
    template_step: dict,
) -> str:
    resource_match = re.search(
        rf"(?m)^  {re.escape(resource_id)}:\s*$", text
    )
    if not resource_match:
        raise ValueError(f"registry text is missing resource '{resource_id}'")
    next_resource = re.search(r"(?m)^  [a-z][a-z0-9-]*:\s*$", text[resource_match.end() :])
    resource_end = (
        resource_match.end() + next_resource.start()
        if next_resource
        else text.find("\nrouting:", resource_match.end())
    )
    if resource_end < 0:
        resource_end = len(text)
    segment = text[resource_match.start() : resource_end]
    step_match = re.search(
        rf"(?m)^(?P<indent>\s*)-\s+id:\s*{re.escape(step_id)}\s*$",
        segment,
    )
    if not step_match:
        raise ValueError(
            f"registry text is missing authentication step '{resource_id}.{step_id}'"
        )
    step_start = resource_match.start() + step_match.start()
    step_indent = len(step_match.group("indent"))
    following = re.search(
        rf"(?m)^\s{{{step_indent}}}-\s+id:\s*|^  [a-z][a-z0-9-]*:\s*$|^routing:\s*$",
        text[step_start + 1 :],
    )
    step_end = (
        step_start + 1 + following.start()
        if following
        else resource_end
    )
    existing = text[step_start:step_end]
    additions = []
    for field in ("session_probe", "fresh_authentication"):
        if re.search(rf"(?m)^\s+{field}:\s*$", existing):
            continue
        dumped = yaml.safe_dump(
            {field: template_step[field]},
            sort_keys=False,
            allow_unicode=True,
            width=100000,
        ).rstrip()
        additions.append(indent_block(dumped, step_indent + 2))
    if not additions:
        return text
    return text[:step_end].rstrip() + "\n" + "\n".join(additions) + "\n" + text[step_end:].lstrip("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--adopt-capability-routing", action="store_true")
    args = parser.parse_args()
    try:
        original = args.registry.read_text(encoding="utf-8")
        customer = load(args.registry)
        template = load(args.template)
        (
            policy_added,
            added,
            authentication_metadata_added,
            execution_host_metadata_added,
        ) = merge_contract(customer, template)
        capability_contract_added = "capability_contract" not in customer
        missing_resources = sorted(set(template["resources"]) - set(customer["resources"]))
        oauth_added = "oauth_public_client" not in (
            customer["resources"].get("dataverse-web-api") or {}
        )
        route_rationales_added = add_missing_route_rationales(customer, template)
        if (
            policy_added
            or added
            or authentication_metadata_added
            or execution_host_metadata_added
            or capability_contract_added
            or missing_resources
            or oauth_added
            or route_rationales_added
            or args.adopt_capability_routing
        ):
            updated = insert_policy(original, template["authentication_policy"])
            if capability_contract_added:
                updated = insert_capability_contract(
                    updated, template["capability_contract"]
                )
            for resource_id in missing_resources:
                updated = insert_resource(
                    updated, resource_id, template["resources"][resource_id]
                )
            if oauth_added:
                updated = insert_resource_field(
                    updated,
                    "dataverse-web-api",
                    "oauth_public_client",
                    template["resources"]["dataverse-web-api"]["oauth_public_client"],
                )
            for resource_id in execution_host_metadata_added:
                updated = insert_resource_execution_hosts(
                    updated,
                    resource_id,
                    template["resources"][resource_id]["supported_execution_hosts"],
                )
            for resource_id in added:
                updated = insert_resource_preflight(
                    updated,
                    resource_id,
                    template["resources"][resource_id]["preflight"],
                )
            for key in authentication_metadata_added:
                resource_id, step_id = key.split(".", 1)
                template_step = next(
                    step
                    for step in template["resources"][resource_id]["preflight"]["steps"]
                    if step["id"] == step_id
                )
                updated = insert_authentication_metadata(
                    updated, resource_id, step_id, template_step
                )
            if args.adopt_capability_routing:
                updated = replace_capability_routing(updated, template)
                adopted = yaml.safe_load(updated)
                adopted["routing"] = deepcopy(template["routing"])
                adopted["validation"] = deepcopy(template["validation"])
                adopted["capability_contract"] = deepcopy(
                    template["capability_contract"]
                )
                adopted["resources"]["dataverse-web-api"]["description"] = template[
                    "resources"
                ]["dataverse-web-api"]["description"]
                adopted["resources"]["dataverse-web-api"]["preflight"] = deepcopy(
                    template["resources"]["dataverse-web-api"]["preflight"]
                )
                updated = yaml.safe_dump(
                    adopted,
                    sort_keys=False,
                    allow_unicode=True,
                    width=100000,
                )
            elif route_rationales_added:
                upgraded = yaml.safe_load(updated)
                add_missing_route_rationales(upgraded, template)
                updated = yaml.safe_dump(
                    upgraded,
                    sort_keys=False,
                    allow_unicode=True,
                    width=100000,
                )
            args.registry.write_text(updated, encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}")
        return 1
    print(
        f"Development resource preflight upgrade complete: "
        f"authentication policy {'added' if policy_added else 'preserved'}; "
        f"{len(added)} missing resource block(s) added; "
        f"{len(authentication_metadata_added)} authentication step(s) upgraded; "
        f"{len(execution_host_metadata_added)} resource host capability field(s) added; "
        f"{len(missing_resources)} resource(s) added; "
        f"capability contract {'added' if capability_contract_added else 'preserved'}; "
        f"capability routing {'adopted' if args.adopt_capability_routing else 'preserved'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
