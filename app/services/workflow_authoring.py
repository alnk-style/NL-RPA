from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from app.actions.registry import ActionRegistry, default_action_registry
from app.actions.validation import (
    WorkflowValidationResult,
    validate_workflow_draft,
)
from app.engine.dag import normalize_executor
from app.schemas.workflow_authoring import WorkflowDraftResult
from app.services.workflow_versions import diff_plans


class WorkflowAuthoringError(ValueError):
    pass


class WorkflowAuthoringService:
    """Build and validate drafts without creating or publishing versions."""

    def __init__(
        self,
        *,
        registry: ActionRegistry | None = None,
        intent_parser: Any | None = None,
        step_planner: Any | None = None,
        web_planner: Any | None = None,
        desktop_planner: Any | None = None,
        observer: Any | None = None,
        dispatcher: Any | None = None,
        desktop_context: Any | None = None,
    ) -> None:
        self.registry = registry or default_action_registry()
        self.intent_parser = intent_parser
        self.step_planner = step_planner
        self.web_planner = web_planner
        self.desktop_planner = desktop_planner
        self.observer = observer
        self.dispatcher = dispatcher
        self.desktop_context = desktop_context

    async def draft_from_goal(
        self,
        goal: str,
        *,
        start_url: str | None = None,
        session_id: str | None = None,
        base_plan: dict[str, Any] | None = None,
        selected_step_id: int | None = None,
    ) -> WorkflowDraftResult:
        del session_id
        base = self._base_plan(base_plan)
        observation: dict[str, Any] | None = None
        errors: list[str] = []
        warnings: list[str] = []
        try:
            parsed: dict[str, Any] | None = None
            if selected_step_id is not None or base_plan is not None:
                if self.step_planner is None:
                    raise WorkflowAuthoringError(
                        "Step planner is not configured"
                    )
                planner_input = deepcopy(base)
                planner_input["modification"] = goal
                if selected_step_id is not None:
                    planner_input["selected_step_id"] = selected_step_id
                planned = await self.step_planner.plan(planner_input)
            else:
                if self.intent_parser is None or self.step_planner is None:
                    raise WorkflowAuthoringError(
                        "Draft authoring is not configured"
                    )
                parsed = await self.intent_parser.parse(goal)
                planned = await self.step_planner.plan(parsed)
                if self._plan_contains_web(planned):
                    if start_url and self.observer is not None:
                        observation = await self.observe(
                            start_url,
                            max_elements=80,
                        )
                    if self.web_planner is not None:
                        planned = await self.web_planner.plan(
                            parsed,
                            observation=observation,
                            start_url=start_url,
                            detailed_plan=planned,
                        )

            if not isinstance(planned, dict):
                raise WorkflowAuthoringError(
                    "Planner must return a workflow object"
                )
            if planned.get("generated_code"):
                errors.append(
                    "Generated code is not allowed in structured drafts"
                )
            candidate = self.normalize_plan(planned)
            validation = self.validate(candidate)
            errors.extend(validation.errors)
            warnings.extend(validation.warnings)
            if observation is not None:
                locator_errors = _validate_observed_locators(
                    candidate,
                    observation,
                )
                errors.extend(locator_errors)
            errors = _unique(errors)
            if errors:
                return WorkflowDraftResult(
                    plan=base,
                    base_plan=deepcopy(base),
                    valid=False,
                    errors=errors,
                    warnings=_unique(warnings),
                    observations=observation,
                    changes=diff_plans(base, base),
                )
            return WorkflowDraftResult(
                plan=candidate,
                base_plan=deepcopy(base),
                valid=True,
                errors=[],
                warnings=_unique(warnings),
                observations=observation,
                changes=diff_plans(base, candidate),
            )
        except Exception as exc:
            return WorkflowDraftResult(
                plan=base,
                base_plan=deepcopy(base),
                valid=False,
                errors=[str(exc) or exc.__class__.__name__],
                warnings=_unique(warnings),
                observations=observation,
                changes=diff_plans(base, base),
            )

    def validate(
        self,
        plan: dict[str, Any],
        *,
        executor_type: str = "web",
    ) -> WorkflowValidationResult:
        normalized = self.normalize_plan(
            plan,
            executor_type=executor_type,
        )
        return validate_workflow_draft(
            normalized,
            executor_type=normalized.get("executor_type") or executor_type,
        )

    def normalize_plan(
        self,
        plan: dict[str, Any],
        *,
        executor_type: str = "web",
    ) -> dict[str, Any]:
        candidate = deepcopy(plan)
        candidate.pop("generated_code", None)
        candidate["schema_version"] = 1
        candidate["execution_mode"] = "structured"
        candidate.setdefault("executor_type", executor_type or "web")
        candidate.setdefault("variables", [])
        candidate.setdefault(
            "trigger",
            {"type": "manual", "cron": None},
        )
        steps = candidate.get("steps")
        if not isinstance(steps, list):
            candidate["steps"] = []
            return candidate
        default = str(candidate.get("executor_type") or "web")
        normalized_steps: list[dict[str, Any]] = []
        for index, raw in enumerate(steps):
            if not isinstance(raw, dict):
                normalized_steps.append(raw)
                continue
            step = deepcopy(raw)
            step.setdefault("step_id", index + 1)
            step["executor"] = normalize_executor(
                step.get("executor"),
                default=default,
            )
            action = str(step.get("action") or "").strip()
            step["action"] = "goto" if action == "navigate" else action
            step.setdefault("params", {})
            step.setdefault("depends_on", [])
            normalized_steps.append(step)
        candidate["steps"] = normalized_steps
        return candidate

    def normalize_recorded_plan(
        self,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self.normalize_plan(
            plan,
            executor_type=str(plan.get("executor_type") or "web"),
        )
        validation = self.validate(normalized)
        if not validation.valid:
            raise WorkflowAuthoringError(
                "; ".join(validation.errors)
                or "Recorded draft is invalid"
            )
        return normalized

    async def observe(
        self,
        url: str,
        *,
        max_elements: int = 80,
        include_accessibility: bool = True,
    ) -> dict[str, Any]:
        if self.observer is None:
            raise WorkflowAuthoringError(
                "Browser observer is not configured"
            )
        snapshot = await self.observer.observe(
            url,
            wait_until="domcontentloaded",
            timeout_ms=30000,
            max_elements=max_elements,
            include_accessibility=include_accessibility,
        )
        return snapshot.compact(max_elements=max_elements)

    @staticmethod
    def _base_plan(value: dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(value, dict):
            return deepcopy(value)
        return {
            "schema_version": 1,
            "execution_mode": "structured",
            "executor_type": "web",
            "variables": [],
            "steps": [],
        }

    @staticmethod
    def _plan_contains_web(plan: Any) -> bool:
        if not isinstance(plan, dict):
            return False
        steps = plan.get("steps")
        if not isinstance(steps, list):
            return False
        executors = {
            normalize_executor(
                step.get("executor"),
                default=str(plan.get("executor_type") or "web"),
            )
            for step in steps
            if isinstance(step, dict)
        }
        return "web" in executors or not executors


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _validate_observed_locators(
    plan: dict[str, Any],
    observation: dict[str, Any],
) -> list[str]:
    elements = observation.get("elements")
    if not isinstance(elements, list):
        return []
    candidates: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        raw = element.get("locators")
        if isinstance(raw, list):
            candidates.extend(
                dict(item) for item in raw if isinstance(item, dict)
            )
        best = element.get("best_locator")
        if isinstance(best, dict):
            candidates.append(dict(best))
    errors: list[str] = []
    steps = plan.get("steps")
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        locator = step.get("locator")
        if not isinstance(locator, dict):
            continue
        if not any(_same_locator(locator, item) for item in candidates):
            errors.append(
                f"Step {step.get('step_id')} locator was not present in "
                "observation candidates"
            )
    return errors


def _same_locator(
    locator: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    for key in ("strategy", "value", "name"):
        left = locator.get(key)
        right = candidate.get(key)
        if left is None and right is None:
            continue
        if str(left or "").strip() != str(right or "").strip():
            return False
    return True


class WorkflowDraftRunner:
    """Run draft plans without creating formal execution records."""

    def __init__(
        self,
        *,
        engine: Any | None = None,
        desktop_executor: Any | None = None,
        session_factory: Any | None = None,
        config: Any | None = None,
        mcp_registry: Any | None = None,
    ) -> None:
        self.engine = engine
        self.desktop_executor = desktop_executor
        self.session_factory = session_factory
        self.config = config
        self.mcp_registry = mcp_registry

    async def run(
        self,
        plan: dict[str, Any],
        *,
        mode: str,
        context: dict[str, Any] | None = None,
    ):
        from app.engine.hybrid_executor import HybridExecutor

        workflow = SimpleNamespace(
            id=None,
            user_id=(context or {}).get("user_id"),
            workspace_id=(context or {}).get("workspace_id"),
            name=f"Draft {mode}",
            executor_type=str(plan.get("executor_type") or "web"),
            required_capabilities=[],
            dag_definition=None,
            structured_plan=deepcopy(plan),
            generated_code=None,
            execution_mode="structured",
        )
        executor = HybridExecutor(
            self.engine,
            self.desktop_executor,
            session_factory=self.session_factory,
            config=self.config,
            mcp_registry=self.mcp_registry,
        )
        run_context = dict(context or {})
        run_context["dry_run"] = mode == "dry_run"
        run_context["test_mode"] = mode
        return await executor.execute(workflow, run_context)


__all__ = [
    "WorkflowAuthoringError",
    "WorkflowAuthoringService",
    "WorkflowDraftRunner",
]
