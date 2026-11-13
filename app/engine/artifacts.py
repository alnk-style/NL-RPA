from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.workflow import Workflow


logger = logging.getLogger(__name__)

ARTIFACT_DIR_NAME = "artifacts"
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _iter_entries(
    value: str | Path | Iterable[str | Path] | None,
) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, (str, Path)):
        values = [str(value)]
    elif isinstance(value, Iterable):
        values = [str(item) for item in value]
    else:
        values = [str(value)]

    for item in values:
        for chunk in item.replace("\r", "\n").split("\n"):
            for entry in chunk.split(";"):
                entry = entry.strip().strip('"')
                if entry:
                    yield entry


def parse_export_dirs(
    value: str | Path | Iterable[str | Path] | None,
) -> list[Path]:
    """把分号/换行分隔的目录配置解析为去重后的绝对路径列表。"""

    roots: list[Path] = []
    for entry in _iter_entries(value):
        path = Path(entry).expanduser()
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def resolve_export_roots(
    config_value: str | Path | Iterable[str | Path] | None = None,
    extra_dirs: Iterable[str | Path] | None = None,
) -> list[Path]:
    roots = parse_export_dirs(config_value)
    for root in parse_export_dirs(extra_dirs):
        if root not in roots:
            roots.append(root)
    return roots


def resolve_within_roots(
    candidate: str | Path,
    roots: Iterable[Path],
) -> Path | None:
    """目标路径位于某个允许目录内时返回该目录，否则返回 None。"""

    path = Path(candidate).expanduser()
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()

    normalized = os.path.normcase(str(resolved))
    for root in roots:
        normalized_root = os.path.normcase(str(root))
        if normalized == normalized_root:
            return root
        if normalized.startswith(normalized_root.rstrip("\\/") + os.sep):
            return root
    return None


def is_absolute_target(value: str) -> bool:
    text = str(value).strip()
    if not text:
        return False
    if _WINDOWS_ABSOLUTE.match(text) or text.startswith("\\\\"):
        return True
    return os.path.isabs(text)


def sanitize_artifact_name(value: str) -> str:
    name = Path(str(value).replace("\\", "/")).name
    sanitized = _INVALID_NAME_CHARS.sub("_", name).strip(" .")
    return sanitized or "artifact"


def deliver_artifacts(
    records: Iterable[Any] | None,
    allowed_roots: Iterable[Path] | None,
) -> list[dict[str, Any]]:
    """把执行产物复制到白名单内的目标路径，并补充投递结果字段。"""

    roots = list(allowed_roots or [])
    delivered: list[dict[str, Any]] = []

    for record in records or []:
        if not isinstance(record, dict):
            continue

        item = dict(record)
        item["delivered"] = False
        item["delivered_path"] = None
        item.setdefault("error", None)

        source_value = str(item.get("path") or "").strip()
        requested = str(item.get("requested_path") or "").strip()

        if not requested or not is_absolute_target(requested):
            if item.get("error") is None:
                item["error"] = (
                    "未指定可写入的目标目录，文件已保存在执行记录中。"
                )
            delivered.append(item)
            continue

        source = Path(source_value).expanduser() if source_value else None
        if source is None or not source.is_file():
            if item.get("error") is None:
                item["error"] = "执行产物文件不存在。"
            delivered.append(item)
            continue

        target = Path(requested).expanduser()
        if resolve_within_roots(target, roots) is None:
            if item.get("error") is None:
                item["error"] = (
                    "目标目录不在允许写入的目录白名单中，"
                    "文件已保存在执行记录中。"
                )
            delivered.append(item)
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            logger.warning(
                "Failed to deliver artifact %s to %s: %s",
                source,
                target,
                exc,
            )
            item["error"] = f"写入目标目录失败：{exc}"
        else:
            item["delivered"] = True
            item["delivered_path"] = str(target)

        delivered.append(item)

    return delivered


async def load_allowed_export_roots(
    session: AsyncSession,
    workflow: Workflow | None,
    config_value: str | Path | Iterable[str | Path] | None = None,
) -> list[Path]:
    """允许目录 = 配置项 + 该用户桌面代理已授权的文档目录。"""

    from sqlalchemy import select

    from app.models.agent import DesktopAgent
    from app.models.app_setting import AppSetting

    extra_dirs: list[str] = []
    user_id = getattr(workflow, "user_id", None)
    if user_id is not None:
        setting = (
            await session.execute(
                select(AppSetting).where(
                    AppSetting.user_id == user_id,
                    AppSetting.key == "export_directories",
                )
            )
        ).scalar_one_or_none()
        if setting is not None and isinstance(setting.value, dict):
            saved_dirs = setting.value.get("directories")
            if isinstance(saved_dirs, list):
                for entry in saved_dirs:
                    text = str(entry).strip()
                    if text:
                        extra_dirs.append(text)
        result = await session.execute(
            select(DesktopAgent).where(DesktopAgent.user_id == user_id)
        )
        for agent in result.scalars().all():
            for entry in agent.allowed_doc_dirs or []:
                text = str(entry).strip()
                if text:
                    extra_dirs.append(text)

    return resolve_export_roots(config_value, extra_dirs)


async def deliver_execution_artifacts(
    session: AsyncSession,
    workflow: Workflow | None,
    records: Iterable[Any] | None,
    config_value: str | Path | Iterable[str | Path] | None = None,
) -> list[dict[str, Any]]:
    materialized = [
        record for record in (records or []) if isinstance(record, dict)
    ]
    if not materialized:
        return []

    roots = await load_allowed_export_roots(
        session,
        workflow,
        config_value,
    )
    return await asyncio.to_thread(
        deliver_artifacts,
        materialized,
        roots,
    )


__all__ = [
    "ARTIFACT_DIR_NAME",
    "deliver_artifacts",
    "deliver_execution_artifacts",
    "is_absolute_target",
    "load_allowed_export_roots",
    "parse_export_dirs",
    "resolve_export_roots",
    "resolve_within_roots",
    "sanitize_artifact_name",
]
