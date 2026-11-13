"""远程工具结果的不可信数据包装。

MCP 等远程工具返回的内容可能包含提示注入。平台不把原始结果直接拼进
提示词，而是统一经过这里：

- 保留 ``trust="untrusted"`` 标记与来源信息；
- 去掉控制字符、限制长度；
- 结果只用于执行日志与显式声明的结构化字段。
"""

from __future__ import annotations

import re
from typing import Any

UNTRUSTED_MARKER_START = "<<<MCP_UNTRUSTED_RESULT"
UNTRUSTED_MARKER_END = "MCP_UNTRUSTED_RESULT>>>"

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_untrusted_text(
    text: Any,
    *,
    max_chars: int = 4000,
) -> str:
    value = str(text if text is not None else "")
    value = _CONTROL_CHARS.sub("", value)
    if len(value) > max_chars:
        value = value[:max_chars] + "...(truncated)"
    return value


def wrap_untrusted_result(
    payload: dict[str, Any] | None,
    *,
    server: str,
    tool: str,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """把工具结果包装成带明确来源与信任级别的结构。"""

    raw = payload if isinstance(payload, dict) else {}
    text = raw.get("text")
    if text is None:
        content = raw.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("text") is not None:
                    parts.append(str(item.get("text")))
            text = "\n".join(parts)
    sanitized = sanitize_untrusted_text(text, max_chars=max_chars)
    structured = raw.get("structuredContent")
    if structured is None:
        structured = raw.get("structured")
    if structured is None:
        structured = raw.get("data")
    return {
        "trust": "untrusted",
        "source": {"server": str(server), "tool": str(tool)},
        "marker": f"{UNTRUSTED_MARKER_START} server={server} tool={tool}",
        "text": sanitized,
        "data": structured,
        "structured": structured,
        "is_error": bool(raw.get("is_error") or raw.get("isError")),
    }


def format_untrusted_for_log(payload: dict[str, Any]) -> str:
    """把包装后的结果渲染成执行日志用的一行文本。"""

    if not isinstance(payload, dict):
        return ""
    source = payload.get("source")
    if not isinstance(source, dict):
        source = {}
    text = str(payload.get("text") or "")
    return (
        f"{UNTRUSTED_MARKER_START} "
        f"server={source.get('server')} tool={source.get('tool')}\n"
        f"{text}\n{UNTRUSTED_MARKER_END}"
    )


__all__ = [
    "UNTRUSTED_MARKER_END",
    "UNTRUSTED_MARKER_START",
    "format_untrusted_for_log",
    "sanitize_untrusted_text",
    "wrap_untrusted_result",
]
