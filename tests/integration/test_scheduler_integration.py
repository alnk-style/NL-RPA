from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.execution import ExecutionLog
from app.models.workflow import Workflow
from app.scheduler import SchedulerManager
from app.scheduler import jobs


@pytest.mark.asyncio
async def test_scheduler_integration(
    async_client,
    db_session,
    mock_engine,
    mock_notifier,
) -> None:
    create_response = async_client.post(
        "/api/workflows",
        json={
            "name": "每分钟定时流程",
            "description": "定时任务整合测试",
            "natural_language_input": "每分钟访问 example.com",
        },
    )
    assert create_response.status_code == 200
    workflow_id = create_response.json()["id"]

    update_response = async_client.put(
        f"/api/workflows/{workflow_id}",
        json={
            "structured_plan": {
                "trigger": {"type": "cron", "cron": "* * * * *"},
                "steps": [
                    {
                        "step_id": 1,
                        "action": "navigate",
                        "params": {"url": "https://example.com"},
                    }
                ],
            },
            "generated_code": (
                "async def main(page):\n"
                "    await page.goto('https://example.com')"
            ),
            "trigger_type": "cron",
            "cron_expression": "* * * * *",
        },
    )
    assert update_response.status_code == 200

    publish_response = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    )
    assert publish_response.status_code == 200
    publish_payload = publish_response.json()
    assert publish_payload["status"] == "published"
    assert publish_payload["workflow_id"] == workflow_id
    assert publish_payload["version"] == 1
    assert publish_payload["active_version_id"] > 0

    scheduler = async_client.app.state.scheduler
    scheduler.schedule_workflow.assert_awaited_once()
    scheduled_workflow = scheduler.schedule_workflow.await_args.args[0]
    assert scheduled_workflow.id == workflow_id
    assert scheduled_workflow.trigger_type == "cron"
    assert scheduled_workflow.cron_expression == "* * * * *"

    await db_session.rollback()
    published_workflow = await db_session.get(Workflow, workflow_id)
    assert published_workflow is not None
    assert published_workflow.status == "published"

    session_factory = db_session.info["session_factory"]
    with (
        patch.object(jobs, "AsyncSessionLocal", session_factory),
        patch.object(
            jobs,
            "_get_application",
            return_value=async_client.app,
        ),
    ):
        await SchedulerManager._execute_workflow_job(workflow_id)

    mock_engine.execute_with_debug.assert_awaited_once()
    mock_notifier.notify.assert_awaited_once()
    notified_log, notified_workflow = mock_notifier.notify.await_args.args
    assert notified_log.status == "success"
    assert notified_workflow.id == workflow_id

    await db_session.rollback()
    result = await db_session.execute(
        select(ExecutionLog).where(
            ExecutionLog.workflow_id == workflow_id
        )
    )
    execution_log = result.scalar_one()
    assert execution_log.status == "success"
    assert execution_log.finished_at is not None
