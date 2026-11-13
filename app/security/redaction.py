"""运行时秘密脱敏。

凭据只在执行上下文里以明文存在；写日志、写产物和执行结果返回前，
统一用这里的工具把秘密替换掉，避免泄漏到执行记录或通知里。
"""

from __future__ import annotations

import re
from typing import Any

# 键名命中这些片段的字段一律不落盘、不回显，只保留占位符。
SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|apikey|authorization|"
    r"auth|cookie|credential|private[_-]?key|session[_-]?id)",
    re.IGNORECASE,
)

REDACTED_PLACEHOLDER = "***"


def secret_values(credentials: Any) -> list[str]:
    """从凭据字典提取需要脱敏的明文列表，长值优先替换。"""

    if not isinstance(credentials, dict):
        return []
    values = {
        str(value)
        for value in credentials.values()
        if value is not None and str(value)
    }
    return sorted(values, key=len, reverse=True)


def redact_text(text: Any, secrets: list[str] | None) -> str:
    result = str(text if text is not None else "")
    for secret in secrets or []:
        if secret:
            result = result.replace(secret, "***")
    return result


def redact_value(value: Any, secrets: list[str] | None) -> Any:
    if not secrets:
        return value
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {
            key: redact_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, secrets) for item in value)
    return value


def redact_execution_result(result: Any, secrets: list[str] | None):
    """就地脱敏 ExecutionResult 的错误、输出、步骤日志与明细。"""

    if not secrets:
        return result
    if getattr(result, "error", None) is not None:
        result.error = redact_text(result.error, secrets)
    if getattr(result, "output", None) is not None:
        result.output = redact_text(result.output, secrets)
    if getattr(result, "step_logs", None):
        result.step_logs = redact_value(
            list(result.step_logs),
            secrets,
        )
    if getattr(result, "artifacts", None):
        result.artifacts = redact_value(
            list(result.artifacts),
            secrets,
        )
    if isinstance(getattr(result, "details", None), dict):
        result.details = redact_value(result.details, secrets)
    return result


def is_sensitive_key(key: Any) -> bool:
    return bool(SENSITIVE_KEY_PATTERN.search(str(key)))


def redact_payload(
    value: Any,
    secrets: list[str] | None = None,
    *,
    depth: int = 6,
    max_items: int = 50,
    max_string_length: int = 500,
) -> Any:
    """把任意结构化数据脱敏并裁剪成可安全落盘的摘要。

    与 ``redact_value`` 的区别：
    - 敏感键名（password/token/authorization 等）直接替换为占位符；
    - 限制嵌套深度、条目数量和字符串长度，避免审计字段无限膨胀；
    - 不修改输入对象。
    """

    if depth <= 0:
        return "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str):
        text = redact_text(value, secrets)
        if len(text) > max_string_length:
            return text[:max_string_length] + "...(truncated)"
        return text
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                redacted["..."] = f"{len(value) - max_items} more keys"
                break
            name = str(key)
            if is_sensitive_key(name):
                redacted[name] = REDACTED_PLACEHOLDER
                continue
            redacted[name] = redact_payload(
                item,
                secrets,
                depth=depth - 1,
                max_items=max_items,
                max_string_length=max_string_length,
            )
        return redacted
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        truncated = items[:max_items]
        redacted_items = [
            redact_payload(
                item,
                secrets,
                depth=depth - 1,
                max_items=max_items,
                max_string_length=max_string_length,
            )
            for item in truncated
        ]
        if len(items) > len(truncated):
            redacted_items.append(f"...({len(items) - len(truncated)} more)")
        return redacted_items
    text = redact_text(str(value), secrets)
    if len(text) > max_string_length:
        return text[:max_string_length] + "...(truncated)"
    return text


__all__ = [
    "REDACTED_PLACEHOLDER",
    "SENSITIVE_KEY_PATTERN",
    "is_sensitive_key",
    "redact_execution_result",
    "redact_payload",
    "redact_text",
    "redact_value",
    "secret_values",
]
