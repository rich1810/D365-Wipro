#!/usr/bin/env python3
"""Validate feature workspace specifications and spec context."""
from __future__ import annotations

import json
import re
import sys

import yaml
from jsonschema import Draft202012Validator

import pipeline_common as P

REQUIRED_FILL = ("intent", "scope", "grounding", "open-decisions")
REQUIRED_ZONES = ("traceability", "scenarios", "nfr", "deps", "provenance")


def main() -> int:
    errors = []
    schema_path = P.SCHEMA_DIR / "spec.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        repo = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        context = P.read_context(P.SPEC_CONTEXT_PATH)
        requirements = P.load_requirements()
        conv = P.conventions()
    except (OSError, json.JSONDecodeError, P.PipelineError) as exc:
        print(f"::error::{exc}")
        return 1
    claimed = {}
    seen_features = set()
    sequences = []
    context_by_feature = {
        item.get("feature"): item for item in context.get("features") or []
    }
    for workspace in P.feature_workspaces():
        path = workspace / "spec.md"
        rel = path.relative_to(P.ROOT).as_posix()
        try:
            front, body, text = P.read_markdown(path)
        except P.PipelineError as exc:
            errors.append(P.error(rel, str(exc)))
            continue
        for issue in sorted(validator.iter_errors(front), key=lambda item: list(item.path)):
            location = "/".join(str(part) for part in issue.path) or "(root)"
            errors.append(P.error(rel, f"schema: {location}: {issue.message}"))
        match = P.WORKSPACE_RE.fullmatch(workspace.name)
        if match:
            sequences.append(int(match.group(1)))
        if front.get("slug") != (match.group(2) if match else None):
            errors.append(P.error(rel, "slug must match the feature workspace directory"))
        feature = front.get("id")
        expected_feature = f"FEAT-{int(match.group(1)):02d}" if match else None
        if match and int(match.group(1)) > 99:
            errors.append(P.error(rel, "workspace sequence exceeds FEAT-## capacity"))
        elif feature != expected_feature:
            errors.append(P.error(rel, f"feature id must be '{expected_feature}' for this workspace"))
        if feature in seen_features:
            errors.append(P.error(rel, f"duplicate feature id '{feature}'"))
        seen_features.add(feature)
        if front.get("repository_context_hash") != repo["context_hash"]:
            errors.append(P.error(rel, "repository_context_hash is stale"))
        if front.get("status") not in (conv.get("status_flow") or []):
            errors.append(P.error(rel, f"status '{front.get('status')}' is not in conventions.yml status_flow"))
        actual_hash = P.authoritative_hash(text, "spec_hash")
        if front.get("spec_hash") != actual_hash:
            errors.append(P.error(rel, "spec_hash is stale; run compile_specs.py"))
        context_entry = context_by_feature.get(feature)
        if not context_entry or context_entry.get("spec_hash") != front.get("spec_hash"):
            errors.append(P.error(rel, "spec context entry is missing or stale"))
        for name in REQUIRED_FILL:
            try:
                value = P.fill_zone(body, name)
                if not value or re.fullmatch(r"<.*>", value, re.DOTALL):
                    errors.append(P.error(rel, f"FILL zone '{name}' is empty or placeholder"))
            except P.PipelineError as exc:
                errors.append(P.error(rel, str(exc)))
        for name in REQUIRED_ZONES:
            if f"<!-- COMPILER:BEGIN {name} -->" not in body:
                errors.append(P.error(rel, f"missing COMPILER zone '{name}'"))
        for req_id in front.get("member_reqs") or []:
            if req_id not in requirements:
                errors.append(P.error(rel, f"unknown member requirement '{req_id}'"))
            elif req_id in claimed:
                errors.append(P.error(rel, f"requirement {req_id} is also owned by {claimed[req_id]}"))
            else:
                claimed[req_id] = feature
    if sequences and sorted(sequences) != list(range(1, max(sequences) + 1)):
        errors.append(f"::error::feature workspace sequence must be contiguous from 001; found {sorted(sequences)}")
    if context.get("repository_context_hash") != repo["context_hash"]:
        errors.append(P.error(".specify/context/spec-context.json", "repository context link is stale"))
    context_features = {item.get("feature") for item in context.get("features") or []}
    if context_features != seen_features:
        errors.append(P.error(".specify/context/spec-context.json", "feature set does not match spec workspaces"))
    for message in errors:
        print(message)
    if errors:
        print(f"\nFeature validation FAILED: {len(errors)} error(s).")
        return 1
    print(f"Feature validation passed: {len(seen_features)} feature(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
