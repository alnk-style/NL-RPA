from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.desktop_planner import (  # noqa: E402
    DesktopExecutionContext,
    DesktopPlanError,
    DesktopPlannerAgent,
    is_desktop_intent,
    select_target_window,
    validate_desktop_plan,
    window_hints,
)
from desktop_agent.action_validator import ActionValidator  # noqa: E402
from desktop_agent.config import AgentConfig  # noqa: E402

CONFIG = SimpleNamespace(
    DEEPSEEK_BASE_URL="http://127.0.0.1:9",
    DEEPSEEK_API_KEY="test-key",
    DEEPSEEK_MODEL="test-model",
)


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[dict[str, Any]] | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
    ) -> str:
        self.messages = messages
        return self.response


def _launch_plan(path: str = "notepad.exe") -> dict[str, Any]:
    return {
        "executor_type": "windows",
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "launch_app",
                "params": {"path": path},
                "depends_on": [],
            }
        ],
        "variables": [],
    }


def _planner(response: dict[str, Any] | str) -> DesktopPlannerAgent:
    agent = DesktopPlannerAgent(CONFIG)
    payload = response if isinstance(response, str) else json.dumps(response)
    agent.llm = _FakeLLM(payload)
    return agent


def test_desktop_plan_rejects_action_outside_catalog() -> None:
    plan = _launch_plan()
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "navigate",
            "params": {"url": "https://example.com"},
        }
    ]

    with pytest.raises(DesktopPlanError, match="navigate"):
        validate_desktop_plan(plan, allowed_apps=("notepad.exe",))


def test_desktop_plan_rejects_application_outside_allowlist() -> None:
    plan = _launch_plan(path=r"C:\Tools\wechat.exe")

    with pytest.raises(DesktopPlanError, match="wechat.exe"):
        validate_desktop_plan(plan, allowed_apps=("notepad.exe",))


def test_desktop_plan_accepts_allowlisted_bare_name() -> None:
    validated = validate_desktop_plan(
        _launch_plan(path="notepad.exe"),
        allowed_apps=("notepad.exe",),
    )

    assert validated["executor_type"] == "windows"
    assert validated["steps"][0]["params"]["path"] == "notepad.exe"


def test_desktop_plan_accepts_allowlisted_full_path() -> None:
    plan = _launch_plan(path=r"C:\Tools\notepad.exe")

    validated = validate_desktop_plan(
        plan,
        allowed_apps=(r"C:\Tools\notepad.exe",),
    )

    assert validated["steps"][0]["params"]["path"] == (
        r"C:\Tools\notepad.exe"
    )


def test_desktop_plan_derives_required_capabilities() -> None:
    plan = _launch_plan()
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "launch_app",
            "params": {"path": "notepad.exe"},
        },
        {
            "step_id": 2,
            "action": "activate_window",
            "params": {"title": "记事本"},
        },
        {
            "step_id": 3,
            "action": "click_element",
            "params": {"name": "文件"},
        },
    ]

    validated = validate_desktop_plan(
        plan,
        allowed_apps=("notepad.exe",),
    )

    assert validated["required_capabilities"] == ["uia", "window"]


def test_desktop_plan_removes_runtime_clarifications() -> None:
    plan = _launch_plan()
    plan["needs_clarification"] = ["请提供窗口类名"]
    plan["steps"][0]["selector"] = "needs_user_input"
    plan["steps"][0]["selector_reason"] = "请提供 UIA 定位信息"

    validated = validate_desktop_plan(
        plan,
        allowed_apps=("notepad.exe",),
    )

    assert "needs_clarification" not in validated
    assert "selector" not in validated["steps"][0]
    assert "selector_reason" not in validated["steps"][0]


