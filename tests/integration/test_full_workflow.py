from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow


@pytest.mark.asyncio
async def test_end_to_end_workflow(
    async_client,
    db_session,
    mock_engine,
    mock_notifier,
) -> None:
    assert await db_session.get(User, 1) is not None

    session_id = "full-workflow-session"
    chat_response = async_client.post(
        "/api/chat/message",
        json={
            "session_id": session_id,
            "message": "每天早上9点登录 example.com 并截图",
            "workflow_id": None,
        },
    )

    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    structured_plan = chat_payload["structured_plan"]
    generated_code = chat_payload["generated_code"]
    assert structured_plan["steps"]
    assert generated_code is None

    create_response = async_client.post(
        "/api/workflows",
        json={
            "name": "每日登录并截图",
            "description": "端到端测试流程",
            "natural_language_input": (
                "每天早上9点登录 example.com 并截图"
            ),
        },
    )
    assert create_response.status_code == 200
    workflow_id = create_response.json()["id"]

    update_response = async_client.put(
        f"/api/workflows/{workflow_id}",
        json={
            "structured_plan": structured_plan,
            "generated_code": generated_code,
            "trigger_type": "cron",
            "cron_expression": "0 9 * * *",
        },
    )
    assert update_response.status_code == 200

    confirm_response = async_client.post(
        "/api/chat/confirm",
        json={
            "session_id": session_id,
            "workflow_id": workflow_id,
        },
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json() == {
        "status": "confirmed",
        "workflow_id": workflow_id,
    }

    await db_session.rollback()
    saved_workflow = await db_session.get(Workflow, workflow_id)
    assert saved_workflow is not None
    assert saved_workflow.structured_plan == structured_plan
    assert saved_workflow.generated_code == generated_code

    publish_response = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["active_version_id"] > 0

    run_response = async_client.post(
        f"/api/executions/{workflow_id}/run"
    )
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert run_payload["success"] is True
    assert run_payload["status"] == "success"
    assert run_payload["output"] == "integration execution complete"

    mock_engine.execute_with_debug.assert_awaited_once()
    executed_workflow = mock_engine.execute_with_debug.await_args.args[0]
    assert executed_workflow.id == workflow_id
    mock_notifier.notify.assert_awaited_once()

    await db_session.rollback()
    result = await db_session.execute(
        select(ExecutionLog).where(
            ExecutionLog.workflow_id == workflow_id
        )
    )
    execution_log = result.scalar_one()
    assert execution_log.status == "success"
    assert execution_log.finished_at is not None
    assert execution_log.screenshots == ["logs/integration/step_1.png"]
    assert execution_log.step_logs[0]["action"] == "navigate"
