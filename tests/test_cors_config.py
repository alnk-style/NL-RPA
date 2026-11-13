from fastapi.testclient import TestClient

from app.main import app


def test_cors_allows_local_frontend_origin() -> None:
    client = TestClient(app)
    response = client.get(
        "/openapi.json",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:5173"
    )


def test_cors_rejects_untrusted_origin() -> None:
    client = TestClient(app)
    response = client.get(
        "/openapi.json",
        headers={"Origin": "https://evil.example"},
    )

    assert "access-control-allow-origin" not in response.headers
