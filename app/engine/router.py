from __future__ import annotations

from typing import Any, Protocol

from app.engine.engine import ExecutionEngine
from app.engine.runner import ExecutionResult
from app.models.workflow import Workflow


class DesktopDispatcher(Protocol):
    async def dispatch(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult: ...


class ExecutionRouter:
    def __init__(
        self,
        engine: ExecutionEngine,
        desktop_dispatcher: DesktopDispatcher,
    ):
        self.web_engine = engine
        self.desktop_dispatcher = desktop_dispatcher

    async def execute(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        if self._is_windows(workflow):
            return await self.desktop_dispatcher.dispatch(workflow, context)
        return await self.web_engine.execute(workflow, context)

    async def execute_with_debug(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        if self._is_windows(workflow):
            return await self.desktop_dispatcher.dispatch(workflow, context)
        return await self.web_engine.execute_with_debug(workflow, context)

    @staticmethod
    def _is_windows(workflow: Workflow) -> bool:
        return (
            getattr(workflow, "executor_type", None) or "web"
        ) == "windows"
