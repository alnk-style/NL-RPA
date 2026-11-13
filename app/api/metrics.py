"""指标查询与阈值告警接口。

全部接口在 ``METRICS_API`` 开关之后，默认关闭。指标默认只统计
当前登录用户的数据；多用户/工作区维度的隔离在阶段 5 批次 B 中
通过 workspace 谓词加强。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.metrics.alerts import MetricsAlertService
from app.metrics.service import (
    MetricsQuery,
    build_overview,
    load_execution_records,
)
from app.models.user import User

router = APIRouter(prefix="/api/metrics", tags=["指标与告警"])

_RANGE_PRESETS: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def require_metrics_enabled() -> None:
    if not get_settings().METRICS_API:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="指标接口未启用，请先设置 METRICS_API=true。",
        )


def require_metrics_alerts_enabled() -> None:
    if not get_settings().METRICS_ALERTS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "指标告警未启用，请先设置 "
                "METRICS_ALERTS_ENABLED=true。"
            ),
        )


def _parse_datetime(value: str | None, field: str) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} 不是合法的 ISO 8601 时间",
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def resolve_window(
    range_value: str,
    start_value: str | None,
    end_value: str | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime, str]:
    """解析时间范围；返回 (start, end, preset)。"""

    resolved_end = _parse_datetime(end_value, "end") or (
        now or datetime.utcnow()
    )
    custom_start = _parse_datetime(start_value, "start")
    preset = str(range_value or "7d").strip().lower()
    if custom_start is not None:
        if custom_start > resolved_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start 不能晚于 end",
            )
        return custom_start, resolved_end, "custom"
    if preset not in _RANGE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "range 只支持 "
                + "、".join(_RANGE_PRESETS)
                + " 或显式 start/end"
            ),
        )
    delta = _RANGE_PRESETS[preset]
    if delta is None:
        return None, resolved_end, preset
    return resolved_end - delta, resolved_end, preset


def _scoped_user_id(
    requested_user_id: int | None,
    current_user: User,
) -> int:
    if requested_user_id is None or requested_user_id == current_user.id:
        return current_user.id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="无权查看其他用户的指标",
    )


def _previous_window(
    start: datetime | None,
    end: datetime,
) -> MetricsQuery | None:
    if start is None:
        return None
    span = end - start
    if span <= timedelta(0):
        return None
    return MetricsQuery(start=start - span, end=start)


@router.get(
    "/overview",
    dependencies=[Depends(require_metrics_enabled)],
)
async def metrics_overview(
    range: str = Query(default="7d"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    workflow_id: int | None = Query(default=None),
    executor: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    include_baseline: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    start_at, end_at, preset = resolve_window(range, start, end)
    scoped_user_id = _scoped_user_id(user_id, current_user)
    query = MetricsQuery(
        start=start_at,
        end=end_at,
        workflow_id=workflow_id,
        executor=executor,
        user_id=scoped_user_id,
    )
    records = await load_execution_records(db, query)

    previous: list[Any] = []
    if include_baseline:
        baseline_query = _previous_window(start_at, end_at)
        if baseline_query is not None:
            baseline_query = MetricsQuery(
                start=baseline_query.start,
                end=baseline_query.end,
                workflow_id=workflow_id,
                executor=executor,
                user_id=scoped_user_id,
            )
            previous = await load_execution_records(db, baseline_query)

    overview = build_overview(records, previous)
    return {
        "enabled": True,
        "generated_at": datetime.utcnow().isoformat(),
        "range": {
            "preset": preset,
            "start": start_at.isoformat() if start_at else None,
            "end": end_at.isoformat(),
        },
        "dimensions": {
            "workflow_id": workflow_id,
            "executor": executor,
            "user_id": scoped_user_id,
        },
        **overview,
    }


@router.get(
    "/failure-categories",
    dependencies=[Depends(require_metrics_enabled)],
)
async def failure_categories(
    range: str = Query(default="7d"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    workflow_id: int | None = Query(default=None),
    executor: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    start_at, end_at, preset = resolve_window(range, start, end)
    scoped_user_id = _scoped_user_id(user_id, current_user)
    records = await load_execution_records(
        db,
        MetricsQuery(
            start=start_at,
            end=end_at,
            workflow_id=workflow_id,
            executor=executor,
            user_id=scoped_user_id,
        ),
    )
    overview = build_overview(records)
    return {
        "enabled": True,
        "range": {
            "preset": preset,
            "start": start_at.isoformat() if start_at else None,
            "end": end_at.isoformat(),
        },
        "dimensions": {
            "workflow_id": workflow_id,
            "executor": executor,
            "user_id": scoped_user_id,
        },
        "execution_count": len(records),
        "failure_categories": overview["failure_categories"],
    }


@router.post(
    "/alerts/check",
    dependencies=[Depends(require_metrics_alerts_enabled)],
)
async def check_alerts(request: Request) -> dict[str, Any]:
    config = get_settings()
    notifier = getattr(request.app.state, "notifier", None)
    session_factory = getattr(
        request.app.state,
        "session_factory",
        None,
    )
    if session_factory is not None:
        service = MetricsAlertService(
            notifier,
            config,
            session_factory=session_factory,
        )
    else:
        service = MetricsAlertService(notifier, config)
    return await service.check_once()


__all__ = [
    "require_metrics_alerts_enabled",
    "require_metrics_enabled",
    "resolve_window",
    "router",
]
