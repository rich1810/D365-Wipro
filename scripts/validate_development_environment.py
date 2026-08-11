#!/usr/bin/env python3
"""Evaluate the compiler-owned developer preflight for one or all current DEV tasks."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pipeline_common as P

RESULT_CODES = {"ready": 0, "blocked": 1, "waiting-for-human": 2}
DEVELOPER_PREFLIGHT_SCHEMA_VERSION = 3
RUNTIME_EXECUTION_HOSTS = {
    "copilot_cli",
    "vscode",
    "github_copilot_cloud",
}
LOCAL_RUNTIME_HOSTS = {"copilot_cli", "vscode"}
HOST_PROOF_SCHEMA_VERSION = 1
HOST_PROOF_SOURCES = {
    "agent_runtime",
    "cloud_runtime_identity",
    "copilot_cli_session",
    "vscode_session",
}
SAFE_MCP_PROBE_KINDS = {
    "capability",
    "describe",
    "metadata_search",
    "tools_list",
}


def version_output_matches(output: str, expected: str) -> bool:
    if not expected:
        return True
    optional_v = "v?" if expected[:1].isdigit() else ""
    pattern = (
        rf"(?<![0-9A-Za-z]){optional_v}{re.escape(expected)}"
        r"(?![0-9A-Za-z.-])"
    )
    return re.search(pattern, output, flags=re.IGNORECASE) is not None


def executable_command(command: list[str]) -> list[str]:
    if not command:
        return []
    executable = shutil.which(str(command[0]))
    return [executable or str(command[0]), *[str(token) for token in command[1:]]]


def load_simulation(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise P.PipelineError(f"invalid simulation file '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise P.PipelineError("simulation root must be an object")
    return value


def load_host_proof(inline: str | None, from_stdin: bool) -> dict[str, Any]:
    if inline is not None and from_stdin:
        raise P.PipelineError(
            "provide host proof through either --host-proof-json or "
            "--host-proof-stdin, not both"
        )
    raw = sys.stdin.read() if from_stdin else inline
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise P.PipelineError(f"invalid host proof JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise P.PipelineError("host proof root must be an object")
    if value.get("schema_version") != HOST_PROOF_SCHEMA_VERSION:
        raise P.PipelineError(
            f"host proof schema_version must be {HOST_PROOF_SCHEMA_VERSION}"
        )
    return value


def find_dev(reference: str) -> Path:
    candidate = Path(reference)
    if candidate.exists():
        path = candidate.resolve()
        try:
            path.relative_to(P.ROOT)
        except ValueError as exc:
            raise P.PipelineError("DEV path must be inside the current repository") from exc
        return path
    dev_id = reference.upper()
    if not P.DEV_ID_RE.fullmatch(dev_id):
        raise P.PipelineError("DEV reference must be a DEV-#### id or artifact path")
    matches = [path for path in P.development_files() if path.stem == dev_id]
    if len(matches) != 1:
        raise P.PipelineError(
            f"DEV reference '{dev_id}' resolved to {len(matches)} artifact(s)"
        )
    return matches[0]


def simulation_result(simulation: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = (simulation.get("steps") or {}).get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return {"status": value}
    if not isinstance(value, dict):
        raise P.PipelineError(f"simulation step '{key}' must be a string or object")
    return value


def normalize_status(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in RESULT_CODES:
        raise P.PipelineError(f"unknown preflight status '{value}'")
    return normalized


def sentinel(value: Any) -> bool:
    if isinstance(value, dict):
        return any(sentinel(child) for child in value.values())
    if isinstance(value, list):
        return any(sentinel(child) for child in value)
    return isinstance(value, str) and P.UNRESOLVED_AUTHORING_VALUE in value


def mcp_servers() -> dict[str, Any]:
    path = P.ROOT / ".vscode" / "mcp.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise P.PipelineError(f".vscode/mcp.json is invalid JSON: {exc}") from exc
    servers = value.get("servers") if isinstance(value, dict) else None
    return servers if isinstance(servers, dict) else {}


def simulation_execution_host(
    simulation: dict[str, Any],
) -> tuple[str | None, set[str]]:
    value = simulation.get("execution_host")
    if value is None:
        return None, set()
    if isinstance(value, str):
        return value, set()
    if not isinstance(value, dict):
        raise P.PipelineError("simulation execution_host must be a string or object")
    host = value.get("host")
    callables = value.get("callable_mcp_servers") or []
    if host is not None and not isinstance(host, str):
        raise P.PipelineError("simulation execution_host.host must be a string")
    if not isinstance(callables, list) or not all(
        isinstance(item, str) and item for item in callables
    ):
        raise P.PipelineError(
            "simulation execution_host.callable_mcp_servers must be a string array"
        )
    return host, set(callables)


def detect_runtime_execution_host(explicit: str | None) -> tuple[str | None, str]:
    if explicit:
        return explicit, "diagnostic override"
    if str(os.environ.get("GITHUB_ACTIONS") or "").lower() == "true":
        return "github_actions", "GitHub Actions environment hint"
    vscode = (
        str(os.environ.get("TERM_PROGRAM") or "").lower() == "vscode"
        or bool(os.environ.get("VSCODE_PID"))
    )
    copilot_cli = any(
        str(os.environ.get(name) or "").lower() in {"1", "true"}
        for name in ("COPILOT_CLI", "GH_COPILOT_CLI")
    )
    if vscode and copilot_cli:
        return None, "conflicting VS Code and Copilot CLI environment hints"
    if vscode:
        return "vscode", "VS Code environment hint"
    if copilot_cli:
        return "copilot_cli", "Copilot CLI environment hint"
    return None, "no supported host signal"


def required_mcp_requirements(row: dict[str, Any]) -> dict[str, list[str]]:
    requirements: dict[str, set[str]] = {}
    for steps in row["developer_preflight"]["phases"].values():
        for step in steps:
            if not step.get("blocking") or step.get("action") not in {
                "mcp_server",
                "mcp_tools",
            }:
                continue
            server = str(step.get("server") or "")
            if not server:
                continue
            requirements.setdefault(server, set())
            if step.get("action") == "mcp_tools":
                requirements[server].update(
                    str(tool)
                    for tool in step.get("expected_tools") or []
                    if str(tool)
                )
    return {
        server: sorted(tools)
        for server, tools in sorted(requirements.items())
    }


def required_mcp_servers(row: dict[str, Any]) -> list[str]:
    return list(required_mcp_requirements(row))


def validate_dev_snapshot(row: dict[str, Any], path: Path) -> None:
    front, body, _ = P.read_markdown(path)
    if front.get("id") != row["id"]:
        raise P.PipelineError(f"{path}: DEV id does not match task context")
    if front.get("execution_host") != row.get("execution_host"):
        raise P.PipelineError(f"{path}: DEV execution_host does not match task context")
    if row.get("authentication_policy") not in P.AUTHENTICATION_POLICIES:
        raise P.PipelineError(f"{row['id']}: task context authentication policy is invalid")
    if (
        row["developer_preflight"].get("schema_version")
        != DEVELOPER_PREFLIGHT_SCHEMA_VERSION
    ):
        raise P.PipelineError(f"{row['id']}: Developer preflight schema is stale")
    if (
        row["developer_preflight"].get("authentication_policy")
        != row["authentication_policy"]
    ):
        raise P.PipelineError(
            f"{row['id']}: Developer preflight authentication policy is stale"
        )
    if row["developer_preflight"].get("execution_host") != row["execution_host"]:
        raise P.PipelineError(
            f"{row['id']}: Developer preflight execution host is stale"
        )
    if (
        row["developer_preflight"].get("capability_matrix_hash")
        != row["development_resources"]["capabilities"]["matrix_hash"]
    ):
        raise P.PipelineError(
            f"{row['id']}: Developer preflight capability matrix is stale"
        )
    expected_zone = P.render_developer_preflight(row["developer_preflight"])
    if P.compiler_zone(body, "developer-preflight") != expected_zone:
        raise P.PipelineError(
            f"{path.relative_to(P.ROOT).as_posix()}: Developer preflight is stale or authored"
        )


def host_requirements_report(row: dict[str, Any], path: Path) -> dict[str, Any]:
    validate_dev_snapshot(row, path)
    return {
        "dev": row["id"],
        "path": path.relative_to(P.ROOT).as_posix(),
        "required_execution_host": row["execution_host"],
        "required_mcp_servers": [
            {"server": server, "tools": tools}
            for server, tools in required_mcp_requirements(row).items()
        ],
    }


def host_proof_execution_host(
    host_proof: dict[str, Any],
) -> tuple[str | None, str | None]:
    if not host_proof:
        return None, None
    value = host_proof.get("execution_host")
    source = host_proof.get("host_source")
    if not isinstance(value, str) or value not in RUNTIME_EXECUTION_HOSTS:
        raise P.PipelineError(
            "host proof execution_host must be copilot_cli, vscode, or "
            "github_copilot_cloud"
        )
    if not isinstance(source, str) or source not in HOST_PROOF_SOURCES:
        raise P.PipelineError(
            "host proof host_source must identify authoritative agent/runtime "
            "or current-session evidence"
        )
    source_hosts = {
        "cloud_runtime_identity": {"github_copilot_cloud"},
        "copilot_cli_session": {"copilot_cli"},
        "vscode_session": {"vscode"},
    }
    if source in source_hosts and value not in source_hosts[source]:
        raise P.PipelineError(
            f"host proof source '{source}' cannot identify execution host '{value}'"
        )
    if value == "github_copilot_cloud" and source not in {
        "agent_runtime",
        "cloud_runtime_identity",
    }:
        raise P.PipelineError(
            "GitHub Copilot cloud requires authoritative cloud runtime identity"
        )
    return value, source


def authenticated_sessions(host_proof: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = host_proof.get("authenticated_sessions") or []
    if not isinstance(sessions, list):
        raise P.PipelineError("host proof authenticated_sessions must be an array")
    normalized: list[dict[str, Any]] = []
    for item in sessions:
        if not isinstance(item, dict):
            raise P.PipelineError(
                "each host proof authenticated session must be an object"
            )
        if set(item) - {"server", "endpoint", "status", "fresh"}:
            raise P.PipelineError(
                "host proof authenticated session contains unsupported fields"
            )
        server = item.get("server")
        endpoint = item.get("endpoint")
        status = item.get("status")
        fresh = item.get("fresh", False)
        if server is not None and (not isinstance(server, str) or not server):
            raise P.PipelineError(
                "host proof authenticated session server must be a non-empty string"
            )
        if not isinstance(endpoint, str) or not endpoint:
            raise P.PipelineError(
                "host proof authenticated session endpoint must be a non-empty string"
            )
        if status != "authenticated":
            raise P.PipelineError(
                "host proof authenticated session status must be authenticated"
            )
        if not isinstance(fresh, bool):
            raise P.PipelineError(
                "host proof authenticated session fresh must be true or false"
            )
        normalized.append(
            {
                "server": server,
                "endpoint": endpoint.rstrip("/").lower(),
                "fresh": fresh,
            }
        )
    return normalized


def matching_authenticated_session(
    sessions: list[dict[str, Any]],
    *,
    endpoints: list[str],
    server: str | None = None,
) -> dict[str, Any] | None:
    expected = {endpoint.rstrip("/").lower() for endpoint in endpoints if endpoint}
    for session in sessions:
        if session["endpoint"] not in expected:
            continue
        if server and session.get("server") not in {None, server}:
            continue
        return session
    return None


def verify_host_proof_mcp(
    row: dict[str, Any],
    host_proof: dict[str, Any],
) -> tuple[set[str], str | None, bool]:
    evidence_keys = {"servers", "tool_surface", "probes"}
    evidence_present = any(key in host_proof for key in evidence_keys)
    if not evidence_present:
        return set(), None, False

    requirements = required_mcp_requirements(row)
    server_statuses: dict[str, str] = {}
    servers = host_proof.get("servers") or []
    if not isinstance(servers, list):
        raise P.PipelineError("host proof servers must be an array")
    for item in servers:
        if not isinstance(item, dict):
            raise P.PipelineError("each host proof server must be an object")
        server = item.get("server")
        status = item.get("status")
        if not isinstance(server, str) or not server:
            raise P.PipelineError("host proof server keys must be non-empty strings")
        if server in server_statuses:
            return set(), f"duplicate MCP server evidence for '{server}'", True
        if not isinstance(status, str):
            raise P.PipelineError(
                f"host proof server '{server}' status must be a string"
            )
        server_statuses[server] = status.strip().lower().replace("_", "-")

    scoped_tools: dict[str, Counter[str]] = defaultdict(Counter)
    unscoped_tools: Counter[str] = Counter()
    disabled_scoped: dict[str, set[str]] = defaultdict(set)
    disabled_unscoped: set[str] = set()
    surface = host_proof.get("tool_surface") or []
    if not isinstance(surface, list):
        raise P.PipelineError("host proof tool_surface must be an array")
    for item in surface:
        if not isinstance(item, dict):
            raise P.PipelineError("each host proof tool must be an object")
        name = item.get("name")
        server = item.get("server")
        enabled = item.get("enabled")
        if not isinstance(name, str) or not name:
            raise P.PipelineError("host proof tool names must be non-empty strings")
        if server is not None and (not isinstance(server, str) or not server):
            raise P.PipelineError("host proof tool server must be a non-empty string")
        if not isinstance(enabled, bool):
            raise P.PipelineError(
                f"host proof tool '{name}' enabled must be true or false"
            )
        if enabled:
            if server:
                scoped_tools[server][name] += 1
            else:
                unscoped_tools[name] += 1
        elif server:
            disabled_scoped[server].add(name)
        else:
            disabled_unscoped.add(name)

    successful_probes: set[str] = set()
    failed_probes: list[str] = []
    probes = host_proof.get("probes") or []
    if not isinstance(probes, list):
        raise P.PipelineError("host proof probes must be an array")
    for item in probes:
        if not isinstance(item, dict):
            raise P.PipelineError("each host proof probe must be an object")
        server = item.get("server")
        kind = item.get("kind")
        status = item.get("status")
        if not isinstance(server, str) or not server:
            raise P.PipelineError("host proof probe server must be a non-empty string")
        if kind not in SAFE_MCP_PROBE_KINDS:
            return (
                set(),
                f"MCP probe '{kind}' for '{server}' is not an approved safe "
                "read-only capability probe",
                True,
            )
        if item.get("read_only") is not True:
            return (
                set(),
                f"MCP probe '{kind}' for '{server}' is not proven read-only",
                True,
            )
        if status not in {"failed", "succeeded"}:
            raise P.PipelineError(
                f"host proof probe status for '{server}' must be failed or succeeded"
            )
        tools = item.get("tools") or []
        if not isinstance(tools, list) or not all(
            isinstance(tool, str) and tool for tool in tools
        ):
            raise P.PipelineError(
                f"host proof probe tools for '{server}' must be a string array"
            )
        tool = item.get("tool")
        if tool is not None and (not isinstance(tool, str) or not tool):
            raise P.PipelineError(
                f"host proof probe tool for '{server}' must be a non-empty string"
            )
        if status == "failed":
            if server in requirements:
                failed_probes.append(f"{server}:{kind}")
            continue
        successful_probes.add(server)
        for name in [*tools, *([tool] if tool else [])]:
            scoped_tools[server][name] += 1

    if failed_probes:
        return (
            set(),
            "safe read-only MCP probe failed for required server(s): "
            + ", ".join(sorted(failed_probes)),
            True,
        )

    required_tool_owners: dict[str, set[str]] = defaultdict(set)
    for server, tools in requirements.items():
        for tool in tools:
            required_tool_owners[tool].add(server)

    verified: set[str] = set()
    failures: list[str] = []
    unavailable_statuses = {
        "disabled",
        "failed",
        "not-running",
        "stopped",
        "unavailable",
    }
    for server, tools in requirements.items():
        status = server_statuses.get(server)
        if status in unavailable_statuses:
            failures.append(f"{server} is {status}")
            continue
        missing: list[str] = []
        ambiguous: list[str] = []
        disabled: list[str] = []
        for tool in tools:
            if scoped_tools[server][tool]:
                continue
            if tool in disabled_scoped[server] or tool in disabled_unscoped:
                disabled.append(tool)
                continue
            count = unscoped_tools[tool]
            owners = required_tool_owners[tool]
            if count == 1 and len(owners) == 1:
                continue
            if count > 1 or (count and len(owners) > 1):
                ambiguous.append(tool)
                continue
            missing.append(tool)
        if disabled:
            failures.append(
                f"{server} has disabled required tool(s): {', '.join(sorted(disabled))}"
            )
        if ambiguous:
            failures.append(
                f"{server} has ambiguous unscoped required tool name(s): "
                + ", ".join(sorted(ambiguous))
            )
        if missing:
            failures.append(
                f"{server} is missing required tool(s): {', '.join(sorted(missing))}"
            )
        if disabled or ambiguous or missing:
            continue
        if not tools and status not in {"callable", "connected", "running"} and (
            server not in successful_probes
        ):
            failures.append(f"{server} callability is unproven")
            continue
        verified.add(server)

    if failures:
        return set(), "; ".join(failures), True
    return verified, None, True


def execution_host_result(
    row: dict[str, Any],
    *,
    simulation: dict[str, Any],
    host_proof: dict[str, Any],
    explicit_host: str | None,
    callable_mcp_servers: set[str],
    dry_run: bool,
) -> tuple[str, str, str | None, set[str]]:
    required_host = row.get("execution_host")
    if required_host not in P.EXECUTION_HOSTS:
        raise P.PipelineError(
            f"{row['id']}: task context execution_host is missing or invalid"
        )
    simulated_host, simulated_callables = simulation_execution_host(simulation)
    proved_host, proof_source = host_proof_execution_host(host_proof)
    if proved_host is not None:
        if explicit_host and explicit_host != proved_host:
            return (
                "blocked",
                f"automatic runtime evidence identifies '{proved_host}'; diagnostic "
                f"--execution-host '{explicit_host}' cannot override it",
                proved_host,
                set(),
            )
        runtime_host, source = proved_host, f"host proof ({proof_source})"
    elif explicit_host:
        runtime_host, source = explicit_host, "diagnostic override"
    elif simulated_host is not None:
        runtime_host, source = simulated_host, "simulation"
    else:
        runtime_host, source = detect_runtime_execution_host(None)
    if runtime_host is not None and runtime_host not in RUNTIME_EXECUTION_HOSTS | {
        "github_actions"
    }:
        raise P.PipelineError(f"unknown runtime execution host '{runtime_host}'")
    proof_callables, proof_failure, proof_mcp_present = verify_host_proof_mcp(
        row, host_proof
    )
    if proof_failure:
        return "blocked", proof_failure, runtime_host, set()
    if proof_mcp_present:
        callables = proof_callables
    else:
        callables = set(callable_mcp_servers) or simulated_callables
    if runtime_host is None:
        if dry_run:
            return (
                "ready",
                "execution-host contract is structurally valid; dry-run did not "
                "prove a live Copilot CLI, VS Code, or cloud coding-agent host",
                None,
                callables,
            )
        return (
            "blocked",
            "runtime execution host is ambiguous or inaccessible. Expose the current "
            "agent/runtime identity, or use --execution-host only as a diagnostic "
            "override after confirming copilot_cli, vscode, or github_copilot_cloud.",
            None,
            callables,
        )
    if runtime_host == "github_actions":
        if dry_run:
            return (
                "ready",
                "execution-host contract is structurally valid; GitHub Actions "
                "dry-run does not claim implementation-host readiness",
                runtime_host,
                callables,
            )
        return (
            "blocked",
            "GitHub Actions is a validation host, not an approved interactive DEV "
            "execution host.",
            runtime_host,
            callables,
        )
    if runtime_host == "github_copilot_cloud":
        if required_host == "local_interactive":
            return (
                "blocked",
                "GitHub Copilot cloud coding agent cannot execute this "
                "local_interactive DEV because OAuth-authenticated Dataverse MCP "
                "servers are not supported there. Run locally from the Works "
                "repository using GitHub Copilot CLI or VS Code, configure and start "
                "dataverse-authoring, complete interactive browser/device OAuth, "
                "verify its tools, then invoke /d365.implement "
                "#<development-issue-number>.",
                runtime_host,
                callables,
            )
        missing_mcp = sorted(set(required_mcp_servers(row)) - callables)
        if missing_mcp and not dry_run:
            return (
                "blocked",
                "GitHub Copilot cloud execution is host-compatible, but automatic "
                "MCP callability proof is unavailable for: "
                + ", ".join(missing_mcp)
                + ". Verify the current cloud agent tool surface and repository "
                "MCP tools allowlist, then rerun /d365.implement. Use "
                "--mcp-server-callable only as a diagnostic override when the "
                "cloud tool surface is inaccessible.",
                runtime_host,
                callables,
            )
        return (
            "ready",
            f"{required_host} permits GitHub Copilot cloud execution ({source}); "
            + (
                "required MCP servers are automatically verified callable"
                if proof_mcp_present
                else "required MCP servers are confirmed by diagnostic override"
                if callables
                else "no required MCP servers apply"
                if not required_mcp_servers(row)
                else "MCP requirements are structurally resolved for dry-run only"
            ),
            runtime_host,
            callables,
        )
    if runtime_host not in LOCAL_RUNTIME_HOSTS:
        return (
            "blocked",
            f"unsupported runtime execution host '{runtime_host}'",
            runtime_host,
            callables,
        )
    missing_mcp = sorted(set(required_mcp_servers(row)) - callables)
    if missing_mcp and not dry_run:
        instructions = (
            "Use /mcp and /env to expose the current server/tool surface"
            if runtime_host == "copilot_cli"
            else "Use MCP: List Servers and the current Chat/Agent tool surface to "
            "trust/start the server and expose its enabled tools"
        )
        return (
            "blocked",
            "local execution host is supported, but automatic MCP callability proof "
            f"is unavailable for: {', '.join(missing_mcp)}. {instructions}; rerun "
            "/d365.implement. Use --mcp-server-callable only as a diagnostic "
            "override when the active client cannot expose proof.",
            runtime_host,
            callables,
        )
    if proof_mcp_present:
        mcp_message = "automatically verified callable"
    elif callables:
        mcp_message = "confirmed callable by diagnostic override"
    elif not required_mcp_servers(row):
        mcp_message = "not required"
    else:
        mcp_message = "structurally resolved for dry-run only"
    return (
        "ready",
        f"{runtime_host} satisfies {required_host}; required local MCP servers are "
        + mcp_message,
        runtime_host,
        callables,
    )


def automatic_result(
    step: dict[str, Any],
    *,
    simulation: dict[str, Any],
    simulation_mode: bool,
    dry_run: bool,
    allow_install: bool,
    runtime_host: str | None = None,
    callable_mcp_servers: set[str] | None = None,
    authenticated_session_evidence: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    simulated = simulation_result(simulation, step["key"])
    if simulated is not None:
        status = normalize_status(str(simulated.get("status") or "ready"))
        if status == "ready":
            action = step["action"]
            if action == "command":
                expected = str(step.get("expected") or step.get("expected_version") or "")
                actual = str(simulated.get("version") or expected)
                if expected and actual != expected:
                    return "blocked", f"version mismatch: expected {expected}, found {actual}"
            elif action == "mcp_server":
                if simulated.get("server", step.get("server")) != step.get("server"):
                    return "blocked", "MCP server key mismatch"
                if simulated.get("endpoint", step.get("endpoint")) != step.get("endpoint"):
                    return "blocked", "MCP endpoint mismatch"
            elif action == "mcp_tools":
                actual = set(simulated.get("tools") or [])
                missing = sorted(set(step.get("expected_tools") or []) - actual)
                if missing:
                    return "blocked", f"missing MCP tools: {', '.join(missing)}"
            elif action == "session_validation":
                expected = {
                    str(endpoint).rstrip("/").lower()
                    for endpoint in step.get("expected_endpoints") or []
                }
                actual = str(simulated.get("endpoint") or "").rstrip("/").lower()
                if simulated.get("authenticated") is not True:
                    return "blocked", "authenticated endpoint session is not proven"
                if actual not in expected:
                    return "blocked", "authenticated session endpoint mismatch"
        return status, str(simulated.get("message") or "simulation result")

    if sentinel(step):
        return "blocked", "required value is CONFIGURE_BEFORE_IMPLEMENTATION"
    action = step["action"]
    if simulation_mode and action == "session_validation":
        return "blocked", f"simulation must provide explicit evidence for {step['key']}"
    if action == "command":
        command = executable_command(step.get("command") or [])
        if dry_run:
            return "ready", "safe command is structurally valid; dry-run did not execute it"
        for attempt in range(2):
            try:
                result = subprocess.run(
                    command,
                    cwd=P.ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = None
                failure = f"command unavailable or timed out: {exc}"
            else:
                expected = str(step.get("expected") or step.get("expected_version") or "")
                output = f"{result.stdout}\n{result.stderr}"
                if result.returncode == 0 and version_output_matches(output, expected):
                    return "ready", "safe command completed with the expected version"
                failure = (
                    f"command exited {result.returncode}"
                    if result.returncode != 0
                    else f"reported version does not match expected {expected}"
                )
            installation = step.get("installation") or {}
            if (
                attempt == 0
                and allow_install
                and installation.get("policy") == "automatic_safe"
                and installation.get("command")
            ):
                try:
                    install = subprocess.run(
                        executable_command(installation["command"]),
                        cwd=P.ROOT,
                        timeout=300,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return "blocked", f"approved automatic installation failed: {exc}"
                if install.returncode == 0:
                    continue
                return "blocked", f"approved automatic installation exited {install.returncode}"
            guidance = installation.get("guidance")
            if guidance:
                return "blocked", f"{failure}. Install guidance: {guidance}"
            return "blocked", failure
    if action == "mcp_server":
        server = str(step.get("server") or "")
        if server in (callable_mcp_servers or set()):
            return "ready", "MCP server and task-routed tools are confirmed callable"
        if dry_run:
            return (
                "ready",
                "MCP server requirement is structurally resolved; dry-run did not "
                "prove live callability",
            )
        if runtime_host == "copilot_cli":
            return (
                "blocked",
                f"confirm MCP server '{server}' through /mcp list or /env, verify "
                "its task-routed tools, then pass --mcp-server-callable",
            )
        if runtime_host == "github_copilot_cloud":
            return (
                "blocked",
                f"confirm MCP server '{server}' in repository Settings -> Copilot -> "
                "MCP servers with its tools allowlist, verify task-routed tools, then "
                "pass --mcp-server-callable",
            )
        record = mcp_servers().get(server)
        if not isinstance(record, dict):
            return "blocked", f"MCP server key '{server}' is not registered"
        expected_endpoint = step.get("endpoint")
        actual_endpoint = record.get("url")
        if expected_endpoint and actual_endpoint != expected_endpoint:
            return "blocked", "MCP endpoint does not match the compiler-owned snapshot"
        return "ready", "MCP server key and endpoint match"
    if action == "mcp_tools":
        server = str(step.get("server") or "")
        if server in (callable_mcp_servers or set()):
            return "ready", "task-routed MCP tools are confirmed callable"
        if dry_run:
            return "ready", "required tool names are resolved; runtime tools/list was not executed"
        return (
            "waiting-for-human",
            "run the active MCP client's tools/list probe and provide the result",
        )
    if action == "endpoint":
        endpoint = str(step.get("endpoint") or "")
        try:
            parsed = urlsplit(endpoint)
        except ValueError:
            return "blocked", "endpoint is invalid"
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "blocked", "endpoint must be credential-free absolute HTTPS"
        return "ready", "endpoint and API contract are structurally valid"
    if action == "package":
        if dry_run:
            return "ready", "exact package version is resolved; dry-run did not inspect a project manifest"
        return (
            "waiting-for-human",
            "verify the exact package in the DEV target project manifest and lockfile",
        )
    if action == "repository_scope":
        return "ready", "repository-only scope is explicit"
    if action == "session_validation":
        session = matching_authenticated_session(
            authenticated_session_evidence or [],
            endpoints=[str(value) for value in step.get("expected_endpoints") or []],
        )
        if session is not None:
            return (
                "ready",
                "authenticated callable session matches an exact compiler-routed endpoint",
            )
        if dry_run:
            return (
                "ready",
                "authenticated endpoint-session requirement is structurally resolved; "
                "dry-run did not prove a live session",
            )
        return (
            "waiting-for-human",
            "authenticate the routed resource at the exact declared endpoint and "
            "provide only the sanitized authenticated-session response in host proof",
        )
    if action == "target_configuration":
        if step.get("authentication_mode") not in {None, "interactive_user"}:
            return "blocked", "authentication mode must be interactive_user"
        identity_field = step.get("identity_field")
        if identity_field and not step.get("component_identity"):
            return "blocked", f"canonical {identity_field} is missing"
        return (
            "ready",
            "compiler-owned endpoint, target, canonical identity, and auth policy "
            "are concrete; live existence is deferred to the scoped action",
        )
    if action == "ready_to_attempt":
        return "ready", "evaluated after all minimal blocking safety checks"
    return "blocked", f"unsupported automatic action '{action}'"


def interactive_result(
    step: dict[str, Any],
    *,
    simulation: dict[str, Any],
    simulation_mode: bool,
    authentication_policy: str,
    run_interactive: bool,
    dry_run: bool,
    authenticated_session_evidence: list[dict[str, Any]],
) -> tuple[str, str]:
    simulated = simulation_result(simulation, step["key"])
    if simulated is not None:
        status = normalize_status(str(simulated.get("status") or "ready"))
        if status == "ready":
            fresh = simulated.get("fresh_authentication") is True
            session_valid = simulated.get("session_valid") is True
            if authentication_policy == "always_prompt" and not fresh:
                return (
                    "blocked",
                    "always_prompt simulation requires fresh_authentication: true",
                )
            if (
                authentication_policy == "reuse_if_valid"
                and not session_valid
                and not fresh
            ):
                return (
                    "blocked",
                    "reuse_if_valid simulation requires session_valid: true or "
                    "fresh_authentication: true",
                )
        return status, str(simulated.get("message") or "simulation result")
    if sentinel(step):
        return "blocked", "interactive authentication target is unresolved"
    session = matching_authenticated_session(
        authenticated_session_evidence,
        endpoints=[str(step.get("endpoint") or "")],
        server=str(step.get("server") or "") or None,
    )
    if session is not None:
        if authentication_policy == "always_prompt" and not session["fresh"]:
            return (
                "waiting-for-human",
                "always_prompt requires a fresh authenticated session for the exact "
                "declared endpoint",
            )
        return (
            "ready",
            "sanitized host proof confirms an authenticated session at the exact "
            "declared endpoint",
        )
    fresh_authentication = step["fresh_authentication"]
    session_probe = step["session_probe"]
    if simulation_mode:
        required = (
            "fresh_authentication: true"
            if authentication_policy == "always_prompt"
            else "session_valid: true or fresh_authentication: true"
        )
        return "blocked", f"simulation must provide {required} for {step['key']}"
    if dry_run:
        if authentication_policy == "always_prompt":
            return (
                "waiting-for-human",
                "always_prompt requires a fresh interactive step; dry-run did not "
                "perform authentication. " + fresh_authentication["instructions"],
            )
        return (
            "ready",
            "existing-session verification is structurally resolved; dry-run did not "
            "probe or launch authentication",
        )
    command = step.get("command")
    if authentication_policy == "reuse_if_valid":
        probe_command = session_probe.get("command")
        if probe_command:
            try:
                probe = subprocess.run(
                    executable_command(probe_command),
                    cwd=P.ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                probe = None
            if probe is not None and probe.returncode == 0:
                return (
                    "ready",
                    "existing session probe succeeded; exact endpoint-session "
                    "validation still applies",
                )
        else:
            return (
                "waiting-for-human",
                session_probe["instructions"]
                + " If the session is absent, expired, or endpoint-mismatched, "
                + fresh_authentication["instructions"],
            )
        if not run_interactive:
            return (
                "waiting-for-human",
                "existing session probe did not establish validity. "
                + fresh_authentication["instructions"],
            )
    if (
        run_interactive
        and fresh_authentication["force_supported"]
        and command
    ):
        try:
            result = subprocess.run(
                executable_command(command),
                cwd=P.ROOT,
                check=False,
            )
        except OSError as exc:
            return "blocked", f"interactive command is unavailable: {exc}"
        if result.returncode != 0:
            return "blocked", f"interactive command exited {result.returncode}"
        return (
            "ready",
            "fresh interactive command completed; exact endpoint-session validation "
            "still applies",
        )
    return "waiting-for-human", str(
        fresh_authentication["instructions"]
    )


def evaluate(
    row: dict[str, Any],
    path: Path,
    *,
    simulation: dict[str, Any],
    host_proof: dict[str, Any],
    simulation_mode: bool,
    execution_host: str | None,
    callable_mcp_servers: set[str],
    host_check_only: bool,
    authentication_policy: str,
    run_interactive: bool,
    dry_run: bool,
    allow_install: bool,
) -> dict[str, Any]:
    validate_dev_snapshot(row, path)
    host_status, host_message, runtime_host, verified_mcp_servers = execution_host_result(
        row,
        simulation=simulation,
        host_proof=host_proof,
        explicit_host=execution_host,
        callable_mcp_servers=callable_mcp_servers,
        dry_run=dry_run,
    )
    results: list[dict[str, Any]] = [
        {
            "phase": "execution_host",
            "key": "execution-host.compatibility",
            "role": "required",
            "blocking": True,
            "status": host_status,
            "message": host_message,
        }
    ]
    session_evidence = authenticated_sessions(host_proof)
    if host_check_only or host_status != "ready":
        return {
            "dev": row["id"],
            "path": path.relative_to(P.ROOT).as_posix(),
            "status": host_status,
            "execution_host": runtime_host,
            "required_execution_host": row["execution_host"],
            "verified_mcp_servers": sorted(verified_mcp_servers),
            "authentication_policy": authentication_policy,
            "result_semantics": (
                "host_ready" if host_status == "ready" else host_status
            ),
            "ready_to_attempt": False,
            "results": results,
        }
    blocking_statuses: list[str] = []
    _, simulated_callables = simulation_execution_host(simulation)
    effective_callables = (
        verified_mcp_servers
        or set(callable_mcp_servers)
        or simulated_callables
    )
    phases = row["developer_preflight"]["phases"]
    for phase in P.PREFLIGHT_OUTPUT_PHASES:
        for step in phases.get(phase) or []:
            if step["action"] == "ready_to_attempt":
                continue
            if step["behavior"] == "automatic":
                status, message = automatic_result(
                    step,
                    simulation=simulation,
                    simulation_mode=simulation_mode,
                    dry_run=dry_run,
                    allow_install=allow_install,
                    runtime_host=runtime_host,
                    callable_mcp_servers=effective_callables,
                    authenticated_session_evidence=session_evidence,
                )
            elif step["behavior"] == "interactive":
                status, message = interactive_result(
                    step,
                    simulation=simulation,
                    simulation_mode=simulation_mode,
                    authentication_policy=authentication_policy,
                    run_interactive=run_interactive,
                    dry_run=dry_run,
                    authenticated_session_evidence=session_evidence,
                )
            else:
                simulated = simulation_result(simulation, step["key"])
                if simulated is None:
                    status = "ready" if dry_run else "waiting-for-human"
                    message = (
                        "manual action is resolved; dry-run did not perform it"
                        if status == "ready"
                        else str(step.get("instructions") or "complete the declared manual action")
                    )
                else:
                    status = normalize_status(str(simulated.get("status") or "ready"))
                    message = str(simulated.get("message") or "simulation result")
            result = {
                "phase": phase,
                "key": step["key"],
                "role": step["role"],
                "blocking": bool(step.get("blocking")),
                "status": status,
                "message": message,
            }
            results.append(result)
            if result["blocking"]:
                blocking_statuses.append(status)
    status = (
        "blocked"
        if "blocked" in blocking_statuses
        else "waiting-for-human"
        if "waiting-for-human" in blocking_statuses
        else "ready"
    )
    results.append(
        {
            "phase": "ready_to_attempt",
            "key": "ready-to-attempt.required-safety",
            "role": "required",
            "blocking": True,
            "status": status,
            "message": (
                "minimal safety checks passed; ready to attempt the exact scoped action"
                if status == "ready"
                else "the scoped action remains blocked until minimal safety checks pass"
            ),
        }
    )
    diagnostic_only = simulation_mode or dry_run
    return {
        "dev": row["id"],
        "path": path.relative_to(P.ROOT).as_posix(),
        "status": status,
        "execution_host": runtime_host,
        "required_execution_host": row["execution_host"],
        "verified_mcp_servers": sorted(verified_mcp_servers),
        "authentication_policy": authentication_policy,
        "result_semantics": (
            "diagnostic_only"
            if diagnostic_only
            else "ready_to_attempt"
            if status == "ready"
            else status
        ),
        "ready_to_attempt": status == "ready" and not diagnostic_only,
        "results": results,
    }


def print_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(
        f"{result['dev']}: {result['status']} "
        f"({result['result_semantics']}; "
        f"execution_host={result.get('execution_host') or 'unproven'}, "
        f"required_execution_host={result['required_execution_host']}, "
        f"authentication_policy={result['authentication_policy']})"
    )
    for item in result["results"]:
        print(
            f"  [{item['status']}] {item['phase']} / {item['key']}: "
            f"{item['message']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dev", nargs="?", help="DEV-#### id or artifact path")
    parser.add_argument("--check-all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--simulate", type=Path)
    parser.add_argument(
        "--execution-host",
        choices=sorted(RUNTIME_EXECUTION_HOSTS),
        help="diagnostic override when automatic runtime identity is inaccessible",
    )
    parser.add_argument(
        "--mcp-server-callable",
        action="append",
        default=[],
        metavar="SERVER",
        help="diagnostic override when automatic MCP proof is inaccessible",
    )
    proof_group = parser.add_mutually_exclusive_group()
    proof_group.add_argument(
        "--host-proof-json",
        metavar="JSON",
        help="ephemeral structured host/tool proof produced by the active agent",
    )
    proof_group.add_argument(
        "--host-proof-stdin",
        action="store_true",
        help="read ephemeral structured host/tool proof JSON from standard input",
    )
    parser.add_argument(
        "--host-requirements",
        action="store_true",
        help="print compiler-owned execution-host and required MCP tool requirements",
    )
    parser.add_argument(
        "--host-check-only",
        action="store_true",
        help="evaluate execution-host compatibility before the live Project gate",
    )
    parser.add_argument(
        "--run-interactive",
        "--interactive",
        dest="run_interactive",
        action="store_true",
        help="launch only approved interactive authentication commands",
    )
    parser.add_argument(
        "--authentication-policy",
        choices=sorted(P.AUTHENTICATION_POLICIES),
        help="override the project authentication policy for this invocation",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="run only registry-approved automatic_safe installation commands",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if bool(args.dev) == bool(args.check_all):
        parser.error("provide exactly one DEV reference or --check-all")
    try:
        context = P.read_context(P.TASK_CONTEXT_PATH)
        rows = {row["id"]: row for row in context.get("tasks") or []}
        simulation = load_simulation(args.simulate)
        host_proof = load_host_proof(args.host_proof_json, args.host_proof_stdin)
        references = sorted(rows) if args.check_all else [find_dev(args.dev).stem]
        if args.host_requirements:
            if args.check_all:
                raise P.PipelineError("--host-requirements requires one DEV reference")
            row = rows.get(references[0])
            if row is None:
                raise P.PipelineError(
                    f"{references[0]} is not a current task-context DEV"
                )
            report = host_requirements_report(row, find_dev(references[0]))
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(
                    f"{report['dev']}: required_execution_host="
                    f"{report['required_execution_host']}"
                )
                for requirement in report["required_mcp_servers"]:
                    tools = ", ".join(requirement["tools"]) or "(none)"
                    print(f"  {requirement['server']}: {tools}")
            return 0
        reports = []
        for dev_id in references:
            row = rows.get(dev_id)
            if row is None:
                raise P.PipelineError(f"{dev_id} is not a current task-context DEV")
            reports.append(
                evaluate(
                    row,
                    find_dev(dev_id),
                    simulation=simulation,
                    host_proof=host_proof,
                    simulation_mode=args.simulate is not None,
                    execution_host=args.execution_host,
                    callable_mcp_servers=set(args.mcp_server_callable),
                    host_check_only=args.host_check_only,
                    authentication_policy=(
                        args.authentication_policy or row["authentication_policy"]
                    ),
                    run_interactive=args.run_interactive,
                    dry_run=args.dry_run or args.simulate is not None,
                    allow_install=args.install,
                )
            )
    except (OSError, P.PipelineError) as exc:
        print(f"::error::{exc}")
        return 1
    aggregate = (
        "blocked"
        if any(report["status"] == "blocked" for report in reports)
        else "waiting-for-human"
        if any(report["status"] == "waiting-for-human" for report in reports)
        else "ready"
    )
    if args.check_all:
        output = {"status": aggregate, "count": len(reports), "reports": reports}
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            for report in reports:
                print_result(report, False)
            print(f"Current DEV preflight summary: {aggregate} ({len(reports)} task(s)).")
    else:
        print_result(reports[0], args.json)
    return RESULT_CODES[aggregate]


if __name__ == "__main__":
    sys.exit(main())
