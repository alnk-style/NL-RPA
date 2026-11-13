"""工具风险分级与权限门。

风险级别：
- ``read_only``：默认允许；
- ``mutating``：需要显式授权（工具清单里的 authorized=true）；
- ``destructive``：需要授权，并且必须确认（调用参数 confirm=true 或
  授权记录里 confirmed=true）。

权限判断完全在模型之外执行，结果用于执行前拦截与审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RISK_READ_ONLY = "read_only"
RISK_MUTATING = "mutating"
RISK_DESTRUCTIVE = "destructive"

RISK_LEVELS: tuple[str, ...] = (
    RISK_READ_ONLY,
    RISK_MUTATING,
    RISK_DESTRUCTIVE,
)

DEFAULT_RISK_LEVEL = RISK_MUTATING


def normalize_risk_level(
    value: Any,
    default: str = DEFAULT_RISK_LEVEL,
) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "readonly": RISK_READ_ONLY,
        "read": RISK_READ_ONLY,
        "safe": RISK_READ_ONLY,
        "write": RISK_MUTATING,
        "mutation": RISK_MUTATING,
        "mutate": RISK_MUTATING,
        "danger": RISK_DESTRUCTIVE,
        "dangerous": RISK_DESTRUCTIVE,
        "destruction": RISK_DESTRUCTIVE,
    }
    text = aliases.get(text, text)
    if text in RISK_LEVELS:
        return text
    fallback = str(default or DEFAULT_RISK_LEVEL).strip().lower()
    return fallback if fallback in RISK_LEVELS else DEFAULT_RISK_LEVEL


def infer_risk_level(
    tool: dict[str, Any] | None,
    default: str = DEFAULT_RISK_LEVEL,
) -> str:
    """从工具清单与 MCP annotations 推断风险级别。"""

    if not isinstance(tool, dict):
        return normalize_risk_level(default, default)

    explicit = tool.get("risk_level") or tool.get("risk")
    if explicit:
        return normalize_risk_level(explicit, default)

    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    if annotations.get("destructiveHint") is True:
        return RISK_DESTRUCTIVE
    if annotations.get("readOnlyHint") is True:
        return RISK_READ_ONLY
    return normalize_risk_level(default, default)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    risk_level: str
    reason: str | None = None
    authorized_by: str | None = None
    requires_confirmation: bool = False

    @property
    def decision(self) -> str:
        return "allowed" if self.allowed else "denied"

    def to_payload(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "authorized_by": self.authorized_by,
            "requires_confirmation": self.requires_confirmation,
        }


def evaluate_permission(
    risk_level: Any,
    authorization: dict[str, Any] | None = None,
    *,
    confirmed: bool = False,
    default: str = DEFAULT_RISK_LEVEL,
) -> PermissionDecision:
    """按风险级别判断一次工具调用能否执行。"""

    level = normalize_risk_level(risk_level, default)
    record = authorization if isinstance(authorization, dict) else {}
    authorized = bool(record.get("authorized"))
    pre_confirmed = bool(record.get("confirmed"))
    call_confirmed = bool(confirmed) or pre_confirmed

    if level == RISK_READ_ONLY:
        return PermissionDecision(
            allowed=True,
            risk_level=level,
            authorized_by="default",
        )

    if not authorized:
        return PermissionDecision(
            allowed=False,
            risk_level=level,
            reason=(
                f"工具风险级别为 {level}，需要显式授权后才能执行。"
            ),
            authorized_by=None,
            requires_confirmation=level == RISK_DESTRUCTIVE,
        )

    if level == RISK_DESTRUCTIVE and not call_confirmed:
        return PermissionDecision(
            allowed=False,
            risk_level=level,
            reason=(
                "破坏性工具需要确认：请在步骤参数里设置 confirm=true，"
                "或在授权时标记 confirmed=true。"
            ),
            authorized_by="explicit",
            requires_confirmation=True,
        )

    return PermissionDecision(
        allowed=True,
        risk_level=level,
        authorized_by=(
            "confirmation"
            if level == RISK_DESTRUCTIVE
            else "explicit"
        ),
    )


__all__ = [
    "DEFAULT_RISK_LEVEL",
    "RISK_DESTRUCTIVE",
    "RISK_LEVELS",
    "RISK_MUTATING",
    "RISK_READ_ONLY",
    "PermissionDecision",
    "evaluate_permission",
    "infer_risk_level",
    "normalize_risk_level",
]
