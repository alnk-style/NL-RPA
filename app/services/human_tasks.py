from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.host_capabilities import (
    environment_or_unknown,
    normalize_capabilities,
)
from app.database import AsyncSessionLocal
from app.engine.host_router import HostInfo
from app.models.agent import DesktopAgent
from app.models.browser_profile import BrowserProfile
from app.models.execution import ExecutionLog
from app.models.human_task import HumanTask
from app.models.human_task_event import HumanTaskEvent
from app.models.workflow import Workflow
from app.security.redaction import redact_payload
from app.visual.redaction import redact_visible_text
from app.engine.preflight import PreflightService
from app.engine.visual_fallback import VISUAL_ACTIONS
from app.services.execution_events import append_execution_event
from app.services.execution_versions import (
    ExecutionSnapshot,
    ExecutionVersionService,
)
from app.services.hosts import safe_human_access_url
from app.schemas.execution_policy import validate_execution_policy

HUMAN_TASK_STATUSES = {
    "pending",
    "resolved",
    "cancelled",
    "expired",
}
HUMAN_TASK_KINDS = {
    "login_required",
    "mfa_required",
    "captcha_required",
    "manual_confirmation",
    "visual_ambiguous",
}
DEFAULT_HUMAN_TASK_TTL = timedelta(minutes=30)


class HumanTaskStateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        verification: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason = message
        self.verification = dict(verification or {})


class HumanTaskVerificationError(HumanTaskStateError):
    pass


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    login_state: str = "unknown"


class HumanTaskVerifier(Protocol):
    async def verify(
        self,
        task: HumanTask,
        request_verification: dict[str, Any],
    ) -> VerificationResult: ...


class ManualOnlyVerifier:
    """Fail closed when no automatic verifier is available."""

    async def verify(
        self,
        task: HumanTask,
        request_verification: dict[str, Any],
    ) -> VerificationResult:
        del task, request_verification
        return VerificationResult(
            ok=False,
            reason="当前人工任务缺少可执行的自动验证器。",
            login_state="unknown",
        )