def test_resolve_allowed_apps_matches_installed_display_names() -> None:
    from app.agents import desktop_planner as desktop_planner_module

    resolve_allowed_apps = getattr(
        desktop_planner_module,
        "resolve_allowed_apps",
    )
    installed_apps = [
        SimpleNamespace(
            name="酷狗音乐",
            executable_path=r"D:\KGMusic\KuGou.exe",
        ),
        SimpleNamespace(
            name="微信",
            executable_path=r"D:\Weixin\Weixin.exe",
        ),
        SimpleNamespace(
            name="记事本",
            executable_path=r"C:\Windows\notepad.exe",
        ),
    ]

    resolved = resolve_allowed_apps(
        "打开酷狗音乐并截图，然后打开微信发给联系人",
        (
            r"D:\KGMusic\KuGou.exe",
            r"D:\Weixin\Weixin.exe",
        ),
        installed_apps,
    )

    assert resolved == [
        {
            "name": "酷狗音乐",
            "path": r"D:\KGMusic\KuGou.exe",
        },
        {
            "name": "微信",
            "path": r"D:\Weixin\Weixin.exe",
        },
    ]


def test_resolve_allowed_apps_accepts_generated_integration_records() -> None:
    from app.agents import desktop_planner as desktop_planner_module

    resolve_allowed_apps = getattr(
        desktop_planner_module,
        "resolve_allowed_apps",
    )
    integrations = [
        {
            "name": "酷狗音乐",
            "executable_path": r"D:\KGMusic\KuGou.exe",
        },
    ]

    resolved = resolve_allowed_apps(
        "打开酷狗音乐并截图",
        (r"D:\KGMusic\KuGou.exe",),
        integrations,
    )

    assert resolved == [
        {
            "name": "酷狗音乐",
            "path": r"D:\KGMusic\KuGou.exe",
        },
    ]


def test_desktop_plan_requires_at_least_one_step() -> None:
    plan = _launch_plan()
    plan["steps"] = []

    with pytest.raises(DesktopPlanError, match="步骤"):
        validate_desktop_plan(plan, allowed_apps=("notepad.exe",))


def test_desktop_planner_requires_allowlisted_apps() -> None:
    planner = _planner(_launch_plan())

    with pytest.raises(DesktopPlanError, match="白名单"):
        asyncio.run(planner.plan({"steps": []}, DesktopExecutionContext()))


def test_desktop_planner_prompt_exposes_allowlist_and_catalog() -> None:
    planner = _planner(_launch_plan())
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe", r"C:\Tools\wechat.exe"),
        capabilities=frozenset({"window", "uia", "screenshot"}),
        online_agent_count=1,
        paired_agent_count=1,
    )

    plan = asyncio.run(
        planner.plan(
            {"executor_type": "windows", "steps": []},
            context,
        )
    )

    assert plan["executor_type"] == "windows"
    assert plan["steps"][0]["action"] == "launch_app"
    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "notepad.exe" in prompt
    assert r"C:\Tools\wechat.exe" in prompt
    assert "launch_app" in prompt
    assert "click_element" in prompt


def test_desktop_planner_prompt_exposes_writable_dirs() -> None:
    planner = _planner(_launch_plan())
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        allowed_doc_dirs=(r"D:\Administrator\Pictures",),
        capabilities=frozenset({"window", "screenshot"}),
        online_agent_count=1,
        paired_agent_count=1,
    )

    asyncio.run(
        planner.plan(
            {"executor_type": "windows", "steps": []},
            context,
        )
    )

    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert r"D:\Administrator\Pictures" in prompt
    assert "screenshot.path" in prompt


def test_desktop_planner_prompt_prefers_runtime_resolution() -> None:
    planner = _planner(_launch_plan())
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        online_agent_count=1,
        paired_agent_count=1,
    )

    asyncio.run(
        planner.plan(
            {
                "executor_type": "windows",
                "resolved_apps": [
                    {
                        "name": "记事本",
                        "path": "notepad.exe",
                    }
                ],
            },
            context,
        )
    )

    assert planner.llm.messages is not None
    rendered = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "不要在计划中向用户追问" in rendered
    assert "始终使用 launch_app" in rendered
    assert "copy_file_to_clipboard" in rendered
    assert "resolved_apps" in rendered


