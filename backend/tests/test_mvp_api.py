from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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