class HumanTaskManager:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        execution_manager: Any | None = None,
        verifier: HumanTaskVerifier | None = None,
        session_manager: Any | None = None,
        preflight_checker: Any | None = None,
        online_agent_ids: Any | None = None,
        host_session_fingerprint: Any | None = None,
        now: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.execution_manager = execution_manager
        self.verifier = verifier or ManualOnlyVerifier()
        self.session_manager = session_manager
        self.preflight_checker = preflight_checker or _ResumePreflightChecker(
            session_factory=self.session_factory,
            online_agent_ids=online_agent_ids,
            host_session_fingerprint=host_session_fingerprint,
        )
        self._now = now or datetime.utcnow
        self._lock = asyncio.Lock()

    async def create(
        self,
        execution_id: int,
        *,
        step_id: int | None,
        kind: str,
        instruction: str,
        payload: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
        expires_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> HumanTask:
        async with self._lock:
            async with self.session_factory() as session:
                task = await self.create_in_session(
                    session,
                    execution_id,
                    step_id=step_id,
                    kind=kind,
                    instruction=instruction,
                    payload=payload,
                    screenshot_path=screenshot_path,
                    expires_at=expires_at,
                    dedupe_key=dedupe_key,
                )
                await session.commit()
                await session.refresh(task)
                return task

    async def create_in_session(
        self,
        session: AsyncSession,
        execution_id: int,
        *,
        step_id: int | None,
        kind: str,
        instruction: str,
        payload: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
        expires_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> HumanTask:
        normalized_kind = str(kind or "").strip()
        if normalized_kind not in HUMAN_TASK_KINDS:
            raise HumanTaskStateError(
                "invalid_kind",
                f"Unsupported human task kind: {normalized_kind}",
            )
        execution = await session.get(ExecutionLog, int(execution_id))
        if execution is None:
            raise HumanTaskStateError(
                "execution_not_found",
                "Execution not found",
            )

        active_key = (
            str(dedupe_key).strip()
            if dedupe_key is not None
            else _default_dedupe_key(
                execution_id,
                step_id,
                normalized_kind,
            )
        )
        existing = (
            await session.execute(
                select(HumanTask).where(
                    HumanTask.execution_id == int(execution_id),
                    HumanTask.dedupe_key == active_key,
                    HumanTask.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = (
                await session.execute(
                    select(HumanTask)
                    .where(
                        HumanTask.execution_id == int(execution_id),
                        HumanTask.status == "pending",
                    )
                    .order_by(HumanTask.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if existing is not None:
            execution.human_task_id = existing.id
            return existing

        now = self._now()
        safe_payload = dict(payload or {})
        if execution.host_id is not None:
            host_agent = await session.get(
                DesktopAgent,
                int(execution.host_id),
            )
            safe_payload["host_id"] = int(execution.host_id)
            safe_payload["host_environment"] = (
                execution.host_environment
            )
            safe_payload["host_bound"] = bool(execution.host_bound)
            if host_agent is not None:
                safe_payload["host"] = {
                    "host_id": int(host_agent.id),
                    "name": host_agent.name,
                    "environment": environment_or_unknown(
                        host_agent.environment
                    ),
                    "capabilities": normalize_capabilities(
                        host_agent.capabilities
                    ),
                    "human_access_info": {
                        "url": safe_human_access_url(
                            host_agent.human_access_url
                        ),
                        "instructions": (
                            redact_visible_text(
                                host_agent.human_access_instructions
                            )
                            if host_agent.human_access_instructions
                            else None
                        ),
                    },
                }
        if safe_payload.get("current_url"):
            safe_payload["current_url"] = _safe_display_url(
                safe_payload.get("current_url")
            )
        task = HumanTask(
            execution_id=int(execution_id),
            step_id=step_id,
            kind=normalized_kind,
            dedupe_key=active_key,
            instruction=str(instruction).strip()
            or _default_instruction(normalized_kind),
            payload=redact_payload(safe_payload),
            screenshot_path=screenshot_path,
            status="pending",
            created_at=now,
            expires_at=expires_at or (now + DEFAULT_HUMAN_TASK_TTL),
        )
        try:
            async with session.begin_nested():
                session.add(task)
                await session.flush()
        except IntegrityError:
            existing = (
                await session.execute(
                    select(HumanTask).where(
                        HumanTask.execution_id == int(execution_id),
                        HumanTask.dedupe_key == active_key,
                        HumanTask.status == "pending",
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing

        previous_status = execution.status
        execution.status = "awaiting_user"
        execution.finished_at = now
        execution.human_task_id = task.id
        execution.error_message = task.instruction
        execution.metrics = _with_intervention(execution.metrics)
        _record_task_event(
            session,
            task,
            "created",
            from_status=None,
            to_status="pending",
            details={"execution_id": execution_id},
        )
        append_execution_event(
            session,
            execution_id,
            "execution.awaiting_user",
            {
                "previous_status": previous_status,
                "human_task_id": task.id,
                "kind": normalized_kind,
            },
        )
        append_execution_event(
            session,
            execution_id,
            "human_task.created",
            {
                "human_task_id": task.id,
                "kind": normalized_kind,
                "step_id": step_id,
            },
        )
        return task

    async def resume(
        self,
        task_id: int,
        *,
        decision: str,
        verification: dict[str, Any] | None = None,
        actor_id: int | None = None,
    ) -> HumanTask:
        if str(decision or "").strip().lower() != "resume":
            raise HumanTaskStateError(
                "invalid_decision",
                "decision must be 'resume'",
            )
        request_verification = dict(verification or {})

        async with self._lock:
            async with self.session_factory() as session:
                task = await session.get(HumanTask, int(task_id))
                if task is None:
                    raise HumanTaskStateError(
                        "task_not_found",
                        "Human task not found",
                    )
                if task.status == "resolved":
                    return task
                if task.status == "cancelled":
                    raise HumanTaskVerificationError(
                        "task_cancelled",
                        "Cancelled human task cannot be resumed",
                    )
                if task.status == "expired":
                    raise HumanTaskVerificationError(
                        "task_expired",
                        "Expired human task cannot be resumed",
                    )
                if task.status != "pending":
                    raise HumanTaskStateError(
                        "invalid_status",
                        f"Human task status is {task.status}",
                    )

                now = self._now()
                if task.expires_at is not None and task.expires_at <= now:
                    await _transition_expired(
                        session,
                        task,
                        actor_id=actor_id,
                        now=now,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "task_expired",
                        "人工任务已过期，不能继续执行。",
                    )
                claim = await session.execute(
                    update(HumanTask)
                    .where(
                        HumanTask.id == task.id,
                        HumanTask.status == "pending",
                        or_(
                            HumanTask.verification_started_at.is_(None),
                            HumanTask.verification_started_at
                            < (now - timedelta(minutes=5)),
                        ),
                    )
                    .values(verification_started_at=now)
                )
                if int(claim.rowcount or 0) != 1:
                    await session.rollback()
                    current = await session.get(HumanTask, int(task_id))
                    if current is not None and current.status == "resolved":
                        return current
                    raise HumanTaskStateError(
                        "verification_in_progress",
                        "Another request is already verifying this task",
                    )
                await session.refresh(task)

                declared = (
                    task.payload.get("verification")
                    if isinstance(task.payload, dict)
                    else None
                )
                if not isinstance(declared, dict) or not declared:
                    task.verification_started_at = None
                    await _record_resume_failed(
                        session,
                        task,
                        "verification_missing",
                        "人工任务缺少服务端验证条件。",
                        actor_id=actor_id,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "verification_missing",
                        "人工任务缺少服务端验证条件，不能仅凭点击继续。",
                    )

                try:
                    if task.kind == "visual_ambiguous":
                        result = _verify_visual_candidate(
                            task,
                            request_verification,
                        )
                    elif str(declared.get("type") or "") == "legacy_checkpoint":
                        legacy_execution = await session.get(
                            ExecutionLog,
                            task.execution_id,
                        )
                        if (
                            legacy_execution is None
                            or not _has_resume_checkpoint(
                                legacy_execution.step_logs
                            )
                        ):
                            result = VerificationResult(
                                ok=False,
                                reason="历史执行缺少可靠检查点。",
                                login_state="checkpoint_missing",
                                details={"type": "legacy_checkpoint"},
                            )
                        else:
                            result = VerificationResult(
                                ok=True,
                                login_state="unknown",
                                details={
                                    "type": "legacy_checkpoint",
                                    "verified": "checkpoint_present",
                                },
                            )
                    else:
                        result = await self.verifier.verify(
                            task,
                            request_verification,
                        )
                except Exception as exc:
                    reason = (
                        "服务端验证器异常："
                        f"{str(exc) or exc.__class__.__name__}"
                    )
                    await _record_resume_failed(
                        session,
                        task,
                        "verifier_unavailable",
                        reason,
                        actor_id=actor_id,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "verifier_unavailable",
                        reason,
                    ) from exc
                if not result.ok:
                    reason = result.reason or "服务端验证未通过。"
                    failure_code = (
                        result.login_state
                        if result.login_state
                        in {"candidate_missing", "candidate_invalid"}
                        else "verification_failed"
                    )
                    task.verification_started_at = None
                    await _update_profile_state(
                        session,
                        task,
                        login_state=result.login_state,
                    )
                    task.payload = redact_payload(
                        {
                            **dict(task.payload or {}),
                            "last_verification": {
                                "ok": False,
                                "reason": reason,
                                "login_state": result.login_state,
                                **dict(result.details or {}),
                            },
                        }
                    )
                    await _record_resume_failed(
                        session,
                        task,
                        failure_code,
                        reason,
                        actor_id=actor_id,
                        verification={
                            "login_state": result.login_state,
                            **dict(result.details or {}),
                        },
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        failure_code,
                        reason,
                        verification={
                            "login_state": result.login_state,
                            **dict(result.details or {}),
                        },
                    )

                execution = await session.get(
                    ExecutionLog,
                    task.execution_id,
                )
                if execution is None:
                    raise HumanTaskStateError(
                        "execution_not_found",
                        "Execution not found",
                    )
                if execution.status != "awaiting_user":
                    await _record_resume_failed(
                        session,
                        task,
                        "execution_not_awaiting",
                        (
                            "Execution is no longer awaiting user: "
                            f"{execution.status}"
                        ),
                        actor_id=actor_id,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "execution_not_awaiting",
                        "执行已不在等待人工处理状态，不能恢复。",
                    )
                preflight = await self.preflight_checker.check(
                    execution,
                    result.login_state,
                )
                if str(getattr(preflight, "status", "")) != "ready":
                    reason = (
                        str(getattr(preflight, "message", "") or "")
                        or "恢复前预检未通过。"
                    )
                    await _record_resume_failed(
                        session,
                        task,
                        "preflight_blocked",
                        reason,
                        actor_id=actor_id,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "preflight_blocked",
                        reason,
                    )
                if not _has_resume_checkpoint(execution.step_logs):
                    await _record_resume_failed(
                        session,
                        task,
                        "checkpoint_missing",
                        "Execution has no checkpoint",
                        actor_id=actor_id,
                    )
                    await session.commit()
                    raise HumanTaskVerificationError(
                        "checkpoint_missing",
                        "执行缺少可靠检查点，不能恢复。",
                    )

                previous_task_status = task.status
                task.status = "resolved"
                task.resolved_at = now
                task.dedupe_key = None
                task.verification_started_at = None
                task.payload = redact_payload(
                    {
                        **dict(task.payload or {}),
                        "resolution": {
                            "verified_at": now.isoformat(),
                            "login_state": result.login_state,
                            **dict(result.details or {}),
                        },
                    }
                )
                execution.status = "running"
                execution.finished_at = None
                execution.error_message = None
                execution.human_task_id = task.id
                execution.metrics = _mark_resumed(execution.metrics)
                visual_selection = result.details.get(
                    "visual_selection"
                )
                if isinstance(visual_selection, dict):
                    execution.step_logs = _persist_visual_selection(
                        execution.step_logs,
                        visual_selection,
                    )
                _record_task_event(
                    session,
                    task,
                    "resolved",
                    from_status=previous_task_status,
                    to_status="resolved",
                    actor_id=actor_id,
                    details={
                        "login_state": result.login_state,
                        **dict(result.details or {}),
                    },
                )
                append_execution_event(
                    session,
                    execution.id,
                    "human_task.resolved",
                    {
                        "human_task_id": task.id,
                        "kind": task.kind,
                    },
                )
                append_execution_event(
                    session,
                    execution.id,
                    "execution.resumed",
                    {
                        "human_task_id": task.id,
                        "login_state": result.login_state,
                    },
                )
                await session.commit()
                await session.refresh(task)

            profile_id = (
                task.payload.get("browser_profile_id")
                if isinstance(task.payload, dict)
                else None
            )
            if str(profile_id or "").isdigit():
                async with self.session_factory() as session:
                    profile = await session.get(
                        BrowserProfile,
                        int(profile_id),
                    )
                    if profile is not None:
                        profile.login_state = "ready"
                        profile.last_verified_at = now
                        await session.commit()

            if self.session_manager is not None:
                with suppress(Exception):
                    await self.session_manager.close_task(int(task.id))
            if self.execution_manager is not None:
                try:
                    await self.execution_manager.dispatch_human_task_resume(
                        task.execution_id
                    )
                except Exception as exc:
                    await self._record_dispatch_failure(
                        task.execution_id,
                        task.id,
                        str(exc) or exc.__class__.__name__,
                    )
                    raise HumanTaskStateError(
                        "resume_dispatch_failed",
                        "人工验证已通过，但恢复执行失败。",
                    ) from exc
            return task

    async def cancel(
        self,
        task_id: int,
        *,
        reason: str,
        actor_id: int | None = None,
    ) -> HumanTask:
        async with self._lock:
            async with self.session_factory() as session:
                task = await session.get(HumanTask, int(task_id))
                if task is None:
                    raise HumanTaskStateError(
                        "task_not_found",
                        "Human task not found",
                    )
                if task.status == "cancelled":
                    return task
                if task.status != "pending":
                    raise HumanTaskStateError(
                        "invalid_status",
                        f"{task.status} human task cannot be cancelled",
                    )
                execution = await session.get(
                    ExecutionLog,
                    task.execution_id,
                )
                if execution is None:
                    raise HumanTaskStateError(
                        "execution_not_found",
                        "Execution not found",
                    )
                now = self._now()
                normalized_reason = (
                    str(reason).strip() or "用户取消了人工任务。"
                )
                previous_status = task.status
                task.status = "cancelled"
                task.resolved_at = now
                task.dedupe_key = None
                task.verification_started_at = None
                task.payload = redact_payload(
                    {
                        **dict(task.payload or {}),
                        "cancellation": {
                            "reason": normalized_reason,
                            "cancelled_at": now.isoformat(),
                        },
                    }
                )
                execution.status = "cancelled"
                execution.finished_at = now
                execution.error_message = normalized_reason
                execution.human_task_id = task.id
                _record_task_event(
                    session,
                    task,
                    "cancelled",
                    from_status=previous_status,
                    to_status="cancelled",
                    actor_id=actor_id,
                    details={"reason": normalized_reason},
                )
                append_execution_event(
                    session,
                    execution.id,
                    "human_task.cancelled",
                    {
                        "human_task_id": task.id,
                        "kind": task.kind,
                        "reason": normalized_reason,
                    },
                )
                await session.commit()
                await session.refresh(task)

            if self.session_manager is not None:
                with suppress(Exception):
                    await self.session_manager.close_task(int(task.id))
            if self.execution_manager is not None:
                await self.execution_manager.cancel(
                    task.execution_id,
                    normalized_reason,
                    already_persisted=True,
                )
            return task

    async def expire_stale(
        self,
        now: datetime | None = None,
    ) -> int:
        cutoff = now or self._now()
        async with self._lock:
            async with self.session_factory() as session:
                tasks = list(
                    (
                        await session.execute(
                            select(HumanTask).where(
                                HumanTask.status == "pending",
                                HumanTask.expires_at.is_not(None),
                                HumanTask.expires_at <= cutoff,
                            )
                        )
                    ).scalars().all()
                )
                for task in tasks:
                    await _transition_expired(
                        session,
                        task,
                        actor_id=None,
                        now=cutoff,
                    )
                if tasks:
                    await session.commit()
                return len(tasks)

    async def _record_dispatch_failure(
        self,
        execution_id: int,
        task_id: int,
        reason: str,
    ) -> None:
        async with self.session_factory() as session:
            execution = await session.get(ExecutionLog, execution_id)
            if execution is None:
                return
            execution.status = "failed"
            execution.finished_at = self._now()
            execution.error_message = reason
            append_execution_event(
                session,
                execution_id,
                "execution.resume_failed",
                {
                    "human_task_id": task_id,
                    "code": "resume_dispatch_failed",
                    "reason": reason,
                },
            )
            await session.commit()


def build_human_task_spec(
    result: Any,
    *,
    execution_id: int,
    workflow_version_id: int | None = None,
    browser_profile_id: int | None = None,
    login_state: str = "unknown",
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    details = (
        dict(result.details)
        if isinstance(getattr(result, "details", None), dict)
        else {}
    )
    failed_step = (
        details.get("failed_step")
        if isinstance(details.get("failed_step"), dict)
        else {}
    )
    human_action = (
        details.get("human_action")
        if isinstance(details.get("human_action"), dict)
        else failed_step.get("human_action")
        if isinstance(failed_step.get("human_action"), dict)
        else {}
    )
    step_id = _optional_int(
        human_action.get("step_id", failed_step.get("step_id"))
    )
    kind = str(
        human_action.get("kind")
        or failed_step.get("interaction_kind")
        or failed_step.get("human_task_kind")
        or (
            "manual_confirmation"
            if human_action
            else "login_required"
        )
    ).strip()
    if kind not in HUMAN_TASK_KINDS:
        kind = "login_required"

    current_url = _safe_display_url(
        human_action.get("current_url")
        or failed_step.get("current_url")
        or details.get("current_url")
    )
    verification = (
        human_action.get("verification")
        if isinstance(human_action.get("verification"), dict)
        else failed_step.get("verification")
        if isinstance(failed_step.get("verification"), dict)
        else {}
    )
    payload = {
        "execution_id": int(execution_id),
        "workflow_version_id": workflow_version_id,
        "step_id": step_id,
        "current_url": current_url,
        "window_title": human_action.get("window_title")
        or failed_step.get("window_title"),
        "process_name": human_action.get("process_name")
        or failed_step.get("process_name"),
        "login_state": login_state,
        "browser_profile_id": browser_profile_id,
        "verification": redact_payload(dict(verification or {})),
        "failure_category": failed_step.get("failure_category"),
        "action": failed_step.get("action"),
    }
    visual_payload = human_action.get("visual")
    if isinstance(visual_payload, dict):
        payload.update(
            {
                key: value
                for key, value in visual_payload.items()
                if value is not None
            }
        )
    for key in (
        "action",
        "risk_level",
        "candidates",
        "provider",
        "model",
        "trigger_reason",
        "execution_allowed",
        "verification_failed",
        "audit",
    ):
        if human_action.get(key) is not None:
            payload[key] = human_action[key]
    for key in (
        "interaction_hint",
        "allow_manual_confirmation",
        "follow_up_verification",
    ):
        if human_action.get(key) is not None:
            payload[key] = human_action[key]
        elif failed_step.get(key) is not None:
            payload[key] = failed_step[key]
    payload = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", {}, [])
    }

    screenshots = list(getattr(result, "screenshots", None) or [])
    screenshot_path = (
        human_action.get("screenshot_path")
        or failed_step.get("screenshot_path")
        or (screenshots[0] if screenshots else None)
    )
    return {
        "step_id": step_id,
        "kind": kind,
        "instruction": str(
            human_action.get("instruction")
            or failed_step.get("instruction")
            or getattr(result, "error", None)
            or _default_instruction(kind)
        ),
        "payload": redact_payload(payload),
        "screenshot_path": (
            str(screenshot_path) if screenshot_path else None
        ),
        "expires_at": expires_at or (datetime.utcnow() + DEFAULT_HUMAN_TASK_TTL),
        "dedupe_key": _default_dedupe_key(
            execution_id,
            step_id,
            kind,
        ),
    }


class _ResumePreflightChecker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        online_agent_ids: Any | None = None,
        host_session_fingerprint: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.online_agent_ids = online_agent_ids
        self.host_session_fingerprint = host_session_fingerprint

    async def check(
        self,
        execution: ExecutionLog,
        login_state: str,
    ) -> Any:
        if execution.workflow_version_id is None:
            return _blocked_preflight(
                "Execution has no immutable workflow version"
            )
        async with self.session_factory() as session:
            workflow = await session.get(Workflow, execution.workflow_id)
            if workflow is None:
                return _blocked_preflight("Workflow not found")
            try:
                snapshot = await ExecutionVersionService(
                    session
                ).resolve_version(
                    workflow,
                    execution.workflow_version_id,
                )
                if isinstance(
                    execution.execution_policy_snapshot,
                    dict,
                ):
                    snapshot = ExecutionSnapshot(
                        workflow_id=snapshot.workflow_id,
                        version_id=snapshot.version_id,
                        version=snapshot.version,
                        plan=snapshot.plan,
                        policy=validate_execution_policy(
                            execution.execution_policy_snapshot
                        ),
                        action_catalog_version=(
                            snapshot.action_catalog_version
                        ),
                        executor_type=snapshot.executor_type,
                        schedule_timezone=(
                            snapshot.schedule_timezone
                        ),
                    )
            except Exception as exc:
                return _blocked_preflight(
                    str(exc) or exc.__class__.__name__
                )
            host_info = None
            if execution.host_id is not None:
                agent = await session.get(
                    DesktopAgent,
                    int(execution.host_id),
                )
                if agent is None:
                    return _blocked_preflight(
                        "host_unavailable: original execution host "
                        "no longer exists"
                    )
                online_ids = _callable_ids(self.online_agent_ids)
                if (
                    online_ids is not None
                    and int(agent.id) not in online_ids
                ):
                    return _blocked_preflight(
                        "host_disconnected: original execution host "
                        "is offline"
                    )
                current_fingerprint = None
                if callable(self.host_session_fingerprint):
                    try:
                        current_fingerprint = (
                            self.host_session_fingerprint(
                                int(agent.id)
                            )
                        )
                    except Exception:
                        current_fingerprint = None
                if (
                    execution.host_affinity_hash
                    and current_fingerprint
                    and execution.host_affinity_hash
                    != current_fingerprint
                ):
                    return _blocked_preflight(
                        "host_session_lost: original Agent session "
                        "is no longer available"
                    )
                host_info = HostInfo(
                    host_id=int(agent.id),
                    name=agent.name,
                    environment=environment_or_unknown(
                        agent.environment
                    ),
                    capabilities=normalize_capabilities(
                        agent.capabilities
                    ),
                    status=(
                        "online"
                        if online_ids is None
                        or int(agent.id) in online_ids
                        else "offline"
                    ),
                    session_fingerprint=current_fingerprint,
                )
        context: dict[str, Any] = {"login_state": login_state}
        online_ids = _callable_ids(self.online_agent_ids)
        if online_ids is not None:
            context["online_agent_ids"] = online_ids
        if host_info is not None:
            context["hosts"] = [host_info]
        return PreflightService().check(
            snapshot,
            context=context,
        )


def _blocked_preflight(message: str) -> Any:
    return SimpleNamespace(
        status="blocked",
        message=message,
    )


def _callable_ids(provider: Any | None) -> list[int] | None:
    if not callable(provider):
        return None
    try:
        return [int(item) for item in provider()]
    except Exception:
        return []


def _default_dedupe_key(
    execution_id: int,
    step_id: int | None,
    kind: str,
) -> str:
    return f"execution:{int(execution_id)}:step:{step_id}:{kind}"


def _default_instruction(kind: str) -> str:
    return {
        "login_required": "请在打开的窗口中完成登录，然后继续执行。",
        "mfa_required": "请在打开的窗口中完成多因素验证，然后继续执行。",
        "captcha_required": "请在打开的窗口中完成验证码或扫码，然后继续执行。",
        "manual_confirmation": "请确认当前窗口或页面状态，然后继续执行。",
        "visual_ambiguous": "请确认正确的页面目标，然后继续执行。",
    }.get(kind, "请完成人工处理后继续执行。")


def _safe_display_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _with_intervention(value: Any) -> dict[str, Any]:
    metrics = dict(value) if isinstance(value, dict) else {}
    try:
        interventions = int(metrics.get("manual_interventions") or 0)
    except (TypeError, ValueError):
        interventions = 0
    metrics["manual_interventions"] = max(interventions, 0) + 1
    return metrics


def _mark_resumed(value: Any) -> dict[str, Any]:
    metrics = dict(value) if isinstance(value, dict) else {}
    metrics["resumed"] = True
    return metrics


def _record_task_event(
    session: AsyncSession,
    task: HumanTask,
    event_type: str,
    *,
    from_status: str | None,
    to_status: str,
    actor_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        HumanTaskEvent(
            human_task_id=task.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            details=redact_payload(dict(details or {})),
        )
    )


async def _record_resume_failed(
    session: AsyncSession,
    task: HumanTask,
    code: str,
    reason: str,
    *,
    actor_id: int | None,
    verification: dict[str, Any] | None = None,
) -> None:
    task.verification_started_at = None
    append_execution_event(
        session,
        task.execution_id,
        "execution.resume_failed",
        {
            "human_task_id": task.id,
            "code": code,
            "reason": reason,
            "verification": dict(verification or {}),
        },
    )
    _record_task_event(
        session,
        task,
        "verification_failed",
        from_status="pending",
        to_status="pending",
        actor_id=actor_id,
        details={"code": code, "reason": reason},
    )


def _verify_visual_candidate(
    task: HumanTask,
    request_verification: dict[str, Any],
) -> VerificationResult:
    candidate_id = str(
        request_verification.get("candidate_id") or ""
    ).strip()
    if not candidate_id:
        return VerificationResult(
            ok=False,
            reason="必须选择服务端持久化的视觉 candidate_id。",
            login_state="candidate_missing",
        )
    payload = task.payload if isinstance(task.payload, dict) else {}
    if payload.get("execution_allowed") is False:
        return VerificationResult(
            ok=False,
            reason=(
                "该视觉任务执行后验证失败或不包含可执行候选，"
                "不能重复点击。"
            ),
            login_state="candidate_invalid",
        )
    action = str(payload.get("action") or "").strip()
    if action not in VISUAL_ACTIONS:
        return VerificationResult(
            ok=False,
            reason="视觉任务中的动作不在允许列表内。",
            login_state="candidate_invalid",
        )
    raw_candidates = payload.get("candidates")
    candidates = (
        [
            dict(item)
            for item in raw_candidates
            if isinstance(item, dict)
        ]
        if isinstance(raw_candidates, list)
        else []
    )
    selected = next(
        (
            item
            for item in candidates
            if str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if selected is None:
        return VerificationResult(
            ok=False,
            reason=f"视觉候选不存在或已失效：{candidate_id}",
            login_state="candidate_invalid",
        )
    try:
        bbox = [int(item) for item in selected.get("bbox") or []]
        confidence = float(selected.get("confidence"))
    except (TypeError, ValueError):
        return VerificationResult(
            ok=False,
            reason="视觉候选格式无效。",
            login_state="candidate_invalid",
        )
    if len(bbox) != 4 or confidence < 0 or confidence > 1:
        return VerificationResult(
            ok=False,
            reason="视觉候选坐标或置信度无效。",
            login_state="candidate_invalid",
        )
    selection = {
        "step_id": int(task.step_id or 0),
        "candidate_id": candidate_id,
        "target": str(selected.get("target") or ""),
        "bbox": bbox,
        "confidence": confidence,
        "evidence": str(selected.get("evidence") or ""),
        "expected_after": str(selected.get("expected_after") or ""),
        "action": action,
        "risk_level": str(payload.get("risk_level") or "unknown"),
        "human_task_id": int(task.id),
        "image_width": _optional_int(payload.get("image_width")),
        "image_height": _optional_int(payload.get("image_height")),
        "window_info": (
            dict(payload.get("window_info"))
            if isinstance(payload.get("window_info"), dict)
            else {}
        ),
    }
    return VerificationResult(
        ok=True,
        login_state="ready",
        details={
            "type": "visual_selection",
            **selection,
            "visual_selection": selection,
        },
    )


def _persist_visual_selection(
    step_logs: Any,
    selection: dict[str, Any],
) -> list[Any]:
    logs = list(step_logs or [])
    for position in range(len(logs) - 1, -1, -1):
        entry = logs[position]
        if (
            isinstance(entry, dict)
            and entry.get("type") == "checkpoint"
        ):
            logs[position] = {
                **entry,
                "visual_selection": dict(selection),
            }
            break
    return logs


async def _transition_expired(
    session: AsyncSession,
    task: HumanTask,
    *,
    actor_id: int | None,
    now: datetime,
) -> None:
    task.status = "expired"
    task.resolved_at = now
    task.dedupe_key = None
    task.verification_started_at = None
    _record_task_event(
        session,
        task,
        "expired",
        from_status="pending",
        to_status="expired",
        actor_id=actor_id,
    )
    append_execution_event(
        session,
        task.execution_id,
        "human_task.expired",
        {"human_task_id": task.id, "kind": task.kind},
    )
    execution = await session.get(ExecutionLog, task.execution_id)
    if execution is None:
        return
    execution.status = "failed"
    execution.finished_at = now
    execution.error_message = "人工任务已过期，执行已终止。"


def _has_resume_checkpoint(step_logs: Any) -> bool:
    from app.engine.checkpoints import find_checkpoint

    return find_checkpoint(step_logs if isinstance(step_logs, list) else []) is not None


async def _update_profile_state(
    session: AsyncSession,
    task: HumanTask,
    *,
    login_state: str,
) -> None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    profile_id = payload.get("browser_profile_id")
    if not str(profile_id or "").isdigit():
        return
    profile = await session.get(BrowserProfile, int(profile_id))
    if profile is None:
        return
    profile.login_state = str(login_state or "unknown")
    if profile.login_state == "ready":
        profile.last_verified_at = datetime.utcnow()


__all__ = [
    "DEFAULT_HUMAN_TASK_TTL",
    "HUMAN_TASK_KINDS",
    "HUMAN_TASK_STATUSES",
    "HumanTaskManager",
    "HumanTaskStateError",
    "HumanTaskVerificationError",
    "ManualOnlyVerifier",
    "VerificationResult",
    "build_human_task_spec",
]