def test_is_desktop_intent_reads_executor_type_and_params() -> None:
    assert is_desktop_intent({"executor_type": "windows"}, {}) is True
    assert (
        is_desktop_intent(
            {},
            {"steps": [{"params": {"window_title": "记事本"}}]},
        )
        is True
    )
    assert (
        is_desktop_intent(
            {"executor_type": "web"},
            {
                "steps": [
                    {
                        "action": "navigate",
                        "params": {"url": "https://example.com"},
                    }
                ]
            },
        )
        is False
    )


def test_is_desktop_intent_recognizes_app_hint_for_mcp_candidate() -> None:
    assert is_desktop_intent(
        {
            "executor_type": "mcp",
            "app_hint": "酷狗音乐",
            "steps": [
                {
                    "executor": "mcp",
                    "action": "call_tool",
                    "params": {
                        "server": "酷狗音乐",
                        "tool": "app_launch",
                    },
                }
            ],
        }
    )


def test_validated_plan_passes_agent_action_validator(
    tmp_path: Path,
) -> None:
    plan = _launch_plan()
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "launch_app",
            "params": {"path": "notepad.exe"},
            "depends_on": [],
        },
        {
            "step_id": 2,
            "action": "type_text",
            "params": {"text": "周报", "method": "clipboard"},
            "depends_on": [1],
        },
        {
            "step_id": 3,
            "action": "screenshot",
            "params": {"name": "result"},
            "depends_on": [2],
        },
    ]
    validated = validate_desktop_plan(
        plan,
        allowed_apps=("notepad.exe",),
    )
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_apps=["notepad.exe"],
        allowed_doc_dirs=[str(tmp_path)],
    )

    for step in validated["steps"]:
        result = ActionValidator.validate(
            {"type": step["action"], "params": step["params"]},
            config,
        )
        assert result.allowed is True, result.reason


def test_screenshot_plan_keeps_target_and_passes_validator(
    tmp_path: Path,
) -> None:
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    target = pictures / "2026-09-24.png"
    plan = {
        "executor_type": "windows",
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "screenshot",
                "params": {
                    "path": str(target),
                    "window": {"title": "微信"},
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    validated = validate_desktop_plan(plan, allowed_apps=())
    step = validated["steps"][0]
    assert step["params"]["path"] == str(target)
    assert step["params"]["window"] == {"title": "微信"}
    assert validated["required_capabilities"] == ["screenshot"]

    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_apps=[],
        allowed_doc_dirs=[str(tmp_path)],
    )
    result = ActionValidator.validate(
        {"type": step["action"], "params": step["params"]},
        config,
    )

    assert result.allowed is True, result.reason


def test_window_hints_collect_app_hint_and_step_targets() -> None:
    hints = window_hints(
        {
            "app_hint": "微信",
            "steps": [
                {"target": "张三"},
                {"target": ""},
                {"target": "https://example.com"},
            ],
        }
    )

    assert hints[0] == "微信"
    assert "张三" in hints


def test_select_target_window_matches_hints() -> None:
    windows = [
        {
            "handle": 1,
            "title": "记事本 - 未命名",
            "class_name": "Notepad",
            "pid": 10,
            "process_name": "notepad.exe",
        },
        {
            "handle": 2,
            "title": "微信",
            "class_name": "WeChatMainWndForPC",
            "pid": 20,
            "process_name": "WeChat.exe",
        },
    ]

    assert select_target_window(windows, ["微信"]) == windows[1]
    assert select_target_window(windows, ["notepad"]) == windows[0]
    assert select_target_window(windows, ["钉钉"]) is None


def test_desktop_planner_prompt_includes_snapshot() -> None:
    planner = _planner(_launch_plan())
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        capabilities=frozenset({"window", "uia"}),
        agent_ids=(1,),
        online_agent_count=1,
        paired_agent_count=1,
    )
    snapshot = {
        "windows": [
            {"handle": 1, "title": "记事本", "process_name": "notepad.exe"}
        ],
        "target": {"handle": 1, "title": "记事本"},
        "elements": [
            {
                "name": "文本编辑器",
                "automation_id": "15",
                "control_type": "Document",
                "depth": 1,
            }
        ],
    }

    plan = asyncio.run(
        planner.plan(
            {"executor_type": "windows"},
            context,
            snapshot=snapshot,
        )
    )

    assert plan["executor_type"] == "windows"
    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "记事本" in prompt
    assert "文本编辑器" in prompt
    assert "15" in prompt


