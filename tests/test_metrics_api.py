"""指标 API：开关、时间窗、维度过滤、失败分类与告警触发。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401
from app.api import metrics as metrics_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.metrics.service import METRIC_KEYS
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "METRICS_API": True,
        "METRICS_ALERTS_ENABLED": True,
        "METRICS_ALERT_THRESHOLDS": "",
        "METRICS_ALERT_WINDOW_SECONDS": 3600,
        "METRICS_ALERT_COOLDOWN_SECONDS": 3600,
        "METRICS_ALERT_EMAIL": "",
        "METRICS_ALERT_WEBHOOK": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _build_client(
    tmp_path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    config: SimpleNamespace | None = None,
    notifier: Any | None = None,
) -> tuple[TestClient, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/metrics.db",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="tester",
                    email="tester@example.com",
                    hashed_password="x",
                )
            )
            await session.commit()

    import asyncio

    asyncio.run(prepare())

    app = FastAPI()
    app.include_router(metrics_api.router)
    app.state.notifier = notifier or SimpleNamespace(
        send_alert=AsyncMock(
            return_value={"email": True, "webhook": False}
        )
    )
    app.state.session_factory = session_factory

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        username="tester",
    )
    client = TestClient(app)

    asyncio.run(_seed(session_factory))
    monkeypatch.setattr(
        metrics_api,
        "get_settings",
        lambda: (config or _config()),
    )
    return client, session_factory


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.utcnow()
    async with session_factory() as session:
        web = Workflow(
            user_id=1,
            name="web-flow",
            natural_language_input="demo",
            executor_type="web",
            created_at=now - timedelta(hours=2),
        )
        desktop = Workflow(
            user_id=1,
            name="desktop-flow",
            natural_language_input="demo",
            executor_type="windows",
            created_at=now - timedelta(hours=3),
        )
        session.add_all([web, desktop])
        await session.commit()
        await session.refresh(web)
        await session.refresh(desktop)
        session.add_all(
            [
                ExecutionLog(
                    workflow_id=web.id,
                    status="failed",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    error_message="timeout",
                    step_logs=[
                        {
                            "step_index": 0,
                            "status": "failed",
                            "failure_category": "locator_not_found",
                        },
                        {"step_index": 1, "status": "success"},
                    ],
                ),
                ExecutionLog(
                    workflow_id=desktop.id,
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    step_logs=[{"step_index": 0, "status": "success"}],
                ),
            ]
        )
        await session.commit()


def test_metrics_api_disabled_returns_403(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = _build_client(
        tmp_path,
        monkeypatch=monkeypatch,
        config=_config(METRICS_API=False),
    )
    response = client.get("/api/metrics/overview")
    assert response.status_code == 403


def test_metrics_overview_and_dimension_filters(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = _build_client(tmp_path, monkeypatch=monkeypatch)
    response = client.get("/api/metrics/overview?range=24h")
    assert response.status_code == 200
    payload = response.json()
    assert set(METRIC_KEYS) <= set(payload["metrics"])
    assert payload["execution_count"] == 2
    assert payload["metrics"]["locator_failure_rate"]["value"] == pytest.approx(
        1 / 3,
        abs=1e-4,
    )
    assert payload["failure_categories"]["locator_not_found"]["count"] == 1
    assert payload["dimensions"]["user_id"] == 1

    desktop = client.get(
        "/api/metrics/overview?range=24h&executor=windows"
    )
    assert desktop.status_code == 200
    assert desktop.json()["execution_count"] == 1
    assert (
        desktop.json()["metrics"]["first_generation_success_rate"]["value"]
        == 1.0
    )

    workflow_filter = client.get(
        "/api/metrics/overview?range=24h&workflow_id=1"
    )
    assert workflow_filter.status_code == 200
    assert workflow_filter.json()["execution_count"] == 1


def test_metrics_custom_window_excludes_old_executions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = _build_client(tmp_path, monkeypatch=monkeypatch)
    now = datetime.utcnow()
    start = (now - timedelta(minutes=15)).isoformat()
    end = now.isoformat()
    response = client.get(
        "/api/metrics/overview",
        params={"start": start, "end": end, "range": "24h"},
    )
    assert response.status_code == 200
    assert response.json()["execution_count"] == 1

    invalid = client.get(
        "/api/metrics/overview",
        params={"start": end, "end": start},
    )
    assert invalid.status_code == 400


def test_metrics_user_dimension_rejects_other_users(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = _build_client(tmp_path, monkeypatch=monkeypatch)
    response = client.get("/api/metrics/overview?user_id=2")
    assert response.status_code == 403


def test_failure_categories_endpoint(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = _build_client(tmp_path, monkeypatch=monkeypatch)
    response = client.get("/api/metrics/failure-categories?range=24h")
    assert response.status_code == 200
    payload = response.json()
    assert payload["failure_categories"]["locator_not_found"]["count"] == 1
    assert payload["failure_categories"]["timeout"]["count"] == 0


def test_alert_check_endpoint_sends_via_notifier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    notifier = SimpleNamespace(
        send_alert=AsyncMock(
            return_value={"email": True, "webhook": False}
        )
    )
    config = _config(
        METRICS_ALERT_THRESHOLDS=json.dumps(
            {"locator_failure_rate": {"threshold": 0.1}}
        ),
        METRICS_ALERT_WINDOW_SECONDS=3600,
        METRICS_ALERT_COOLDOWN_SECONDS=0,
    )
    client, _factory = _build_client(
        tmp_path,
        monkeypatch=monkeypatch,
        config=config,
        notifier=notifier,
    )
    response = client.post("/api/metrics/alerts/check")
    assert response.status_code == 200
    payload = response.json()
    fired_metrics = [alert["metric"] for alert in payload["fired"]]
    assert "locator_failure_rate" in fired_metrics
    notifier.send_alert.assert_awaited_once()

    (tmp_path / "disabled").mkdir()
    disabled = _build_client(
        tmp_path / "disabled",
        monkeypatch=monkeypatch,
        config=_config(METRICS_ALERTS_ENABLED=False),
    )[0]
    assert (
        disabled.post("/api/metrics/alerts/check").status_code == 403
    )
