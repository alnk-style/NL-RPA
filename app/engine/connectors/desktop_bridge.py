"""把既有 Desktop Dispatcher / Agent WebSocket 协议包装成 Connector。

不改动原协议：仍然由 ``DesktopDispatcher`` 生成 ``desktop.task`` 消息、
沿用任务、进度与审计行为。Connector 只提供统一的
``capabilities / observe / act / verify / auth_status`` 视图。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Callable

from app.engine.desktop_catalog import DESKTOP_ACTIONS
from app.engine.connectors.base import Connector, connector_result


class DesktopBridgeConnector(Connector):
    name = "desktop"
    kind = "desktop"

    def __init__(self, desktop: Any) -> None:
        self.desktop = desktop

    # ------------------------------------------------------------ 能力查询

    async def capabilities(self) -> dict[str, Any]:
        return connector_result(
            True,
            data={
                "actions": sorted(DESKTOP_ACTIONS),
                "action_count": len(DESKTOP_ACTIONS),
                "resource": "desktop",
                "protocol": "desktop.task/desktop.completed",
            },
            online_agents=self._online_agent_ids(),
        )

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = dict(context or {})
        dispatcher = self._dispatcher()
        inspect = getattr(dispatcher, "inspect", None)
        if not callable(inspect):
            return connector_result(
                False,
                error="桌面 Agent 未连接或未实现 inspect",
            )
        payload = {
            key: value
            for key, value in request.items()
            if key
            in {
                "target",
                "title",
                "class_name",
                "pid",
                "include_windows",
                "max_elements",
                "depth",
            }
        }
        result = await inspect(
            payload,
            agent_id=_as_int(request.get("agent_id")),
            timeout=_as_float(request.get("timeout")),
        )
        if not isinstance(result, dict):
            return connector_result(
                False,
                error="桌面观察没有返回结果",
            )
        success = bool(result.get("success"))
        return connector_result(
            success,
            data=result,
            error=result.get("error"),
        )

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = dict(context or {})
        payload = dict(action or {})
        action_type = str(
            payload.get("action") or payload.get("type") or ""
        ).strip()
        if action_type not in DESKTOP_ACTIONS:
            return connector_result(
                False,
                error=f"桌面动作不在目录中：{action_type}",
            )
        dispatch = self._dispatch_callable()
        if dispatch is None:
            return connector_result(
                False,
                error="桌面 Agent 未连接",
            )

        workflow = self._single_step_workflow(payload, request)
        if workflow is None:
            return connector_result(
                False,
                error="Desktop Bridge 调用需要 workflow_id",
            )

        step_params = payload.get("params")
        dispatch_context = dict(request.get("dispatch_context") or {})
        dispatch_context.setdefault("step_offset", 0)
        started_at = time.perf_counter()
        result = await dispatch(workflow, dispatch_context)
        success = bool(getattr(result, "success", False))
        return connector_result(
            success,
            data={
                "output": getattr(result, "output", ""),
                "step_logs": list(getattr(result, "step_logs", []) or []),
                "screenshots": list(
                    getattr(result, "screenshots", []) or []
                ),
                "action": action_type,
                "params": (
                    dict(step_params)
                    if isinstance(step_params, dict)
                    else {}
                ),
            },
            error=getattr(result, "error", None),
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )

    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(assertion or {})
        observation = await self.observe(
            {
                **dict(context or {}),
                "title": payload.get("title"),
                "class_name": payload.get("class_name"),
                "target": payload.get("target"),
            }
        )
        if not observation.get("success"):
            return observation
        data = observation.get("data")
        observation_data = data if isinstance(data, dict) else {}
        kind = str(payload.get("kind") or "window").strip().lower()

        if kind in {"window", "window_present"}:
            title = str(payload.get("title") or "").strip().lower()
            windows = observation_data.get("windows")
            windows = windows if isinstance(windows, list) else []
            found = title == "" or any(
                isinstance(window, dict)
                and title in str(window.get("title") or "").lower()
                for window in windows
            )
            return connector_result(
                bool(found),
                data={"windows": windows},
                error=None if found else "没有找到匹配窗口",
            )

        if kind in {"element", "element_present"}:
            elements = observation_data.get("elements")
            elements = elements if isinstance(elements, list) else []
            automation_id = str(
                payload.get("automation_id") or ""
            ).strip()
            name = str(payload.get("name") or "").strip()
            found = any(
                isinstance(element, dict)
                and (
                    (
                        automation_id
                        and str(element.get("automation_id") or "")
                        == automation_id
                    )
                    or (name and name in str(element.get("name") or ""))
                )
                for element in elements
            )
            return connector_result(
                bool(found),
                data={"elements": elements},
                error=None if found else "没有找到匹配控件",
            )

        return connector_result(
            bool(observation_data),
            data=observation_data,
        )

    async def auth_status(self) -> dict[str, Any]:
        online = self._online_agent_ids()
        return connector_result(
            True,
            data={
                "online_agents": online,
                "connected": bool(online),
                "protocol": "websocket",
            },
        )

    # ---------------------------------------------------------------- 内部

    def _dispatch_callable(self) -> Callable[..., Any] | None:
        execute = getattr(self.desktop, "execute", None)
        if callable(execute):
            return execute
        dispatch = getattr(self.desktop, "dispatch", None)
        if callable(dispatch):
            return dispatch
        dispatcher = self._dispatcher()
        if dispatcher is not None:
            return getattr(dispatcher, "dispatch", None)
        return None

    def _dispatcher(self) -> Any | None:
        if hasattr(self.desktop, "inspect") or hasattr(
            self.desktop,
            "dispatch",
        ):
            return self.desktop
        return getattr(self.desktop, "dispatcher", None)

    def _online_agent_ids(self) -> list[int]:
        dispatcher = self._dispatcher()
        manager = getattr(dispatcher, "connection_manager", None)
        if manager is None:
            return []
        try:
            values = list(manager.get_online_agents())
        except Exception:  # noqa: BLE001
            return []
        agent_ids: list[int] = []
        for value in values:
            parsed = _as_int(value)
            if parsed is not None:
                agent_ids.append(parsed)
        return agent_ids

    @staticmethod
    def _single_step_workflow(
        action: dict[str, Any],
        context: dict[str, Any],
    ) -> Any | None:
        workflow = context.get("workflow")
        workflow_id = getattr(workflow, "id", None)
        user_id = getattr(workflow, "user_id", None)
        if workflow_id is None:
            workflow_id = context.get("workflow_id")
            user_id = user_id or context.get("user_id")
        if workflow_id is None:
            return None

        step: dict[str, Any] = {
            "step_id": action.get("step_id") or 1,
            "executor": "windows",
            "action": action.get("action") or action.get("type"),
            "params": (
                dict(action.get("params"))
                if isinstance(action.get("params"), dict)
                else {}
            ),
        }
        key = action.get("idempotency_key")
        if key:
            step["idempotency_key"] = key
        return SimpleNamespace(
            id=int(workflow_id),
            user_id=user_id,
            name=getattr(workflow, "name", None)
            or f"desktop-bridge-{step['action']}",
            executor_type="windows",
            required_capabilities=getattr(
                workflow,
                "required_capabilities",
                None,
            ),
            structured_plan={"executor_type": "windows", "steps": [step]},
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = ["DesktopBridgeConnector"]