def test_repair_returns_validated_steps() -> None:
    planner = _planner(
        {
            "steps": [
                {
                    "step_id": 2,
                    "action": "click_element",
                    "params": {
                        "name": "保存",
                        "control_type": "Button",
                    },
                    "depends_on": [1],
                },
                {
                    "step_id": 3,
                    "action": "screenshot",
                    "params": {"name": "result"},
                    "depends_on": [2],
                },
            ]
        }
    )
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        capabilities=frozenset({"window", "uia"}),
        agent_ids=(1,),
    )

    steps = asyncio.run(
        planner.repair(
            plan=_launch_plan(),
            step_index=1,
            failure={
                "step_index": 1,
                "action_type": "click_element",
                "error": "No matching UI Automation element",
                "candidates": ["保存(Button)", "关闭(Button)"],
            },
            context=context,
        )
    )

    assert [step["action"] for step in steps] == [
        "click_element",
        "screenshot",
    ]
    assert steps[0]["params"]["control_type"] == "Button"
    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "保存(Button)" in prompt
    assert "click_element" in prompt


def test_repair_rejects_non_allowlisted_application() -> None:
    planner = _planner(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "launch_app",
                    "params": {"path": r"C:\Tools\wechat.exe"},
                    "depends_on": [],
                }
            ]
        }
    )
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        capabilities=frozenset({"window"}),
        agent_ids=(1,),
    )

    with pytest.raises(DesktopPlanError, match="wechat.exe"):
        asyncio.run(
            planner.repair(
                plan=_launch_plan(),
                step_index=0,
                failure={
                    "step_index": 0,
                    "action_type": "launch_app",
                    "error": "no window",
                    "candidates": [],
                },
                context=context,
            )
        )


def test_planner_prompt_exposes_scored_element_fields() -> None:
    planner = _planner(_launch_plan())
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        capabilities=frozenset({"window", "uia"}),
        online_agent_count=1,
        paired_agent_count=1,
    )
    snapshot = {
        "windows": [
            {
                "handle": 10,
                "title": "记事本",
                "class_name": "Notepad",
                "pid": 100,
                "process_name": "notepad.exe",
            }
        ],
        "target": {"handle": 10, "title": "记事本"},
        "elements": [
            {
                "index": 1,
                "depth": 1,
                "name": "保存",
                "automation_id": "saveBtn",
                "control_type": "Button",
                "class_name": "Button",
                "value": "保存",
                "enabled": True,
                "visible": True,
                "locators": [
                    {
                        "strategy": "automation_id",
                        "value": "saveBtn",
                        "name": None,
                        "control_type": None,
                        "count": 1,
                        "unique": True,
                        "score": 100,
                    }
                ],
                "best_locator": {
                    "strategy": "automation_id",
                    "value": "saveBtn",
                    "name": None,
                    "control_type": None,
                    "count": 1,
                    "unique": True,
                    "score": 100,
                },
            }
        ],
    }

    asyncio.run(
        planner.plan(
            {"executor_type": "windows", "steps": []},
            context,
            snapshot=snapshot,
        )
    )

    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "best_locator=automation_id:saveBtn(100)" in prompt
    assert "enabled=true" in prompt
    assert "value=保存" in prompt


