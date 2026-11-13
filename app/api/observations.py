"""网页与桌面观察接口。

网页观察接口默认关闭，需要 OBSERVE_BEFORE_PLAN=true 才可用。
完整快照保存到日志目录，响应只返回精简摘要与快照路径。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.engine.browser_observer import (
    BrowserObserver,
    ObservationError,
)
from app.protocols import ObservationSnapshot, classify_failure
from runtime_paths import stable_logs_dir

router = APIRouter(prefix="/api/observations", tags=["观察层"])


class BrowserObservationRequest(BaseModel):
    url: str = Field(min_length=1)
    wait_until: Literal[
        "commit",
        "domcontentloaded",
        "load",
        "networkidle",
    ] = "domcontentloaded"
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    max_elements: int = Field(default=80, ge=1, le=300)
    include_accessibility: bool = True
    store_snapshot: bool = True


class BrowserObservationResponse(BaseModel):
    observation: dict[str, Any]
    snapshot_path: str | None = None


def require_observation_enabled() -> None:
    if not get_settings().OBSERVE_BEFORE_PLAN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "网页观察功能未启用，请先在设置中开启 "
                "OBSERVE_BEFORE_PLAN。"
            ),
        )


def get_browser_observer() -> BrowserObserver:
    return BrowserObserver(get_settings())


def _observation_dir() -> Path:
    if os.getenv("NL_RPA_DESKTOP_MODE") == "1":
        return stable_logs_dir() / "observations"
    return Path("logs") / "observations"


def persist_observation(
    snapshot: ObservationSnapshot,
    directory: Path | None = None,
) -> str:
    """把完整快照写入日志目录，返回文件路径。"""

    target_dir = directory or _observation_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = (
        f"observation_{timestamp}_{uuid4().hex[:8]}.json"
    )
    path = target_dir / filename
    path.write_text(
        json.dumps(
            snapshot.to_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(path)


@router.post(
    "/browser",
    response_model=BrowserObservationResponse,
    dependencies=[Depends(require_observation_enabled)],
)
async def observe_browser(
    payload: BrowserObservationRequest,
    observer: BrowserObserver = Depends(get_browser_observer),
) -> BrowserObservationResponse:
    try:
        snapshot = await observer.observe(
            payload.url,
            wait_until=payload.wait_until,
            timeout_ms=payload.timeout_ms,
            max_elements=payload.max_elements,
            include_accessibility=payload.include_accessibility,
        )
    except ObservationError as exc:
        category = classify_failure(str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": str(exc),
                "failure_category": category.value,
            },
        ) from exc

    snapshot_path = None
    if payload.store_snapshot:
        snapshot_path = persist_observation(snapshot)

    return BrowserObservationResponse(
        observation=snapshot.compact(
            max_elements=payload.max_elements
        ),
        snapshot_path=snapshot_path,
    )


__all__ = [
    "BrowserObservationRequest",
    "BrowserObservationResponse",
    "get_browser_observer",
    "observe_browser",
    "persist_observation",
    "require_observation_enabled",
    "router",
]
