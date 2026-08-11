#!/usr/bin/env python3
"""Generate the canonical repository context."""
from __future__ import annotations

import argparse
import json
import sys

import jsonschema

import pipeline_common as P


def build() -> dict:
    compatibility_path = P.ROOT / ".d365" / "spec-kit" / "compatibility.yml"
    design_sources_path = P.ROOT / ".d365" / "design-sources.yml"
    design_sources_schema_path = P.ROOT / ".d365" / "design-sources.schema.json"
    development_resources_path = P.DEVELOPMENT_RESOURCES_PATH
    development_resources_schema_path = P.DEVELOPMENT_RESOURCES_SCHEMA_PATH
    dataverse_capabilities_path = P.DATAVERSE_CAPABILITIES_PATH
    dataverse_capabilities_schema_path = P.DATAVERSE_CAPABILITIES_SCHEMA_PATH
    compatibility = P.load_yaml(compatibility_path)
    design_sources = P.load_yaml(design_sources_path)
    if not design_sources_schema_path.exists():
        raise P.PipelineError("required file not found: .d365/design-sources.schema.json")
    try:
        design_sources_schema = json.loads(
            design_sources_schema_path.read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(design_sources_schema).validate(design_sources)
    except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
        raise P.PipelineError(f"invalid design source registry: {exc}") from exc
    conventions = P.conventions()
    authoring_targets = P.load_authoring_targets()
    development_resources = P.load_development_resources()
    dataverse_capabilities = P.load_dataverse_capabilities()
    schemas = {}
    for path in sorted(P.SCHEMA_DIR.glob("*.json")):
        schemas[path.name] = P.sha256_text(path.read_text(encoding="utf-8"))
    data = {
        "schema_version": 1,
        "compatibility": compatibility,
        "compatibility_hash": P.sha256_text(compatibility_path.read_text(encoding="utf-8")),
        "design_sources": design_sources,
        "design_sources_hash": P.sha256_text(design_sources_path.read_text(encoding="utf-8")),
        "design_sources_schema_hash": P.sha256_text(
            design_sources_schema_path.read_text(encoding="utf-8")
        ),
        "authoring_targets": authoring_targets,
        "authoring_targets_hash": P.sha256_text(
            P.AUTHORING_TARGETS_PATH.read_text(encoding="utf-8")
        ),
        "authoring_targets_schema_hash": P.sha256_text(
            P.AUTHORING_TARGETS_SCHEMA_PATH.read_text(encoding="utf-8")
        ),
        "development_resources": development_resources,
        "development_resources_hash": P.sha256_text(
            development_resources_path.read_text(encoding="utf-8")
        ),
        "development_resources_schema_hash": P.sha256_text(
            development_resources_schema_path.read_text(encoding="utf-8")
        ),
        "dataverse_web_api_capabilities": dataverse_capabilities,
        "dataverse_web_api_capabilities_hash": P.sha256_text(
            dataverse_capabilities_path.read_text(encoding="utf-8")
        ),
        "dataverse_web_api_capabilities_schema_hash": P.sha256_text(
            dataverse_capabilities_schema_path.read_text(encoding="utf-8")
        ),
        "conventions_hash": P.sha256_text(P.CONVENTIONS_PATH.read_text(encoding="utf-8")),
        "schemas": schemas,
        "component_types": conventions.get("component_types") or [],
        "component_type_skills": conventions.get("component_type_skills") or {},
        "component_type_payloads": conventions.get("component_type_payloads") or {},
        "component_implementation_scopes": conventions.get("component_implementation_scopes") or {},
        "component_task_profiles": conventions.get("component_task_profiles") or {},
        "statuses": {
            "artifacts": conventions.get("status_flow") or [],
            "development": conventions.get("dev_status_flow") or [],
            "executors": conventions.get("dev_executor_types") or [],
            "execution_hosts": conventions.get("dev_execution_hosts") or [],
        },
    }
    data["context_hash"] = P.context_hash(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        data = build()
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    changed = P.write_json(P.REPOSITORY_CONTEXT_PATH, data, args.check)
    if changed and args.check:
        print(P.error("specs/_index/repository-context.json", "repository context is stale"))
        return 1
    print(f"Repository context {'checked' if args.check else 'compiled'}: {data['context_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
