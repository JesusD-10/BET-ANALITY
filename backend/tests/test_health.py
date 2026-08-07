from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bet-analizador-api"}


def test_highlights_returns_matches() -> None:
    response = client.get("/api/v1/matches/highlights")

    assert response.status_code == 200
    assert len(response.json()["matches"]) > 0
    assert "source" in response.json()

