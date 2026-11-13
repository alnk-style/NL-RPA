from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import runtime_flags
from app.engine.web_catalog import WebPlanError, validate_web_plan
from app.actions.validation import validate_workflow_draft
from app.engine.code_validator import CodeValidator
from app.engine.runner import ExecutionResult, PlaywrightRunner
from app.engine.sandbox import DockerSandbox
from app.engine.web_executor import WebExecutor

if TYPE_CHECKING:
    from app.config import Settings
    from app.models.workflow import Workflow


class ExecutionEngine:
    def __init__(self, config: Settings):
        self.config = config
        self.runner = PlaywrightRunner(config)
        self.sandbox = DockerSandbox(config)
        self.web_executor = WebExecutor(config, runner=self.runner)

    async def execute(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        execution_context = self._build_context(workflow, context)
        mode = self._execution_mode(workflow)
        if mode == "structured":
            self.uses_structured_web(workflow)
            return await self.web_executor.execute(
                workflow,
                execution_context,
            )

        if not runtime_flags.legacy_code_execution_enabled(self.config):
            raise ValueError("Legacy code execution is disabled")
        code = CodeValidator.strip_main_guard(workflow.generated_code)
        if not code or not code.strip():
            raise ValueError("Workflow has no generated code")

        self._validate_code(code)
        return await self.sandbox.execute_with_fallback(
            code,
            execution_context,
            workflow=workflow,
        )

    async def execute_code(
        self,
        code: str,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        code = CodeValidator.strip_main_guard(code)
        self._validate_code(code)
        return await self.sandbox.execute_with_fallback(
            code,
            dict(context or {}),
        )

    async def execute_with_debug(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        if self.uses_structured_web(workflow):
            # 结构化步骤已经在执行器内按步骤重试，不再整段重跑。
            return await self.execute(workflow, context)

        attempts = max(1, self.config.MAX_RETRY)
        result: ExecutionResult | None = None

        for _attempt in range(attempts):
            result = await self.execute(workflow, context)
            if result.success:
                return result

            # TODO: Integrate DebugAgent.fix() and retry with repaired code.

        if result is None:
            raise RuntimeError("Execution engine did not run")
        return result

    def uses_structured_web(self, workflow: Workflow) -> bool:
        """判断当前流程是否应走结构化网页执行路径。"""

        mode = self._execution_mode(workflow)
        if mode == "legacy_code":
            return False
        if not runtime_flags.structured_execution_enabled(self.config):
            raise ValueError("Structured execution is disabled")
        validation = validate_workflow_draft(
            getattr(workflow, "structured_plan", None),
            executor_type=(
                getattr(workflow, "executor_type", None) or "web"
            ),
        )
        if not validation.valid:
            raise ValueError(
                "结构化网页计划无效："
                + "; ".join(validation.errors)
            )
        return True

    @staticmethod
    def _execution_mode(workflow: Workflow) -> str:
        explicit = str(
            getattr(workflow, "execution_mode", "") or ""
        ).strip().lower()
        if explicit:
            return explicit
        plan = getattr(workflow, "structured_plan", None)
        if isinstance(plan, dict):
            plan_mode = str(
                plan.get("execution_mode") or ""
            ).strip().lower()
            if plan_mode:
                return plan_mode
        if (getattr(workflow, "generated_code", None) or "").strip():
            return "legacy_code"
        return "structured"

    async def dry_run(
        self,
        code: str,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        execution_context = dict(context or {})
        execution_context["dry_run"] = True
        return await self.execute_code(code, execution_context)

    def _build_context(
        self,
        workflow: Workflow | None,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        execution_context = dict(context or {})
        execution_context.setdefault("credentials", {})
        execution_context.setdefault("variables", {})
        execution_context.setdefault("user_context", {})

        if not execution_context["variables"] and workflow is not None:
            structured_plan = workflow.structured_plan
            if isinstance(structured_plan, dict):
                variables = structured_plan.get("variables")
                if isinstance(variables, dict):
                    execution_context["variables"] = variables

        return execution_context

    @staticmethod
    def _validate_code(code: str) -> None:
        validation = CodeValidator.validate(code)
        if not validation.is_safe:
            raise ValueError(
                "Unsafe generated code: "
                + "; ".join(validation.violations)
            )
