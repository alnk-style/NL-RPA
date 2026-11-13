from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.api.observations import (
    get_browser_observer,
    persist_observation,
    require_observation_enabled,
    router,
)
from app.engine.browser_observer import ObservationError
from app.protocols import ObservationKind, ObservationSnapshot


async def _request(
    app: FastAPI,
    payload: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(
            "/api/observations/browser",
            json=payload
            or {
                "url": "https://example.com",
                "max_elements": 10,
                "include_accessibility": True,
                "store_snapshot": False,
            },
        )


def _snapshot() -> ObservationSnapshot:
    return ObservationSnapshot(
        kind=ObservationKind.BROWSER,
        source="playwright",
        url="https://example.com",
        title="示例",
        summary="可交互元素 1 个",
        elements=[{"name": "登录", "visible": True}],
        accessibility={"node_count": 1, "nodes": [], "role_counts": {}},
        payload={"url": "https://example.com"},
    )


def test_browser_observation_disabled_by_default() -> None:
    app = FastAPI()
    app.include_router(router)

    response = asyncio.run(_request(app))

    assert response.status_code == 403
    assert "OBSERVE_BEFORE_PLAN" in response.json()["detail"]


def test_browser_observation_returns_compact_snapshot() -> None:
    app = FastAPI()
    app.include_router(router)
    observer = SimpleNamespace(
        observe=AsyncMock(return_value=_snapshot())
    )
    app.dependency_overrides[require_observation_enabled] = lambda: None
    app.dependency_overrides[get_browser_observer] = lambda: observer

    response = asyncio.run(_request(app))

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_path"] is None
    assert body["observation"]["url"] == "https://example.com"
    assert body["observation"]["accessibility"]["node_count"] == 1
    assert "payload" not in body["observation"]
    assert observer.observe.await_args.kwargs["max_elements"] == 10


def test_browser_observation_maps_failure_category() -> None:
    app = FastAPI()
    app.include_router(router)
    observer = SimpleNamespace(
        observe=AsyncMock(
            side_effect=ObservationError("Timeout 30000ms exceeded")
        )
    )
    app.dependency_overrides[require_observation_enabled] = lambda: None
    app.dependency_overrides[get_browser_observer] = lambda: observer

    response = asyncio.run(_request(app))

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["failure_category"] == "timeout"
    assert "Timeout" in detail["message"]


def test_browser_observation_validates_payload() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_observation_enabled] = lambda: None

    response = asyncio.run(
        _request(
            app,
            {
                "url": "",
                "timeout_ms": 100,
                "max_elements": 9999,
            },
        )
    )

    assert response.status_code == 422


def test_persist_observation_writes_utf8_json(tmp_path: Path) -> None:
    path = Path(persist_observation(_snapshot(), tmp_path))

    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == "browser"
    assert payload["elements"][0]["name"] == "登录"
