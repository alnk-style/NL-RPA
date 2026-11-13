from __future__ import annotations

from typing import Any

DEFAULT_CONFIRMATION = "已为您生成自动化流程，请确认是否需要修改？"

_EXECUTOR_LABELS = {
    "web": "网页自动化（后台浏览器执行）",
    "windows": "Windows 桌面自动化（本机桌面代理执行）",
    "mcp": "应用 MCP 工具（受授权与审计保护）",
    "hybrid": "混合自动化（网页、桌面与应用工具组合执行）",
}

_ACTION_LABELS = {
    "navigate": "打开网页",
    "goto": "打开网页",
    "click": "点击",
    "fill": "填写",
    "press": "按键",
    "extract": "提取数据",
    "extract_text": "提取文本",
    "extract_table": "提取表格",
    "collect_links": "收集链接",
    "extract_fields": "提取字段",
    "paginate": "分页抓取",
    "wait_for_response": "等待网络响应",
    "observe_page": "观察页面",
    "assert_data": "校验抓取数据",
    "compare": "比较数据",
    "notify": "发送通知",
    "screenshot": "截图",
    "wait": "等待",
    "wait_for": "等待页面元素",
    "scroll": "滚动页面",
    "download": "下载文件",
    "save_file": "保存结果文件",
    "launch_app": "启动程序",
    "activate_window": "切换窗口",
    "open_document": "打开文档",
    "create_document": "新建文档",
    "create_spreadsheet": "新建表格",
    "split_lines": "拆分歌词行",
    "trim_lines": "清理文本行",
    "remove_blank_lines": "移除空行",
    "deduplicate": "去重",
    "filter_contains": "筛选文本",
    "map_fields": "映射字段",
    "merge_rows": "合并数据",
    "count_chars": "统计字数",
    "aggregate_sum": "汇总数值",
    "sort_by": "排序",
    "foreach": "循环处理",
    "insert_image": "插入图片",
    "save_document": "保存文档",
    "export_pdf": "导出 PDF",
    "click_element": "点击控件",
    "click_relative": "按窗口比例点击",
    "type_text": "输入文本",
    "press_hotkey": "发送快捷键",
    "copy_file_to_clipboard": "复制文件到剪贴板",
    "copy_selected_text": "提取选中文本",
    "read_text": "读取文本",
    "read_window_text": "读取窗口歌词",
    "fetch_kugou_lyrics": "获取酷狗歌词",
    "close_app": "关闭程序",
    "call_tool": "调用应用工具",
}

_MCP_RISK_LABELS = {
    "read_only": "只读操作",
    "mutating": "写入操作",
    "destructive": "破坏性操作",
}

_PATH_KEYS = (
    "file_path",
    "save_path",
    "output_path",
    "target_path",
    "download_path",
    "path",
)

_FILE_OUTPUT_ACTIONS = {
    "save_file",
    "screenshot",
    "create_document",
    "create_spreadsheet",
    "save_document",
    "export_pdf",
    "download",
}

_DOCUMENT_ACTIONS = {"open_document", "insert_image"}

_WEEKDAY_LABELS = {
    "0": "周日",
    "7": "周日",
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
}


def describe_plan(plan: Any, executor_type: str = "web") -> str:
    """把结构化流程翻译成用户可读的说明，供确认前判断是否要修改。"""

    if not isinstance(plan, dict):
        return ""

    lines: list[str] = ["**流程说明**"]
    lines.append(
        "- 执行方式："
        + _EXECUTOR_LABELS.get(
            str(executor_type or "web"),
            _EXECUTOR_LABELS["web"],
        )
    )
    lines.append(f"- 触发方式：{describe_trigger(plan.get('trigger'))}")

    output_paths = _collect_output_paths(plan)
    if output_paths:
        lines.append(f"- 结果文件：{'；'.join(output_paths)}")

    variables = _variable_names(plan)
    if variables:
        lines.append(f"- 使用的变量：{'、'.join(variables)}")

    steps = plan.get("steps")
    if isinstance(steps, list) and steps:
        lines.append("")
        lines.append("**执行步骤**")
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {_describe_step(step)}")

    clarifications = _collect_clarifications(plan)
    if clarifications:
        lines.append("")
        lines.append("**需要你确认**")
        lines.extend(f"- {item}" for item in clarifications)

    return "\n".join(lines)


def confirmation_reply(
    plan: Any,
    executor_type: str = "web",
    intro: str = DEFAULT_CONFIRMATION,
) -> str:
    summary = describe_plan(plan, executor_type)
    if not summary:
        return intro
    return f"{intro}\n\n{summary}"


def describe_trigger(trigger: Any) -> str:
    if not isinstance(trigger, dict):
        return "手动触发（保存后手动运行）"

    trigger_type = str(trigger.get("type") or "manual").strip().lower()
    if trigger_type != "cron":
        return "手动触发（保存后手动运行）"

    cron = trigger.get("cron") or trigger.get("cron_expression")
    return describe_cron(cron)


