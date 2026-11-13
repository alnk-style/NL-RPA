"""规划器可见的桌面动作目录。

目录只收录桌面 Agent 真正注册并有处理函数的动作，避免出现“规划通过但
执行时报未知动作”的漂移。
"""

from __future__ import annotations

from typing import Any, Iterable

DESKTOP_ACTIONS: dict[str, dict[str, Any]] = {
    "launch_app": {
        "summary": "启动白名单内的程序，并可选等待主窗口出现",
        "params": {
            "path": "白名单条目原文，例如 notepad.exe 或完整 .exe 路径",
            "args": "可选，字符串数组，作为启动参数",
            "wait_for_window": "可选，布尔，默认 true",
            "window_timeout": "可选，等待主窗口的秒数，默认 10",
        },
        "capabilities": (),
    },
    "activate_window": {
        "summary": "按标题、类名或进程号激活窗口",
        "params": {
            "title": "窗口标题，支持部分匹配",
            "class_name": "可选，窗口类名",
            "pid": "可选，进程号，或 last 表示最近启动的进程",
            "index": "可选，同标题窗口的序号，默认 0",
        },
        "capabilities": ("window",),
    },
    "click_element": {
        "summary": "点击 UI Automation 控件，或按屏幕坐标点击",
        "params": {
            "automation_id": "可选，控件的 AutomationId",
            "name": "可选，控件名称",
            "control_type": "可选，控件类型，例如 Button、Edit",
            "x": "可选，屏幕横坐标，缺少控件信息时使用",
            "y": "可选，屏幕纵坐标",
            "button": "可选，left 或 right，默认 left",
            "double_click": "可选，布尔，默认 false",
        },
        "capabilities": ("uia",),
    },
    "click_relative": {
        "summary": "按当前窗口比例坐标点击，供已验证应用配方使用",
        "params": {
            "x": "横坐标比例，范围 0..1",
            "y": "纵坐标比例，范围 0..1",
            "button": "可选，left 或 right",
            "double_click": "可选，布尔",
        },
        "capabilities": ("window",),
    },
    "type_text": {
        "summary": "向当前焦点输入文本",
        "params": {
            "text": "要输入的文本",
            "clear_first": "可选，布尔，先全选并清空",
            "method": "可选，send_keys 或 clipboard，中文建议 clipboard",
        },
        "capabilities": (),
    },
    "press_hotkey": {
        "summary": "发送快捷键",
        "params": {
            "keys": "pywinauto 快捷键语法，例如 ^s 表示 Ctrl+S",
        },
        "capabilities": (),
    },
    "copy_file_to_clipboard": {
        "summary": "把白名单目录内的文件放入剪贴板，供 Ctrl+V 粘贴",
        "params": {
            "path": "要复制的文件完整路径，必须位于“可写入目录”内",
        },
        "capabilities": (),
    },
    "copy_selected_text": {
        "summary": "复制当前选区并从剪贴板提取文本",
        "params": {
            "keys": "复制快捷键，默认 ^a^c",
            "min_chars": "可选，最少非空白字符数，默认 1",
            "settle_seconds": "可选，复制后等待秒数，默认 0.4",
            "timeout_seconds": "可选，读取剪贴板超时秒数，默认 3",
        },
        "capabilities": (),
    },
    "read_text": {
        "summary": "读取 UI Automation 控件的文本",
        "params": {
            "automation_id": "可选，控件的 AutomationId",
            "name": "可选，控件名称",
            "control_type": "可选，控件类型",
        },
        "capabilities": ("uia",),
    },
    "read_window_text": {
        "summary": "读取指定窗口内可见的 UIA 文本，适合歌词等只读窗口",
        "params": {
            "title": "可选，窗口标题片段",
            "class_name": "可选，窗口类名",
            "pid": "可选，进程号",
            "handle": "可选，窗口句柄",
            "min_chars": "可选，最少字符数，默认 2",
        },
        "capabilities": ("uia",),
    },
    "fetch_kugou_lyrics": {
        "summary": "从酷狗固定公开接口按歌名读取 LRC 歌词",
        "params": {
            "query": "歌曲名称",
            "timeout_seconds": "可选，请求超时秒数，默认 15",
            "min_chars": "可选，最少字符数，默认 2",
        },
        "capabilities": (),
    },
    "wait": {
        "summary": "等待固定时间，或等待窗口、控件出现",
        "params": {
            "seconds": "等待秒数，默认 1",
            "condition": (
                "可选，window_appears、window_disappears 或 element_appears"
            ),
            "title": "condition 为窗口条件时必填",
            "automation_id": "可选，element_appears 的控件 AutomationId",
            "name": "可选，element_appears 的控件名称",
            "control_type": "可选，element_appears 的控件类型",
        },
        "capabilities": (),
    },
    "screenshot": {
        "summary": "截取全屏、指定区域或指定窗口，并保存为 PNG",
        "params": {
            "path": (
                "可选，保存位置：完整文件路径（例如 "
                "D:\\Pictures\\2026-09-24.png）或已存在的目录。"
                "必须位于“可写入目录”内，目录不存在时该步骤直接失败"
            ),
            "name": "可选，文件名或文件名前缀；path 未提供或指向目录时使用",
            "window": (
                "可选，只截取某个窗口：{title, class_name, pid, index}，"
                "字符串 last 表示最近激活的窗口"
            ),
            "region": "可选，{x, y, width, height}",
            "overwrite": "可选，布尔，默认 true；false 且文件已存在时失败",
        },
        "capabilities": ("screenshot",),
    },
    "create_document": {
        "summary": "通过 WPS/Office COM 新建并保存文档",
        "params": {
            "path": "保存路径，需位于允许的目录内",
            "content": "文档正文",
            "progid": "COM ProgID，WPS 为 kwps.Application",
            "overwrite": "可选，布尔，默认 true",
        },
        "capabilities": ("wps",),
    },
    "create_spreadsheet": {
        "summary": "通过 WPS/Office COM 新建 Excel 工作簿",
        "params": {
            "path": "保存路径，必须在允许目录内且以 .xlsx 或 .xls 结尾",
            "sheet_name": "非空工作表名称",
            "columns": (
                "列定义数组；每列包含 header，以及 values 或 "
                "可含 {row} 的 formula"
            ),
            "column_widths": "可选，按列顺序设置列宽",
            "progid": "可选，默认 ket.Application，可显式指定 Excel.Application",
            "overwrite": "可选，布尔，默认 true",
        },
        "capabilities": ("wps",),
    },
    "insert_image": {
        "summary": "把一张图片插入到文档中并保存（WPS/Office COM）",
        "params": {
            "path": "文档路径，需位于允许的目录内；文档不存在时会新建",
            "image_path": (
                "要插入的图片文件路径，需位于允许的目录内且必须已存在"
            ),
            "progid": "可选，COM ProgID，WPS 为 kwps.Application",
            "position": "可选，end（文档末尾，默认）或 cursor（当前光标处）",
            "save": "可选，布尔，默认 true，插入后立即保存文档",
        },
        "capabilities": ("wps",),
    },
    "close_app": {
        "summary": "关闭先前启动的程序",
        "params": {
            "target": "last，或已启动程序的路径、名称、进程号",
            "force": "可选，布尔",
            "graceful_timeout": "可选，优雅退出的等待秒数，默认 5",
        },
        "capabilities": (),
    },
}

ACTION_TYPES = frozenset(DESKTOP_ACTIONS)


def action_capabilities(action_type: str) -> tuple[str, ...]:
    spec = DESKTOP_ACTIONS.get(str(action_type))
    if not spec:
        return ()
    capabilities = spec.get("capabilities")
    if not isinstance(capabilities, (list, tuple)):
        return ()
    return tuple(str(item) for item in capabilities)


def capabilities_for_actions(actions: Iterable[str]) -> list[str]:
    required: set[str] = set()
    for action in actions:
        required.update(action_capabilities(action))
    return sorted(required)


def desktop_action_catalog_text() -> str:
    lines: list[str] = []
    for action, spec in DESKTOP_ACTIONS.items():
        lines.append(f"- {action}：{spec.get('summary', '')}")
        params = spec.get("params")
        if isinstance(params, dict):
            for name, description in params.items():
                lines.append(f"  - {name}：{description}")
    return "\n".join(lines)


__all__ = [
    "ACTION_TYPES",
    "DESKTOP_ACTIONS",
    "action_capabilities",
    "capabilities_for_actions",
    "desktop_action_catalog_text",
]

