from __future__ import annotations

from app.engine.preflight import PreflightService
from app.engine.host_router import HostInfo
from app.schemas.execution_policy import default_execution_policy_dict


def _snapshot(plan: dict) -> dict:
    return {
        "workflow_id": 1,
        "version_id": 2,
        "version": 1,
        "plan": plan,
        "policy": default_execution_policy_dict(),
        "action_catalog_version": "2026-10-28.1",
        "executor_type": "web",
        "schedule_timezone": "Asia/Shanghai",
    }


def test_preflight_ready_for_valid_structured_plan() -> None:
    result = PreflightService().check(
        _snapshot(
            {
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                    }
                ],
            }
        ),
        context={},
    )
    assert result.status == "ready"
    assert all(check["passed"] for check in result.checks)


def test_preflight_blocks_unknown_action_and_timezone() -> None:
    unknown = PreflightService().check(
        _snapshot(
            {
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "missing_action",
                        "params": {},
                    }
                ],
            }
        ),
        context={},
    )
    assert unknown.status == "blocked"
    assert any(
        check["code"] == "invalid_plan" for check in unknown.checks
    )

    timezone = _snapshot(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": "https://example.com"},
                }
            ],
        }
    )
    timezone["schedule_timezone"] = "Mars/Olympus"
    blocked = PreflightService().check(timezone, context={})
    assert blocked.status == "blocked"
    assert any(
        check["code"] == "invalid_timezone" for check in blocked.checks
    )


def test_preflight_blocks_missing_credentials_and_agents() -> None:
    missing_credential = PreflightService().check(
        _snapshot(
            {
                "execution_mode": "structured",
                "required_credentials": ["smtp_default"],
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                    }
                ],
            }
        ),
        context={"credentials": {}},
    )
    assert missing_credential.status == "blocked"
    assert any(
        check["code"] == "missing_credentials"
        for check in missing_credential.checks
    )

    missing_agent = PreflightService().check(
        _snapshot(
            {
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": "launch_app",
                        "params": {"path": "notepad.exe"},
                    }
                ],
            }
        ),
        context={"online_agent_ids": []},
    )
    assert missing_agent.status == "blocked"
    assert any(
        check["code"] == "agent_unavailable"
        for check in missing_agent.checks
    )


def test_preflight_returns_awaiting_user_for_expired_login() -> None:
    result = PreflightService().check(
        _snapshot(
            {
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "assert_logged_in",
                        "params": {},
                    }
                ],
            }
        ),
        context={"login_state": "expired"},
    )
    assert result.status == "awaiting_user"
    assert any(
        check["code"] == "login_required" for check in result.checks
    )


def test_preflight_blocks_missing_isolated_capability() -> None:
    snapshot = _snapshot(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": "isolated_desktop",
                    "params": {"path": "notepad.exe"},
                }
            ],
        }
    )
    snapshot["policy"] = {
        **default_execution_policy_dict(),
        "mode": "isolated_preferred",
        "host_policy": "isolated_only",
    }
    result = PreflightService().check(
        snapshot,
        context={
            "hosts": [
                HostInfo(
                    host_id=1,
                    name="vm",
                    environment="vm",
                    capabilities=["interactive_desktop"],
                    status="online",
                )
            ]
        },
    )
    assert result.status == "blocked"
    assert any(
        check["code"] == "host_capability_missing"
        for check in result.checks
    )


def test_preflight_background_blocks_local_interactive_desktop() -> None:
    snapshot = _snapshot(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": "interactive_desktop",
                    "params": {"path": "notepad.exe"},
                }
            ],
        }
    )
    snapshot["policy"] = {
        **default_execution_policy_dict(),
        "mode": "background_if_safe",
        "host_policy": "local_only",
    }
    result = PreflightService().check(
        snapshot,
        context={
            "hosts": [
                HostInfo(
                    host_id=1,
                    name="local",
                    environment="local",
                    capabilities=["interactive_desktop"],
                    status="online",
                )
            ]
        },
    )
    assert result.status == "blocked"
    assert any(
        check["code"] == "policy_conflict"
        for check in result.checks
    )


def test_preflight_server_steps_ignore_host_policy() -> None:
    snapshot = _snapshot(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "transform",
                    "action": "filter",
                    "host_requirement": "any",
                    "params": {},
                }
            ],
        }
    )
    snapshot["policy"] = {
        **default_execution_policy_dict(),
        "mode": "background_if_safe",
        "host_policy": "isolated_only",
    }
    result = PreflightService().check(snapshot, context={"hosts": []})
    assert result.status == "ready"