def test_repair_prompt_renders_candidate_details_and_last_locator() -> None:
    planner = _planner(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "click_element",
                    "params": {
                        "automation_id": "saveBtn",
                        "control_type": "Button",
                    },
                    "depends_on": [],
                }
            ]
        }
    )
    context = DesktopExecutionContext(
        allowed_apps=("notepad.exe",),
        capabilities=frozenset({"uia"}),
        agent_ids=(1,),
    )

    steps = asyncio.run(
        planner.repair(
            plan=_launch_plan(),
            step_index=0,
            failure={
                "step_index": 0,
                "action_type": "click_element",
                "error": "No matching UI Automation element",
                "candidates": ["保存(Button)"],
                "candidate_details": [
                    {
                        "index": 1,
                        "name": "保存",
                        "automation_id": "saveBtn",
                        "control_type": "Button",
                        "class_name": "Button",
                        "enabled": True,
                        "visible": True,
                        "score": 124,
                        "best_locator": {
                            "strategy": "automation_id",
                            "value": "saveBtn",
                            "count": 1,
                            "unique": True,
                            "score": 100,
                        },
                    }
                ],
                "last_locator": {
                    "automation_id": "saveBtn",
                    "name": None,
                    "control_type": None,
                    "window_handle": 11,
                },
            },
            context=context,
        )
    )

    assert steps[0]["action"] == "click_element"
    assert planner.llm.messages is not None
    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages
    )
    assert "automation_id=saveBtn" in prompt
    assert "score=124" in prompt
    assert "best_locator=automation_id:saveBtn(100)" in prompt
    assert "上次成功定位" in prompt
    assert "window_handle" in prompt


def test_desktop_plan_uses_result_variable_for_document(
    tmp_path: Path,
) -> None:
    document = tmp_path / "lyrics.docx"
    plan = validate_desktop_plan(
        {
            "executor_type": "windows",
            "variables": [{"name": "lyrics", "default": ""}],
            "steps": [
                {
                    "step_id": 1,
                    "action": "copy_selected_text",
                    "result_var": "lyrics",
                    "params": {"keys": "^a^c"},
                },
                {
                    "step_id": 2,
                    "action": "create_document",
                    "params": {
                        "path": str(document),
                        "content": "${lyrics}",
                    },
                },
            ],
        },
        allowed_apps=(),
        allowed_doc_dirs=(str(tmp_path),),
    )

    assert plan["steps"][0]["result_var"] == "lyrics"


def test_desktop_plan_rejects_duplicate_result_variable() -> None:
    plan = _launch_plan()
    plan["variables"] = [{"name": "text"}]
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "copy_selected_text",
            "result_var": "text",
            "params": {},
        },
        {
            "step_id": 2,
            "action": "read_text",
            "result_var": "text",
            "params": {},
        },
    ]

    with pytest.raises(DesktopPlanError, match="result_var"):
        validate_desktop_plan(plan, allowed_apps=())


def test_desktop_plan_rejects_forward_variable_reference() -> None:
    plan = _launch_plan()
    plan["variables"] = [{"name": "lyrics"}]
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "create_document",
            "params": {"path": "lyrics.docx", "content": "${lyrics}"},
        },
        {
            "step_id": 2,
            "action": "copy_selected_text",
            "result_var": "lyrics",
            "params": {},
        },
    ]

    with pytest.raises(DesktopPlanError, match="forward"):
        validate_desktop_plan(plan, allowed_apps=())


def test_desktop_plan_rejects_foreach_without_limit() -> None:
    plan = _launch_plan()
    plan["steps"] = [
        {
            "step_id": 1,
            "executor": "windows",
            "action": "foreach",
            "params": {"items": "${items}", "steps": []},
        }
    ]

    with pytest.raises(DesktopPlanError, match="max_iterations"):
        validate_desktop_plan(plan, allowed_apps=())


