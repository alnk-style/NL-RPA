from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.actions import router


def test_action_catalog_api_returns_new_actions_only() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/action-catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "2026-10-28.1"
    ids = {
        (item["executor"], item["id"])
        for item in payload["actions"]
    }
    assert ("web", "goto") in ids
    assert ("web", "code_step") not in ids
    assert ("windows", "fetch_kugou_lyrics") not in ids


def test_action_catalog_can_include_legacy_for_compatibility_tools() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(
            "/api/action-catalog",
            params={"include_legacy": "true"},
        )
    assert response.status_code == 200
    ids = {
        (item["executor"], item["id"])
        for item in response.json()["actions"]
    }
    assert ("web", "code_step") in ids
