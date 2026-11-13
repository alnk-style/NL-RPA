from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from desktop_agent.action_validator import ActionValidator
from desktop_agent.actions import ACTION_REGISTRY, ActionResult
from desktop_agent.audit_logger import AuditLogger
from desktop_agent.config import AgentConfig

if TYPE_CHECKING:
    from desktop_agent.client import AgentWebSocketClient


def _post_action_delay(
    action_type: str,
    params: dict[str, Any],
) -> float:
    if action_type == "launch_app":
        return 0.5
    if action_type == "type_text":
        return 0.5
    if action_type == "copy_file_to_clipboard":
        return 0.5
    if action_type != "press_hotkey":
        return 0.0

    keys = str(params.get("keys") or "").strip().casefold()
    if keys == "^f":
        return 0.7
    if keys == "^v":
        return 1.5
    if keys in {"{enter}", "{return}", "~"}:
        return 1.0
    return 0.0


class TaskExecutor:
    def __init__(
        self,
        config: AgentConfig,
        client: AgentWebSocketClient,
    ) -> None:
        self.config = config
        self.client = client
        self.validator = ActionValidator()
        self.audit_logger = AuditLogger(config.log_dir)
        self._cancelled = False
        self._cancel_requested = False

    async def execute(self, task: dict) -> None:
        self._cancelled = False
        self._cancel_requested = False
        task_id = task.get("task_id")
        actions = task.get("actions")
        if not isinstance(actions, list):
            await self._send_failure(
                task_id,
                "desktop.task actions must be a list",
                [],
                0.0,
            )
            return

        started_at = time.perf_counter()
        step_logs: list[dict[str, Any]] = []
        screenshots: list[str] = []
        context: dict[str, Any] = {
            "task_id": task_id,
            "processes": {},
            "log_dir": self.config.log_dir,
            "step_results": [],
        }

        step_offset = self._step_offset(task)
        resume_from_step = self._resume_from_step(task)
        completed_keys = self._normalize_keys(
            task.get("completed_idempotency_keys")
        )
        completed_key_set = set(completed_keys)
        for offset_index, raw_action in enumerate(actions):
            step_index = step_offset + offset_index
            if self._cancelled:
                await self._send_failure(
                    task_id,
                    "Task cancelled",
                    step_logs,
                    time.perf_counter() - started_at,
                    completed_idempotency_keys=completed_keys,
                )
                return

            action = raw_action if isinstance(raw_action, dict) else {}
            action_type = str(action.get("type", "")).strip()
            params = action.get("params")
            if not isinstance(params, dict):
                params = {}
            params = dict(params)
            step_id = action.get("step_id")
            idempotency_key = self._idempotency_key(action)
            context["step_index"] = step_index

            if self._should_skip(
                step_index,
                resume_from_step,
                idempotency_key,
                completed_key_set,
            ):
                step_log = self._skipped_log(
                    step_index=step_index,
                    step_id=step_id,
                    action_type=action_type,
                    idempotency_key=idempotency_key,
                    before_resume=step_index < resume_from_step,
                )
                step_logs.append(step_log)
                await self.client.send_event(
                    {
                        "type": "desktop.progress",
                        "task_id": task_id,
                        "step_index": step_index,
                        "status": "skipped",
                        "message": step_log["message"],
                        "step_log": step_log,
                        "result": None,
                        "screenshot_path": None,
                    }
                )
                continue

            handler = ACTION_REGISTRY.get(action_type)
            if handler is None:
                error = f"No handler registered for action: {action_type}"
                self.audit_logger.log_start(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                )
                self.audit_logger.log_end(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                    success=False,
                    error=error,
                )
                await self._send_failure(
                    task_id,
                    error,
                    step_logs,
                    time.perf_counter() - started_at,
                    failed_step_index=step_index,
                    action_type=action_type,
                    step_id=step_id,
                    idempotency_key=idempotency_key,
                    completed_idempotency_keys=completed_keys,
                    params=params,
                    context=context,
                )
                return

            validation = self.validator.validate(action, self.config)
            self.audit_logger.log_start(
                task_id=task_id,
                step_index=step_index,
                action_type=action_type,
                params=params,
            )
            if not validation.allowed:
                reason = validation.reason or "Action rejected"
                self.audit_logger.log_end(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                    success=False,
                    error=reason,
                )
                await self._send_failure(
                    task_id,
                    reason,
                    step_logs,
                    time.perf_counter() - started_at,
                    failed_step_index=step_index,
                    action_type=action_type,
                    step_id=step_id,
                    idempotency_key=idempotency_key,
                    completed_idempotency_keys=completed_keys,
                    params=params,
                    context=context,
                )
                return

            action_started_at = time.perf_counter()
            try:
                raw_result = await handler(params, context)
                result = self._coerce_result(raw_result)
            except asyncio.CancelledError:
                self.audit_logger.log_end(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                    success=False,
                    error="Task cancelled",
                )
                raise
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                self.audit_logger.log_end(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                    success=False,
                    error=error,
                )
                await self._send_failure(
                    task_id,
                    error,
                    step_logs,
                    time.perf_counter() - started_at,
                    failed_step_index=step_index,
                    action_type=action_type,
                    step_id=step_id,
                    idempotency_key=idempotency_key,
                    completed_idempotency_keys=completed_keys,
                    params=params,
                    context=context,
                )
                return

            if not result.success:
                error = result.message or f"{action_type} failed"
                self.audit_logger.log_end(
                    task_id=task_id,
                    step_index=step_index,
                    action_type=action_type,
                    params=params,
                    success=False,
                    error=error,
                )
                await self._send_failure(
                    task_id,
                    error,
                    step_logs,
                    time.perf_counter() - started_at,
                    failed_step_index=step_index,
                    action_type=action_type,
                    details=result.data,
                    step_id=step_id,
                    idempotency_key=idempotency_key,
                    completed_idempotency_keys=completed_keys,
                    params=params,
                    context=context,
                )
                return

            delay = _post_action_delay(action_type, params)
            if delay > 0:
                await asyncio.sleep(delay)
            duration = time.perf_counter() - action_started_at

            self.audit_logger.log_end(
                task_id=task_id,
                step_index=step_index,
                action_type=action_type,
                params=params,
                success=True,
            )

            result_payload = result.to_dict()
            step_log = {
                "step_index": step_index,
                "action_type": action_type,
                "status": "success",
                "message": result.message,
                "result": result_payload,
                "duration_seconds": duration,
            }
            if step_id is not None:
                step_log["step_id"] = step_id
            if idempotency_key:
                step_log["idempotency_key"] = idempotency_key
            if result.screenshot_path:
                step_log["screenshot_path"] = result.screenshot_path
            step_logs.append(step_log)
            self._record_idempotency_key(
                idempotency_key,
                completed_keys,
                completed_key_set,
            )
            context["step_results"].append(result_payload)

            if result.pid is not None:
                context["last_pid"] = result.pid
            if result.window_handle is not None:
                context["last_window_handle"] = result.window_handle
            if result.screenshot_path:
                screenshots.append(result.screenshot_path)

            await self.client.send_event(
                {
                    "type": "desktop.progress",
                    "task_id": task_id,
                    "step_index": step_index,
                    "status": "success",
                    "message": result.message or f"{action_type} completed",
                    "step_log": step_log,
                    "result": result_payload,
                    "screenshot_path": result.screenshot_path,
                }
            )

        if self._cancelled:
            await self._send_failure(
                task_id,
                "Task cancelled",
                step_logs,
                time.perf_counter() - started_at,
                completed_idempotency_keys=completed_keys,
            )
            return

        await self.client.send_event(
            {
                "type": "desktop.completed",
                "task_id": task_id,
                "success": True,
                "output": "desktop task completed",
                "error": None,
                "screenshots": screenshots,
                "step_logs": step_logs,
                "completed_idempotency_keys": list(completed_keys),
                "duration_seconds": time.perf_counter() - started_at,
            }
        )

    async def cancel(self) -> None:
        self._cancelled = True
        self._cancel_requested = True

    @staticmethod
    def _coerce_result(value: ActionResult | dict[str, Any]) -> ActionResult:
        if isinstance(value, ActionResult):
            return value
        if isinstance(value, dict):
            return ActionResult(
                success=bool(value.get("success", True)),
                message=str(value.get("message") or ""),
                pid=value.get("pid"),
                window_handle=value.get("window_handle"),
                screenshot_path=value.get("screenshot_path"),
                text=value.get("text"),
                data=dict(value.get("data") or {}),
            )
        raise TypeError("Action handler must return ActionResult or a dictionary")

    async def _send_failure(
        self,
        task_id: int | str | None,
        error: str,
        step_logs: list[dict[str, Any]],
        duration_seconds: float,
        *,
        failed_step_index: int | None = None,
        action_type: str | None = None,
        details: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        step_id: Any | None = None,
        idempotency_key: str | None = None,
        completed_idempotency_keys: list[str] | None = None,
    ) -> None:
        failed_step = None
        failure_screenshot: str | None = None
        if (
            failed_step_index is not None
            and context is not None
            and "screenshot" in ACTION_REGISTRY
        ):
            try:
                screenshot_result = await ACTION_REGISTRY["screenshot"](
                    {
                        "window": (
                            "last"
                            if context.get("last_window_handle")
                            else "foreground"
                        ),
                        "name": f"failed-step-{failed_step_index}",
                    },
                    {
                        **dict(context),
                        "task_id": task_id,
                        "step_index": failed_step_index,
                    },
                )
                failure_screenshot = getattr(
                    screenshot_result,
                    "screenshot_path",
                    None,
                )
            except Exception:
                failure_screenshot = None
        if failed_step_index is not None:
            failed_step = {
                "step_index": failed_step_index,
                "action_type": action_type,
                "status": "failed",
                "error": error,
                "details": dict(details or {}),
            }
            if step_id is not None:
                failed_step["step_id"] = step_id
            if idempotency_key:
                failed_step["idempotency_key"] = idempotency_key
            if isinstance(params, dict):
                failed_step["params"] = dict(params)
            if failure_screenshot:
                failed_step["screenshot_path"] = failure_screenshot
        await self.client.send_event(
            {
                "type": "desktop.failed",
                "task_id": task_id,
                "success": False,
                "output": "",
                "error": error,
                "screenshots": [
                    *(
                        [failure_screenshot]
                        if failure_screenshot
                        else []
                    ),
                    *self._screenshots_from_steps(step_logs),
                ],
                "step_logs": step_logs,
                "failed_step": failed_step,
                "completed_idempotency_keys": list(
                    completed_idempotency_keys or []
                ),
                "duration_seconds": duration_seconds,
            }
        )

    @staticmethod
    def _screenshots_from_steps(
        step_logs: list[dict[str, Any]],
    ) -> list[str]:
        screenshots: list[str] = []
        for step in step_logs:
            path = step.get("screenshot_path")
            if isinstance(path, str) and path not in screenshots:
                screenshots.append(path)
        return screenshots

    @staticmethod
    def _step_offset(task: dict[str, Any]) -> int:
        try:
            offset = int(task.get("step_offset") or 0)
        except (TypeError, ValueError):
            return 0
        return max(offset, 0)

    @staticmethod
    def _resume_from_step(task: dict[str, Any]) -> int:
        for key in ("resume_from_step", "next_step_index"):
            try:
                value = int(task.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0

    @staticmethod
    def _idempotency_key(action: dict[str, Any]) -> str | None:
        value = action.get("idempotency_key")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _should_skip(
        step_index: int,
        resume_from_step: int,
        idempotency_key: str | None,
        completed_key_set: set[str],
    ) -> bool:
        if step_index < resume_from_step:
            return True
        return bool(
            idempotency_key and idempotency_key in completed_key_set
        )

    @staticmethod
    def _skipped_log(
        *,
        step_index: int,
        step_id: Any,
        action_type: str,
        idempotency_key: str | None,
        before_resume: bool,
    ) -> dict[str, Any]:
        if idempotency_key and not before_resume:
            message = f"幂等标记已完成：{idempotency_key}"
        else:
            message = "断点前已完成，续跑跳过"
        step_log: dict[str, Any] = {
            "step_index": step_index,
            "action_type": action_type,
            "status": "skipped",
            "message": message,
            "duration_seconds": 0.0,
        }
        if step_id is not None:
            step_log["step_id"] = step_id
        if idempotency_key:
            step_log["idempotency_key"] = idempotency_key
        return step_log

    @staticmethod
    def _normalize_keys(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        normalized: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _record_idempotency_key(
        key: str | None,
        completed_keys: list[str],
        completed_key_set: set[str],
    ) -> None:
        if not key or key in completed_key_set:
            return
        completed_key_set.add(key)
        completed_keys.append(key)


__all__ = ["TaskExecutor"]
