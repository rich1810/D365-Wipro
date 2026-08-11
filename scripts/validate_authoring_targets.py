#!/usr/bin/env python3
"""Validate the consumer-owned authoring environment and solution contract."""
from __future__ import annotations

import sys

import pipeline_common as P


def main() -> int:
    try:
        contract = P.load_authoring_targets()
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    unresolved = [
        name
        for name, environment in contract["environments"].items()
        if P.UNRESOLVED_AUTHORING_VALUE
        in {environment["environment_url"], environment["environment_id"]}
    ]
    print(
        "Authoring target validation passed: "
        f"{len(contract['environments'])} environment(s), "
        f"{len(contract['solutions'])} solution(s), "
        f"{len(contract['targets'])} target(s), "
        f"{len(contract['routing'])} route(s)."
    )
    if unresolved:
        print(
            "Authoring identity remains unresolved for "
            f"{', '.join(unresolved)}; Dataverse implementation is blocked."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
