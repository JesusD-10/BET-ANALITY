import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.matches import _analysis_cache, _fixture_by_id, _fixture_cache


client = TestClient(app)


@pytest.fixture(autouse=True)
def use_explicit_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    _fixture_cache.clear()
    _fixture_by_id.clear()
    _analysis_cache.clear()


def test_search_filters_mock_matches() -> None:
    response = client.get("/api/v1/matches/search", params={"q": "Arsenal"})

    assert response.status_code == 200
    assert [match["home_team"] for match in response.json()["matches"]] == ["Arsenal"]


def test_analysis_exposes_fair_odds_and_without_odds_mode() -> None:
    response = client.get("/api/v1/matches/demo-bayern-dortmund/analysis")

    assert response.status_code == 200
    first_market = response.json()["markets"][0]
    assert first_market["fair_odds"] > 1.0
    assert "probability" in first_market



def test_assistant_uses_fallback_or_openai() -> None:
    response = client.post("/api/v1/assistant/question", json={"question": "Que respalda esta señal?"})

    assert response.status_code == 200
    assert response.json()["source"] in {"fallback-local", "openai"}
