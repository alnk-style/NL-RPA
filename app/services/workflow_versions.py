from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.validation import validate_workflow_draft
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.schemas.workflow_version import (
    WorkflowVersionChange,
    WorkflowVersionDiff,
)


LEGACY_CATALOG_VERSION = "legacy-2026-10-22"


class WorkflowVersionError(ValueError):
    pass


class WorkflowVersionNotFound(WorkflowVersionError):
    pass


class WorkflowVersionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish(
        self,
        workflow_id: int,
        user_id: int,
        *,
        workspace_id: int | None = None,
    ) -> WorkflowVersion:
        workflow = await self._owned_workflow(
            workflow_id,
            user_id,
            workspace_id=workspace_id,
        )
        plan, validation, catalog_version = self._snapshot(workflow)
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
        workflow.status = "published"
        await self.session.commit()
        await self.session.refresh(version)
        await self.session.refresh(workflow)
        return version

    async def list(
        self,
        workflow_id: int,
        user_id: int,
        *,
        workspace_id: int | None = None,
    ) -> list[WorkflowVersion]:
        await self._owned_workflow(
            workflow_id,
            user_id,
            workspace_id=workspace_id,
        )
        result = await self.session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(
                WorkflowVersion.version.desc(),
                WorkflowVersion.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def rollback(
        self,
        workflow_id: int,
        user_id: int,
        version_id: int,
        *,
        workspace_id: int | None = None,
    ) -> WorkflowVersion:
        workflow = await self._owned_workflow(
            workflow_id,
            user_id,
            workspace_id=workspace_id,
        )
        version = await self.session.get(WorkflowVersion, version_id)
        if version is None or version.workflow_id != workflow.id:
            raise WorkflowVersionNotFound("Workflow version not found")
        workflow.active_version_id = version.id
        workflow.status = "published"
        await self.session.commit()
        await self.session.refresh(workflow)
        return version

    async def diff(
        self,
        workflow_id: int,
        user_id: int,
        from_version_id: int,
        to_version_id: int,
        *,
        workspace_id: int | None = None,
    ) -> WorkflowVersionDiff:
        await self._owned_workflow(
            workflow_id,
            user_id,
            workspace_id=workspace_id,
        )
        from_version = await self.session.get(
            WorkflowVersion,
            from_version_id,
        )
        to_version = await self.session.get(
            WorkflowVersion,
            to_version_id,
        )
        if (
            from_version is None
            or to_version is None
            or from_version.workflow_id != workflow_id
            or to_version.workflow_id != workflow_id
        ):
            raise WorkflowVersionNotFound("Workflow version not found")
        changes: list[WorkflowVersionChange] = []
        changes.extend(
            diff_plans(from_version.plan, to_version.plan)
        )
        return WorkflowVersionDiff(
            from_version_id=from_version.id,
            to_version_id=to_version.id,
            changes=changes,
        )

    async def _owned_workflow(
        self,
        workflow_id: int,
        user_id: int,
        *,
        workspace_id: int | None,
    ) -> Workflow:
        statement = select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.user_id == user_id,
        )
        if workspace_id is not None:
            statement = statement.where(
                Workflow.workspace_id == workspace_id
            )
        workflow = (
            await self.session.execute(statement)
        ).scalar_one_or_none()
        if workflow is None:
            raise WorkflowVersionNotFound("Workflow not found")
        return workflow

    @staticmethod
    def _snapshot(
        workflow: Workflow,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        generated_code = (workflow.generated_code or "").strip()
        execution_mode = str(
            workflow.execution_mode or ""
        ).strip().lower()
        if execution_mode == "legacy_code" or (
            not execution_mode and generated_code
        ):
            workflow.execution_mode = "legacy_code"
            if not generated_code:
                raise WorkflowVersionError(
                    "Legacy workflow has no generated code"
                )
            return (
                {
                    "schema_version": 1,
                    "execution_mode": "legacy_code",
                    "generated_code": workflow.generated_code,
                },
                {
                    "valid": True,
                    "legacy_autosnapshot": True,
                },
                LEGACY_CATALOG_VERSION,
            )

        validation = validate_workflow_draft(
            workflow.structured_plan,
            executor_type=workflow.executor_type or "web",
        )
        if not validation.valid:
            raise WorkflowVersionError(
                "; ".join(validation.errors)
                or "Workflow plan is invalid"
            )
        plan = {
            **dict(workflow.structured_plan or {}),
            "schema_version": 1,
            "execution_mode": "structured",
            "schedule_timezone": workflow.schedule_timezone,
            "required_capabilities": list(
                workflow.required_capabilities or []
            ),
            "dag_definition": (
                dict(workflow.dag_definition)
                if isinstance(workflow.dag_definition, dict)
                else None
            ),
        }
        plan.pop("generated_code", None)
        return (
            plan,
            validation.model_dump(mode="json"),
            validation.action_catalog_version,
        )


def _diff_values(
    before: Any,
    after: Any,
    path: str,
    changes: list[WorkflowVersionChange],
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append(
                    WorkflowVersionChange(
                        kind="add",
                        path=child_path,
                        after=after[key],
                    )
                )
            elif key not in after:
                changes.append(
                    WorkflowVersionChange(
                        kind="remove",
                        path=child_path,
                        before=before[key],
                    )
                )
            else:
                _diff_values(
                    before[key],
                    after[key],
                    child_path,
                    changes,
                )
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                changes.append(
                    WorkflowVersionChange(
                        kind="add",
                        path=child_path,
                        after=after[index],
                    )
                )
            elif index >= len(after):
                changes.append(
                    WorkflowVersionChange(
                        kind="remove",
                        path=child_path,
                        before=before[index],
                    )
                )
            else:
                _diff_values(
                    before[index],
                    after[index],
                    child_path,
                    changes,
                )
        return
    if before != after:
        changes.append(
            WorkflowVersionChange(
                kind="change",
                path=path or "$",
                before=before,
                after=after,
            )
        )


def diff_plans(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[WorkflowVersionChange]:
    changes: list[WorkflowVersionChange] = []
    _diff_values(before, after, "", changes)
    return changes


__all__ = [
    "LEGACY_CATALOG_VERSION",
    "WorkflowVersionError",
    "WorkflowVersionNotFound",
    "WorkflowVersionService",
    "diff_plans",
]
