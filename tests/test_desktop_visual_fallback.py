from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from unittest.mock import AsyncMock

from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.runner import ExecutionResult
from app.engine.visual_runtime import VisualRuntimeOutcome


class FakeConnectionManager:
    def get_online_agents(self):
        return [1]


class FakeVisualRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate_desktop(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return VisualRuntimeOutcome(
            status="awaiting_user",
            payload={
                "kind": "visual_ambiguous",
                "instruction": "请选择目标",
                "action": "click_element",
                "risk_level": "read_only",
                "candidates": [
                    {
                        "candidate_id": "cand_1",
                        "target": "保存",
                        "bbox": [10, 10, 50, 30],
                        "confidence": 0.94,
                    }
                ],
                "execution_allowed": True,
                "screenshot_scope": "window",
            },
            reason="ambiguous",
        )


def test_desktop_uia_failure_triggers_visual_human_task_payload(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (120, 80), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    screenshot_path = tmp_path / "window.png"
    screenshot_path.write_bytes(output.getvalue())
    runtime = FakeVisualRuntime()
    dispatcher = DesktopDispatcher(
        FakeConnectionManager(),
        visual_runtime=runtime,
    )
    workflow = SimpleNamespace(
        id=1,
        user_id=1,
        workspace_id=None,
        structured_plan={
            "steps": [
                {
                    "step_id": 2,
                    "executor": "windows",
                    "action": "click_element",
                    "visual_fallback": True,
                    "params": {
                        "name": "保存",
                        "visual_fallback": True,
                    },
                }
            ]
        },
    )
    result = ExecutionResult(
        success=False,
        output="",
        error="element not found",
        screenshots=[str(screenshot_path)],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 2,
                "action": "click_element",
                "status": "failed",
            }
        ],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 0,
                "step_id": 2,
                "action": "click_element",
                "failure_category": "locator_not_found",
                "window": {
                    "handle": 99,
                    "title": "编辑器",
                    "pid": 10,
                },
                "candidate_details": [
                    {"name": "保存", "control_type": "Button"}
                ],
            }
        },
    )

    updated = asyncio.run(
        dispatcher._maybe_apply_visual_fallback(
            workflow,
            result,
            {"user_id": 1, "execution_log_id": 5},
            agent_id=1,
        )
    )

    assert runtime.calls == 1
    assert updated.details["awaiting_user"] is True
    failed = updated.details["failed_step"]
    assert failed["failure_category"] == "visual_ambiguous"
    assert failed["human_action"]["kind"] == "visual_ambiguous"
    assert failed["human_action"]["candidates"][0][
        "candidate_id"
    ] == "cand_1"


def test_desktop_selected_candidate_replaces_only_target_action() -> None:
    dispatcher = DesktopDispatcher(FakeConnectionManager())
    plan = {
        "steps": [
            {
                "step_id": 1,
                "executor": "windows",
                "action": "activate_window",
                "params": {"title": "编辑器"},
            },
            {
                "step_id": 2,
                "executor": "windows",
                "action": "click_element",
                "params": {"name": "保存"},
            },
            {
                "step_id": 3,
                "executor": "windows",
                "action": "read_window_text",
                "params": {"variable": "text"},
            },
        ]
    }

    actions = dispatcher._convert_plan_to_actions(
        plan,
        {
            "visual_selection": {
                "step_id": 2,
                "candidate_id": "cand_1",
                "bbox": [10, 20, 50, 40],
                "window_info": {"handle": 99, "title": "编辑器"},
            }
        },
    )

    assert actions[1]["type"] == "activate_window"
    assert actions[2]["type"] == "click_relative"
    assert actions[2]["params"] == {"x": 30.0, "y": 30.0}
    assert actions[0]["type"] == "activate_window"
    assert actions[3]["type"] == "read_window_text"


def test_desktop_visual_auto_execution_replays_full_plan_from_checkpoint() -> None:
    dispatcher = DesktopDispatcher(FakeConnectionManager())
    dispatcher.dispatch = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            output="continued",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )
    )
    execute = dispatcher._visual_desktop_executor(
        workflow=SimpleNamespace(id=1),
        agent_id=7,
        failed_step_index=2,
    )

    passed, verification = asyncio.run(
        execute(
            {
                "step_id": 2,
                "candidate_id": "cand_1",
                "bbox": [10, 10, 30, 30],
            },
            SimpleNamespace(step_id=2),
            {"execution_log_id": 5, "user_id": 1},
        )
    )

    assert passed is True
    assert verification["type"] == "desktop_full_plan_replay"
    called_context = dispatcher.dispatch.await_args.args[1]
    assert called_context["visual_selection"]["candidate_id"] == "cand_1"
    assert called_context["resume_from_step"] == 2
    assert called_context["desktop_agent_id"] == 7
