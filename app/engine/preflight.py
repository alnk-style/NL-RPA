from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.host_capabilities import normalize_required_capabilities
from app.actions.validation import validate_workflow_draft
from app.engine.host_router import HostRouter
from app.schemas.execution_policy import validate_execution_policy


class PreflightResult(BaseModel):
    status: str
    checks: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class PreflightService:
    def check(
        self,
        snapshot: Any,
        *,
        context: dict[str, Any] | None = None,
    ) -> PreflightResult:
        context = dict(context or {})
        plan = _snapshot_value(snapshot, "plan")
        if not isinstance(plan, dict):
            return PreflightResult(
                status="blocked",
                checks=[
                    _check(
                        "invalid_plan",
                        False,
                        "Workflow version has no plan",
                    )
                ],
                message="Workflow version has no plan",
            )

        checks: list[dict[str, Any]] = []
        execution_mode = str(
            plan.get("execution_mode") or "structured"
        ).strip().lower()
        if execution_mode == "legacy_code":
            enabled = bool(context.get("legacy_code_enabled", True))
            checks.append(
                _check(
                    "legacy_code_enabled",
                    enabled,
                    "Legacy code execution is disabled"
                    if not enabled
                    else "Legacy code execution enabled",
                )
            )
        else:
            action_catalog_version = str(
                _snapshot_value(snapshot, "action_catalog_version") or ""
            )
            validation = validate_workflow_draft(
                plan,
                executor_type=str(
                    _snapshot_value(snapshot, "executor_type") or "web"
                ),
                include_legacy=action_catalog_version.startswith("legacy-"),
            )
            checks.append(
                _check(
                    "invalid_plan",
                    validation.valid,
                    "; ".join(validation.errors)
                    if validation.errors
                    else "Workflow plan is valid",
                )
            )

        timezone_name = str(
            _snapshot_value(snapshot, "schedule_timezone")
            or "Asia/Shanghai"
        )
        try:
            ZoneInfo(timezone_name)
            timezone_valid = True
            timezone_message = "Timezone is valid"
        except (ZoneInfoNotFoundError, ValueError):
            timezone_valid = False
            timezone_message = f"Unknown timezone: {timezone_name}"
        checks.append(
            _check(
                "invalid_timezone",
                timezone_valid,
                timezone_message,
            )
        )

        required_credentials = plan.get("required_credentials")
        if isinstance(required_credentials, list) and required_credentials:
            credentials = context.get("credentials")
            if not isinstance(credentials, dict):
                credentials = {}
            missing = [
                str(item)
                for item in required_credentials
                if str(item).strip() and str(item) not in credentials
            ]
            checks.append(
                _check(
                    "missing_credentials",
                    not missing,
                    (
                        "Missing credentials: " + ", ".join(missing)
                        if missing
                        else "Required credentials are present"
                    ),
                )
            )

        if "online_agent_ids" in context:
            steps = plan.get("steps")
            needs_desktop = isinstance(steps, list) and any(
                isinstance(step, dict)
                and str(step.get("executor") or "").lower()
                in {"windows", "desktop"}
                for step in steps
            )
            if needs_desktop:
                online = context.get("online_agent_ids")
                checks.append(
                    _check(
                        "agent_unavailable",
                        isinstance(online, (list, tuple, set))
                        and bool(online),
                        "No required desktop Agent is online",
                    )
                )

        if "hosts" in context:
            host_check = _host_preflight_check(
                plan,
                snapshot,
                context,
            )
            if host_check is not None:
                checks.append(host_check)

        login_state = str(context.get("login_state") or "ready").lower()
        login_ok = login_state in {"ready", "unknown"}
        checks.append(
            _check(
                "login_required",
                login_ok,
                (
                    "Login state is ready"
                    if login_ok
                    else f"Login state is {login_state}"
                ),
            )
        )

        failed = [item for item in checks if not item["passed"]]
        if any(
            item["code"] in {"login_required"} for item in failed
        ):
            status = "awaiting_user"
        elif failed:
            status = "blocked"
        else:
            status = "ready"
        return PreflightResult(
            status=status,
            checks=checks,
            message=(
                ""
                if status == "ready"
                else "; ".join(item["message"] for item in failed)
            ),
        )


def _snapshot_value(snapshot: Any, name: str) -> Any:
    if isinstance(snapshot, dict):
        return snapshot.get(name)
    return getattr(snapshot, name, None)


def _check(code: str, passed: bool, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "passed": bool(passed),
        "message": message,
    }


def _host_preflight_check(
    plan: dict[str, Any],
    snapshot: Any,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    steps = [
        dict(step)
        for step in plan.get("steps") or []
        if isinstance(step, dict)
    ]
    desktop_steps = [
        step
        for step in steps
        if str(step.get("executor") or "").lower()
        in {"windows", "desktop"}
    ]
    if not desktop_steps:
        return _check(
            "host_capability_missing",
            True,
            "No desktop host is required",
        )
    requirements = {
        str(step.get("host_requirement") or "any")
        for step in desktop_steps
    }
    if "isolated_desktop" in requirements:
        requirement = "isolated_desktop"
    elif "interactive_desktop" in requirements:
        requirement = "interactive_desktop"
    else:
        requirement = "any"
    route_step = {
        **desktop_steps[0],
        "executor": "windows",
        "host_requirement": requirement,
    }
    try:
        policy = validate_execution_policy(
            _snapshot_value(snapshot, "policy")
        )
    except Exception as exc:
        return _check(
            "invalid_execution_policy",
            False,
            f"Invalid execution policy: {exc}",
        )
    required = normalize_required_capabilities(
        plan.get("required_capabilities")
    )
    for step in desktop_steps:
        required.update(
            normalize_required_capabilities(
                step.get("required_capabilities")
            )
        )
    decision = HostRouter().select(
        route_step,
        policy=policy,
        hosts=list(context.get("hosts") or []),
        required_capabilities=sorted(required),
    )
    if decision.status in {"server", "agent"}:
        return _check(
            "host_ready",
            True,
            (
                f"Host {decision.host_id} selected"
                if decision.host_id is not None
                else "Server-side host selected"
            ),
        )
    return _check(
        decision.error_code or "host_unavailable",
        False,
        decision.reason,
    )


__all__ = ["PreflightResult", "PreflightService"]
