from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


async def activate_test_version(
    session: AsyncSession,
    workflow: Workflow,
    *,
    plan: dict[str, Any] | None = None,
    action_catalog_version: str = "2026-10-28.1",
    validation: dict[str, Any] | None = None,
) -> WorkflowVersion:
    """Attach an immutable active snapshot to directly seeded workflows."""

    resolved_plan = dict(plan or workflow.structured_plan or {})
    if not resolved_plan:
        resolved_plan = {
            "schema_version": 1,
            "execution_mode": "legacy_code",
            "generated_code": workflow.generated_code or "",
        }
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        plan=resolved_plan,
        validation=validation or {
            "valid": True,
            "legacy_autosnapshot": True,
        },
        action_catalog_version=action_catalog_version,
    )
    session.add(version)
    await session.flush()
    workflow.active_version_id = version.id
    workflow.status = "published"
    await session.commit()
    await session.refresh(workflow)
    return version


__all__ = ["activate_test_version"]
