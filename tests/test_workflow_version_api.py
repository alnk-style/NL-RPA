from __future__ import annotations

from fastapi.testclient import TestClient


def _plan(url: str) -> dict:
    return {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "web",
        "variables": [],
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {"url": url},
            }
        ],
    }


def test_publish_creates_immutable_versions_and_rollback_switches_pointer(
    async_client: TestClient,
) -> None:
    created = async_client.post(
        "/api/workflows",
        json={
            "name": "Versioned",
            "natural_language_input": "open a page",
        },
    )
    assert created.status_code == 200
    workflow = created.json()
    assert workflow["execution_mode"] == "structured"
    assert workflow["active_version_id"] is None

    workflow_id = workflow["id"]
    updated = async_client.put(
        f"/api/workflows/{workflow_id}",
        json={"structured_plan": _plan("https://example.com/one")},
    )
    assert updated.status_code == 200

    first = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["status"] == "published"
    assert first_payload["workflow_id"] == workflow_id
    assert first_payload["version"] == 1
    assert first_payload["active_version_id"] == first_payload["active_version_id"]

    plan_changed = async_client.put(
        f"/api/workflows/{workflow_id}",
        json={"structured_plan": _plan("https://example.com/two")},
    )
    assert plan_changed.status_code == 200

    second = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["version"] == 2
    assert second_payload["active_version_id"] != first_payload["active_version_id"]

    versions = async_client.get(
        f"/api/workflows/{workflow_id}/versions"
    )
    assert versions.status_code == 200
    items = versions.json()["items"]
    assert [item["version"] for item in items] == [2, 1]
    assert items[0]["plan"]["steps"][0]["params"]["url"].endswith("/two")
    assert items[1]["plan"]["steps"][0]["params"]["url"].endswith("/one")

    rolled_back = async_client.post(
        f"/api/workflows/{workflow_id}/rollback",
        json={"version_id": first_payload["active_version_id"]},
    )
    assert rolled_back.status_code == 200
    rollback_payload = rolled_back.json()
    assert rollback_payload["active_version_id"] == first_payload["active_version_id"]
    assert rollback_payload["version"] == 1

    reloaded = async_client.get(f"/api/workflows/{workflow_id}")
    assert reloaded.status_code == 200
    assert (
        reloaded.json()["active_version_id"]
        == first_payload["active_version_id"]
    )


def test_versions_endpoint_hides_other_workflows(
    async_client: TestClient,
) -> None:
    response = async_client.get("/api/workflows/99999/versions")
    assert response.status_code == 404


def test_version_diff_endpoint_returns_structured_changes(
    async_client: TestClient,
) -> None:
    created = async_client.post(
        "/api/workflows",
        json={
            "name": "Diff workflow",
            "natural_language_input": "open a page",
        },
    )
    workflow_id = created.json()["id"]
    async_client.put(
        f"/api/workflows/{workflow_id}",
        json={"structured_plan": _plan("https://example.com/one")},
    )
    first = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    ).json()
    async_client.put(
        f"/api/workflows/{workflow_id}",
        json={"structured_plan": _plan("https://example.com/two")},
    )
    second = async_client.post(
        f"/api/workflows/{workflow_id}/publish"
    ).json()

    response = async_client.get(
        f"/api/workflows/{workflow_id}/versions/diff",
        params={
            "from_version_id": first["active_version_id"],
            "to_version_id": second["active_version_id"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["from_version_id"] == first["active_version_id"]
    assert payload["to_version_id"] == second["active_version_id"]
    assert any(
        item["kind"] == "change"
        and item["path"].endswith("steps[0].params.url")
        and item["after"].endswith("/two")
        for item in payload["changes"]
    )
