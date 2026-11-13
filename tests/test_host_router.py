from __future__ import annotations

import pytest

from app.engine.host_router import HostInfo, HostRouter


def _host(
    host_id: int,
    environment: str,
    *,
    capabilities: list[str] | None = None,
    status: str = "online",
    active_task_count: int = 0,
    session_fingerprint: str = "fingerprint-1",
) -> HostInfo:
    return HostInfo(
        host_id=host_id,
        name=f"host-{host_id}",
        environment=environment,
        capabilities=list(capabilities or []),
        status=status,
        active_task_count=active_task_count,
        session_fingerprint=session_fingerprint,
    )


def _step(
    *,
    executor: str = "windows",
    host_requirement: str = "interactive_desktop",
) -> dict:
    return {
        "step_id": 1,
        "executor": executor,
        "action": "launch_app",
        "host_requirement": host_requirement,
        "params": {},
    }


def test_headless_web_routes_to_server() -> None:
    decision = HostRouter().select(
        _step(executor="web", host_requirement="headless_web"),
        policy={
            "mode": "background_if_safe",
            "host_policy": "isolated_only",
        },
        hosts=[],
    )
    assert decision.status == "server"
    assert decision.host_id is None
    assert decision.error_code is None


def test_interactive_desktop_uses_matching_local_agent() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(1, "local", capabilities=["interactive_desktop"]),
        ],
    )
    assert decision.status == "agent"
    assert decision.host_id == 1
    assert decision.environment == "local"


def test_isolated_desktop_requires_vm_and_capability() -> None:
    decision = HostRouter().select(
        _step(host_requirement="isolated_desktop"),
        policy={
            "mode": "isolated_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(1, "vm", capabilities=["interactive_desktop"]),
            _host(
                2,
                "vm",
                capabilities=["interactive_desktop", "isolated_desktop"],
            ),
        ],
    )
    assert decision.status == "agent"
    assert decision.host_id == 2
    assert decision.environment == "vm"


def test_vm_without_isolated_capability_is_not_isolated() -> None:
    decision = HostRouter().select(
        _step(host_requirement="isolated_desktop"),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "isolated_only",
        },
        hosts=[_host(1, "vm", capabilities=["interactive_desktop"])],
    )
    assert decision.status == "blocked"
    assert decision.error_code == "host_capability_missing"


def test_background_if_safe_never_uses_local_interactive_desktop() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "background_if_safe",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(1, "local", capabilities=["interactive_desktop"]),
            _host(
                2,
                "vm",
                capabilities=["interactive_desktop", "isolated_desktop"],
            ),
        ],
    )
    assert decision.status == "agent"
    assert decision.host_id == 2
    assert decision.environment == "vm"


def test_background_if_safe_blocks_without_isolated_host() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "background_if_safe",
            "host_policy": "local_only",
        },
        hosts=[_host(1, "local", capabilities=["interactive_desktop"])],
    )
    assert decision.status == "blocked"
    assert decision.error_code == "policy_conflict"


def test_hard_host_policies_cannot_be_overridden_by_preferences() -> None:
    local_only = HostRouter().select(
        _step(),
        policy={
            "mode": "isolated_preferred",
            "host_policy": "local_only",
        },
        hosts=[
            _host(1, "local", capabilities=["interactive_desktop"]),
            _host(
                2,
                "vm",
                capabilities=["interactive_desktop", "isolated_desktop"],
            ),
        ],
    )
    assert local_only.host_id == 1

    isolated_only = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "isolated_only",
        },
        hosts=[_host(1, "local", capabilities=["interactive_desktop"])],
    )
    assert isolated_only.status == "blocked"
    assert isolated_only.error_code == "policy_conflict"


def test_isolated_preferred_can_fall_back_under_local_then_isolated() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "isolated_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[_host(1, "local", capabilities=["interactive_desktop"])],
    )
    assert decision.status == "agent"
    assert decision.host_id == 1


def test_server_steps_ignore_host_policy() -> None:
    decision = HostRouter().select(
        _step(executor="transform", host_requirement="any"),
        policy={
            "mode": "background_if_safe",
            "host_policy": "isolated_only",
        },
        hosts=[],
    )
    assert decision.status == "server"


def test_bound_host_never_falls_back_to_another_agent() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(1, "local", capabilities=["interactive_desktop"]),
            _host(
                2,
                "local",
                capabilities=["interactive_desktop"],
                session_fingerprint="fingerprint-2",
            ),
        ],
        bound_host_id=2,
        bound_session_fingerprint="fingerprint-2",
    )
    assert decision.host_id == 2


def test_bound_host_disconnect_blocks_instead_of_rebinding() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(
                1,
                "local",
                capabilities=["interactive_desktop"],
                status="offline",
            ),
            _host(2, "local", capabilities=["interactive_desktop"]),
        ],
        bound_host_id=1,
        bound_session_fingerprint="fingerprint-1",
    )
    assert decision.status == "blocked"
    assert decision.host_id == 1
    assert decision.error_code == "host_disconnected"


def test_bound_session_loss_blocks_resume() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(
                1,
                "local",
                capabilities=["interactive_desktop"],
                session_fingerprint="new-fingerprint",
            )
        ],
        bound_host_id=1,
        bound_session_fingerprint="old-fingerprint",
    )
    assert decision.status == "blocked"
    assert decision.error_code == "host_session_lost"


def test_busy_host_reports_host_busy() -> None:
    decision = HostRouter().select(
        _step(),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[
            _host(
                1,
                "local",
                capabilities=["interactive_desktop"],
                active_task_count=1,
            )
        ],
    )
    assert decision.status == "blocked"
    assert decision.error_code == "host_busy"


@pytest.mark.parametrize(
    "requirement",
    ["unknown", "", "INTERACTIVE_DESKTOP"],
)
def test_unknown_host_requirement_is_rejected(requirement: str) -> None:
    decision = HostRouter().select(
        _step(host_requirement=requirement),
        policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        hosts=[],
    )
    assert decision.status == "blocked"
    assert decision.error_code == "host_capability_missing"
