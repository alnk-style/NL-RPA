"""后台指标告警轮询。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AsyncSessionLocal
from app.metrics.alerts import MetricsAlertService

logger = logging.getLogger(__name__)


class MetricsAlertMonitor:
    """按固定间隔运行阈值检查，直到应用关闭。"""

    def __init__(
        self,
        config: Any,
        notifier: Any | None,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        interval_seconds: float | None = None,
    ) -> None:
        self.config = config
        self.interval_seconds = interval_seconds
        self.service = MetricsAlertService(
            notifier,
            config,
            session_factory=session_factory,
        )
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def _interval(self) -> float:
        if self.interval_seconds is not None:
            return max(15.0, float(self.interval_seconds))
        raw = getattr(self.config, "METRICS_ALERT_INTERVAL_SECONDS", 300)
        try:
            return max(15.0, float(raw))
        except (TypeError, ValueError):
            return 300.0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="nl-rpa-metrics-alerts",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    async def check_now(self) -> dict[str, Any]:
        return await self.service.check_once()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.service.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("指标告警检查失败")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._interval(),
                )
            except asyncio.TimeoutError:
                continue


__all__ = ["MetricsAlertMonitor"]
