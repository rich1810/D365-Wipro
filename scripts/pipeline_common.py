#!/usr/bin/env python3
"""Shared deterministic artifact and context helpers."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
CONVENTIONS_PATH = ROOT / "conventions.yml"
SCHEMA_DIR = ROOT / "specs" / "_schema"
REPOSITORY_CONTEXT_PATH = ROOT / "specs" / "_index" / "repository-context.json"
SPEC_CONTEXT_PATH = ROOT / ".specify" / "context" / "spec-context.json"
PLAN_CONTEXT_PATH = ROOT / ".specify" / "context" / "plan-context.json"
TASK_CONTEXT_PATH = ROOT / ".specify" / "context" / "task-context.json"
AUTHORING_TARGETS_PATH = ROOT / ".d365" / "authoring-targets.yml"
AUTHORING_TARGETS_SCHEMA_PATH = ROOT / ".d365" / "authoring-targets.schema.json"
DEVELOPMENT_RESOURCES_PATH = ROOT / ".d365" / "development-resources.yml"
DEVELOPMENT_RESOURCES_SCHEMA_PATH = ROOT / ".d365" / "development-resources.schema.json"
DATAVERSE_CAPABILITIES_PATH = ROOT / ".d365" / "dataverse-web-api-capabilities.yml"
DATAVERSE_CAPABILITIES_SCHEMA_PATH = (
    ROOT / ".d365" / "dataverse-web-api-capabilities.schema.json"
)

FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
COMPILER_ZONE = re.compile(
    r"(<!-- COMPILER:BEGIN ([A-Za-z0-9_-]+) -->)\r?\n.*?\r?\n?(<!-- COMPILER:END \2 -->)",
    re.DOTALL,
)
FILL_ZONE = re.compile(
    r"<!-- FILL:([A-Za-z0-9_-]+) -->\r?\n(.*?)\r?\n<!-- /FILL -->",
    re.DOTALL,
)
HASH_LINE = re.compile(r"(?m)^([a-z_]+_hash):\s*.*$")
WORKSPACE_RE = re.compile(r"^([0-9]{3})-([a-z0-9]+(?:-[a-z0-9]+)*)$")
DEV_ID_RE = re.compile(r"^DEV-([0-9]{4})$")
TERMINAL_DEV_STATUSES = frozenset({"completed", "superseded", "cancelled"})
IMPLEMENTATION_SCOPES = frozenset(
    {
        "repository_only",
        "repository_and_dataverse_solution",
        "repository_and_dataverse_environment",
    }
)
EXECUTION_HOSTS = frozenset({"local_interactive", "cloud_or_local"})
RESOURCE_EXECUTION_HOSTS = frozenset(
    {"local_interactive", "github_copilot_cloud"}
)
UNRESOLVED_AUTHORING_VALUE = "CONFIGURE_BEFORE_IMPLEMENTATION"
AUTHENTICATION_POLICIES = frozenset({"reuse_if_valid", "always_prompt"})
PREFLIGHT_PHASES = (
    "availability_version",
    "capability",
    "authentication",
    "manual",
)
PREFLIGHT_OUTPUT_PHASES = (
    "availability_version",
    "capability",
    "authentication",
    "session_validation",
    "target_configuration",
    "ready_to_attempt",
)
PREFLIGHT_PLACEHOLDERS = frozenset(
    {
        "authoring_target.environment_url",
        "authoring_target.environment_id",
        "authoring_target.solution_unique_name",
        "authoring_target.publisher_name",
        "authoring_target.publisher_prefix",
        "component.identity",
        "resource.version",
        "resource.endpoint",
        "resource.server",
        "resource.command",
        "resource.package",
        "resource.required_tools",
    }
)
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
SECRET_FIELD = re.compile(
    r"^(?:password|token|access_token|refresh_token|client_secret|secret|"
    r"credential|credentials|connection_string|auth_profile|certificate)$",
    re.IGNORECASE,
)
DESTRUCTIVE_COMMAND_WORDS = frozenset(
    {
        "delete",
        "destroy",
        "drop",
        "erase",
        "format",
        "purge",
        "remove",
        "reset",
        "truncate",
        "uninstall",
    }
)
DATAVERSE_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.crm[0-9]*\."
    r"(?:dynamics\.com|microsoftdynamics\.com|microsoftdynamics\.us|"
    r"appsplatform\.us|dynamics\.cn|microsoftdynamics\.de)$",
    re.IGNORECASE,
)
ALLOWED_CAPABILITY_PATH_TEMPLATES = frozenset(
    {
        "EntityDefinitions",
        "EntityDefinitions(LogicalName='{identity}')",
        "EntityDefinitions(LogicalName='{table}')/Attributes",
        "EntityDefinitions(LogicalName='{table}')/Attributes(LogicalName='{identity}')",
        "EntityDefinitions(LogicalName='{table}')/Keys",
        "EntityDefinitions(LogicalName='{table}')/Keys(MetadataId='{metadata_id}')",
        "EntityDefinitions(LogicalName='{table}')/Keys(LogicalName='{identity}')",
        "RelationshipDefinitions",
        "RelationshipDefinitions(SchemaName='{identity}')",
        "RelationshipDefinitions(MetadataId='{metadata_id}')",
        "GlobalOptionSetDefinitions",
        "GlobalOptionSetDefinitions(Name='{identity}')",
        "UpdateOptionValue",
        "environmentvariabledefinitions",
        "environmentvariabledefinitions({record_id})",
        "connectionreferences",
        "connectionreferences({record_id})",
        "PublishXml",
        "AddSolutionComponent",
        "RemoveSolutionComponent",
    }
)


class PipelineError(ValueError):
    """A deterministic contract violation."""


def normalize_text(text: str) -> str:
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip() + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def hash_json(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_text(path: Path, text: str, check: bool = False) -> bool:
    normalized = normalize_text(text)
    current = normalize_text(path.read_text(encoding="utf-8")) if path.exists() else None
    if current == normalized:
        return False
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8", newline="\n")
    return True


def write_json(path: Path, data: dict[str, Any], check: bool = False) -> bool:
    return write_text(path, canonical_json(data), check)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PipelineError(f"required file not found: {path.relative_to(ROOT).as_posix()}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PipelineError(f"invalid YAML in {path.relative_to(ROOT).as_posix()}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"YAML root must be a mapping: {path.relative_to(ROOT).as_posix()}")
    return value


def conventions() -> dict[str, Any]:
    return load_yaml(CONVENTIONS_PATH)


def split_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER.match(text)
    if not match:
        raise PipelineError("missing YAML front matter")
    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PipelineError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(front, dict):
        raise PipelineError("front matter must be a mapping")
    return front, match.group(2)


def read_markdown(path: Path) -> tuple[dict[str, Any], str, str]:
    if not path.exists():
        raise PipelineError(f"required artifact not found: {path.relative_to(ROOT).as_posix()}")
    text = normalize_text(path.read_text(encoding="utf-8"))
    front, body = split_markdown(text)
    return front, body, text


def render_markdown(front: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{yaml_text}\n---\n{normalize_text(body)}"


def authoritative_hash(text: str, hash_field: str) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(
        rf"(?m)^{re.escape(hash_field)}:\s*.*$",
        f'{hash_field}: ""',
        normalized,
        count=1,
    )
    normalized = COMPILER_ZONE.sub(lambda m: m.group(1) + "\n" + m.group(3), normalized)
    return sha256_text(normalized)


def replace_compiler_zone(text: str, name: str, content: str) -> str:
    pattern = re.compile(
        rf"(<!-- COMPILER:BEGIN {re.escape(name)} -->)\r?\n.*?\r?\n?(<!-- COMPILER:END {re.escape(name)} -->)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise PipelineError(f"missing COMPILER zone '{name}'")
    return pattern.sub(
        lambda m: m.group(1) + "\n" + content.rstrip() + "\n" + m.group(2),
        text,
        count=1,
    )


def compiler_zone(text: str, name: str) -> str | None:
    pattern = re.compile(
        rf"<!-- COMPILER:BEGIN {re.escape(name)} -->\r?\n(.*?)\r?\n<!-- COMPILER:END {re.escape(name)} -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def parse_yaml_fence(text: str, description: str) -> dict[str, Any]:
    fenced = re.fullmatch(r"```ya?ml\s*\n(.*?)\n```", text.strip(), re.DOTALL)
    if not fenced:
        raise PipelineError(f"{description} must contain exactly one YAML fenced block")
    try:
        value = yaml.safe_load(fenced.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PipelineError(f"invalid YAML in {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"{description} YAML root must be a mapping")
    return value


def development_files() -> list[Path]:
    return sorted((ROOT / "specs").glob("*/development/DEV-[0-9][0-9][0-9][0-9].md"))


def fill_zone(body: str, name: str) -> str:
    pattern = re.compile(
        rf"<!-- FILL:{re.escape(name)} -->\r?\n(.*?)\r?\n<!-- /FILL -->",
        re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        raise PipelineError(f"missing FILL zone '{name}'")
    return match.group(1).strip()


def feature_workspaces() -> list[Path]:
    specs = ROOT / "specs"
    if not specs.exists():
        return []
    result = [p for p in specs.iterdir() if p.is_dir() and WORKSPACE_RE.fullmatch(p.name)]
    return sorted(result, key=lambda p: (int(p.name[:3]), p.name))


def requirement_files() -> list[Path]:
    base = ROOT / "specs" / "intakes"
    return sorted(base.glob("INTK-[0-9][0-9][0-9][0-9]/requirements/INTK-*-REQ-*.md"))


def requirement_group_registry_path(intake: str) -> Path:
    return ROOT / "specs" / "intakes" / intake / "requirement-groups.yml"


def requirement_group_registry_files() -> list[Path]:
    base = ROOT / "specs" / "intakes"
    return sorted(base.glob("INTK-[0-9][0-9][0-9][0-9]/requirement-groups.yml"))


def load_requirement_group_registry(intake: str) -> dict[str, Any]:
    path = requirement_group_registry_path(intake)
    data = load_yaml(path)
    if data.get("intake") != intake:
        raise PipelineError(
            f"{path.relative_to(ROOT).as_posix()}: intake must be '{intake}'"
        )
    groups = data.get("groups")
    if not isinstance(groups, list) or not groups:
        raise PipelineError(
            f"{path.relative_to(ROOT).as_posix()}: groups must be a non-empty list"
        )
    return data


def load_requirements() -> dict[str, tuple[dict[str, Any], str, Path, str]]:
    result: dict[str, tuple[dict[str, Any], str, Path, str]] = {}
    for path in requirement_files():
        front, body, text = read_markdown(path)
        req_id = front.get("id")
        if not isinstance(req_id, str):
            raise PipelineError(f"{path.relative_to(ROOT).as_posix()}: missing requirement id")
        if req_id in result:
            raise PipelineError(f"duplicate requirement id: {req_id}")
        result[req_id] = (front, body, path, text)
    return result


def type_matches(component_type: str, pattern: str) -> bool:
    return fnmatchcase(component_type, pattern)


def resolve_component_skill(component_type: str, data: dict[str, Any]) -> str:
    matches = [
        skill
        for skill, patterns in (data.get("component_type_skills") or {}).items()
        if any(type_matches(component_type, str(pattern)) for pattern in patterns or [])
    ]
    if len(matches) != 1:
        raise PipelineError(
            f"component_type '{component_type}' must resolve to exactly one component_type_skills entry; "
            f"resolved: {matches or 'none'}"
        )
    return str(matches[0])


def component_type_allowed(component_type: str, data: dict[str, Any]) -> bool:
    return any(type_matches(component_type, str(pattern)) for pattern in data.get("component_types") or [])


def resolve_implementation_scope(component_type: str, data: dict[str, Any]) -> str:
    mappings = data.get("component_implementation_scopes") or {}
    matches: list[tuple[int, str, str]] = []
    for scope, patterns in mappings.items():
        if scope not in IMPLEMENTATION_SCOPES:
            raise PipelineError(f"unknown implementation scope '{scope}' in conventions.yml")
        for pattern in patterns or []:
            pattern = str(pattern)
            if type_matches(component_type, pattern):
                matches.append((2 if "*" not in pattern else 1, str(scope), pattern))
    if not matches:
        raise PipelineError(f"component_type '{component_type}' has no implementation scope")
    precedence = max(item[0] for item in matches)
    winners = [(scope, pattern) for rank, scope, pattern in matches if rank == precedence]
    if len(winners) != 1:
        raise PipelineError(
            f"component_type '{component_type}' must resolve to exactly one implementation scope; "
            f"resolved: {winners}"
        )
    return winners[0][0]


def resolve_execution_host(
    implementation_scope: str,
    development_resources: dict[str, Any],
) -> str:
    required = development_resources.get("required") or []
    if not required:
        raise PipelineError("execution host resolution requires at least one required resource")
    unsupported_local = sorted(
        item["id"]
        for item in required
        if "local_interactive" not in (item.get("supported_execution_hosts") or [])
    )
    if unsupported_local:
        raise PipelineError(
            "required resource(s) do not support local interactive execution: "
            + ", ".join(unsupported_local)
        )
    if implementation_scope in {
        "repository_and_dataverse_solution",
        "repository_and_dataverse_environment",
    }:
        return "local_interactive"
    if implementation_scope != "repository_only":
        raise PipelineError(f"unknown implementation scope '{implementation_scope}'")
    cloud_limited = sorted(
        item["id"]
        for item in required
        if "github_copilot_cloud" not in (item.get("supported_execution_hosts") or [])
    )
    return "local_interactive" if cloud_limited else "cloud_or_local"


def _authoring_schema() -> dict[str, Any]:
    if not AUTHORING_TARGETS_SCHEMA_PATH.exists():
        raise PipelineError("required file not found: .d365/authoring-targets.schema.json")
    try:
        value = json.loads(AUTHORING_TARGETS_SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"invalid authoring target schema: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError("authoring target schema root must be an object")
    return value


def _normalize_environment_url(alias: str, value: str) -> str:
    if value == UNRESOLVED_AUTHORING_VALUE:
        return value
    if value != value.strip():
        raise PipelineError(f"environment '{alias}' environment_url must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PipelineError(f"environment '{alias}' environment_url is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise PipelineError(
            f"environment '{alias}' environment_url must be an absolute HTTPS URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise PipelineError(
            f"environment '{alias}' environment_url must not contain embedded credentials"
        )
    if parsed.query or parsed.fragment:
        raise PipelineError(
            f"environment '{alias}' environment_url must not contain a query or fragment"
        )
    if parsed.path not in {"", "/"}:
        raise PipelineError(
            f"environment '{alias}' environment_url must not contain a non-root path"
        )
    if port is not None:
        raise PipelineError(f"environment '{alias}' environment_url must not contain a port")
    hostname = (parsed.hostname or "").lower()
    if not DATAVERSE_HOST.fullmatch(hostname):
        raise PipelineError(
            f"environment '{alias}' environment_url host '{hostname}' is not a supported "
            "Dataverse/Dynamics host"
        )
    if hostname.split(".", 1)[0] in {"disco", "globaldisco"}:
        raise PipelineError(
            f"environment '{alias}' environment_url must identify a Dataverse environment, "
            f"not discovery host '{hostname}'"
        )
    return f"https://{hostname}"


def _normalize_environment_id(alias: str, value: str) -> str:
    if value == UNRESOLVED_AUTHORING_VALUE:
        return value
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise PipelineError(
            f"environment '{alias}' environment_id must be a valid GUID"
        ) from exc
    return str(parsed)


def load_authoring_targets(data: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = load_yaml(AUTHORING_TARGETS_PATH) if data is None else data
    try:
        Draft202012Validator(_authoring_schema()).validate(contract)
    except ValidationError as exc:
        raise PipelineError(f"invalid authoring target contract: {exc.message}") from exc
    environments = contract.get("environments") or {}
    solutions = contract.get("solutions") or {}
    targets = contract.get("targets") or {}
    seen_urls: dict[str, str] = {}
    seen_ids: dict[str, str] = {}
    for environment_name, environment in environments.items():
        environment_url = _normalize_environment_url(
            environment_name, environment["environment_url"]
        )
        environment_id = _normalize_environment_id(
            environment_name, environment["environment_id"]
        )
        environment["environment_url"] = environment_url
        environment["environment_id"] = environment_id
        if environment_url != UNRESOLVED_AUTHORING_VALUE:
            other = seen_urls.get(environment_url)
            if other:
                raise PipelineError(
                    f"environments '{other}' and '{environment_name}' use duplicate normalized "
                    f"environment_url '{environment_url}'"
                )
            seen_urls[environment_url] = environment_name
        if environment_id != UNRESOLVED_AUTHORING_VALUE:
            other = seen_ids.get(environment_id)
            if other:
                raise PipelineError(
                    f"environments '{other}' and '{environment_name}' use duplicate "
                    f"environment_id '{environment_id}'"
                )
            seen_ids[environment_id] = environment_name
    for solution_name, solution in solutions.items():
        environment_name = solution["environment"]
        if environment_name not in environments:
            raise PipelineError(
                f"solution '{solution_name}' references unknown environment '{environment_name}'"
            )
        normalized_unique_name = re.sub(r"[^a-z0-9]", "", solution["unique_name"].lower())
        if normalized_unique_name in {"default", "defaultsolution", "commondataservicesdefaultsolution"}:
            raise PipelineError(
                f"solution '{solution_name}' must reference a predefined custom solution, "
                f"not '{solution['unique_name']}'"
            )
        if solution["publisher_prefix"].lower() == "new":
            raise PipelineError(
                f"solution '{solution_name}' must not use the default publisher prefix 'new'"
            )
    for target_name, target in targets.items():
        environment_name = target["environment"]
        if environment_name not in environments:
            raise PipelineError(
                f"target '{target_name}' references unknown environment '{environment_name}'"
            )
        if target["kind"] == "dataverse_solution":
            solution_name = target["solution"]
            if solution_name not in solutions:
                raise PipelineError(
                    f"target '{target_name}' references unknown solution '{solution_name}'"
                )
            if solutions[solution_name]["environment"] != environment_name:
                raise PipelineError(
                    f"target '{target_name}' environment does not match solution '{solution_name}'"
                )
    families = {f"{value}*" for value in conventions().get("component_type_families") or []}
    for index, route in enumerate(contract.get("routing") or [], start=1):
        target_name = route["target"]
        if target_name not in targets:
            raise PipelineError(f"routing entry {index} references unknown target '{target_name}'")
        selector = route["match"]
        if selector.get("component_id"):
            required_approval = ("rationale", "approved_by", "approved_on")
            missing = [field for field in required_approval if not route.get(field)]
            if missing:
                raise PipelineError(
                    f"routing entry {index} component override requires {', '.join(missing)}"
                )
            try:
                date.fromisoformat(str(route["approved_on"]))
            except ValueError as exc:
                raise PipelineError(
                    f"routing entry {index} approved_on must be a real ISO calendar date"
                ) from exc
        component_type = selector.get("component_type")
        if component_type:
            if "*" in component_type:
                if component_type not in families:
                    raise PipelineError(
                        f"routing entry {index} wildcard '{component_type}' is not a registered component family"
                    )
            elif not component_type_allowed(component_type, conventions()):
                raise PipelineError(
                    f"routing entry {index} references unknown component_type '{component_type}'"
                )
    for pattern in conventions().get("component_types") or []:
        sample = str(pattern).replace("*", "example")
        resolve_implementation_scope(sample, conventions())
    return json.loads(json.dumps(contract, sort_keys=True))


def resolve_authoring_target(
    component_id: str,
    component_type: str,
    scope: str,
    contract: dict[str, Any],
) -> dict[str, Any] | None:
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for index, route in enumerate(contract.get("routing") or []):
        selector = route["match"]
        if selector.get("component_id") == component_id:
            matches.append((3, index, route))
            continue
        pattern = selector.get("component_type")
        if not pattern or not type_matches(component_type, pattern):
            continue
        matches.append((2 if "*" not in pattern else 1, index, route))
    if scope == "repository_only":
        if matches:
            raise PipelineError(
                f"repository-only component {component_id} ({component_type}) must not have a Dataverse route"
            )
        return None
    if not matches:
        raise PipelineError(
            f"component {component_id} ({component_type}) has no authoring target route"
        )
    precedence = max(item[0] for item in matches)
    winners = [(index, route) for rank, index, route in matches if rank == precedence]
    if len(winners) != 1:
        raise PipelineError(
            f"component {component_id} ({component_type}) has ambiguous authoring target routing "
            f"at precedence {precedence}: {[route['target'] for _, route in winners]}"
        )
    route = winners[0][1]
    target_name = route["target"]
    target = contract["targets"][target_name]
    expected_kind = {
        "repository_and_dataverse_solution": "dataverse_solution",
        "repository_and_dataverse_environment": "dataverse_environment",
    }[scope]
    if target["kind"] != expected_kind:
        raise PipelineError(
            f"component {component_id} ({component_type}) scope '{scope}' requires target kind "
            f"'{expected_kind}', found '{target['kind']}'"
        )
    environment_name = target["environment"]
    environment = contract["environments"][environment_name]
    resolved: dict[str, Any] = {
        "name": target_name,
        "kind": target["kind"],
        "environment": environment_name,
        "environment_url": environment["environment_url"],
        "environment_id": environment["environment_id"],
        "authentication_mode": environment["authentication_mode"],
    }
    if environment.get("connection_alias"):
        resolved["connection_alias"] = environment["connection_alias"]
    if target["kind"] == "dataverse_solution":
        solution_name = target["solution"]
        solution = contract["solutions"][solution_name]
        resolved.update(
            {
                "solution": solution_name,
                "solution_unique_name": solution["unique_name"],
                "publisher_name": solution["publisher_name"],
                "publisher_prefix": solution["publisher_prefix"],
            }
        )
    return resolved


def resolve_component_authoring(
    component: dict[str, Any],
    data: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    component_id = str(component.get("id") or "")
    component_type = str(component.get("component_type") or "")
    scope = resolve_implementation_scope(component_type, data)
    target = resolve_authoring_target(component_id, component_type, scope, contract)
    return scope, target


def required_component_fields(component_type: str, data: dict[str, Any]) -> list[str]:
    payloads = data.get("component_type_payloads") or {}
    exact = payloads.get(component_type)
    if exact is None:
        matches = [
            value for pattern, value in payloads.items()
            if pattern != "_default" and type_matches(component_type, str(pattern))
        ]
        if len(matches) > 1:
            raise PipelineError(f"component_type '{component_type}' matches multiple payload contracts")
        exact = matches[0] if matches else payloads.get("_default")
    required = exact.get("required") if isinstance(exact, dict) else None
    if not isinstance(required, list) or not required:
        raise PipelineError(f"component_type '{component_type}' has no required payload contract")
    return [str(item) for item in required]


def required_component_identity_field(scope: str, data: dict[str, Any]) -> str | None:
    mappings = data.get("component_identity_fields") or {}
    value = mappings.get(scope)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PipelineError(f"implementation scope '{scope}' has an invalid identity field")
    return value


def load_dataverse_capabilities(value: dict[str, Any] | None = None) -> dict[str, Any]:
    matrix = value if value is not None else load_yaml(DATAVERSE_CAPABILITIES_PATH)
    if not DATAVERSE_CAPABILITIES_SCHEMA_PATH.exists():
        raise PipelineError(
            "required file not found: .d365/dataverse-web-api-capabilities.schema.json"
        )
    try:
        schema = json.loads(
            DATAVERSE_CAPABILITIES_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(matrix)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise PipelineError(f"invalid Dataverse Web API capability matrix: {exc}") from exc

    references = matrix.get("official_references") or {}
    profiles = matrix.get("profiles") or {}
    components = matrix.get("components") or {}
    expected_components = set(conventions().get("component_types") or [])
    actual_components = set(components)
    if actual_components != expected_components:
        missing = sorted(expected_components - actual_components)
        extra = sorted(actual_components - expected_components)
        raise PipelineError(
            "Dataverse Web API capability matrix component coverage mismatch: "
            f"missing={missing or 'none'}, extra={extra or 'none'}"
        )
    for profile_id, profile in profiles.items():
        if not profile.get("official_references"):
            raise PipelineError(
                f"capability profile '{profile_id}' has no official reference"
            )
        for reference_id in profile.get("official_references") or []:
            if reference_id not in references:
                raise PipelineError(
                    f"capability profile '{profile_id}' references unknown official "
                    f"reference '{reference_id}'"
                )
        http = profile.get("http")
        if http and http.get("path_template") not in ALLOWED_CAPABILITY_PATH_TEMPLATES:
            raise PipelineError(
                f"capability profile '{profile_id}' uses non-whitelisted path template "
                f"'{http.get('path_template')}'"
            )
        if profile.get("support") == "supported":
            if profile.get("primary_resource") != "dataverse-web-api":
                raise PipelineError(
                    f"supported capability profile '{profile_id}' must use "
                    "dataverse-web-api primary"
                )
            if not (profile.get("executor") or {}).get("supported"):
                raise PipelineError(
                    f"supported capability profile '{profile_id}' is not executor-enabled"
                )
            if profile.get("operation") in {"create", "update", "delete"}:
                mechanism = (profile.get("solution_context") or {}).get("mechanism")
                if mechanism not in {
                    "MSCRM.SolutionUniqueName",
                    "action_parameter",
                }:
                    raise PipelineError(
                        f"supported write profile '{profile_id}' lacks explicit "
                        "solution context"
                    )
            fallback = profile.get("fallback") or {}
            if (
                fallback.get("resource") == "maker-portal"
                and fallback.get("allowed_when")
                == "documented_operation_unsupported_or_primary_unavailable"
                and fallback.get("issue_evidence_required") is not True
            ):
                raise PipelineError(
                    f"capability profile '{profile_id}' portal fallback must require "
                    "issue evidence"
                )
    conv = conventions()
    for component_type, component in components.items():
        expected_scope = resolve_implementation_scope(component_type, conv)
        if component.get("implementation_scope") != expected_scope:
            raise PipelineError(
                f"capability component '{component_type}' scope does not match "
                f"conventions.yml ({expected_scope})"
            )
        for operation, profile_id in (component.get("operations") or {}).items():
            if profile_id not in profiles:
                raise PipelineError(
                    f"capability component '{component_type}' operation '{operation}' "
                    f"references unknown profile '{profile_id}'"
                )
            declared_operation = profiles[profile_id].get("operation")
            if declared_operation not in {"any", operation}:
                raise PipelineError(
                    f"capability component '{component_type}' operation '{operation}' "
                    f"uses incompatible profile '{profile_id}'"
                )
    return matrix


def resolve_dataverse_capabilities(
    component_type: str, matrix: dict[str, Any]
) -> dict[str, Any]:
    components = matrix.get("components") or {}
    exact = components.get(component_type)
    if exact is not None:
        component = exact
    else:
        matches = [
            (pattern, value)
            for pattern, value in components.items()
            if "*" in pattern and type_matches(component_type, pattern)
        ]
        if len(matches) != 1:
            raise PipelineError(
                f"component_type '{component_type}' must resolve to exactly one "
                f"capability entry; resolved: {[item[0] for item in matches] or 'none'}"
            )
        component = matches[0][1]
    profiles = matrix["profiles"]
    return {
        "implementation_scope": component["implementation_scope"],
        "operations": {
            operation: {
                "profile": profile_id,
                **profiles[profile_id],
            }
            for operation, profile_id in component["operations"].items()
        },
        "policy": matrix["policy"],
        "matrix_hash": sha256_text(
            DATAVERSE_CAPABILITIES_PATH.read_text(encoding="utf-8")
        ),
    }


def load_development_resources(value: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = value if value is not None else load_yaml(DEVELOPMENT_RESOURCES_PATH)
    if not DEVELOPMENT_RESOURCES_SCHEMA_PATH.exists():
        raise PipelineError("required file not found: .d365/development-resources.schema.json")
    try:
        schema = json.loads(DEVELOPMENT_RESOURCES_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(registry)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise PipelineError(f"invalid development resource registry: {exc}") from exc
    resources = registry.get("resources") or {}
    capability_contract = registry.get("capability_contract") or {}
    if capability_contract.get("path") != ".d365/dataverse-web-api-capabilities.yml":
        raise PipelineError("development resource capability contract path is invalid")
    if (
        capability_contract.get("schema_path")
        != ".d365/dataverse-web-api-capabilities.schema.json"
    ):
        raise PipelineError("development resource capability schema path is invalid")
    load_dataverse_capabilities()
    authentication_policy = registry.get("authentication_policy")
    if authentication_policy not in AUTHENTICATION_POLICIES:
        raise PipelineError(
            "development resource authentication_policy must be reuse_if_valid "
            "or always_prompt"
        )
    phase_rank = {name: index for index, name in enumerate(PREFLIGHT_PHASES)}
    automatic_actions = {"command", "mcp_server", "mcp_tools", "endpoint", "package"}
    interactive_actions = {"command", "interactive_auth", "portal"}
    manual_actions = {"portal", "human_confirmation"}
    kind_actions = {
        "mcp": {"mcp_server", "mcp_tools", "interactive_auth"},
        "endpoint": {"endpoint", "interactive_auth"},
        "cli": {"command", "interactive_auth"},
        "sdk": {"command", "package", "interactive_auth", "human_confirmation"},
        "portal": {"endpoint", "portal", "human_confirmation"},
    }

    def visit(node: Any, location: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if SECRET_FIELD.fullmatch(str(key)):
                    raise PipelineError(
                        f"development resource registry must not contain secret field "
                        f"'{location}.{key}'"
                    )
                visit(child, f"{location}.{key}")
            return
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{location}[{index}]")
            return
        if not isinstance(node, str):
            return
        placeholders = set(PLACEHOLDER.findall(node))
        unsupported = sorted(placeholders - PREFLIGHT_PLACEHOLDERS)
        if unsupported:
            raise PipelineError(
                f"{location} uses unsupported placeholder(s): {unsupported}"
            )
        if node.lower().startswith(("http://", "https://")):
            try:
                parsed = urlsplit(node)
            except ValueError as exc:
                raise PipelineError(f"{location} contains an invalid URL") from exc
            if parsed.username is not None or parsed.password is not None:
                raise PipelineError(f"{location} must not contain embedded credentials")

    visit(registry, "development-resources")
    for resource_id, resource in resources.items():
        supported_hosts = set(resource.get("supported_execution_hosts") or [])
        if not supported_hosts:
            raise PipelineError(
                f"resource '{resource_id}' must declare supported_execution_hosts"
            )
        unsupported_hosts = sorted(supported_hosts - RESOURCE_EXECUTION_HOSTS)
        if unsupported_hosts:
            raise PipelineError(
                f"resource '{resource_id}' declares unknown execution host(s): "
                f"{unsupported_hosts}"
            )
        if (
            resource.get("kind") == "mcp"
            and resource.get("authentication") == "interactive_user"
            and "github_copilot_cloud" in supported_hosts
        ):
            raise PipelineError(
                f"resource '{resource_id}' is an OAuth-authenticated remote MCP and "
                "must not claim GitHub Copilot cloud support"
            )
        version = str(resource.get("version") or "")
        if (
            resource.get("version_policy") == "project_pinned"
            and version != UNRESOLVED_AUTHORING_VALUE
        ):
            lowered = version.lower()
            if (
                lowered in {"latest", "stable", "current", "next"}
                or any(symbol in version for symbol in ("*", "^", "~", "<", ">", "="))
                or re.search(r"(^|[.\-])x($|[.\-])", lowered)
            ):
                raise PipelineError(
                    f"resource '{resource_id}' project_pinned version must be exact"
                )
        preflight = resource["preflight"]
        installation = preflight["installation"]
        if installation["policy"] == "automatic_safe":
            command = installation["command"]
            words = {str(token).lower() for token in command}
            destructive = sorted(words & DESTRUCTIVE_COMMAND_WORDS)
            if destructive:
                raise PipelineError(
                    f"resource '{resource_id}' automatic installation is destructive: "
                    f"{destructive}"
                )
        seen_steps: set[str] = set()
        previous_phase = -1
        for step in preflight["steps"]:
            step_id = step["id"]
            if step_id in seen_steps:
                raise PipelineError(
                    f"resource '{resource_id}' has duplicate preflight step '{step_id}'"
                )
            seen_steps.add(step_id)
            current_phase = phase_rank[step["phase"]]
            if current_phase < previous_phase:
                raise PipelineError(
                    f"resource '{resource_id}' preflight phases must follow "
                    "availability_version, capability, authentication, manual"
                )
            previous_phase = current_phase
            behavior = step["behavior"]
            action = step["action"]
            allowed = {
                "automatic": automatic_actions,
                "interactive": interactive_actions,
                "manual": manual_actions,
            }[behavior]
            if action not in allowed:
                raise PipelineError(
                    f"resource '{resource_id}' preflight step '{step_id}' action "
                    f"'{action}' is not valid for {behavior} behavior"
                )
            if action not in kind_actions[resource["kind"]]:
                raise PipelineError(
                    f"resource '{resource_id}' kind '{resource['kind']}' does not support "
                    f"preflight action '{action}'"
                )
            if action == "command":
                command = step.get("command")
                if not command:
                    raise PipelineError(
                        f"resource '{resource_id}' command step '{step_id}' has no command"
                    )
                if behavior == "automatic":
                    words = {str(token).lower() for token in command}
                    destructive = sorted(words & DESTRUCTIVE_COMMAND_WORDS)
                    if destructive:
                        raise PipelineError(
                            f"resource '{resource_id}' automatic step '{step_id}' is "
                            f"destructive: {destructive}"
                        )
            elif step.get("command"):
                raise PipelineError(
                    f"resource '{resource_id}' non-command step '{step_id}' must not "
                    "declare command"
                )
            if step["phase"] == "availability_version" and behavior != "automatic":
                raise PipelineError(
                    f"resource '{resource_id}' availability/version step '{step_id}' "
                    "must be automatic"
                )
            if step["phase"] == "authentication" and behavior != "interactive":
                raise PipelineError(
                    f"resource '{resource_id}' authentication step '{step_id}' "
                    "must be interactive"
                )
            if step["phase"] == "authentication":
                session_probe = step.get("session_probe")
                fresh_authentication = step.get("fresh_authentication")
                if not isinstance(session_probe, dict):
                    raise PipelineError(
                        f"resource '{resource_id}' authentication step '{step_id}' "
                        "must declare session_probe"
                    )
                if not isinstance(fresh_authentication, dict):
                    raise PipelineError(
                        f"resource '{resource_id}' authentication step '{step_id}' "
                        "must declare fresh_authentication"
                    )
                probe_command = session_probe.get("command")
                if probe_command:
                    words = {str(token).lower() for token in probe_command}
                    destructive = sorted(words & DESTRUCTIVE_COMMAND_WORDS)
                    if destructive:
                        raise PipelineError(
                            f"resource '{resource_id}' session probe '{step_id}' is "
                            f"destructive: {destructive}"
                        )
                force_supported = fresh_authentication.get("force_supported")
                if force_supported and (action != "command" or not step.get("command")):
                    raise PipelineError(
                        f"resource '{resource_id}' authentication step '{step_id}' "
                        "can force fresh authentication only with an approved command"
                    )
            if step["phase"] == "manual" and behavior != "manual":
                raise PipelineError(
                    f"resource '{resource_id}' manual step '{step_id}' must be manual"
                )
    for route in registry.get("routing") or []:
        for assignments in (route.get("resources") or {}).values():
            for assignment in assignments or []:
                resource_id = assignment.get("resource")
                if resource_id not in resources:
                    raise PipelineError(
                        f"development resource routing references unknown resource '{resource_id}'"
                    )
                if not str(assignment.get("rationale") or "").strip():
                    raise PipelineError(
                        f"development resource assignment '{resource_id}' has no rationale"
                    )
    return registry


def _replace_placeholders(value: Any, substitutions: dict[str, Any], location: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(child, substitutions, f"{location}.{key}")
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_placeholders(child, substitutions, f"{location}[{index}]")
            for index, child in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in substitutions or substitutions[name] is None:
            raise PipelineError(f"{location} cannot resolve placeholder '{{{name}}}'")
        replacement = substitutions[name]
        if isinstance(replacement, list):
            return ", ".join(str(item) for item in replacement)
        return str(replacement)

    resolved = PLACEHOLDER.sub(replace, value)
    remaining = PLACEHOLDER.findall(resolved)
    if remaining:
        raise PipelineError(f"{location} has unresolved placeholder(s): {remaining}")
    return resolved


def _resource_substitutions(
    resource: dict[str, Any],
    authoring_target: dict[str, Any] | None,
    component_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = authoring_target or {}
    payload = component_payload or {}
    identity = payload.get("schema_name") or payload.get("record_name")
    return {
        "authoring_target.environment_url": target.get("environment_url"),
        "authoring_target.environment_id": target.get("environment_id"),
        "authoring_target.solution_unique_name": target.get("solution_unique_name"),
        "authoring_target.publisher_name": target.get("publisher_name"),
        "authoring_target.publisher_prefix": target.get("publisher_prefix"),
        "component.identity": identity,
        "resource.version": resource.get("version"),
        "resource.endpoint": resource.get("endpoint"),
        "resource.server": resource.get("server"),
        "resource.command": resource.get("command"),
        "resource.package": resource.get("package"),
        "resource.required_tools": resource.get("required_tools") or [],
    }


def resolve_development_resources(
    component_id: str,
    component_type: str,
    build_skill: str,
    registry: dict[str, Any],
    authoring_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matches: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for route in registry.get("routing") or []:
        match = route.get("match") or {}
        if match.get("component_id") == component_id:
            matches.append(((4, 0), route))
        elif match.get("component_type") == component_type:
            matches.append(((3, 0), route))
        elif isinstance(match.get("component_type"), str) and type_matches(
            component_type, match["component_type"]
        ):
            pattern = match["component_type"]
            matches.append(((2, len(pattern.replace("*", ""))), route))
        elif match.get("build_skill") == build_skill:
            matches.append(((1, 0), route))
    if not matches:
        raise PipelineError(
            f"component {component_id} ({component_type}) has no development resource route"
        )
    rank = max(item[0] for item in matches)
    winners = [route for candidate_rank, route in matches if candidate_rank == rank]
    if len(winners) != 1:
        raise PipelineError(
            f"component {component_id} ({component_type}) has ambiguous development "
            f"resource routing at precedence {rank[0]}"
        )
    winner = winners[0]
    resolved: dict[str, Any] = {
        "route_rationale": winner["rationale"],
        "capabilities": resolve_dataverse_capabilities(
            component_type, load_dataverse_capabilities()
        ),
    }
    catalog = registry["resources"]
    for role, assignments in (winner.get("resources") or {}).items():
        resolved[role] = []
        for assignment in assignments or []:
            item = dict(catalog[assignment["resource"]])
            item.pop("preflight", None)
            if authoring_target:
                for key in ("endpoint",):
                    value = item.get(key)
                    if isinstance(value, str):
                        item[key] = value.replace(
                            "{authoring_target.environment_url}",
                            str(authoring_target.get("environment_url", "")),
                        )
            item["id"] = assignment["resource"]
            item["purpose"] = assignment["purpose"]
            item["rationale"] = assignment["rationale"]
            if assignment.get("tools"):
                missing = sorted(set(assignment["tools"]) - set(item.get("tools") or []))
                if missing:
                    raise PipelineError(
                        f"resource '{item['id']}' route requests undeclared tools {missing}"
                    )
                item["required_tools"] = assignment["tools"]
            resolved[role].append(item)
    resolved["registry_hash"] = sha256_text(
        DEVELOPMENT_RESOURCES_PATH.read_text(encoding="utf-8")
    )
    return resolved


def resolve_developer_preflight(
    development_resources: dict[str, Any],
    registry: dict[str, Any],
    implementation_scope: str,
    execution_host: str,
    authoring_target: dict[str, Any] | None,
    component_payload: dict[str, Any],
) -> dict[str, Any]:
    resolved_execution_host = resolve_execution_host(
        implementation_scope, development_resources
    )
    if execution_host != resolved_execution_host:
        raise PipelineError(
            f"execution host '{execution_host}' does not match resolved host "
            f"'{resolved_execution_host}'"
        )
    phases: dict[str, list[dict[str, Any]]] = {
        phase: [] for phase in PREFLIGHT_OUTPUT_PHASES
    }
    direct_resource_actions: list[dict[str, Any]] = []
    for role in ("required", "preferred", "allowed", "fallback"):
        for resolved_resource in development_resources.get(role) or []:
            resource_id = resolved_resource["id"]
            resource = registry["resources"][resource_id]
            substitutions = _resource_substitutions(
                resolved_resource, authoring_target, component_payload
            )
            preflight = _replace_placeholders(
                resource["preflight"],
                substitutions,
                f"resource '{resource_id}' preflight",
            )
            installation_added = False
            for step in preflight["steps"]:
                item = {
                    "key": f"{resource_id}.{step['id']}",
                    "resource": resource_id,
                    "role": role,
                    "purpose": resolved_resource["purpose"],
                    "rationale": resolved_resource["rationale"],
                    "blocking": (
                        role == "required"
                        and not (
                            step["phase"] == "authentication"
                            and resource["kind"] == "cli"
                        )
                    ),
                    **step,
                }
                if resolved_resource.get("endpoint") and not item.get("endpoint"):
                    item["endpoint"] = resolved_resource["endpoint"]
                if step["phase"] == "availability_version" and not installation_added:
                    item["installation"] = preflight["installation"]
                    installation_added = True
                action = step["action"]
                if action in {"mcp_server", "mcp_tools"}:
                    item["server"] = resolved_resource.get("server")
                    if resolved_resource.get("endpoint"):
                        item["endpoint"] = resolved_resource["endpoint"]
                    item["expected_version"] = resolved_resource.get("version")
                    if action == "mcp_tools":
                        item["expected_tools"] = resolved_resource.get("required_tools") or []
                elif action == "endpoint":
                    item["endpoint"] = resolved_resource.get("endpoint")
                    item["expected_version"] = resolved_resource.get("version")
                    if resolved_resource.get("oauth_public_client"):
                        item["oauth_public_client"] = resolved_resource[
                            "oauth_public_client"
                        ]
                elif action == "package":
                    item["package"] = resolved_resource.get("package")
                    item["ecosystem"] = resolved_resource.get("ecosystem")
                    item["expected_version"] = resolved_resource.get("version")
                elif action == "portal":
                    item["endpoint"] = resolved_resource.get("endpoint")
                if step["phase"] == "manual":
                    direct_resource_actions.append(item)
                else:
                    phases[step["phase"]].append(item)

    authentication_resources = sorted(
        {
            step["resource"]
            for step in phases["authentication"]
            if step.get("blocking")
        }
    )

    if authoring_target:
        authenticated_endpoints = sorted(
            {
                str(step["endpoint"])
                for step in phases["authentication"]
                if step.get("blocking") and step.get("endpoint")
            }
        )
        if not authenticated_endpoints:
            authenticated_endpoints = [authoring_target["environment_url"]]
        phases["session_validation"].append(
            {
                "key": "authentication.endpoint-session",
                "resource": "preflight",
                "role": "required",
                "purpose": "authenticated_endpoint_session",
                "blocking": True,
                "phase": "session_validation",
                "action": "session_validation",
                "behavior": "automatic",
                "description": (
                    "Confirm an authenticated, callable session exists for an exact "
                    "compiler-routed endpoint without broad environment, solution, "
                    "publisher, component, membership, or permission discovery."
                ),
                "expected_endpoints": authenticated_endpoints,
                "authentication_mode": authoring_target["authentication_mode"],
                "authentication_resources": authentication_resources,
            }
        )
        identity_field = (
            "schema_name"
            if implementation_scope == "repository_and_dataverse_solution"
            else "record_name"
        )
        phases["target_configuration"].append(
            {
                "key": "target.compiler-configuration",
                "resource": "authoring-target",
                "role": "required",
                "purpose": "deterministic_target_configuration",
                "blocking": True,
                "phase": "target_configuration",
                "action": "target_configuration",
                "behavior": "automatic",
                "description": (
                    "Require concrete compiler-owned environment, routed target, "
                    "canonical identity, and interactive authentication configuration. "
                    "Do not live-enumerate or preverify their existence."
                ),
                "implementation_scope": implementation_scope,
                "environment_url": authoring_target["environment_url"],
                "environment_id": authoring_target["environment_id"],
                "authentication_mode": authoring_target["authentication_mode"],
                "target_kind": authoring_target["kind"],
                "solution_unique_name": authoring_target.get("solution_unique_name"),
                "publisher_name": authoring_target.get("publisher_name"),
                "publisher_prefix": authoring_target.get("publisher_prefix"),
                "identity_field": identity_field,
                "component_identity": component_payload.get(identity_field),
                "capability_matrix_hash": development_resources["capabilities"][
                    "matrix_hash"
                ],
            }
        )
    else:
        identity_field = None
        phases["target_configuration"].append(
            {
                "key": "target.repository-only",
                "resource": "repository",
                "role": "required",
                "purpose": "repository_only",
                "blocking": True,
                "phase": "target_configuration",
                "action": "repository_scope",
                "behavior": "automatic",
                "description": (
                    "Confirm repository-only scope, compiler-owned target roots, and "
                    "the prohibition on Dataverse access."
                ),
            }
        )

    required_ids = [
        item["id"] for item in development_resources.get("required") or []
    ]
    phases["ready_to_attempt"].append(
        {
            "key": "ready-to-attempt.required-safety",
            "resource": "preflight",
            "role": "required",
            "purpose": "ready_to_attempt_gate",
            "blocking": True,
            "phase": "ready_to_attempt",
            "action": "ready_to_attempt",
            "behavior": "automatic",
            "description": (
                "Permit the first scoped platform action only after host/tool, live "
                "Project and compiler integrity, resource/version, endpoint-session, "
                "and structural target checks pass."
            ),
            "required_resources": required_ids,
        }
    )
    canonical_identity = (
        {
            "field": identity_field,
            "value": component_payload.get(identity_field),
        }
        if identity_field
        else None
    )
    direct_action = {
        "mode": "direct_scoped_action",
        "implementation_scope": implementation_scope,
        "environment_url": (
            authoring_target.get("environment_url") if authoring_target else None
        ),
        "environment_id": (
            authoring_target.get("environment_id") if authoring_target else None
        ),
        "target_kind": authoring_target.get("kind") if authoring_target else None,
        "solution_unique_name": (
            authoring_target.get("solution_unique_name") if authoring_target else None
        ),
        "canonical_identity": canonical_identity,
        "expected_payload": component_payload,
        "route_rationale": development_resources["route_rationale"],
        "capabilities": development_resources["capabilities"]["operations"],
        "capability_policy": development_resources["capabilities"]["policy"],
        "resource_actions": direct_resource_actions,
        "prohibited_pre_action_operations": [
            "solution_export",
            "solution_unpack",
            "full_solution_inventory",
            "publisher_inventory",
            "component_absence_scan",
            "cross_solution_membership_scan",
            "full_permission_discovery",
        ],
        "fallback_target_allowed": False,
        "portal_fallback_requires_issue_evidence": True,
        "automatic_prerequisite_repair_allowed": False,
    }
    post_action_verification = {
        "canonical_identity": canonical_identity,
        "expected_payload": component_payload,
        "targeted_read_only_verification": True,
        "solution_membership_verification": (
            implementation_scope == "repository_and_dataverse_solution"
        ),
        "declared_solution_unique_name": (
            authoring_target.get("solution_unique_name") if authoring_target else None
        ),
        "verify_absence_from_other_declared_custom_solutions": (
            implementation_scope == "repository_and_dataverse_solution"
        ),
        "full_solution_export_or_unpack_allowed": False,
        "automatic_repair_allowed": False,
        "verification_resources": {
            operation: capability["verification"]
            for operation, capability in development_resources["capabilities"][
                "operations"
            ].items()
        },
    }
    snapshot = {
        "schema_version": 3,
        "authentication_policy": registry["authentication_policy"],
        "registry_hash": development_resources["registry_hash"],
        "capability_matrix_hash": development_resources["capabilities"]["matrix_hash"],
        "implementation_scope": implementation_scope,
        "execution_host": execution_host,
        "phases": phases,
        "direct_action": direct_action,
        "post_action_verification": post_action_verification,
    }
    placeholder_check = {
        **snapshot,
        "direct_action": {
            key: value
            for key, value in direct_action.items()
            if key != "capabilities"
        },
        "post_action_verification": {
            key: value
            for key, value in post_action_verification.items()
            if key != "verification_resources"
        },
    }
    unresolved = PLACEHOLDER.findall(
        json.dumps(placeholder_check, ensure_ascii=False)
    )
    if unresolved:
        raise PipelineError(
            f"developer preflight snapshot has unresolved placeholder(s): {sorted(set(unresolved))}"
        )
    return snapshot


PREFLIGHT_HEADINGS = {
    "availability_version": "1. Availability and version",
    "capability": "2. MCP and API capability",
    "authentication": "3. Interactive authentication",
    "session_validation": "4. Authenticated endpoint session",
    "target_configuration": "5. Compiler-owned target configuration",
    "ready_to_attempt": "6. Ready to attempt",
}


def render_developer_preflight(snapshot: dict[str, Any]) -> str:
    policy = snapshot["authentication_policy"]
    policy_description = (
        "Reuse a verified existing session; if it is absent, expired, mismatched, "
        "or under-permissioned, complete interactive authentication and revalidate."
        if policy == "reuse_if_valid"
        else "Complete fresh interactive authentication for every DEV execution, "
        "even when an existing session appears valid, then revalidate."
    )
    lines: list[str] = [
        f"Required execution host: `{snapshot['execution_host']}`.",
        f"Effective authentication policy: `{policy}`. {policy_description}",
        "`ready` means ready to attempt the exact scoped action. It does not prove "
        "that the live environment, solution, publisher, component, or permissions exist.",
        "Broad discovery, solution export/unpack, absence scans, and cross-solution "
        "membership scans are not pre-action gates.",
        "",
    ]
    for phase in PREFLIGHT_OUTPUT_PHASES:
        lines.extend([f"### {PREFLIGHT_HEADINGS[phase]}", ""])
        steps = snapshot.get("phases", {}).get(phase) or []
        if not steps:
            lines.extend(["- Not required for this DEV task.", ""])
            continue
        for step in steps:
            blocking = "blocking" if step.get("blocking") else "non-blocking unless selected"
            lines.append(
                f"- **{step['key']}** — {step['description']} "
                f"(`{step['role']}`, `{step['behavior']}`, {blocking})"
            )
            installation = step.get("installation")
            if installation:
                lines.append(
                    f"  - Installation: `{installation['policy']}` — "
                    f"{installation['guidance']}"
                )
                if installation.get("command"):
                    lines.append(
                        "  - Approved automatic install: `"
                        + " ".join(installation["command"])
                        + "`"
                    )
            if step.get("command"):
                lines.append("  - Command: `" + " ".join(step["command"]) + "`")
            session_probe = step.get("session_probe")
            if session_probe:
                if session_probe.get("command"):
                    lines.append(
                        "  - Existing-session probe: `"
                        + " ".join(session_probe["command"])
                        + "`"
                    )
                lines.append(
                    f"  - Existing-session verification: {session_probe['instructions']}"
                )
            fresh_authentication = step.get("fresh_authentication")
            if fresh_authentication:
                support = (
                    "runner can launch the approved command"
                    if fresh_authentication["force_supported"]
                    else "host/client cannot be forced programmatically"
                )
                lines.append(f"  - Fresh authentication: {support}.")
                lines.append(
                    f"  - Fresh-auth action: {fresh_authentication['instructions']}"
                )
            for label, key in (
                ("Expected", "expected"),
                ("Server", "server"),
                ("Endpoint", "endpoint"),
                ("Version", "expected_version"),
                ("Package", "package"),
                ("Environment URL", "environment_url"),
                ("Environment ID", "environment_id"),
                ("Solution", "solution_unique_name"),
                ("Publisher", "publisher_name"),
                ("Publisher prefix", "publisher_prefix"),
                ("Component identity", "component_identity"),
                ("Record name", "record_name"),
            ):
                value = step.get(key)
                if value not in (None, "", []):
                    lines.append(f"  - {label}: `{value}`")
            if step.get("expected_tools"):
                lines.append(
                    "  - Required tools: `"
                    + "`, `".join(step["expected_tools"])
                    + "`"
                )
            if step.get("expected_endpoints"):
                lines.append(
                    "  - Accepted authenticated endpoints: `"
                    + "`, `".join(step["expected_endpoints"])
                    + "`"
                )
            if step.get("authentication_resources"):
                resources = step["authentication_resources"] or ["none"]
                lines.append(
                    "  - Authentication resources: `"
                    + "`, `".join(resources)
                    + "`"
                )
            if step.get("instructions"):
                lines.append(f"  - Human action: {step['instructions']}")
            if step.get("rationale"):
                lines.append(f"  - Rationale: {step['rationale']}")
            oauth = step.get("oauth_public_client")
            if oauth:
                lines.append(
                    "  - OAuth: `msal_public_client`; client ID and tenant are "
                    "non-secret project prerequisites; token cache is `memory_only`."
                )
        lines.append("")
    direct = snapshot["direct_action"]
    identity = direct.get("canonical_identity")
    lines.extend(
        [
            "### Direct scoped action",
            "",
            "- Invoke only the DEV/build-skill action against the exact compiler-owned "
            "endpoint and target; never fall back to another environment or solution.",
            "- Do not export or unpack a solution, enumerate solution/publisher "
            "inventories, scan component absence, or inspect cross-solution membership "
            "before the first action.",
            "- Surface sanitized platform/MCP/PAC errors and stop without further writes; "
            "never create or repair environment, publisher, or solution prerequisites.",
            f"- Route rationale: {direct['route_rationale']}",
            "- The operation primary is selected from the compiler-owned capability "
            "matrix; Maker Portal cannot be selected for convenience.",
            "- Portal fallback requires sanitized issue evidence naming the exact API "
            "failure or unsupported capability.",
        ]
    )
    if direct.get("environment_url"):
        lines.append(f"- Environment URL: `{direct['environment_url']}`")
    if direct.get("solution_unique_name"):
        lines.append(f"- Routed solution: `{direct['solution_unique_name']}`")
    if identity:
        lines.append(
            f"- Canonical identity: `{identity['field']}={identity['value']}`"
        )
    for action in direct.get("resource_actions") or []:
        lines.append(
            f"- Resource action **{action['key']}** — {action['description']}"
        )
    lines.extend(["", "#### Capability routes", ""])
    for operation, capability in sorted(direct.get("capabilities", {}).items()):
        http = capability.get("http") or {}
        endpoint = (
            f"{http.get('method')} {http.get('path_template')}"
            if http
            else "no Web API request"
        )
        lines.append(
            f"- `{operation}` → `{capability['primary_resource']}` "
            f"(`{capability['support']}`; {endpoint}); "
            f"{capability['rationale']}"
        )
    verification = snapshot["post_action_verification"]
    lines.extend(
        [
            "",
            "### Post-action verification",
            "",
            "- After a successful write, target-read the exact canonical identity and "
            "verify the expected DEV payload outcomes.",
        ]
    )
    if verification["solution_membership_verification"]:
        lines.append(
            "- Verify membership in the routed custom solution and absence from other "
            "declared custom solutions only after the write or for explicit conflict "
            "diagnosis; block mismatches without repair."
        )
    lines.append(
        "- Record sanitized non-secret evidence. Full solution export/unpack is not a "
        "verification mechanism."
    )
    lines.append(
        f"Registry SHA-256: `{snapshot['registry_hash']}`. "
        f"Capability matrix SHA-256: `{snapshot['capability_matrix_hash']}`. "
        "This compiler-owned snapshot contains no credentials."
    )
    return "\n".join(lines).rstrip()


def parse_components(body: str) -> list[dict[str, Any]]:
    raw = fill_zone(body, "components")
    fenced = re.fullmatch(r"```ya?ml\s*\n(.*?)\n```", raw, re.DOTALL)
    if not fenced:
        raise PipelineError("FILL:components must contain exactly one YAML fenced block")
    try:
        value = yaml.safe_load(fenced.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PipelineError(f"invalid components YAML: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("components"), list):
        raise PipelineError("components YAML must be a mapping with a components list")
    if not value["components"]:
        raise PipelineError("components list must not be empty")
    if not all(isinstance(item, dict) for item in value["components"]):
        raise PipelineError("every component must be a mapping")
    return value["components"]


def context_hash(data: dict[str, Any]) -> str:
    material = dict(data)
    material.pop("context_hash", None)
    return hash_json(material)


def read_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PipelineError(f"required context not found: {path.relative_to(ROOT).as_posix()}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"invalid JSON context {path.relative_to(ROOT).as_posix()}: {exc}") from exc
    if not isinstance(data, dict):
        raise PipelineError(f"context root must be an object: {path.relative_to(ROOT).as_posix()}")
    declared = data.get("context_hash")
    actual = context_hash(data)
    if declared != actual:
        raise PipelineError(
            f"stale context hash in {path.relative_to(ROOT).as_posix()}: expected {actual}, found {declared}"
        )
    return data


def error(rel: str, message: str) -> str:
    return f"::error file={rel}::{message}"
