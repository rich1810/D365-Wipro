#!/usr/bin/env python3
"""Validate the project-owned design grounding source registry."""
from __future__ import annotations

import json
import sys

import jsonschema

import pipeline_common as P

REGISTRY_PATH = P.ROOT / ".d365" / "design-sources.yml"
SCHEMA_PATH = P.ROOT / ".d365" / "design-sources.schema.json"
MCP_PATH = P.ROOT / ".vscode" / "mcp.json"
LEARN_URL = "https://learn.microsoft.com/api/mcp"


def main() -> int:
    try:
        registry = P.load_yaml(REGISTRY_PATH)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(registry)
        mcp = json.loads(MCP_PATH.read_text(encoding="utf-8"))
    except (P.PipelineError, OSError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        print(f"::error file=.d365/design-sources.yml::{exc}")
        return 1

    sources = registry["sources"]
    ids = [source["id"] for source in sources]
    if len(ids) != len(set(ids)):
        print("::error file=.d365/design-sources.yml::source ids must be unique")
        return 1

    by_id = {source["id"]: source for source in sources}
    learn = by_id.get("microsoft-learn")
    if not learn or learn.get("kind") != "mcp" or not learn.get("required"):
        print("::error file=.d365/design-sources.yml::microsoft-learn must be a required MCP source")
        return 1
    current = by_id.get("current-repository")
    if (
        not current
        or current.get("kind") != "repository"
        or not current.get("required")
        or current.get("location") != "."
        or current.get("ref") != "working-tree"
    ):
        print(
            "::error file=.d365/design-sources.yml::"
            "current-repository must be a required repository at '.' with ref 'working-tree'"
        )
        return 1

    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        print("::error file=.vscode/mcp.json::missing servers mapping")
        return 1
    for source in sources:
        server = source.get("server")
        if source["kind"] == "mcp" or source.get("access") == "mcp":
            if not server or server not in servers:
                print(
                    f"::error file=.d365/design-sources.yml::"
                    f"source {source['id']} references unavailable MCP server '{server}'"
                )
                return 1

    learn_server = servers.get(learn["server"])
    if (
        not isinstance(learn_server, dict)
        or learn_server.get("type") != "http"
        or learn_server.get("url") != LEARN_URL
    ):
        print(
            "::error file=.vscode/mcp.json::microsoft-learn must use "
            f"type http and URL {LEARN_URL}"
        )
        return 1

    print(f"Design source validation passed: {len(sources)} source(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
