from __future__ import annotations

from app.agents.desktop_plan import build_desktop_document_plan


def test_wps_document_plan_becomes_windows_action() -> None:
    plan = build_desktop_document_plan(
        {
            "trigger": {"type": "manual", "cron": None},
            "steps": [
                {
                    "step_id": 1,
                    "action": "navigate",
                    "target": "WPS",
                    "params": {"app": "WPS"},
                }
            ],
            "variables": [
                {
                    "name": "save_path",
                    "default": r"D:\Administrator\Documents",
                },
                {
                    "name": "document_name",
                    "default": "NL-RPA测试",
                },
                {
                    "name": "document_content",
                    "default": "测试内容",
                },
            ],
        }
    )

    assert plan is not None
    assert plan["executor_type"] == "windows"
    assert plan["steps"] == [
        {
            "step_id": 1,
            "action": "create_document",
            "params": {
                "path": (
                    r"D:\Administrator\Documents"
                    r"\NL-RPA测试.docx"
                ),
                "content": "测试内容",
                "progid": "kwps.Application",
                "overwrite": True,
            },
            "depends_on": [],
        }
    ]