def test_desktop_plan_rejects_unknown_output_variable() -> None:
    plan = _launch_plan()
    plan["variables"] = []
    plan["steps"] = [
        {
            "step_id": 1,
            "executor": "windows",
            "action": "foreach",
            "output_var": "rows",
            "params": {
                "items": [],
                "max_iterations": 2,
                "steps": [],
            },
        }
    ]

    with pytest.raises(DesktopPlanError, match="output_var"):
        validate_desktop_plan(plan, allowed_apps=())


def test_desktop_plan_rejects_file_outside_allowed_dirs(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    plan = _launch_plan()
    plan["steps"] = [
        {
            "step_id": 1,
            "action": "create_document",
            "params": {
                "path": str(outside / "lyrics.docx"),
                "content": "hello",
            },
        }
    ]

    with pytest.raises(DesktopPlanError, match="白名单"):
        validate_desktop_plan(
            plan,
            allowed_apps=(),
            allowed_doc_dirs=(str(allowed),),
        )


def test_desktop_planner_prompt_compiles_unverified_profile_after_confirmation(
    tmp_path: Path,
) -> None:
    planner = _planner(
        _launch_plan(path=r"D:\KGMusic\KuGou.exe")
    )
    profile = {
        "schema_version": 1,
        "app_id": "kugou",
        "display_name": "酷狗音乐",
        "executable_path": r"D:\KGMusic\KuGou.exe",
        "window": {"title": "酷狗音乐"},
        "recipes": {
            "search_song": {
                "verified": False,
                "steps": [
                    {
                        "action": "click_relative",
                        "x": 0.78,
                        "y": 0.06,
                    },
                    {
                        "action": "type_text",
                        "text": "${query}",
                        "method": "clipboard",
                    },
                    {"action": "press_hotkey", "keys": "{ENTER}"},
                ],
            },
            "open_lyrics": {
                "verified": False,
                "steps": [
                    {
                        "action": "click_relative",
                        "x": 0.93,
                        "y": 0.88,
                    }
                ],
            },
        },
    }
    context = DesktopExecutionContext(
        allowed_apps=(r"D:\KGMusic\KuGou.exe",),
        allowed_doc_dirs=(str(tmp_path),),
        online_agent_count=1,
        paired_agent_count=1,
        app_profiles=(profile,),
    )

    asyncio.run(
        planner.plan(
            {
                "executor_type": "windows",
                "app_hint": "酷狗音乐",
                "raw_request": "打开酷狗音乐并截图",
                "steps": [],
            },
            context,
        )
    )

    prompt = "\n".join(
        str(message.get("content", ""))
        for message in planner.llm.messages or []
    )
    assert "verified=false" in prompt
    assert "requires_confirmation" in prompt
    assert "copy_selected_text" in prompt
    assert "create_spreadsheet" in prompt
    assert "禁止因此返回暂不支持" in prompt
    assert "search_and_play" in prompt


def test_desktop_planner_compiles_profile_lyrics_export_fallback(
    tmp_path: Path,
) -> None:
    planner = _planner({"error": "该需求暂不支持：未知动作"})
    profile = {
        "schema_version": 1,
        "app_id": "kugou",
        "display_name": "酷狗音乐",
        "executable_path": r"D:\KGMusic\KuGou.exe",
        "window": {"title": "酷狗音乐"},
        "recipes": {
            "search_song": {
                "verified": False,
                "steps": [
                    {
                        "action": "click_relative",
                        "x": 0.78,
                        "y": 0.06,
                    },
                    {
                        "action": "type_text",
                        "text": "${query}",
                        "method": "clipboard",
                    },
                    {"action": "press_hotkey", "keys": "{ENTER}"},
                ],
            },
            "play_first_result": {
                "verified": False,
                "steps": [
                    {"action": "wait", "seconds": 0.8},
                    {"action": "press_hotkey", "keys": "{ENTER}"},
                ],
            },
            "open_lyrics": {
                "verified": False,
                "steps": [
                    {
                        "action": "click_relative",
                        "x": 0.93,
                        "y": 0.88,
                    }
                ],
            },
        },
    }
    context = DesktopExecutionContext(
        allowed_apps=(r"D:\KGMusic\KuGou.exe",),
        allowed_doc_dirs=(str(tmp_path),),
        online_agent_count=1,
        paired_agent_count=1,
        app_profiles=(profile,),
    )

    plan = asyncio.run(
        planner.plan(
            {
                "executor_type": "windows",
                "app_hint": "酷狗音乐",
                "raw_request": (
                    f"打开酷狗音乐，播放第一推动力，把歌词导出成"
                    f"Word 和 Excel，保存在 {tmp_path}"
                ),
                "steps": [],
            },
            context,
        )
    )

    actions = [step["action"] for step in plan["steps"]]
    assert plan["requires_confirmation"] is True
    assert "launch_app" in actions
    assert "click_relative" in actions
    assert "fetch_kugou_lyrics" in actions
    assert "split_lines" in actions
    assert "count_chars" in actions
    assert "create_document" in actions
    assert "create_spreadsheet" in actions
    assert actions[-2:] == ["create_document", "create_spreadsheet"]
    assert plan["steps"][-2]["params"]["path"].endswith(
        "第一推动力歌词.docx"
    )
    assert plan["steps"][-1]["params"]["path"].endswith(
        "第一推动力歌词.xlsx"
    )


def test_desktop_planner_prefers_deterministic_profile_lyrics_data_flow(
    tmp_path: Path,
) -> None:
    planner = _planner(
        {
            "executor_type": "windows",
            "variables": [{"name": "lyrics_text"}],
            "steps": [
                {
                    "step_id": 1,
                    "action": "create_document",
                    "params": {
                        "path": str(tmp_path / "broken.docx"),
                        "content": "${lyrics_text}",
                    },
                    "depends_on": [],
                }
            ],
        }
    )
    profile = {
        "schema_version": 1,
        "app_id": "kugou",
        "display_name": "酷狗音乐",
        "executable_path": r"D:\KGMusic\KuGou.exe",
        "window": {"title": "酷狗音乐"},
        "recipes": {
            "search_song": {
                "verified": False,
                "steps": [
                    {
                        "action": "type_text",
                        "text": "${query}",
                        "method": "clipboard",
                    },
                    {"action": "press_hotkey", "keys": "{ENTER}"},
                ],
            },
            "open_lyrics": {
                "verified": False,
                "steps": [
                    {
                        "action": "click_relative",
                        "x": 0.9,
                        "y": 0.8,
                    }
                ],
            },
        },
    }
    context = DesktopExecutionContext(
        allowed_apps=(r"D:\KGMusic\KuGou.exe",),
        allowed_doc_dirs=(str(tmp_path),),
        online_agent_count=1,
        paired_agent_count=1,
        app_profiles=(profile,),
    )

    plan = asyncio.run(
        planner.plan(
            {
                "executor_type": "windows",
                "app_hint": "酷狗音乐",
                "raw_request": (
                    f"打开酷狗音乐，播放第一推动力，把歌词导出成"
                    f"Word 和 Excel，保存在 {tmp_path}"
                ),
                "steps": [],
            },
            context,
        )
    )

    actions = [step["action"] for step in plan["steps"]]
    assert "fetch_kugou_lyrics" in actions
    assert "copy_selected_text" not in actions
    assert "split_lines" in actions
    assert "count_chars" in actions
    assert plan["steps"][actions.index("fetch_kugou_lyrics")][
        "result_var"
    ] == "lyrics_text"
    fetch_step = plan["steps"][actions.index("fetch_kugou_lyrics")]
    assert fetch_step["postconditions"][0]["type"] == "text_not_equals"
