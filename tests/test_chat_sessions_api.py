from __future__ import annotations


SESSION_PAYLOAD = {
    "title": "恢复后的会话",
    "messages": [
        {
            "id": "message-1",
            "role": "user",
            "content": "创建一个测试文档",
            "timestamp": "10:00",
        }
    ],
    "structured_plan": {
        "trigger": {"type": "manual", "cron": None},
        "steps": [{"step_id": 1, "action": "create_document"}],
    },
    "generated_code": None,
    "needs_confirmation": False,
    "active_workflow_id": None,
    "initial_input": "创建一个测试文档",
    "updated_at": "2026-09-22T04:34:26.377Z",
}


def test_chat_sessions_are_persisted_and_listed(async_client) -> None:
    saved = async_client.put(
        "/api/chat/sessions/session-1",
        json=SESSION_PAYLOAD,
    )

    assert saved.status_code == 200
    assert saved.json()["session_id"] == "session-1"
    assert saved.json()["messages"][0]["content"] == "创建一个测试文档"

    listed = async_client.get("/api/chat/sessions")

    assert listed.status_code == 200
    assert [item["session_id"] for item in listed.json()] == ["session-1"]


def test_chat_sessions_can_be_deleted(async_client) -> None:
    async_client.put(
        "/api/chat/sessions/session-1",
        json=SESSION_PAYLOAD,
    )

    deleted = async_client.delete("/api/chat/sessions/session-1")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "session_id": "session-1"}
    assert async_client.get("/api/chat/sessions").json() == []
