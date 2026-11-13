from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.actions.registry import default_action_registry
from app.engine.dag import (
    ExecutionPlanError,
    normalize_execution_plan,
    normalize_executor,
)

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")


class WorkflowValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    action_catalog_version: str


def validate_workflow_draft(
    plan: Any,
    *,
    executor_type: str = "web",
    include_legacy: bool = False,
    validate_params: bool = True,
) -> WorkflowValidationResult:
    registry = default_action_registry()
    if not isinstance(plan, dict):
        return WorkflowValidationResult(
            valid=False,
            errors=["Workflow plan must be an object"],
            action_catalog_version=registry.version,
        )
    try:
        nodes = normalize_execution_plan(
            plan,
            default_executor=executor_type or "web",
        )
    except ExecutionPlanError as exc:
        return WorkflowValidationResult(
            valid=False,
            errors=[str(exc)],
            action_catalog_version=registry.version,
        )

    errors: list[str] = []
    producers = _declared_variable_names(plan.get("variables"))
    crawl_policy = plan.get("crawl_policy")
    allowed_domains = _allowed_domains(crawl_policy)
    risk_confirmations = _risk_confirmations(plan)
    for node in nodes:
        host_error = _host_requirement_error(node)
        if host_error:
            errors.append(host_error)
        references = _template_references(
            {
                "params": node.raw.get("params"),
                "condition": node.raw.get("condition"),
                "preconditions": node.raw.get("preconditions"),
                "postconditions": node.raw.get("postconditions"),
                "verify": node.raw.get("verify"),
            }
        )
        normalized_references = {
            name.split(".", 1)[0].strip()
            for name in references
            if name.strip()
        }
        local_names = {
            str(node.raw.get("foreach", {}).get(key) or "").strip()
            if isinstance(node.raw.get("foreach"), dict)
            else ""
            for key in ("item_var",)
        }
        unknown = sorted(
            name
            for name in normalized_references - producers - local_names
            if name
        )
        if unknown:
            errors.append(
                f"Step {node.step_id} references unknown variable(s): "
                + ", ".join(unknown)
            )

        if normalize_executor(node.executor) == "code":
            errors.append(
                f"Step {node.step_id} uses forbidden executor 'code'"
            )
            continue
        try:
            registered = registry.resolve(node.executor, node.action)
        except KeyError as exc:
            errors.append(f"Step {node.step_id}: {exc}")
            continue
        if registered.definition.id == "code_step":
            errors.append(
                f"Step {node.step_id} uses forbidden action web.code_step"
            )
            continue
        if registered.definition.legacy_only and not include_legacy:
            errors.append(
                "Step "
                f"{node.step_id} uses legacy-only action "
                f"{node.executor}.{registered.definition.id}"
            )
            continue
        if (
            registered.definition.risk_level in {"high", "critical"}
            and node.step_id not in risk_confirmations
        ):
            errors.append(
                f"Step {node.step_id} requires explicit risk confirmation"
            )
        params = node.raw.get("params")
        if params is None:
            params = {}
        if not validate_params:
            continue
        validation = registry.validate(
            node.executor,
            registered.definition.id,
            params,
        )
        if not validation.valid:
            errors.append(
                f"Step {node.step_id} {node.executor}.{node.action}: "
                f"{validation.message}"
            )
        if node.executor == "mcp":
            server = (
                params.get("server")
                or params.get("server_id")
                or params.get("mcp_server")
            )
            tool = params.get("tool") or params.get("name")
            if not server or not tool:
                errors.append(
                    f"Step {node.step_id} mcp.call_tool requires server and tool"
                )
        if node.executor == "web":
            errors.extend(
                _web_safety_errors(
                    node.step_id,
                    node.action,
                    params if isinstance(params, dict) else {},
                    allowed_domains,
                    crawl_policy,
                )
            )
        _extend_producers(producers, node.raw)

    if not nodes:
        errors.append("Workflow has no structured steps")
    return WorkflowValidationResult(
        valid=not errors,
        errors=errors,
        action_catalog_version=registry.version,
    )


