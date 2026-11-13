from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.recorder import (  # noqa: E402
    RecordingError,
    compile_recording,
)
from app.protocols import parse_structured_steps  # noqa: E402


def test_compile_web_recording_builds_valid_steps() -> None:
    draft = compile_recording(
        [
            {"kind": "navigate", "url": "https://example.com/login"},
            {
                "kind": "click",
                "locators": [
                    {"strategy": "css", "value": "#login"},
                    {
                        "strategy": "role",
                        "value": "button",
                        "name": "登录",
                    },
                ],
            },
            {
                "kind": "fill",
                "locators": [{"strategy": "label", "value": "用户名"}],
                "value": "a",
            },
            {
                "kind": "fill",
                "locators": [{"strategy": "label", "value": "用户名"}],
                "value": "alice",
            },
            {
                "kind": "select_option",
                "locators": [{"strategy": "label", "value": "角色"}],
                "value": "管理员",
            },
            {
                "kind": "press",
                "key": "Enter",
                "locators": [{"strategy": "label", "value": "角色"}],
            },
        ],
        mode="web",
    )

    plan = draft.plan
    assert plan["executor_type"] == "web"
    assert plan["execution_mode"] == "structured"
    assert [step["action"] for step in plan["steps"]] == [
        "goto",
        "click",
        "fill",
        "select_option",
        "press",
    ]
    # 连续输入合并为最后一次取值，定位按语义优先级排序。
    assert plan["steps"][2]["params"]["text"] == "alice"
    locator = plan["steps"][1]["locator"]
    assert locator["strategy"] == "role"
    assert locator["fallbacks"] == [{"strategy": "css", "value": "#login"}]
    assert plan["steps"][3]["locator"]["strategy"] == "label"
    assert plan["steps"][3]["params"] == {"label": "管理员"}

    keys = [step["idempotency_key"] for step in plan["steps"]]
    assert all(key.startswith("rec-") for key in keys)
    assert len(set(keys)) == len(keys)
    assert plan["steps"][1]["retry_policy"]["max_attempts"] == 1
    assert plan["steps"][2]["retry_policy"]["max_attempts"] == 2

    steps = parse_structured_steps(plan)
    assert [step.step_id for step in steps] == [1, 2, 3, 4, 5]
    assert steps[0].executor.value == "web"


def test_compile_web_recording_drops_click_before_fill() -> None:
    locator = {"strategy": "label", "value": "用户名"}
    draft = compile_recording(
        [
            {"kind": "click", "locators": [locator]},
            {"kind": "fill", "locators": [locator], "value": "alice"},
        ],
        mode="web",
    )

    assert [step["action"] for step in draft.plan["steps"]] == ["fill"]


def test_compile_web_recording_reports_warnings() -> None:
    draft = compile_recording(
        [
            {"kind": "click", "locators": []},
            {
                "kind": "upload",
                "locators": [{"strategy": "label", "value": "附件"}],
                "params": {"files": ["report.pdf"]},
            },
            {"kind": "wait", "value": 800},
            {"kind": "desktop_hotkey", "key": "Control+S"},
        ],
        mode="web",
    )

    assert [step["action"] for step in draft.plan["steps"]] == [
        "upload",
        "wait_for",
    ]
    assert draft.plan["steps"][1]["params"] == {"milliseconds": 800}
    assert draft.dropped_count == 1
    assert any(
        "点击" in warning or "click" in warning
        for warning in draft.warnings
    )
    assert any(
        "人工确认文件路径" in warning for warning in draft.warnings
    )
    assert any(
        "不匹配的事件" in warning for warning in draft.warnings
    )


def test_compile_desktop_recording_adds_window_activation() -> None:
    window = {"title": "记事本", "pid": 100}
    draft = compile_recording(
        [
            {"kind": "desktop_window", "window": window},
            {
                "kind": "desktop_click",
                "params": {
                    "automation_id": "saveBtn",
                    "name": "保存",
                    "control_type": "Button",
                },
                "window": window,
            },
            {"kind": "desktop_type", "value": "中文内容"},
            {"kind": "desktop_hotkey", "key": "Control+S"},
        ],
        mode="windows",
    )

    steps = draft.plan["steps"]
    assert [step["action"] for step in steps] == [
        "activate_window",
        "click_element",
        "type_text",
        "press_hotkey",
    ]
    assert steps[0]["params"] == {"title": "记事本", "pid": 100}
    assert steps[1]["params"]["automation_id"] == "saveBtn"
    assert steps[2]["params"]["method"] == "clipboard"
    assert steps[3]["retry_policy"]["max_attempts"] == 1
    assert draft.plan["required_capabilities"] == ["uia", "window"]


def test_compile_desktop_recording_requires_allowlisted_launch() -> None:
    events = [
        {"kind": "desktop_launch", "path": "notepad.exe"},
        {"kind": "desktop_wait", "params": {"seconds": 2}},
    ]

    with pytest.raises(RecordingError, match="白名单"):
        compile_recording(events, mode="windows", allowed_apps=[])

    draft = compile_recording(
        events,
        mode="windows",
        allowed_apps=["notepad.exe"],
    )
    assert draft.plan["steps"][0]["params"]["path"] == "notepad.exe"
    assert draft.plan["steps"][1]["params"] == {"seconds": 2.0}


def test_compile_recording_rejects_empty_and_invalid_events() -> None:
    with pytest.raises(RecordingError, match="没有生成任何可用步骤"):
        compile_recording([], mode="web")

    with pytest.raises(RecordingError, match="事件无效"):
        compile_recording(
            [{"kind": "not-a-kind"}],
            mode="web",
        )


def test_compile_recording_is_deterministic() -> None:
    events = [
        {"kind": "navigate", "url": "https://example.com"},
        {
            "kind": "click",
            "locators": [{"strategy": "text", "value": "提交"}],
        },
    ]

    first = compile_recording(events, mode="web")
    second = compile_recording(events, mode="web")

    assert [
        step["idempotency_key"] for step in first.plan["steps"]
    ] == [
        step["idempotency_key"] for step in second.plan["steps"]
    ]


def test_browser_recorder_script_contains_capture_hooks() -> None:
    from app.engine.recorder import BROWSER_RECORDER_SCRIPT

    for hook in (
        "__nlrpaRecorder",
        "data-testid",
        "select_option",
        "sessionStorage",
        "ctrlKey",
    ):
        assert hook in BROWSER_RECORDER_SCRIPT


def test_record_browser_helpers_write_draft(tmp_path: Path) -> None:
    from scripts import record_browser

    events = [{"kind": "navigate", "url": "https://example.com"}]
    draft = compile_recording(events, mode="web")

    payload = record_browser._payload(events, draft)
    path = record_browser._write_payload(
        tmp_path / "draft.json",
        payload,
    )

    assert path.is_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["draft"]["executor_type"] == "web"
    assert any(
        "goto" in line for line in record_browser._summary_lines(draft)
    )
    args = record_browser._parse_args(
        ["--url", "https://example.com"]
    )
    assert args.url == "https://example.com"
    assert args.headless is False
