"""阶段 5 指标聚合与失败分类的单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics.service import (
    ExecutionRecord,
    MetricsQuery,
    build_overview,
    collect_step_stats,
    failure_category_breakdown,
    load_execution_records,
)
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow

BASE = datetime(2026, 10, 1, 12, 0, 0)


def _record(
    execution_id: int,
    workflow_id: int,
    *,
    status: str = "success",
    started_offset: float = 0,
    duration: float = 10.0,
    step_logs: list[dict] | None = None,
    metrics: dict | None = None,
    workflow_created_offset: float = -60,
) -> ExecutionRecord:
    started_at = BASE + timedelta(seconds=started_offset)
    return ExecutionRecord(
        id=execution_id,
        workflow_id=workflow_id,
        workflow_name=f"workflow-{workflow_id}",
        user_id=1,
        executor="web",
        status=status,
        started_at=started_at,
        finished_at=(
            started_at + timedelta(seconds=duration)
            if duration is not None
            else None
        ),
        error_message=None,
        step_logs=step_logs or [],
        metrics=metrics or {},
        workflow_created_at=BASE + timedelta(
            seconds=workflow_created_offset
        ),
    )


def test_first_generation_success_rate_uses_first_execution() -> None:
    records = [
        _record(1, 1, status="failed"),
        _record(2, 1, status="success", started_offset=30),
        _record(3, 2, status="success"),
        _record(4, 3, status="success"),
        _record(5, 3, status="failed", started_offset=20),
    ]
    overview = build_overview(records)
    metric = overview["metrics"]["first_generation_success_rate"]
    assert metric["denominator"] == 3
    assert metric["numerator"] == 2
    assert metric["value"] == pytest.approx(2 / 3, abs=1e-4)


def test_locator_failure_and_repair_rates() -> None:
    steps = [
        {
            "step_index": 0,
            "status": "failed",
            "failure_category": "locator_not_found",
            "attempts": 2,
            "error": "waiting for locator",
        },
        {
            "step_index": 1,
            "status": "repaired",
            "failure_category": "locator_not_found",
            "attempts": 2,
            "repaired_step": {"action": "click"},
        },
        {"step_index": 2, "status": "success"},
        {"type": "checkpoint", "status": "awaiting_user"},
    ]
    records = [_record(1, 1, status="failed", step_logs=steps)]
    overview = build_overview(records)

    locator = overview["metrics"]["locator_failure_rate"]
    assert locator["locator_failures"] == 2
    assert locator["step_count"] == 3
    assert locator["value"] == pytest.approx(2 / 3, abs=1e-4)

    repair = overview["metrics"]["auto_repair_success_rate"]
    assert repair["repairs_attempted"] == 2
    assert repair["repairs_succeeded"] == 1
    assert repair["value"] == pytest.approx(0.5)

    stats = collect_step_stats(records)
    assert stats["failed_steps"] == 1


def test_failure_category_breakdown_and_fallback_classification() -> None:
    steps = [
        {
            "step_index": 0,
            "status": "error",
            "error": "Timeout 30000ms exceeded",
        },
        {
            "step_index": 1,
            "status": "failed",
            "failure_category": "permission_denied",
        },
    ]
    records = [_record(1, 1, status="failed", step_logs=steps)]
    breakdown = failure_category_breakdown(records)
    assert breakdown["timeout"]["count"] == 1
    assert breakdown["permission_denied"]["count"] == 1
    assert breakdown["unknown"]["count"] == 0
    assert breakdown["timeout"]["rate"] == pytest.approx(0.5)


def test_execution_failure_without_step_logs_is_classified() -> None:
    record = _record(1, 1, status="failed")
    record.error_message = "login required for session"
    breakdown = failure_category_breakdown([record])
    assert breakdown["auth_required"]["count"] == 1


def test_manual_interventions_and_full_rerun_count() -> None:
    records = [
        _record(1, 1, status="failed", metrics={}),
        _record(
            2,
            1,
            status="success",
            started_offset=60,
            metrics={"resumed": True},
        ),
        _record(
            3,
            1,
            status="failed",
            started_offset=120,
            metrics={},
        ),
        _record(
            4,
            1,
            status="success",
            started_offset=180,
            metrics={"manual_interventions": 2},
        ),
        _record(5, 2, status="awaiting_user", started_offset=240),
    ]
    overview = build_overview(records)
    interventions = overview["metrics"][
        "manual_interventions_per_100_executions"
    ]
    # 记录 3 计 2 次，记录 4（awaiting_user）回退计 1 次。
    assert interventions["interventions"] == 3
    assert interventions["value"] == pytest.approx(60.0)

    reruns = overview["metrics"]["full_rerun_count"]
    # 记录 2 是检查点续跑，不计；记录 4 在记录 3 失败后新起，计 1 次。
    assert reruns["value"] == 1


def test_execution_duration_delta() -> None:
    current = [
        _record(1, 1, duration=12.0),
        _record(2, 2, duration=8.0),
    ]
    previous = [
        _record(3, 1, duration=10.0),
        _record(4, 2, duration=10.0),
    ]
    metric = build_overview(current, previous)["metrics"][
        "execution_duration_delta_ratio"
    ]
    assert metric["current_avg_seconds"] == 10.0
    assert metric["baseline_avg_seconds"] == 10.0
    assert metric["value"] == 1.0
    assert metric["delta_percent"] == 0.0

    slower = build_overview(
        [record for record in current],
        [
            _record(5, 1, duration=5.0),
            _record(6, 2, duration=5.0),
        ],
    )["metrics"]["execution_duration_delta_ratio"]
    assert slower["value"] == 2.0
    assert slower["delta_percent"] == 100.0


def test_workflow_creation_duration_uses_first_execution() -> None:
    records = [
        _record(
            1,
            1,
            started_offset=120,
            workflow_created_offset=0,
        ),
        _record(2, 1, started_offset=240),
    ]
    metric = build_overview(records)["metrics"][
        "workflow_creation_duration_seconds"
    ]
    assert metric["samples"] == 1
    assert metric["value"] == 120.0


@pytest.mark.asyncio
async def test_load_execution_records_filters_window_and_dimensions(
    db_session: AsyncSession,
) -> None:
    user = User(id=1, username="u1", email="u1@x", hashed_password="x")
    db_session.add(user)
    await db_session.commit()

    web = Workflow(
        user_id=1,
        name="web-flow",
        natural_language_input="demo",
        executor_type="web",
    )
    desktop = Workflow(
        user_id=1,
        name="desktop-flow",
        natural_language_input="demo",
        executor_type="windows",
    )
    db_session.add_all([web, desktop])
    await db_session.commit()
    await db_session.refresh(web)
    await db_session.refresh(desktop)

    db_session.add_all(
        [
            ExecutionLog(
                workflow_id=web.id,
                status="success",
                started_at=BASE,
                finished_at=BASE + timedelta(seconds=5),
                step_logs=[{"step_index": 0, "status": "success"}],
                metrics={"duration_seconds": 5},
            ),
            ExecutionLog(
                workflow_id=web.id,
                status="failed",
                started_at=BASE + timedelta(hours=2),
                finished_at=BASE + timedelta(hours=2, seconds=8),
                step_logs=[
                    {
                        "step_index": 0,
                        "status": "failed",
                        "failure_category": "locator_not_found",
                    }
                ],
            ),
            ExecutionLog(
                workflow_id=desktop.id,
                status="success",
                started_at=BASE + timedelta(hours=1),
                finished_at=BASE + timedelta(hours=1, seconds=3),
                step_logs=[],
            ),
        ]
    )
    await db_session.commit()

    all_records = await load_execution_records(
        db_session,
        MetricsQuery(
            start=BASE - timedelta(minutes=1),
            end=BASE + timedelta(hours=3),
            user_id=1,
        ),
    )
    assert [record.executor for record in all_records] == [
        "web",
        "windows",
        "web",
    ]

    windowed = await load_execution_records(
        db_session,
        MetricsQuery(
            start=BASE + timedelta(minutes=30),
            end=BASE + timedelta(hours=3),
            workflow_id=web.id,
        ),
    )
    assert len(windowed) == 1
    assert windowed[0].status == "failed"
    assert windowed[0].metrics == {}

    desktop_only = await load_execution_records(
        db_session,
        MetricsQuery(executor="windows", user_id=1),
    )
    assert len(desktop_only) == 1
    assert desktop_only[0].workflow_name == "desktop-flow"
