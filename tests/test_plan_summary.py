from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.plan_summary import (  # noqa: E402
    confirmation_reply,
    describe_cron,
    describe_plan,
    describe_trigger,
)


def _bilibili_plan() -> dict:
    return {
        "executor_type": "web",
        "trigger": {"type": "cron", "cron": "0 18 * * *"},
        "steps": [
            {
                "step_id": 1,
                "action": "navigate",
                "target": "B站网页",
                "params": {"url": "https://www.bilibili.com"},
            },
            {
                "step_id": 2,
                "action": "extract",
                "target": "我的消息",
                "params": {
                    "fields": ["消息内容", "发送者", "点赞数"],
                    "pagination": True,
                },
            },
            {
                "step_id": 3,
                "action": "save_file",
                "target": "导出表格",
                "params": {
                    "file_path": (
                        "D:\\Administrator\\Documents\\B站+{date}.csv"
                    )
                },
            },
        ],
    }


def test_describe_cron_translates_common_expressions() -> None:
    assert describe_cron("0 18 * * *") == "每天 18:00 自动执行"
    assert describe_cron("0 9 * * 1-5") == "工作日 09:00 自动执行"
    assert describe_cron("*/15 * * * *") == "每 15 分钟自动执行"
    assert "定时触发" in describe_cron("not-a-cron")


def test_describe_trigger_handles_manual_and_cron() -> None:
    assert "手动" in describe_trigger(None)
    assert describe_trigger({"type": "cron", "cron": "0 18 * * *"}) == (
        "每天 18:00 自动执行"
    )


def test_describe_plan_explains_steps_and_outputs() -> None:
    text = describe_plan(_bilibili_plan(), "web")

    assert "流程说明" in text
    assert "网页自动化" in text
    assert "每天 18:00 自动执行" in text
    assert "D:\\Administrator\\Documents\\B站+{date}.csv" in text
    assert "https://www.bilibili.com" in text
    assert "提取数据：我的消息" in text
    assert "自动翻页" in text
    assert "3. 保存结果文件" in text


def test_describe_plan_lists_clarification_steps() -> None:
    plan = {
        "steps": [
            {
                "step_id": 2,
                "action": "click",
                "target": "消息入口",
                "selector": "needs_user_input",
                "params": {},
            }
        ]
    }

    text = describe_plan(plan, "web")

    assert "需要你确认" in text
    assert "步骤 2" in text
    assert "页面元素" in text


def test_confirmation_reply_keeps_confirmation_question() -> None:
    reply = confirmation_reply(_bilibili_plan(), "web")

    assert reply.startswith("已为您生成自动化流程，请确认是否需要修改？")
    assert "**执行步骤**" in reply


def test_confirmation_reply_handles_empty_plan() -> None:
    assert confirmation_reply(None, "web") == (
        "已为您生成自动化流程，请确认是否需要修改？"
    )


def test_describe_plan_includes_mcp_tool_and_risk() -> None:
    plan = {
        "executor_type": "hybrid",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "step_id": 1,
                "executor": "mcp",
                "action": "call_tool",
                "risk_level": "mutating",
                "requires_confirmation": True,
                "params": {
                    "server": "demo_app",
                    "tool": "demo_export",
                    "arguments": {"text": "hello"},
                },
            }
        ],
    }

    text = describe_plan(plan, "hybrid")

    assert "混合自动化" in text
    assert "demo_app.demo_export" in text
    assert "写入操作" in text
    assert "需要确认" in text


def test_describe_plan_labels_selected_text_capture() -> None:
    text = describe_plan(
        {
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "copy_selected_text",
                    "params": {"keys": "^a^c"},
                }
            ]
        },
        "windows",
    )

    assert "提取选中文本" in text
    assert "^a^c" in text
