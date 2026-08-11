#!/usr/bin/env python3
"""Render, post, and verify sanitized Development execution evidence."""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


class EvidenceError(RuntimeError):
    """Raised when execution evidence cannot be handled safely."""


RESULTS = {"succeeded", "blocked", "waiting-for-human"}
SCOPES = {
    "repository_only",
    "repository_and_dataverse_environment",
    "repository_and_dataverse_solution",
}
VERIFICATION_RESULTS = {"matched", "mismatch", "not-applicable", "not-run"}
HASH_RE = re.compile(r"[0-9a-f]{64}")
ATTEMPT_RE = re.compile(r"(\d{8}T\d{6}Z)-[a-z0-9]{6,32}")
DEV_RE = re.compile(r"DEV-\d{4}")
COMPONENT_RE = re.compile(r"DES-\d{2}-CMP-\d{3}")
SLUG_RE = re.compile(r"[a-z][a-z0-9_-]{1,63}")
FIELD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
FORBIDDEN_OPERATION_RE = re.compile(
    r"(?i)(?:solution.{0,40}\b(?:export|unpack)\b|"
    r"\b(?:export|unpack)\b.{0,40}solution|pac\s+solution\s+(?:export|unpack))"
)
PROHIBITED_KEY_PARTS = {
    "authorization",
    "auth_profile",
    "body",
    "connection_string",
    "cookie",
    "credential",
    "customer_content",
    "environment_variable_value",
    "file_contents",
    "header",
    "headers",
    "password",
    "raw_request",
    "raw_response",
    "refresh_data",
    "secret",
    "token",
}
SENSITIVE_NAME_PARTS = {
    "authorization",
    "client_secret",
    "connection_string",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "token",
}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:Authorization|Cookie|Set-Cookie)\s*:\s*[^\r\n]+"),
    re.compile(
        r"(?i)\b(?:AccountKey|ClientSecret|Password|SharedAccessKey|"
        r"SharedAccessSignature)\s*=\s*[^;\s]+"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)

TOP_KEYS = {
    "schema_version",
    "attempt_id",
    "timestamp_utc",
    "issue_number",
    "result",
    "dev_id",
    "component_id",
    "component_type",
    "build_skill",
    "task_context_hash",
    "source_plan_hash",
    "operation",
    "target",
    "request",
    "response",
    "verification",
    "remediation",
    "write_occurred",
    "further_writes_stopped",
}
OPERATION_KEYS = {"resource", "server", "tool", "api_operation"}
TARGET_KEYS = {
    "scope",
    "environment_url",
    "solution_or_record",
    "identity_field",
    "identity_value",
}
REQUEST_KEYS = {"operation", "parameter_names", "identifiers"}
RESPONSE_KEYS = {
    "status",
    "error_code",
    "message",
    "immutable_id",
    "changed_fields",
    "verified_fields",
    "correlation_id",
    "details_withheld",
}
VERIFICATION_KEYS = {"identity", "payload", "membership"}


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def secret_bearing_key(value: str) -> bool:
    normalized = normalized_key(value)
    return any(
        normalized == part
        or normalized.startswith(part + "_")
        or normalized.endswith("_" + part)
        for part in PROHIBITED_KEY_PARTS
    )


def reject_secret_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise EvidenceError(f"{path} contains a non-string key.")
            if secret_bearing_key(key):
                raise EvidenceError(f"{path}.{key} is a forbidden secret-bearing key.")
            reject_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_keys(child, f"{path}[{index}]")


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must be an object.")
    return value


def require_allowed_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EvidenceError(f"{path} contains unsupported keys: {', '.join(unknown)}.")