def _host_requirement_error(node: Any) -> str | None:
    raw = node.raw.get("host_requirement")
    requirement = "any" if raw is None else str(raw)
    allowed = {
        "any",
        "headless_web",
        "interactive_desktop",
        "isolated_desktop",
    }
    if requirement not in allowed:
        return (
            f"Step {node.step_id} has invalid host_requirement "
            f"'{requirement}'"
        )
    if normalize_executor(node.executor) == "windows":
        if requirement == "headless_web":
            return (
                f"Step {node.step_id} desktop executor cannot use "
                "host_requirement 'headless_web'"
            )
        return None
    if requirement in {"interactive_desktop", "isolated_desktop"}:
        return (
            f"Step {node.step_id} server-side executor cannot use "
            f"host_requirement '{requirement}'"
        )
    if (
        requirement == "headless_web"
        and normalize_executor(node.executor) != "web"
    ):
        return (
            f"Step {node.step_id} only web actions can use "
            "host_requirement 'headless_web'"
        )
    return None


def _declared_variable_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        names.update(str(key).strip() for key in value if str(key).strip())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.add(name)
    return names


def _template_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, str):
        for match in _TEMPLATE_PATTERN.finditer(value):
            name = match.group(1).strip()
            if name:
                references.add(name)
    elif isinstance(value, dict):
        for item in value.values():
            references.update(_template_references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            references.update(_template_references(item))
    return references


def _extend_producers(producers: set[str], step: dict[str, Any]) -> None:
    for key in ("result_var", "output_var"):
        name = str(step.get(key) or "").strip()
        if name:
            producers.add(name)
    params = step.get("params")
    if isinstance(params, dict):
        for key in ("variable", "result_var", "output_var"):
            name = str(params.get(key) or "").strip()
            if name:
                producers.add(name)


def _risk_confirmations(plan: dict[str, Any]) -> set[int]:
    raw = plan.get("risk_confirmations")
    if not isinstance(raw, list):
        return set()
    confirmed: set[int] = set()
    for item in raw:
        try:
            confirmed.add(int(item))
        except (TypeError, ValueError):
            continue
    return confirmed


def _allowed_domains(crawl_policy: Any) -> set[str]:
    if not isinstance(crawl_policy, dict):
        return set()
    raw = crawl_policy.get("allowed_domains")
    if not isinstance(raw, list):
        return set()
    return {
        str(item).strip().lower()
        for item in raw
        if str(item).strip()
    }


def _web_safety_errors(
    step_id: int,
    action: str,
    params: dict[str, Any],
    allowed_domains: set[str],
    crawl_policy: Any,
) -> list[str]:
    errors: list[str] = []
    normalized_action = "goto" if action == "navigate" else action
    if normalized_action == "goto":
        url = str(params.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append(f"Step {step_id} goto requires an HTTP(S) URL")
        elif allowed_domains and parsed.hostname.lower() not in allowed_domains:
            errors.append(
                f"Step {step_id} domain '{parsed.hostname}' is outside "
                "crawl_policy.allowed_domains"
            )
    if normalized_action in {
        "collect_links",
        "extract_fields",
        "paginate",
    }:
        if not isinstance(crawl_policy, dict):
            errors.append(
                f"Step {step_id} {normalized_action} requires crawl_policy"
            )
        else:
            if not allowed_domains:
                errors.append(
                    f"Step {step_id} requires crawl_policy.allowed_domains"
                )
            for key in ("max_pages", "max_items"):
                value = crawl_policy.get(key)
                if not isinstance(value, int) or value <= 0:
                    errors.append(
                        f"Step {step_id} requires positive crawl_policy.{key}"
                    )
    return errors


__all__ = [
    "WorkflowValidationResult",
    "validate_workflow_draft",
]
