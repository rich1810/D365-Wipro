#!/usr/bin/env python3
"""Validate the global development-resource registry and component coverage."""
from __future__ import annotations

import sys

import pipeline_common as P


def main() -> int:
    try:
        registry = P.load_development_resources()
        capabilities = P.load_dataverse_capabilities()
        conventions = P.conventions()
        for component_type in conventions.get("component_types") or []:
            sample = str(component_type).replace("*", "example")
            skill = P.resolve_component_skill(sample, conventions)
            resolved = P.resolve_development_resources(
                "DES-00-CMP-000", sample, skill, registry
            )
            scope = P.resolve_implementation_scope(sample, conventions)
            P.resolve_execution_host(scope, resolved)
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    print(
        "Development resource validation passed: "
        f"{len(registry['resources'])} resource(s), "
        f"{len(registry['routing'])} route(s), "
        f"{len(conventions.get('component_types') or [])} component type(s), "
        f"{len(capabilities['profiles'])} capability profile(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
