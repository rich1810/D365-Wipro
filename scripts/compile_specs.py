#!/usr/bin/env python3
"""Compile feature spec generated zones and spec-context.json."""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any

import yaml

import pipeline_common as P

ZONES = ("traceability", "scenarios", "nfr", "deps", "provenance")
GHERKIN = re.compile(r"```gherkin\b\n?(.*?)```", re.DOTALL)


def _cell(value: Any) -> str:
    return "—" if value in (None, "", []) else str(value).replace("|", r"\|")


def build_zones(reqs: list[tuple[dict, str, str]]) -> dict[str, str]:
    trace = ["| REQ | Title | Type | Priority |", "| --- | --- | --- | --- |"]
    scenarios = []
    deps = ["| REQ | Depends on |", "| --- | --- |"]
    provenance = ["| REQ | Intake | Source | Requirement SHA-256 |", "| --- | --- | --- | --- |"]
    for front, body, digest in reqs:
        req_id = front["id"]
        trace.append(
            f"| {req_id} | {_cell(front.get('title'))} | {_cell(front.get('type'))} | "
            f"{_cell(front.get('priority'))} |"
        )
        scenario = GHERKIN.search(body)
        if scenario:
            scenarios.append(scenario.group(1).strip())
        deps.append(f"| {req_id} | {_cell(', '.join(front.get('depends_on') or []))} |")
        provenance.append(
            f"| {req_id} | {_cell(front.get('intake_batch'))} | {_cell(front.get('source_file'))} | `{digest}` |"
        )
    return {
        "traceability": "\n".join(trace),
        "scenarios": "```gherkin\n" + "\n\n".join(scenarios) + "\n```",
        "nfr": "\n".join(
            ["| REQ | NFR |", "| --- | --- |"]
            + [
                f"| {front['id']} | {_cell(front.get('nfr'))} |"
                for front, _, _ in reqs
                if front.get("nfr")
            ]
        ),
        "deps": "\n".join(deps),
        "provenance": "\n".join(provenance),
    }


def compile_spec(path, repo_hash: str, requirements: dict, check: bool) -> tuple[dict, bool]:
    front, body, text = P.read_markdown(path)
    req_ids = front.get("member_reqs")
    if not isinstance(req_ids, list) or not req_ids:
        raise P.PipelineError("member_reqs must be a non-empty list")
    if len(req_ids) != len(set(req_ids)):
        raise P.PipelineError("member_reqs must be unique")
    missing = [req_id for req_id in req_ids if req_id not in requirements]
    if missing:
        raise P.PipelineError(f"member requirements not found: {missing}")
    reqs = []
    for req_id in req_ids:
        req_front, req_body, _, req_text = requirements[req_id]
        reqs.append((req_front, req_body, P.sha256_text(req_text)))
    front["repository_context_hash"] = repo_hash
    provisional = P.render_markdown(front, body)
    for name, content in build_zones(reqs).items():
        provisional = P.replace_compiler_zone(provisional, name, content)
    front, body = P.split_markdown(provisional)
    front["spec_hash"] = P.authoritative_hash(P.render_markdown(front, body), "spec_hash")
    compiled = P.render_markdown(front, body)
    changed = P.write_text(path, compiled, check)
    entry = {
        "feature": front["id"],
        "workspace": path.parent.relative_to(P.ROOT).as_posix(),
        "slug": front["slug"],
        "epic": front["epic"],
        "member_reqs": req_ids,
        "status": front["status"],
        "spec_hash": front["spec_hash"],
        "requirements": [
            {"id": req_front["id"], "hash": digest}
            for req_front, _, digest in reqs
        ],
    }
    return entry, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    errors = []
    drifted = []
    try:
        repo_context = P.read_context(P.REPOSITORY_CONTEXT_PATH)
        requirements = P.load_requirements()
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    workspaces = P.feature_workspaces()
    entries = []
    for workspace in workspaces:
        path = workspace / "spec.md"
        rel = path.relative_to(P.ROOT).as_posix()
        try:
            entry, changed = compile_spec(
                path, repo_context["context_hash"], requirements, args.check
            )
            entries.append(entry)
            if changed:
                drifted.append(rel)
        except (P.PipelineError, KeyError, TypeError, yaml.YAMLError) as exc:
            errors.append(P.error(rel, str(exc)))
    context = {
        "schema_version": 1,
        "repository_context_hash": repo_context["context_hash"],
        "features": sorted(entries, key=lambda item: item["workspace"]),
    }
    context["context_hash"] = P.context_hash(context)
    if P.write_json(P.SPEC_CONTEXT_PATH, context, args.check):
        drifted.append(P.SPEC_CONTEXT_PATH.relative_to(P.ROOT).as_posix())
    for message in errors:
        print(message)
    if errors:
        return 1
    if args.check and drifted:
        for rel in drifted:
            print(P.error(rel, "generated content is stale; run compile_specs.py"))
        return 1
    print(f"{'Checked' if args.check else 'Compiled'} {len(entries)} feature spec(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