def describe_cron(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "定时触发（未提供时间表达式）"

    fields = text.split()
    if len(fields) != 5:
        return f"定时触发（{text}）"

    minute, hour, day, month, weekday = fields

    if minute.startswith("*/") and _all_star(hour, day, month, weekday):
        return f"每 {minute[2:]} 分钟自动执行"
    if hour.startswith("*/") and _all_star(minute, day, month, weekday):
        return f"每 {hour[2:]} 小时自动执行"

    time_text = ""
    if minute.isdigit() and hour.isdigit():
        time_text = f"{int(hour):02d}:{int(minute):02d}"

    if time_text and _all_star(day, month, weekday):
        return f"每天 {time_text} 自动执行"
    if time_text and weekday not in {"*", "?"}:
        return f"{_describe_weekday(weekday)} {time_text} 自动执行"
    if time_text and day.isdigit():
        return f"每月 {int(day)} 日 {time_text} 自动执行"
    return f"按 cron 表达式定时执行（{text}）"


def _all_star(*values: str) -> bool:
    return all(value == "*" for value in values)


def _describe_weekday(value: str) -> str:
    text = value.strip()
    if text == "1-5":
        return "工作日"
    if text in {"0,6", "6,0", "0,7", "6,7"}:
        return "周末"
    if "," in text:
        labels = [
            _WEEKDAY_LABELS.get(item.strip(), item.strip())
            for item in text.split(",")
            if item.strip()
        ]
        if labels:
            return "每周" + "、".join(labels)
    if text in _WEEKDAY_LABELS:
        return f"每{_WEEKDAY_LABELS[text]}"
    return f"每周（{text}）"


def _describe_step(step: Any) -> str:
    if not isinstance(step, dict):
        return str(step)

    executor = str(step.get("executor") or "").strip().lower()
    action = str(
        step.get("action") or step.get("action_type") or ""
    ).strip()
    target = str(step.get("target") or "").strip()
    params = (
        step.get("params")
        if isinstance(step.get("params"), dict)
        else {}
    )

    if executor == "mcp" or action in {"call_tool", "call", "tool"}:
        server = str(params.get("server") or "").strip()
        tool = str(params.get("tool") or "").strip()
        target_name = (
            f"{server}.{tool}" if server and tool else tool or server
        )
        pieces = ["调用应用工具"]
        if target_name:
            pieces.append(f" {target_name}")
        risk = str(
            step.get("risk_level") or params.get("risk_level") or ""
        ).strip()
        if risk in _MCP_RISK_LABELS:
            pieces.append(f"（{_MCP_RISK_LABELS[risk]}")
            if step.get("requires_confirmation") is True or risk in {
                "mutating",
                "destructive",
            }:
                pieces.append("，需要确认")
            pieces.append("）")
        return "".join(pieces)

    if action in {"navigate", "goto", "open_url"}:
        url = _first_text(
            params.get("url"),
            params.get("target_url"),
            target,
        )
        return f"打开网页 {url}" if url else "打开网页"

    label = _ACTION_LABELS.get(action, action or "执行操作")

    if action in {"extract", "read_text"}:
        parts = [label]
        if target:
            parts.append(f"：{target}")
        fields = params.get("fields")
        if isinstance(fields, list) and fields:
            parts.append(
                "（字段：" + "、".join(str(f) for f in fields) + "）"
            )
        if params.get("pagination") is True:
            parts.append("，自动翻页抓取")
        return "".join(parts)

    pieces = [label]
    if target:
        pieces.append(f"“{target}”")
    detail = _describe_params(action, params)
    if detail:
        pieces.append(detail)
    return " ".join(pieces)


def _describe_params(action: str, params: dict[str, Any]) -> str:
    parts: list[str] = []

    url = _first_text(params.get("url"), params.get("target_url"))
    if url:
        parts.append(f"地址 {url}")

    path = _first_text(*(params.get(key) for key in _PATH_KEYS))
    if path:
        if action in _FILE_OUTPUT_ACTIONS:
            parts.append(f"输出文件 {path}")
        elif action in _DOCUMENT_ACTIONS:
            parts.append(f"文档 {path}")
        else:
            parts.append(f"路径 {path}")

    keys = _first_text(params.get("keys"))
    if keys:
        parts.append(f"按键 {keys}")

    seconds = params.get("seconds")
    if isinstance(seconds, (int, float)) and seconds:
        parts.append(f"等待 {seconds} 秒")

    if params.get("pagination") is True:
        parts.append("自动翻页")

    return "，".join(parts)


def _collect_output_paths(plan: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return paths

    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(
            step.get("action") or step.get("action_type") or ""
        ).strip()
        if action not in _FILE_OUTPUT_ACTIONS and action != "extract":
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        for key in _PATH_KEYS:
            value = _first_text(params.get(key))
            if value and value not in paths:
                paths.append(value)
    return paths


def _variable_names(plan: dict[str, Any]) -> list[str]:
    variables = plan.get("variables")
    if not isinstance(variables, list):
        return []
    names: list[str] = []
    for entry in variables:
        if isinstance(entry, dict):
            name = _first_text(entry.get("name"))
            if name:
                names.append(name)
        elif isinstance(entry, str) and entry.strip():
            names.append(entry.strip())
    steps = plan.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("result_var", "output_var"):
                value = _first_text(step.get(key))
                if value and value not in names:
                    names.append(value)
    return names


def _collect_clarifications(plan: dict[str, Any]) -> list[str]:
    items: list[str] = []
    raw = plan.get("needs_clarification")

    if isinstance(raw, str) and raw.strip():
        items.append(raw.strip())
    elif isinstance(raw, list):
        items.extend(str(item).strip() for item in raw if str(item).strip())
    elif isinstance(raw, dict):
        items.append(str(raw))

    if items:
        return items

    steps = plan.get("steps")
    if not isinstance(steps, list):
        return items

    for step in steps:
        if not isinstance(step, dict):
            continue
        selector = str(step.get("selector") or "").strip()
        if selector != "needs_user_input":
            continue
        label = _first_text(step.get("target"), step.get("action"))
        step_id = step.get("step_id", "?")
        items.append(
            f"步骤 {step_id}（{label or '未命名步骤'}）"
            "的页面元素需要确认或补充选择器"
        )
    return items


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


__all__ = [
    "DEFAULT_CONFIRMATION",
    "confirmation_reply",
    "describe_cron",
    "describe_plan",
    "describe_trigger",
]
