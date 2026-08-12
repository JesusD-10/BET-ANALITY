from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.matches import _fixture_by_id, _fixture_cache

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bet-analizador-api"}


def test_highlights_returns_matches(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    _fixture_cache.clear()
    _fixture_by_id.clear()
    response = client.get("/api/v1/matches/highlights")

    assert response.status_code == 200
    assert len(response.json()["matches"]) > 0
    assert "source" in response.json()

