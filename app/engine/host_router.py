from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.host_capabilities import (
    normalize_required_capabilities,
)

HOST_REQUIREMENTS = frozenset(
    {"any", "headless_web", "interactive_desktop", "isolated_desktop"}
)
SERVER_EXECUTORS = frozenset(
    {"web", "transform", "files", "email", "mcp", "code"}
)
DESKTOP_EXECUTORS = frozenset({"windows", "desktop"})


@dataclass(frozen=True)
class HostInfo:
    host_id: int
    name: str
    environment: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"
    active_task_count: int = 0
    session_fingerprint: str | None = None


@dataclass(frozen=True)
class HostDecision:
    status: Literal["server", "agent", "blocked"]
    host_id: int | None = None
    environment: str | None = None
    capabilities: list[str] = field(default_factory=list)
    reason: str = ""
    error_code: str | None = None
    fallback_allowed: bool = True
    session_fingerprint: str | None = None

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


class HostRouter:
    def select(
        self,
        step: Any,
        *,
        policy: Any,
        hosts: list[HostInfo],
        bound_host_id: int | None = None,
        bound_session_fingerprint: str | None = None,
        required_capabilities: list[str] | None = None,
    ) -> HostDecision:
        executor = _value(step, "executor")
        raw_requirement = _value(step, "host_requirement")
        requirement = (
            "any"
            if raw_requirement is None
            else str(raw_requirement)
        )
        if requirement not in HOST_REQUIREMENTS:
            return _blocked("host_capability_missing")

        if executor in SERVER_EXECUTORS:
            if requirement in {"any", "headless_web"}:
                return HostDecision(
                    status="server",
                    reason="server-side step",
                )
            return _blocked(
                "host_capability_missing",
                reason=(
                    f"executor {executor!r} cannot satisfy "
                    f"host_requirement {requirement!r}"
                ),
            )
        if executor not in DESKTOP_EXECUTORS:
            return _blocked("host_capability_missing")

        mode = str(_value(policy, "mode") or "interactive_preferred")
        host_policy = str(
            _value(policy, "host_policy") or "local_then_isolated"
        )
        if host_policy not in {
            "local_only",
            "local_then_isolated",
            "isolated_only",
        }:
            return _blocked("policy_conflict")

        required = set(
            normalize_required_capabilities(required_capabilities)
        )
        if requirement in {"interactive_desktop", "isolated_desktop"}:
            required.add(requirement)
        elif requirement == "any":
            required.add("interactive_desktop")
        else:
            return _blocked("host_capability_missing")

        requires_isolated = requirement == "isolated_desktop"
        if mode == "background_if_safe":
            requires_isolated = True
            required.add("isolated_desktop")
        if requires_isolated and host_policy == "local_only":
            return _blocked("policy_conflict")
        if requirement == "isolated_desktop" and host_policy == "local_only":
            return _blocked("policy_conflict")

        if bound_host_id is not None:
            return self._select_bound(
                hosts,
                bound_host_id=int(bound_host_id),
                bound_session_fingerprint=bound_session_fingerprint,
                required=required,
                host_policy=host_policy,
                requires_isolated=requires_isolated,
            )

        candidates = [
            host
            for host in hosts
            if self._matches_hard_constraints(
                host,
                required=required,
                host_policy=host_policy,
                requires_isolated=requires_isolated,
            )
        ]
        if not candidates:
            return self._no_candidate(
                hosts,
                required=required,
                host_policy=host_policy,
                requires_isolated=requires_isolated,
            )
        available = [
            host for host in candidates if host.active_task_count <= 0
        ]
        if not available:
            return _blocked("host_busy")

        available.sort(
            key=lambda host: self._preference_key(
                host,
                mode=mode,
                host_policy=host_policy,
                requires_isolated=requires_isolated,
            )
        )
        selected = available[0]
        return HostDecision(
            status="agent",
            host_id=selected.host_id,
            environment=selected.environment,
            capabilities=list(selected.capabilities),
            reason=self._reason(
                selected,
                mode=mode,
                host_policy=host_policy,
            ),
            fallback_allowed=True,
            session_fingerprint=selected.session_fingerprint,
        )

    def _select_bound(
        self,
        hosts: list[HostInfo],
        *,
        bound_host_id: int,
        bound_session_fingerprint: str | None,
        required: set[str],
        host_policy: str,
        requires_isolated: bool,
    ) -> HostDecision:
        host = next(
            (item for item in hosts if item.host_id == bound_host_id),
            None,
        )
        if host is None:
            return _blocked(
                "host_unavailable",
                host_id=bound_host_id,
                fallback_allowed=False,
            )
        if host.status != "online":
            return _blocked(
                "host_disconnected",
                host_id=host.host_id,
                environment=host.environment,
                capabilities=list(host.capabilities),
                fallback_allowed=False,
            )
        if (
            bound_session_fingerprint
            and host.session_fingerprint
            and bound_session_fingerprint != host.session_fingerprint
        ):
            return _blocked(
                "host_session_lost",
                host_id=host.host_id,
                environment=host.environment,
                capabilities=list(host.capabilities),
                fallback_allowed=False,
            )
        if not self._matches_hard_constraints(
            host,
            required=required,
            host_policy=host_policy,
            requires_isolated=requires_isolated,
        ):
            return _blocked(
                "host_capability_missing",
                host_id=host.host_id,
                environment=host.environment,
                capabilities=list(host.capabilities),
                fallback_allowed=False,
            )
        if host.active_task_count > 0:
            return _blocked(
                "host_busy",
                host_id=host.host_id,
                environment=host.environment,
                capabilities=list(host.capabilities),
                fallback_allowed=False,
            )
        return HostDecision(
            status="agent",
            host_id=host.host_id,
            environment=host.environment,
            capabilities=list(host.capabilities),
            reason="bound host",
            fallback_allowed=False,
            session_fingerprint=host.session_fingerprint,
        )

    @staticmethod
    def _matches_hard_constraints(
        host: HostInfo,
        *,
        required: set[str],
        host_policy: str,
        requires_isolated: bool,
    ) -> bool:
        if host.status != "online":
            return False
        if host.environment not in {"local", "vm"}:
            return False
        if host_policy == "local_only" and host.environment != "local":
            return False
        if host_policy == "isolated_only" and host.environment != "vm":
            return False
        if requires_isolated and host.environment != "vm":
            return False
        capabilities = set(host.capabilities)
        if not required.issubset(capabilities):
            return False
        return True

    @staticmethod
    def _no_candidate(
        hosts: list[HostInfo],
        *,
        required: set[str],
        host_policy: str,
        requires_isolated: bool,
    ) -> HostDecision:
        online = [host for host in hosts if host.status == "online"]
        if not online:
            return _blocked("host_unavailable")
        capability_matches = [
            host
            for host in online
            if required.issubset(set(host.capabilities))
            and (
                not requires_isolated
                or host.environment == "vm"
            )
        ]
        if capability_matches:
            return _blocked("policy_conflict")
        return _blocked("host_capability_missing")

    @staticmethod
    def _preference_key(
        host: HostInfo,
        *,
        mode: str,
        host_policy: str,
        requires_isolated: bool,
    ) -> tuple[int, int, str]:
        if requires_isolated:
            environment_rank = 0
        elif mode == "isolated_preferred":
            environment_rank = 0 if host.environment == "vm" else 1
        elif host_policy == "local_then_isolated":
            environment_rank = 0 if host.environment == "local" else 1
        else:
            environment_rank = 0
        return (
            environment_rank,
            host.active_task_count,
            host.name.casefold(),
        )

    @staticmethod
    def _reason(
        host: HostInfo,
        *,
        mode: str,
        host_policy: str,
    ) -> str:
        if mode == "background_if_safe":
            return "background-safe isolated desktop host"
        if mode == "isolated_preferred" and host.environment == "vm":
            return "isolated preference matched"
        if host_policy == "local_only":
            return "local-only host policy"
        if host_policy == "isolated_only":
            return "isolated-only host policy"
        return "lowest-cost capability match"


