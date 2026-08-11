#!/usr/bin/env python3
"""Enforce the pinned consumer specify-cli compatibility contract."""
from __future__ import annotations

import re
import subprocess
import sys

import pipeline_common as P

EXPECTED_PACKAGE = "specify-cli"
EXPECTED_VERSION = "0.12.4"


def main() -> int:
    path = P.ROOT / ".d365" / "spec-kit" / "compatibility.yml"
    try:
        data = P.load_yaml(path)
    except P.PipelineError as exc:
        print(f"::error::{exc}. Copy integrations/spec-kit/compatibility.yml into .d365/spec-kit/compatibility.yml.")
        return 1
    spec_kit = data.get("spec_kit")
    if not isinstance(spec_kit, dict):
        print("::error file=.d365/spec-kit/compatibility.yml::missing spec_kit mapping")
        return 1
    package = spec_kit.get("package")
    version = str(spec_kit.get("version", ""))
    if package != EXPECTED_PACKAGE or version != EXPECTED_VERSION:
        print(
            "::error file=.d365/spec-kit/compatibility.yml::"
            f"expected {EXPECTED_PACKAGE} {EXPECTED_VERSION}, found {package} {version}"
        )
        return 1
    command = spec_kit.get("verify_command")
    if command != "specify version":
        print("::error file=.d365/spec-kit/compatibility.yml::verify_command must be exactly 'specify version'")
        return 1
    try:
        result = subprocess.run(
            ["specify", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        print(f"::error::specify-cli {EXPECTED_VERSION} is required but 'specify' was not found on PATH")
        return 1
    output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode:
        print(f"::error::'specify version' failed ({result.returncode}): {output}")
        return 1
    match = re.search(r"CLI Version\s+([0-9]+\.[0-9]+\.[0-9]+)", output)
    found_version = match.group(1) if match else ""
    if found_version != EXPECTED_VERSION:
        found = found_version or output or "(no CLI version reported)"
        print(f"::error::expected specify-cli exactly {EXPECTED_VERSION}; found {found}")
        return 1
    print(f"Compatibility validation passed: specify-cli {EXPECTED_VERSION}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
