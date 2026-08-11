#!/usr/bin/env python3
"""Validate Dataverse Web API capability coverage and routing claims."""
from __future__ import annotations

import sys

import pipeline_common as P


def main() -> int:
    try:
        matrix = P.load_dataverse_capabilities()
        supported_actions = 0
        supported_types: set[str] = set()
        for component_type, component in matrix["components"].items():
            for operation, profile_id in component["operations"].items():
                profile = matrix["profiles"][profile_id]
                if not profile["official_references"]:
                    raise P.PipelineError(
                        f"{component_type}.{operation} has no official reference"
                    )
                if profile["support"] == "supported":
                    supported_actions += 1
                    supported_types.add(component_type)
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    print(
        "Dataverse capability validation passed: "
        f"{len(matrix['components'])} component type(s), "
        f"{len(matrix['profiles'])} profile(s), "
        f"{len(supported_types)} Web API-routed type(s), "
        f"{supported_actions} supported type-operation mapping(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
