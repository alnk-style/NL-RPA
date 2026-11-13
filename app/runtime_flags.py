"""运行时可覆盖的功能开关。

``.env`` 里的开关作为默认值；应用内切换写入 ``runtime_feature_flag``
表并立即生效，服务重启后自动恢复。应用内设置优先于环境变量。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings

MCP_CLIENT_FLAG = "mcp_client"
STRUCTURED_EXECUTION_FLAG = "structured_execution"
LEGACY_CODE_EXECUTION_FLAG = "legacy_code_execution"

FLAG_ENV_FIELDS: dict[str, str] = {
    MCP_CLIENT_FLAG: "MCP_CLIENT",
    STRUCTURED_EXECUTION_FLAG: "STRUCTURED_EXECUTION_ENABLED",
    LEGACY_CODE_EXECUTION_FLAG: "LEGACY_CODE_EXECUTION_ENABLED",
}

FLAG_DEFAULTS: dict[str, bool] = {
    MCP_CLIENT_FLAG: False,
    STRUCTURED_EXECUTION_FLAG: True,
    LEGACY_CODE_EXECUTION_FLAG: True,
}

_overrides: dict[str, bool] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def env_default(flag: str, config: Any | None = None) -> bool:
    field = FLAG_ENV_FIELDS.get(flag)
    if field is None:
        raise KeyError(f"未知功能开关：{flag}")
    if config is None:
        try:
            config = get_settings()
        except Exception:  # noqa: BLE001 - 配置缺失时按关闭处理
            return FLAG_DEFAULTS[flag]
    return bool(getattr(config, field, FLAG_DEFAULTS[flag]))


def resolve(flag: str, config: Any | None = None) -> bool:
    """应用内设置优先；未显式设置时回退环境变量默认值。"""

    override = _overrides.get(flag)
    if override is not None:
        return override
    return env_default(flag, config)


def source(flag: str) -> Literal["runtime", "environment"]:
    return "runtime" if flag in _overrides else "environment"


def structured_execution_enabled(
    config: Any | None = None,
) -> bool:
    """Return the canonical structured-execution switch.

    The former ``WEB_STRUCTURED_ACTIONS`` setting is intentionally ignored.
    It remains parseable so existing ``.env`` files continue to load.
    """

    return resolve(STRUCTURED_EXECUTION_FLAG, config)


def legacy_code_execution_enabled(
    config: Any | None = None,
) -> bool:
    return resolve(LEGACY_CODE_EXECUTION_FLAG, config)


async def load_overrides(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, bool]:
    """启动时从数据库恢复应用内开关。"""

    from app.models.runtime_flag import RuntimeFeatureFlag

    async with session_factory() as session:
        rows = (
            await session.execute(select(RuntimeFeatureFlag))
        ).scalars().all()

    restored = {
        row.key: bool(row.enabled)
        for row in rows
        if row.key in FLAG_ENV_FIELDS
    }
    _overrides.clear()
    _overrides.update(restored)
    return dict(restored)


async def set_override(
    session: AsyncSession,
    flag: str,
    enabled: bool,
    *,
    updated_by: str | None = None,
) -> None:
    """写入应用内开关并立即更新进程内缓存。"""

    from app.models.runtime_flag import RuntimeFeatureFlag

    if flag not in FLAG_ENV_FIELDS:
        raise KeyError(f"未知功能开关：{flag}")

    row = await session.get(RuntimeFeatureFlag, flag)
    now = _utcnow()
    if row is None:
        session.add(
            RuntimeFeatureFlag(
                key=flag,
                enabled=bool(enabled),
                updated_by=updated_by,
                updated_at=now,
            )
        )
    else:
        row.enabled = bool(enabled)
        row.updated_by = updated_by
        row.updated_at = now
    await session.commit()
    _overrides[flag] = bool(enabled)


def reset_overrides() -> None:
    """测试辅助：只清空进程内缓存，不修改数据库。"""

    _overrides.clear()


__all__ = [
    "FLAG_DEFAULTS",
    "FLAG_ENV_FIELDS",
    "MCP_CLIENT_FLAG",
    "LEGACY_CODE_EXECUTION_FLAG",
    "STRUCTURED_EXECUTION_FLAG",
    "env_default",
    "legacy_code_execution_enabled",
    "load_overrides",
    "reset_overrides",
    "resolve",
    "set_override",
    "source",
    "structured_execution_enabled",
]
