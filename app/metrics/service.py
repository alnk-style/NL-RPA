"""执行指标聚合。

对齐方案第 8 节的 7 个验收指标，全部从现有数据推导：

1. 首次生成成功率：窗口内新建流程的首个执行成功率；
2. 定位失败率：定位类 FailureCategory 的步骤数 / 步骤总数；
3. 自动修复成功率：有修复尝试的步骤中最终成功的比例；
4. 每百次执行人工介入次数；
5. 流程创建耗时（创建到首次执行）；
6. 整条流程重跑次数（失败后新建执行而非检查点续跑）；
7. 执行耗时增量（窗口均值 vs 等长前序窗口均值）。

失败分类统一来自 ``app.protocols.FailureCategory``；旧数据缺少
``failure_category`` 时用 ``classify_failure`` 从错误文本回退推断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import ExecutionLog
from app.models.workflow import Workflow
from app.metrics.steps import (
    CHECKPOINT_TYPE,
    FAILED_STEP_STATUSES,
    entry_failure_category,
    iter_steps,
    step_status,
    summarize_steps,
)
from app.protocols import FailureCategory, classify_failure

METRIC_KEYS: tuple[str, ...] = (
    "first_generation_success_rate",
    "locator_failure_rate",
    "auto_repair_success_rate",
    "manual_interventions_per_100_executions",
    "workflow_creation_duration_seconds",
    "full_rerun_count",
    "execution_duration_delta_ratio",
)

DEFAULT_DIMENSIONS: dict[str, Any] = {
    "start": None,
    "end": None,
    "workflow_id": None,
    "executor": None,
    "user_id": None,
}

# 定位类失败：选择器找不到/不唯一、元素不可见/不可用。
LOCATOR_FAILURE_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.LOCATOR_NOT_FOUND,
        FailureCategory.LOCATOR_NOT_UNIQUE,
        FailureCategory.ELEMENT_NOT_VISIBLE,
        FailureCategory.ELEMENT_DISABLED,
    }
)

_FAILED_EXECUTION_STATUSES = {"failed", "error", "cancelled"}
_INTERVENTION_STATUSES = {"awaiting_user", "paused"}


@dataclass(frozen=True)
class MetricsQuery:
    """指标查询的时间范围与维度。"""

    start: datetime | None = None
    end: datetime | None = None
    workflow_id: int | None = None
    executor: str | None = None
    user_id: int | None = None
    workspace_id: int | None = None

    def dimensions(self) -> dict[str, Any]:
        return {
            "start": _iso(self.start),
            "end": _iso(self.end),
            "workflow_id": self.workflow_id,
            "executor": self.executor,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
        }


@dataclass
class ExecutionRecord:
    """聚合所需的一次执行快照。"""

    id: int
    workflow_id: int
    workflow_name: str
    user_id: int | None
    executor: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    step_logs: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    workflow_created_at: datetime | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return max(
            (self.finished_at - self.started_at).total_seconds(),
            0.0,
        )


async def load_execution_records(
    session: AsyncSession,
    query: MetricsQuery,
) -> list[ExecutionRecord]:
    """按时间范围与维度加载执行记录（含流程元数据）。"""

    statement = (
        select(ExecutionLog, Workflow)
        .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
        .order_by(ExecutionLog.started_at, ExecutionLog.id)
    )
    if query.start is not None:
        statement = statement.where(
            ExecutionLog.started_at >= query.start
        )
    if query.end is not None:
        statement = statement.where(
            ExecutionLog.started_at <= query.end
        )
    if query.workflow_id is not None:
        statement = statement.where(
            ExecutionLog.workflow_id == query.workflow_id
        )
    if query.executor:
        statement = statement.where(
            Workflow.executor_type == str(query.executor)
        )
    if query.user_id is not None:
        statement = statement.where(Workflow.user_id == query.user_id)
    workspace_ids = getattr(Workflow, "workspace_id", None)
    if query.workspace_id is not None and workspace_ids is not None:
        statement = statement.where(
            Workflow.workspace_id == query.workspace_id
        )

    rows = (await session.execute(statement)).all()
    records: list[ExecutionRecord] = []
    for execution_log, workflow in rows:
        records.append(
            ExecutionRecord(
                id=int(execution_log.id),
                workflow_id=int(workflow.id),
                workflow_name=str(workflow.name or ""),
                user_id=(
                    int(workflow.user_id)
                    if workflow.user_id is not None
                    else None
                ),
                executor=str(workflow.executor_type or "web"),
                status=str(execution_log.status or ""),
                started_at=execution_log.started_at,
                finished_at=execution_log.finished_at,
                error_message=execution_log.error_message,
                step_logs=_as_step_logs(execution_log.step_logs),
                metrics=_as_dict(execution_log.metrics),
                workflow_created_at=workflow.created_at,
            )
        )
    return records


def build_overview(
    records: Iterable[ExecutionRecord],
    previous_records: Iterable[ExecutionRecord] | None = None,
) -> dict[str, Any]:
    """把执行记录聚合为 7 个指标 + 失败分类明细。"""

    current = list(records)
    previous = list(previous_records or [])
    return {
        "execution_count": len(current),
        "metrics": {
            "first_generation_success_rate": _first_generation_success(
                current
            ),
            "locator_failure_rate": _locator_failure_rate(current),
            "auto_repair_success_rate": _auto_repair_success(current),
            "manual_interventions_per_100_executions": (
                _manual_interventions_per_100(current)
            ),
            "workflow_creation_duration_seconds": (
                _workflow_creation_duration(current)
            ),
            "full_rerun_count": _full_rerun_count(current),
            "execution_duration_delta_ratio": _duration_delta(
                current,
                previous,
            ),
        },
        "failure_categories": failure_category_breakdown(current),
    }


def failure_category_breakdown(
    records: Iterable[ExecutionRecord],
) -> dict[str, dict[str, Any]]:
    """按 ``FailureCategory`` 聚合失败次数与占比。"""

    counts = {category.value: 0 for category in FailureCategory}
    for record in records:
        counted_step_failure = False
        for entry in iter_steps(record.step_logs):
            if step_status(entry) not in FAILED_STEP_STATUSES:
                continue
            category = entry_failure_category(entry)
            counts[category.value] += 1
            counted_step_failure = True
        if (
            not counted_step_failure
            and record.status in _FAILED_EXECUTION_STATUSES
        ):
            category = _record_category(record)
            counts[category.value] += 1

    total = sum(counts.values())
    return {
        category: {
            "count": count,
            "rate": (count / total) if total else 0.0,
            "share_percent": round((count / total) * 100, 2)
            if total
            else 0.0,
        }
        for category, count in counts.items()
    }


def collect_step_stats(records: Iterable[ExecutionRecord]) -> dict[str, int]:
    """步骤级统计，供修复成功率与定位失败率复用。"""

    totals = {
        "step_count": 0,
        "failed_steps": 0,
        "locator_failures": 0,
        "repairs_attempted": 0,
        "repairs_succeeded": 0,
    }
    for record in records:
        summary = summarize_steps(record.step_logs)
        for key in totals:
            totals[key] += int(summary.get(key) or 0)
    return totals


def _first_generation_success(records: list[ExecutionRecord]) -> dict[str, Any]:
    by_workflow: dict[int, ExecutionRecord] = {}
    for record in records:
        existing = by_workflow.get(record.workflow_id)
        if existing is None or _sort_key(record) < _sort_key(existing):
            by_workflow[record.workflow_id] = record

    total = len(by_workflow)
    success = sum(
        1
        for record in by_workflow.values()
        if record.status in {"success", "completed"}
    )
    return _ratio_metric(success, total)


def _locator_failure_rate(records: list[ExecutionRecord]) -> dict[str, Any]:
    stats = collect_step_stats(records)
    metric = _ratio_metric(
        stats["locator_failures"],
        stats["step_count"],
    )
    metric["locator_failures"] = stats["locator_failures"]
    metric["step_count"] = stats["step_count"]
    return metric


def _auto_repair_success(records: list[ExecutionRecord]) -> dict[str, Any]:
    stats = collect_step_stats(records)
    metric = _ratio_metric(
        stats["repairs_succeeded"],
        stats["repairs_attempted"],
    )
    metric["repairs_succeeded"] = stats["repairs_succeeded"]
    metric["repairs_attempted"] = stats["repairs_attempted"]
    return metric


def _manual_interventions_per_100(
    records: list[ExecutionRecord],
) -> dict[str, Any]:
    interventions = 0
    for record in records:
        interventions += _manual_interventions(record)
    total = len(records)
    value = (interventions / total) * 100 if total else 0.0
    return {
        "value": round(value, 3),
        "unit": "per_100_executions",
        "interventions": interventions,
        "execution_count": total,
    }


def _workflow_creation_duration(
    records: list[ExecutionRecord],
) -> dict[str, Any]:
    first_by_workflow: dict[int, ExecutionRecord] = {}
    for record in records:
        existing = first_by_workflow.get(record.workflow_id)
        if existing is None or _sort_key(record) < _sort_key(existing):
            first_by_workflow[record.workflow_id] = record

    durations: list[float] = []
    for record in first_by_workflow.values():
        if (
            record.workflow_created_at is None
            or record.started_at is None
        ):
            continue
        delta = (
            record.started_at - record.workflow_created_at
        ).total_seconds()
        if delta >= 0:
            durations.append(delta)

    average = sum(durations) / len(durations) if durations else None
    return {
        "value": round(average, 3) if average is not None else None,
        "unit": "seconds",
        "samples": len(durations),
    }


def _full_rerun_count(records: list[ExecutionRecord]) -> dict[str, Any]:
    last_failed: set[int] = set()
    reruns = 0
    for record in sorted(records, key=_sort_key):
        if (
            record.workflow_id in last_failed
            and not bool(record.metrics.get("resumed"))
        ):
            reruns += 1
        last_failed.discard(record.workflow_id)
        if record.status in {
            *_FAILED_EXECUTION_STATUSES,
            "paused",
            "awaiting_user",
        }:
            last_failed.add(record.workflow_id)
    return {
        "value": reruns,
        "unit": "executions",
        "execution_count": len(records),
    }


def _duration_delta(
    current: list[ExecutionRecord],
    previous: list[ExecutionRecord],
) -> dict[str, Any]:
    current_durations = [
        duration
        for duration in (record.duration_seconds for record in current)
        if duration is not None
    ]
    previous_durations = [
        duration
        for duration in (record.duration_seconds for record in previous)
        if duration is not None
    ]
    current_avg = (
        sum(current_durations) / len(current_durations)
        if current_durations
        else None
    )
    previous_avg = (
        sum(previous_durations) / len(previous_durations)
        if previous_durations
        else None
    )
    ratio: float | None = None
    if (
        current_avg is not None
        and previous_avg is not None
        and previous_avg > 0
    ):
        ratio = current_avg / previous_avg
    return {
        "value": round(ratio, 4) if ratio is not None else None,
        "unit": "ratio",
        "current_avg_seconds": (
            round(current_avg, 3) if current_avg is not None else None
        ),
        "baseline_avg_seconds": (
            round(previous_avg, 3) if previous_avg is not None else None
        ),
        "delta_percent": (
            round((ratio - 1) * 100, 2) if ratio is not None else None
        ),
        "current_samples": len(current_durations),
        "baseline_samples": len(previous_durations),
    }


def _manual_interventions(record: ExecutionRecord) -> int:
    raw = record.metrics.get("manual_interventions")
    if isinstance(raw, (int, float)) and raw >= 0:
        return int(raw)
    if record.status in _INTERVENTION_STATUSES:
        return 1
    if any(
        entry.get("type") == CHECKPOINT_TYPE
        for entry in record.step_logs
        if isinstance(entry, dict)
    ):
        return 1
    return 0


def _record_category(record: ExecutionRecord) -> FailureCategory:
    raw = record.metrics.get("failure_category")
    if isinstance(raw, str) and raw.strip():
        try:
            return FailureCategory(raw.strip())
        except ValueError:
            pass
    for entry in reversed(list(iter_steps(record.step_logs))):
        if step_status(entry) in FAILED_STEP_STATUSES:
            return entry_failure_category(entry)
    return classify_failure(record.error_message)


def _sort_key(record: ExecutionRecord) -> tuple[datetime, int]:
    return (
        record.started_at or datetime.min,
        record.id,
    )


def _ratio_metric(
    numerator: int,
    denominator: int,
    **extra: Any,
) -> dict[str, Any]:
    value = (numerator / denominator) if denominator else 0.0
    return {
        "value": round(value, 4),
        "unit": "ratio",
        "numerator": numerator,
        "denominator": denominator,
        **extra,
    }


def _as_step_logs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "DEFAULT_DIMENSIONS",
    "LOCATOR_FAILURE_CATEGORIES",
    "METRIC_KEYS",
    "ExecutionRecord",
    "MetricsQuery",
    "build_overview",
    "collect_step_stats",
    "failure_category_breakdown",
    "load_execution_records",
]
