from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_agent.actions import ACTION_REGISTRY, ActionResult  # noqa: E402
from desktop_agent.config import AgentConfig  # noqa: E402
from desktop_agent.executor import TaskExecutor  # noqa: E402


def test_post_action_delay_stabilizes_wechat_keyboard_sequence() -> None:
    from desktop_agent.executor import _post_action_delay

    assert _post_action_delay("press_hotkey", {"keys": "^f"}) >= 0.5
    assert _post_action_delay("type_text", {"text": "文件传输助手"}) >= 0.4
    assert _post_action_delay("press_hotkey", {"keys": "{ENTER}"}) >= 0.8
    assert _post_action_delay("copy_file_to_clipboard", {}) >= 0.4
    assert _post_action_delay("press_hotkey", {"keys": "^v"}) >= 1.0
    assert _post_action_delay("screenshot", {}) == 0


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _executor(tmp_path: Path) -> tuple[TaskExecutor, _FakeClient]:
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_apps=[],
        allowed_doc_dirs=[str(tmp_path)],
        log_dir=str(tmp_path / "logs"),
        heartbeat_interval=60,
    )
    config._config_path = tmp_path / "config.json"
    client = _FakeClient()
    return TaskExecutor(config, client), client


def test_failed_step_reports_candidate_controls(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)

    async def handler(
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> ActionResult:
        return ActionResult(
            success=False,
            message="No matching UI Automation element",
            data={"candidates": ["保存(Button)"]},
        )

    ACTION_REGISTRY["_test_fail"] = handler
    try:
        asyncio.run(
            executor.execute(
                {
                    "task_id": 11,
                    "actions": [{"type": "_test_fail", "params": {}}],
                }
            )
        )
    finally:
        ACTION_REGISTRY.pop("_test_fail", None)

    failed = [
        event
        for event in client.events
        if event.get("type") == "desktop.failed"
    ]
    assert len(failed) == 1
    assert failed[0]["failed_step"]["details"]["candidates"] == [
        "保存(Button)"
    ]


def test_step_offset_shifts_reported_indices(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)

    async def handler(
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> ActionResult:
        return ActionResult(success=True, message="ok")

    ACTION_REGISTRY["_test_ok"] = handler
    try:
        asyncio.run(
            executor.execute(
                {
                    "task_id": 12,
                    "step_offset": 2,
                    "actions": [{"type": "_test_ok", "params": {}}],
                }
            )
        )
    finally:
        ACTION_REGISTRY.pop("_test_ok", None)

    progress = [
        event
        for event in client.events
        if event.get("type") == "desktop.progress"
    ]
    assert progress[0]["step_index"] == 2
    assert progress[0]["step_log"]["step_index"] == 2

    completed = [
        event
        for event in client.events
        if event.get("type") == "desktop.completed"
    ][0]
    assert completed["step_logs"][0]["step_index"] == 2


def test_resume_skips_completed_steps(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    executed: list[int] = []

    async def handler(
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> ActionResult:
        executed.append(context["step_index"])
        return ActionResult(success=True, message="ok")

    ACTION_REGISTRY["_test_ok"] = handler
    try:
        asyncio.run(
            executor.execute(
                {
                    "task_id": 21,
                    "resume_from_step": 1,
                    "completed_idempotency_keys": ["create-doc"],
                    "actions": [
                        {
                            "type": "_test_ok",
                            "step_id": 1,
                            "idempotency_key": "create-doc",
                            "params": {},
                        },
                        {
                            "type": "_test_ok",
                            "step_id": 2,
                            "idempotency_key": "insert-image",
                            "params": {},
                        },
                        {
                            "type": "_test_ok",
                            "step_id": 3,
                            "params": {},
                        },
                    ],
                }
            )
        )
    finally:
        ACTION_REGISTRY.pop("_test_ok", None)

    assert executed == [1, 2]

    skipped = [
        event
        for event in client.events
        if event.get("type") == "desktop.progress"
        and event.get("status") == "skipped"
    ]
    assert [event["step_index"] for event in skipped] == [0]
    assert skipped[0]["step_log"]["step_id"] == 1
    assert skipped[0]["step_log"]["idempotency_key"] == "create-doc"

    completed = [
        event
        for event in client.events
        if event.get("type") == "desktop.completed"
    ][0]
    assert completed["completed_idempotency_keys"] == [
        "create-doc",
        "insert-image",
    ]
    assert [entry["status"] for entry in completed["step_logs"]] == [
        "skipped",
        "success",
        "success",
    ]


def test_failed_step_reports_idempotency_markers(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)

    async def handler(
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> ActionResult:
        return ActionResult(success=False, message="WPS 文档服务繁忙")

    ACTION_REGISTRY["_test_fail"] = handler
    try:
        asyncio.run(
            executor.execute(
                {
                    "task_id": 22,
                    "completed_idempotency_keys": ["done-key"],
                    "actions": [
                        {
                            "type": "_test_fail",
                            "step_id": 7,
                            "idempotency_key": "failed-key",
                            "params": {"path": "report.docx"},
                        }
                    ],
                }
            )
        )
    finally:
        ACTION_REGISTRY.pop("_test_fail", None)

    failed = [
        event
        for event in client.events
        if event.get("type") == "desktop.failed"
    ][0]
    assert failed["completed_idempotency_keys"] == ["done-key"]
    assert failed["failed_step"]["step_id"] == 7
    assert failed["failed_step"]["idempotency_key"] == "failed-key"
    assert failed["failed_step"]["step_index"] == 0
