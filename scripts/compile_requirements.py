#!/usr/bin/env python3
"""Generate each intake workspace requirements.md index."""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pipeline_common as P


def markdown_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", r"\|").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        requirements = P.load_requirements()
    except P.PipelineError as exc:
        print(f"::error::{exc}")
        return 1
    if not requirements:
        print(f"{'Checked' if args.check else 'Compiled'} 0 requirement index(es).")
        return 0
    grouped = defaultdict(list)
    for req_id, (front, _, path, _) in requirements.items():
        grouped[str(front["intake_batch"])].append((req_id, front, path))
    drifted = []
    for intake, rows in sorted(grouped.items()):
        try:
            registry = P.load_requirement_group_registry(intake)
        except P.PipelineError as exc:
            print(f"::error::{exc}")
            return 1
        groups = sorted(
            registry["groups"],
            key=lambda item: (item.get("order", 0), str(item.get("id", ""))),
        )
        rows_by_group: dict[str, list[tuple[str, dict[str, Any], Path]]] = defaultdict(list)
        for req_id, front, path in rows:
            group_id = front.get("requirement_group")
            if not isinstance(group_id, str):
                print(f"::error::{req_id} must declare exactly one requirement_group")
                return 1
            rows_by_group[group_id].append((req_id, front, path))
        known_groups = {str(group.get("id")) for group in groups}
        unknown_groups = sorted(set(rows_by_group) - known_groups)
        if unknown_groups:
            print(f"::error::{intake} requirements reference unknown groups: {unknown_groups}")
            return 1
        lines = [
            f"# {intake} requirements",
            "",
            "Generated index. Individual requirement files are authoritative.",
            "",
            "Requirement groups are non-governing navigation. They do not replace "
            "atomic REQ provenance or determine epic/feature boundaries.",
            "",
            "## Requirement group summary",
            "",
            "| Order | Group | Capability or process | Atomic REQs |",
            "| ---: | --- | --- | ---: |",
        ]
        for group in groups:
            group_id = str(group.get("id"))
            lines.append(
                f"| {group.get('order', '—')} | `{markdown_cell(group_id)}` | "
                f"{markdown_cell(group.get('name'))} | {len(rows_by_group.get(group_id, []))} |"
            )
        for group in groups:
            group_id = str(group.get("id"))
            group_rows = sorted(rows_by_group.get(group_id, []))
            lines.extend(
                [
                    "",
                    f"## {group.get('order')}. {group.get('name')} (`{group_id}`)",
                    "",
                    f"{group.get('description')}",
                    "",
                    f"**Atomic requirements:** {len(group_rows)}",
                    "",
                    "**Group evidence:**",
                ]
            )
            for evidence in group.get("evidence") or []:
                lines.append(
                    f"- `{markdown_cell(evidence.get('source_file'))}` — "
                    f"{markdown_cell(evidence.get('location'))}"
                )
            lines.extend(
                [
                    "",
                    "| Requirement | Title | Status | Evidence provenance |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for req_id, front, path in group_rows:
                provenance = (
                    f"`{markdown_cell(front.get('source_file'))}` — "
                    f"{markdown_cell(front.get('location'))}"
                )
                lines.append(
                    f"| [{req_id}](requirements/{path.name}) | "
                    f"{markdown_cell(front.get('title'))} | "
                    f"{markdown_cell(front.get('status'))} | {provenance} |"
                )
        target = P.ROOT / "specs" / "intakes" / intake / "requirements.md"
        if P.write_text(target, "\n".join(lines) + "\n", args.check):
            drifted.append(target.relative_to(P.ROOT).as_posix())
    if drifted and args.check:
        for path in drifted:
            print(P.error(path, "generated requirement index is stale; run compile_requirements.py"))
        return 1
    print(f"{'Checked' if args.check else 'Compiled'} {len(grouped)} requirement index(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
