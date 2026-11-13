from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.validation import validate_workflow_draft
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.schemas.execution_policy import (
    ExecutionPolicy,
    validate_execution_policy,
)
from app.services.workflow_versions import (
    WorkflowVersionError,
    WorkflowVersionService,
)


@dataclass(frozen=True)
class ExecutionSnapshot:
    workflow_id: int
    version_id: int
    version: int
    plan: dict[str, Any]
    policy: ExecutionPolicy
    action_catalog_version: str
    executor_type: str
    schedule_timezone: str

    def workflow_view(self, workflow: Workflow) -> SimpleNamespace:
        plan = dict(self.plan)
        return SimpleNamespace(
            id=workflow.id,
            user_id=workflow.user_id,
            workspace_id=workflow.workspace_id,
            name=workflow.name,
            description=workflow.description,
            structured_plan=plan,
            generated_code=plan.get("generated_code"),
            dag_definition=(
                plan.get("dag_definition")
                if isinstance(
                    plan.get("dag_definition"),
                    dict,
                )
                else workflow.dag_definition
            ),
            executor_type=self.executor_type,
            required_capabilities=(
                plan.get("required_capabilities")
                if isinstance(
                    plan.get("required_capabilities"),
                    list,
                )
                else workflow.required_capabilities
            ),
            execution_mode=plan.get(
                "execution_mode",
                workflow.execution_mode,
            ),
            execution_policy=self.policy.model_dump(mode="json"),
            schedule_timezone=self.schedule_timezone,
            workflow_version_id=self.version_id,
        )


class ExecutionVersionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_active(
        self,
        workflow: Workflow,
    ) -> ExecutionSnapshot:
        if workflow.active_version_id is None:
            raise WorkflowVersionError(
                "Workflow has no published active version"
            )
        return await self.resolve_version(
            workflow,
            int(workflow.active_version_id),
        )

    async def resolve_version(
        self,
        workflow: Workflow,
        version_id: int,
    ) -> ExecutionSnapshot:
        version = await self.session.get(WorkflowVersion, int(version_id))
        if version is None or version.workflow_id != workflow.id:
            raise WorkflowVersionError("Workflow version not found")

        policy = validate_execution_policy(workflow.execution_policy)
        plan = dict(version.plan)
        return ExecutionSnapshot(
            workflow_id=workflow.id,
            version_id=version.id,
            version=version.version,
            plan=plan,
            policy=policy,
            action_catalog_version=version.action_catalog_version,
            executor_type=str(
                plan.get("executor_type")
                or workflow.executor_type
                or "web"
            ),
            schedule_timezone=workflow.schedule_timezone,
        )

    async def _create_compatibility_snapshot(
        self,
        workflow: Workflow,
    ) -> WorkflowVersion:
        has_plan = (
            isinstance(workflow.structured_plan, dict)
            and bool(workflow.structured_plan.get("steps"))
        )
        has_code = bool((workflow.generated_code or "").strip())
        if not has_plan and not has_code:
            plan = {
                "schema_version": 1,
                "execution_mode": "legacy_code",
                "generated_code": "",
            }
            validation = {
                "valid": True,
                "migrated": True,
                "empty_compatibility": True,
            }
            catalog_version = "legacy-2026-10-22"
        elif has_plan:
            plan = {
                **dict(workflow.structured_plan or {}),
                "schema_version": 1,
                "execution_mode": "structured",
                "required_capabilities": list(
                    workflow.required_capabilities or []
                ),
                "dag_definition": (
                    dict(workflow.dag_definition)
                    if isinstance(
                        workflow.dag_definition,
                        dict,
                    )
                    else None
                ),
            }
            validation_result = validate_workflow_draft(
                plan,
                executor_type=workflow.executor_type or "web",
                include_legacy=True,
                validate_params=False,
            )
            if not validation_result.valid:
                raise WorkflowVersionError(
                    "; ".join(validation_result.errors)
                )
            validation = {
                "valid": True,
                "migrated": True,
                "legacy_autosnapshot": True,
                "warnings": validation_result.warnings,
            }
            catalog_version = "legacy-2026-10-22"
        else:
            plan, validation, catalog_version = (
                WorkflowVersionService._snapshot(workflow)
            )
        latest = await self.session.scalar(
            select(func.max(WorkflowVersion.version)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=int(latest or 0) + 1,
            plan=plan,
            validation=validation,
            action_catalog_version=catalog_version,
        )
        self.session.add(version)
        await self.session.flush()
        workflow.active_version_id = version.id
        return version


__all__ = [
    "ExecutionSnapshot",
    "ExecutionVersionService",
]