def host_step_for_plan(
    plan: Any,
    *,
    default_executor: str = "windows",
) -> dict[str, Any]:
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        return {
            "step_id": 1,
            "executor": "windows",
            "action": "",
            "host_requirement": "interactive_desktop",
        }
    requirements = {
        str(step.get("host_requirement") or "any")
        for step in steps
        if isinstance(step, dict)
        and str(step.get("executor") or "").lower()
        in {"windows", "desktop"}
    }
    if "isolated_desktop" in requirements:
        requirement = "isolated_desktop"
    elif "interactive_desktop" in requirements:
        requirement = "interactive_desktop"
    else:
        requirement = "any"
    return {
        "step_id": 1,
        "executor": "windows",
        "action": "",
        "host_requirement": requirement,
    }


def _blocked(
    code: str,
    *,
    host_id: int | None = None,
    environment: str | None = None,
    capabilities: list[str] | None = None,
    reason: str = "",
    fallback_allowed: bool = True,
) -> HostDecision:
    return HostDecision(
        status="blocked",
        host_id=host_id,
        environment=environment,
        capabilities=list(capabilities or []),
        reason=reason or code,
        error_code=code,
        fallback_allowed=fallback_allowed,
    )


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


__all__ = [
    "HOST_REQUIREMENTS",
    "HostDecision",
    "HostInfo",
    "HostRouter",
    "host_step_for_plan",
]