def require_text(value: Any, path: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{path} must be a non-empty string.")
    text = value.strip()
    if len(text) > maximum:
        raise EvidenceError(f"{path} exceeds {maximum} characters.")
    return text


def require_optional_text(value: Any, path: str, *, maximum: int = 500) -> str:
    if value is None or value == "":
        return ""
    return require_text(value, path, maximum=maximum)


def require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise EvidenceError(f"{path} must be an array.")
    result = []
    for index, item in enumerate(value):
        result.append(require_text(item, f"{path}[{index}]", maximum=120))
    if len(result) > 50:
        raise EvidenceError(f"{path} exceeds 50 entries.")
    return result


def validate_timestamp(timestamp: str, attempt_id: str) -> None:
    if not timestamp.endswith("Z"):
        raise EvidenceError("$.timestamp_utc must be an ISO-8601 UTC value ending in Z.")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError("$.timestamp_utc is not a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceError("$.timestamp_utc must be UTC.")
    match = ATTEMPT_RE.fullmatch(attempt_id)
    if not match:
        raise EvidenceError(
            "$.attempt_id must match YYYYMMDDTHHMMSSZ-<6-32 lowercase letters/digits>."
        )
    if match.group(1) != parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"):
        raise EvidenceError("$.attempt_id timestamp must match $.timestamp_utc.")


def validate_url(value: str, path: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise EvidenceError(
            f"{path} must be an HTTPS URL without credentials, query, or fragment."
        )


def ensure_no_forbidden_solution_operation(value: Any) -> None:
    if isinstance(value, str) and FORBIDDEN_OPERATION_RE.search(value):
        raise EvidenceError(
            "Solution export/unpack is forbidden for execution evidence and verification."
        )
    if isinstance(value, dict):
        for child in value.values():
            ensure_no_forbidden_solution_operation(child)
    elif isinstance(value, list):
        for child in value:
            ensure_no_forbidden_solution_operation(child)


def sanitize_text(value: str) -> tuple[str, bool]:
    sanitized = value
    redacted = False
    for pattern in SENSITIVE_VALUE_PATTERNS:
        if pattern.search(sanitized):
            sanitized = pattern.sub("[withheld:sensitive-pattern]", sanitized)
            redacted = True
    return sanitized, redacted


def sanitize_values(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        redacted = False
        items = []
        for child in value:
            sanitized, child_redacted = sanitize_values(child)
            items.append(sanitized)
            redacted = redacted or child_redacted
        return items, redacted
    if isinstance(value, dict):
        redacted = False
        result = {}
        for key, child in value.items():
            sanitized, child_redacted = sanitize_values(child)
            result[key] = sanitized
            redacted = redacted or child_redacted
        return result, redacted
    return value, False


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    reject_secret_keys(payload)
    require_allowed_keys(payload, TOP_KEYS, "$")
    missing = sorted(TOP_KEYS - set(payload))
    if missing:
        raise EvidenceError(f"$ is missing required keys: {', '.join(missing)}.")
    if payload["schema_version"] != 1:
        raise EvidenceError("$.schema_version must be 1.")
    if not isinstance(payload["issue_number"], int) or payload["issue_number"] < 1:
        raise EvidenceError("$.issue_number must be a positive integer.")
    result = require_text(payload["result"], "$.result", maximum=40)
    if result not in RESULTS:
        raise EvidenceError("$.result must be succeeded, blocked, or waiting-for-human.")
    dev_id = require_text(payload["dev_id"], "$.dev_id", maximum=20)
    if not DEV_RE.fullmatch(dev_id):
        raise EvidenceError("$.dev_id must match DEV-####.")
    component_id = require_text(payload["component_id"], "$.component_id", maximum=30)
    if not COMPONENT_RE.fullmatch(component_id):
        raise EvidenceError("$.component_id must match DES-##-CMP-###.")
    for key in ("component_type", "build_skill"):
        text = require_text(payload[key], f"$.{key}", maximum=64)
        if not SLUG_RE.fullmatch(text):
            raise EvidenceError(f"$.{key} must be a lowercase identifier.")
    for key in ("task_context_hash", "source_plan_hash"):
        text = require_text(payload[key], f"$.{key}", maximum=64)
        if not HASH_RE.fullmatch(text):
            raise EvidenceError(f"$.{key} must be a lowercase SHA-256 value.")
    timestamp = require_text(payload["timestamp_utc"], "$.timestamp_utc", maximum=40)
    attempt_id = require_text(payload["attempt_id"], "$.attempt_id", maximum=60)
    validate_timestamp(timestamp, attempt_id)

    operation = require_mapping(payload["operation"], "$.operation")
    require_allowed_keys(operation, OPERATION_KEYS, "$.operation")
    if not operation:
        raise EvidenceError("$.operation must identify at least one resource/tool/API operation.")
    for key, value in operation.items():
        require_text(value, f"$.operation.{key}", maximum=200)

    target = require_mapping(payload["target"], "$.target")
    require_allowed_keys(target, TARGET_KEYS, "$.target")
    missing_target = sorted(TARGET_KEYS - set(target))
    if missing_target:
        raise EvidenceError(f"$.target is missing: {', '.join(missing_target)}.")
    scope = require_text(target["scope"], "$.target.scope", maximum=60)
    if scope not in SCOPES:
        raise EvidenceError("$.target.scope is invalid.")
    environment_url = require_text(
        target["environment_url"], "$.target.environment_url", maximum=300
    )
    solution_or_record = require_text(
        target["solution_or_record"], "$.target.solution_or_record", maximum=200
    )
    identity_field = require_text(
        target["identity_field"], "$.target.identity_field", maximum=80
    )
    require_text(target["identity_value"], "$.target.identity_value", maximum=200)
    if scope == "repository_only":
        if environment_url != "not-applicable" or solution_or_record != "not-applicable":
            raise EvidenceError(
                "Repository-only evidence must use not-applicable for environment and target."
            )
        if identity_field != "repository_path":
            raise EvidenceError(
                "Repository-only evidence must use repository_path as canonical identity."
            )
    else:
        validate_url(environment_url, "$.target.environment_url")
        expected_identity = (
            "schema_name"
            if scope == "repository_and_dataverse_solution"
            else "record_name"
        )
        if identity_field != expected_identity:
            raise EvidenceError(
                f"{scope} evidence must use {expected_identity} as canonical identity."
            )

    request = require_mapping(payload["request"], "$.request")
    require_allowed_keys(request, REQUEST_KEYS, "$.request")
    missing_request = sorted(REQUEST_KEYS - set(request))
    if missing_request:
        raise EvidenceError(f"$.request is missing: {', '.join(missing_request)}.")
    require_text(request["operation"], "$.request.operation", maximum=200)
    parameter_names = require_string_list(
        request["parameter_names"], "$.request.parameter_names"
    )
    for name in parameter_names:
        if not FIELD_RE.fullmatch(name):
            raise EvidenceError(f"Unsafe request parameter name: {name}.")
        normalized = normalized_key(name)
        if any(part in normalized for part in SENSITIVE_NAME_PARTS):
            raise EvidenceError(f"Secret-bearing request parameter name is forbidden: {name}.")
    identifiers = require_mapping(request["identifiers"], "$.request.identifiers")
    if len(identifiers) > 30:
        raise EvidenceError("$.request.identifiers exceeds 30 entries.")
    for key, value in identifiers.items():
        if not FIELD_RE.fullmatch(key) or secret_bearing_key(key):
            raise EvidenceError(f"Unsafe request identifier key: {key}.")
        require_text(value, f"$.request.identifiers.{key}", maximum=200)

    response = require_mapping(payload["response"], "$.response")
    require_allowed_keys(response, RESPONSE_KEYS, "$.response")
    if "status" not in response:
        raise EvidenceError("$.response.status is required.")
    require_text(response["status"], "$.response.status", maximum=120)
    for key in (
        "error_code",
        "message",
        "immutable_id",
        "correlation_id",
    ):
        require_optional_text(response.get(key), f"$.response.{key}", maximum=500)
    for key in ("changed_fields", "verified_fields"):
        values = require_string_list(response.get(key, []), f"$.response.{key}")
        for name in values:
            if not FIELD_RE.fullmatch(name):
                raise EvidenceError(f"Unsafe response field name: {name}.")
    if not isinstance(response.get("details_withheld", False), bool):
        raise EvidenceError("$.response.details_withheld must be true or false.")

    verification = require_mapping(payload["verification"], "$.verification")
    require_allowed_keys(verification, VERIFICATION_KEYS, "$.verification")
    missing_verification = sorted(VERIFICATION_KEYS - set(verification))
    if missing_verification:
        raise EvidenceError(
            f"$.verification is missing: {', '.join(missing_verification)}."
        )
    for key, value in verification.items():
        if value not in VERIFICATION_RESULTS:
            raise EvidenceError(f"$.verification.{key} is invalid.")

    remediation = require_text(payload["remediation"], "$.remediation", maximum=500)
    if not isinstance(payload["write_occurred"], bool):
        raise EvidenceError("$.write_occurred must be true or false.")
    if not isinstance(payload["further_writes_stopped"], bool):
        raise EvidenceError("$.further_writes_stopped must be true or false.")

    if result == "succeeded":
        if verification["identity"] != "matched" or verification["payload"] != "matched":
            raise EvidenceError(
                "Succeeded evidence requires targeted identity and payload verification."
            )
        expected_membership = (
            "matched"
            if scope == "repository_and_dataverse_solution"
            else "not-applicable"
        )
        if verification["membership"] != expected_membership:
            raise EvidenceError(
                f"Succeeded {scope} evidence requires membership={expected_membership}."
            )
        if payload["further_writes_stopped"]:
            raise EvidenceError("Succeeded evidence cannot say further writes were stopped.")
    else:
        if not payload["further_writes_stopped"]:
            raise EvidenceError(
                "Blocked or waiting evidence must state that no further writes occurred."
            )

    ensure_no_forbidden_solution_operation(payload)
    sanitized, redacted = sanitize_values(copy.deepcopy(payload))
    sanitized["response"]["details_withheld"] = bool(
        sanitized["response"].get("details_withheld") or redacted
    )
    return sanitized


def load_payload(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise EvidenceError("JSON evidence is required on standard input.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"Standard input is not valid JSON: {exc}.") from exc
    return validate_payload(require_mapping(value, "$"))


def marker_for(payload: dict[str, Any]) -> str:
    return (
        "<!-- d365-execution-evidence:v1 "
        f"attempt={payload['attempt_id']} dev={payload['dev_id']} "
        f"task={payload['task_context_hash']} plan={payload['source_plan_hash']} -->"
    )


def markdown(value: Any) -> str:
    if value is None or value == "":
        return "not reported"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_list(values: list[str]) -> str:
    return ", ".join(markdown(value) for value in values) if values else "none"


def render_identifiers(values: dict[str, str]) -> str:
    return (
        ", ".join(f"{markdown(key)}={markdown(values[key])}" for key in sorted(values))
        if values
        else "none"
    )


def render_evidence(payload: dict[str, Any]) -> str:
    operation = payload["operation"]
    target = payload["target"]
    request = payload["request"]
    response = payload["response"]
    verification = payload["verification"]
    operation_used = ", ".join(
        f"{key}={markdown(operation[key])}" for key in sorted(operation)
    )
    withheld = (
        "yes — sensitive details were withheld"
        if response.get("details_withheld")
        else "no"
    )
    return f"""{marker_for(payload)}
### D365 execution evidence (sanitized)

This append-only comment records sanitized execution evidence. “Request and
response output” never means raw payloads, headers, records, customer content,
files, environment-variable values, or authentication material.

| Field | Value |
| --- | --- |
| UTC timestamp | {markdown(payload['timestamp_utc'])} |
| Attempt | {markdown(payload['attempt_id'])} |
| Result | {markdown(payload['result'])} |
| Development issue | #{payload['issue_number']} |
| DEV / component / type | {markdown(payload['dev_id'])} / {markdown(payload['component_id'])} / {markdown(payload['component_type'])} |
| Build skill | {markdown(payload['build_skill'])} |
| Task context hash | `{payload['task_context_hash']}` |
| Source plan hash | `{payload['source_plan_hash']}` |
| Resource / server / tool / API | {operation_used} |
| Scope | {markdown(target['scope'])} |
| Environment URL | {markdown(target['environment_url'])} |
| Solution or record target | {markdown(target['solution_or_record'])} |
| Canonical identity | {markdown(target['identity_field'])}={markdown(target['identity_value'])} |
| Write occurred | {markdown(payload['write_occurred'])} |
| No further writes after stop | {markdown(payload['further_writes_stopped'])} |

#### Sanitized request summary

- Operation: {markdown(request['operation'])}
- Parameter names: {render_list(request['parameter_names'])}
- Non-sensitive identifiers: {render_identifiers(request['identifiers'])}

#### Sanitized response summary

- HTTP/platform status: {markdown(response['status'])}
- Error code/category: {markdown(response.get('error_code'))}
- Immutable component/record ID: {markdown(response.get('immutable_id'))}
- Changed fields: {render_list(response.get('changed_fields', []))}
- Verified fields: {render_list(response.get('verified_fields', []))}
- Correlation/request ID: {markdown(response.get('correlation_id'))}
- Sanitized platform message: {markdown(response.get('message'))}
- Details withheld: {withheld}

#### Post-action verification

| Check | Result |
| --- | --- |
| Identity | {verification['identity']} |
| Payload | {verification['payload']} |
| Membership | {verification['membership']} |

**Remediation / next step:** {markdown(payload['remediation'])}
"""


def run_gh(args: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise EvidenceError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def resolve_repo(repo: str | None) -> str:
    if repo:
        return repo
    return run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()


def issue_metadata(repo: str, issue_number: int) -> dict[str, Any]:
    return json.loads(
        run_gh(
            [
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,state,labels,body",
            ]
        )
    )


def validate_development_issue(
    repo: str, issue_number: int, dev_id: str
) -> dict[str, Any]:
    issue = issue_metadata(repo, issue_number)
    if issue.get("number") != issue_number:
        raise EvidenceError("GitHub returned a different issue number.")
    if issue.get("state") != "OPEN":
        raise EvidenceError(f"Issue #{issue_number} is not open.")
    labels = {
        str(label.get("name"))
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    }
    if "development" not in labels:
        raise EvidenceError(f"Issue #{issue_number} is not a Development issue.")
    if not re.search(rf"(?<![A-Z0-9-]){re.escape(dev_id)}(?![A-Z0-9-])", str(issue.get("body") or "")):
        raise EvidenceError(
            f"Issue #{issue_number} is unrelated to {dev_id}; evidence posting is forbidden."
        )
    return issue


def issue_comments(repo: str, issue_number: int) -> list[dict[str, Any]]:
    raw = json.loads(
        run_gh(
            [
                "api",
                f"repos/{repo}/issues/{issue_number}/comments",
                "--paginate",
                "--slurp",
            ]
        )
    )
    if raw and all(isinstance(page, list) for page in raw):
        return [comment for page in raw for comment in page]
    return raw if isinstance(raw, list) else []


def post_evidence(
    payload: dict[str, Any], *, issue_number: int, repo: str | None = None
) -> dict[str, Any]:
    if payload["issue_number"] != issue_number:
        raise EvidenceError(
            "CLI issue number does not match the evidence payload; unrelated posting is forbidden."
        )
    repository = resolve_repo(repo)
    validate_development_issue(repository, issue_number, payload["dev_id"])
    marker = marker_for(payload)
    if any(marker in str(comment.get("body") or "") for comment in issue_comments(repository, issue_number)):
        return {
            "result": "duplicate",
            "issue_number": issue_number,
            "marker": marker,
        }
    body = render_evidence(payload)
    output = run_gh(
        [
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            repository,
            "--body-file",
            "-",
        ],
        input_text=body,
    ).strip()
    return {
        "result": "posted",
        "issue_number": issue_number,
        "marker": marker,
        "url": output,
    }


def verify_evidence(
    *,
    issue_number: int,
    dev_id: str,
    task_context_hash: str,
    source_plan_hash: str,
    attempt_id: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    if not DEV_RE.fullmatch(dev_id):
        raise EvidenceError("--dev-id must match DEV-####.")
    if not HASH_RE.fullmatch(task_context_hash) or not HASH_RE.fullmatch(source_plan_hash):
        raise EvidenceError("Current task and source-plan hashes must be lowercase SHA-256 values.")
    repository = resolve_repo(repo)
    validate_development_issue(repository, issue_number, dev_id)
    required = (
        "d365-execution-evidence:v1",
        f"dev={dev_id}",
        f"task={task_context_hash}",
        f"plan={source_plan_hash}",
        "| Result | succeeded |",
        "| Identity | matched |",
        "| Payload | matched |",
    )
    if attempt_id:
        required += (f"attempt={attempt_id}",)
    for comment in issue_comments(repository, issue_number):
        body = str(comment.get("body") or "")
        if all(fragment in body for fragment in required):
            return {
                "result": "verified",
                "issue_number": issue_number,
                "comment_url": comment.get("html_url"),
            }
    raise EvidenceError(
        "No current succeeded execution-evidence comment matches this DEV and its current hashes."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("render")

    post = subparsers.add_parser("post")
    post.add_argument("--issue-number", type=int, required=True)
    post.add_argument("--repo")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--issue-number", type=int, required=True)
    verify.add_argument("--dev-id", required=True)
    verify.add_argument("--task-context-hash", required=True)
    verify.add_argument("--source-plan-hash", required=True)
    verify.add_argument("--attempt-id")
    verify.add_argument("--repo")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command in {"render", "post"}:
        payload = load_payload(sys.stdin.read())
        if args.command == "render":
            print(render_evidence(payload))
            return 0
        result = post_evidence(
            payload,
            issue_number=args.issue_number,
            repo=args.repo,
        )
        print(json.dumps(result, sort_keys=True))
        return 0

    result = verify_evidence(
        issue_number=args.issue_number,
        dev_id=args.dev_id,
        task_context_hash=args.task_context_hash,
        source_plan_hash=args.source_plan_hash,
        attempt_id=args.attempt_id,
        repo=args.repo,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(2)
