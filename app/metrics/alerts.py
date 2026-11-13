"""指标阈值告警。

复用 :class:`app.notifications.NotificationService` 的 SMTP / Webhook
通道；告警规则可以在 ``METRICS_ALERT_THRESHOLDS`` 里用 JSON 覆盖，
默认阈值只作为兜底（方案第 8 节的目标是相对基线，绝对阈值需要按
部署环境调整）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AsyncSessionLocal
from app.metrics.service import (
    MetricsQuery,
    build_overview,
    load_execution_records,
)

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "first_generation_success_rate": {
        "comparator": "lt",
        "threshold": 0.6,
        "severity": "warning",
    },
    "locator_failure_rate": {
        "comparator": "gt",
        "threshold": 0.15,
        "severity": "warning",
    },
    "auto_repair_success_rate": {
        "comparator": "lt",
        "threshold": 0.6,
        "severity": "warning",
    },
    "manual_interventions_per_100_executions": {
        "comparator": "gt",
        "threshold": 20.0,
        "severity": "warning",
    },
    "workflow_creation_duration_seconds": {
        "comparator": "gt",
        "threshold": 300.0,
        "severity": "info",
    },
    "full_rerun_count": {
        "comparator": "gt",
        "threshold": 10,
        "severity": "warning",
    },
    "execution_duration_delta_ratio": {
        "comparator": "gt",
        "threshold": 1.2,
        "severity": "warning",
    },
}

ALERT_TITLES = {
    "first_generation_success_rate": "首次生成成功率低于阈值",
    "locator_failure_rate": "定位失败率高于阈值",
    "auto_repair_success_rate": "自动修复成功率低于阈值",
    "manual_interventions_per_100_executions": "人工介入次数高于阈值",
    "workflow_creation_duration_seconds": "流程创建耗时高于阈值",
    "full_rerun_count": "整条流程重跑次数高于阈值",
    "execution_duration_delta_ratio": "执行耗时增量高于阈值",
}


@dataclass(frozen=True)
class Alert:
    metric: str
    comparator: str
    threshold: float
    observed: float
    severity: str
    message: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "observed": self.observed,
            "severity": self.severity,
            "message": self.message,
        }


def resolve_thresholds(config: Any) -> dict[str, dict[str, Any]]:
    """合并默认阈值与 ``METRICS_ALERT_THRESHOLDS`` JSON 覆盖。"""

    thresholds = {
        key: dict(value) for key, value in DEFAULT_THRESHOLDS.items()
    }
    raw = getattr(config, "METRICS_ALERT_THRESHOLDS", "") or ""
    if isinstance(raw, dict):
        overrides = raw
    else:
        text = str(raw).strip()
        if not text:
            return thresholds
        try:
            overrides = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "METRICS_ALERT_THRESHOLDS 不是合法 JSON，使用默认阈值"
            )
            return thresholds
    if not isinstance(overrides, dict):
        return thresholds
    for metric, override in overrides.items():
        if not isinstance(override, dict):
            continue
        merged = dict(thresholds.get(metric, {}))
        merged.update(override)
        thresholds[str(metric)] = merged
    return thresholds


def evaluate_alerts(
    metrics: dict[str, dict[str, Any]],
    thresholds: dict[str, dict[str, Any]],
) -> list[Alert]:
    """把指标值与阈值比较，返回触发的告警列表。"""

    alerts: list[Alert] = []
    for metric, rule in thresholds.items():
        if not isinstance(rule, dict):
            continue
        payload = metrics.get(metric)
        if not isinstance(payload, dict):
            continue
        observed = payload.get("value")
        if not isinstance(observed, (int, float)):
            continue
        raw_threshold = rule.get("threshold")
        if not isinstance(raw_threshold, (int, float)):
            continue
        threshold = float(raw_threshold)
        comparator = str(rule.get("comparator") or "gt").lower()
        if not _compare(float(observed), comparator, threshold):
            continue
        severity = str(rule.get("severity") or "warning")
        title = ALERT_TITLES.get(metric, metric)
        alerts.append(
            Alert(
                metric=metric,
                comparator=comparator,
                threshold=threshold,
                observed=float(observed),
                severity=severity,
                message=(
                    f"{title}：当前值 {float(observed):.4g}，"
                    f"阈值 {comparator} {threshold:g}。"
                ),
            )
        )
    return alerts


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "lt":
        return value < threshold
    if comparator == "lte":
        return value <= threshold
    if comparator == "gte":
        return value >= threshold
    return value > threshold


class MetricsAlertService:
    """指标查询 + 阈值判断 + 通知发送。"""

    def __init__(
        self,
        notifier: Any | None,
        config: Any,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self.notifier = notifier
        self.config = config
        self.session_factory = session_factory
        self._last_fired: dict[str, float] = {}

    def cooldown_seconds(self) -> float:
        raw = getattr(self.config, "METRICS_ALERT_COOLDOWN_SECONDS", 3600)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 3600.0

    def alert_email(self) -> str | None:
        value = getattr(self.config, "METRICS_ALERT_EMAIL", None)
        text = str(value).strip() if value else ""
        return text or None

    def alert_webhook(self) -> str | None:
        value = getattr(self.config, "METRICS_ALERT_WEBHOOK", None)
        text = str(value).strip() if value else ""
        return text or None

    async def check_once(
        self,
        *,
        now: datetime | None = None,
        window_seconds: float | None = None,
    ) -> dict[str, Any]:
        """计算最近一个窗口的指标并发送触发告警。"""

        resolved_now = now or datetime.utcnow()
        window = self._window_seconds(window_seconds)
        start = resolved_now - timedelta(seconds=window)
        previous_start = start - timedelta(seconds=window)

        query = MetricsQuery(start=start, end=resolved_now)
        previous_query = MetricsQuery(
            start=previous_start,
            end=start,
        )
        async with self.session_factory() as session:
            records = await load_execution_records(session, query)
            previous = await load_execution_records(
                session,
                previous_query,
            )
        overview = build_overview(records, previous)
        thresholds = resolve_thresholds(self.config)
        alerts = evaluate_alerts(overview["metrics"], thresholds)
        fired = [
            alert for alert in alerts if self._should_fire(alert.metric)
        ]
        delivered = await self._deliver(fired, resolved_now)
        return {
            "checked_at": resolved_now.isoformat(),
            "window_seconds": window,
            "execution_count": overview["execution_count"],
            "fired": [alert.to_payload() for alert in fired],
            "delivered": delivered,
        }

    def _window_seconds(self, window_seconds: float | None) -> float:
        if window_seconds is not None:
            return max(60.0, float(window_seconds))
        raw = getattr(
            self.config,
            "METRICS_ALERT_WINDOW_SECONDS",
            3600,
        )
        try:
            return max(60.0, float(raw))
        except (TypeError, ValueError):
            return 3600.0

    def _should_fire(self, metric: str) -> bool:
        cooldown = self.cooldown_seconds()
        last = self._last_fired.get(metric)
        now = time.monotonic()
        if last is not None and now - last < cooldown:
            return False
        self._last_fired[metric] = now
        return True

    async def _deliver(
        self,
        alerts: list[Alert],
        now: datetime,
    ) -> dict[str, Any]:
        if not alerts or self.notifier is None:
            return {"email": False, "webhook": False, "reason": None}

        send_alert = getattr(self.notifier, "send_alert", None)
        if not callable(send_alert):
            logger.debug("通知服务不支持 send_alert，跳过指标告警")
            return {
                "email": False,
                "webhook": False,
                "reason": "notifier_has_no_send_alert",
            }

        lines = [alert.message for alert in alerts]
        subject = (
            f"[NL-RPA] 指标告警 {len(alerts)} 项 "
            f"({now.strftime('%Y-%m-%d %H:%M')})"
        )
        payload = {
            "type": "metrics_alert",
            "fired_at": now.isoformat(),
            "alerts": [alert.to_payload() for alert in alerts],
        }
        result = send_alert(
            subject,
            "\n".join(lines),
            email=self.alert_email(),
            webhook_url=self.alert_webhook(),
            payload=payload,
        )
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, dict):
            return result
        return {"email": False, "webhook": False, "reason": "unknown"}


__all__ = [
    "ALERT_TITLES",
    "DEFAULT_THRESHOLDS",
    "Alert",
    "MetricsAlertService",
    "evaluate_alerts",
    "resolve_thresholds",
]
