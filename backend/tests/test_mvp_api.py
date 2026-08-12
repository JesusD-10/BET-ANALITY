import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api import routes as routes_module
from app.core.config import settings
from app.main import app
from app.services.ai_gateway import ai_gateway
from app.services.matches import _analysis_cache, _fixture_by_id, _fixture_cache


client = TestClient(app)


@pytest.fixture(autouse=True)
def use_explicit_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(ai_gateway, "is_available", lambda: False)
    _fixture_cache.clear()
    _fixture_by_id.clear()
    _analysis_cache.clear()
    routes_module._assistant_request_times.clear()
    routes_module._assistant_response_cache.clear()


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



def test_assistant_uses_local_fallback_without_configured_ai() -> None:
    response = client.post("/api/v1/assistant/question", json={"question": "Que respalda esta señal?"})

    assert response.status_code == 200
    assert response.json()["source"] == "fallback-local"


def test_assistant_uses_only_the_in_memory_analysis_context(monkeypatch) -> None:
    requested_match_ids: list[str | None] = []

    def local_context(match_id: str | None):
        requested_match_ids.append(match_id)
        return None

    monkeypatch.setattr(routes_module, "get_assistant_analysis_context", local_context)

    response = client.post(
        "/api/v1/assistant/question",
        json={"question": "Que datos hay del partido?", "match_id": "api-football-123"},
    )

    assert response.status_code == 200
    assert requested_match_ids == ["api-football-123"]
    assert response.json()["source"] == "fallback-local"


def test_assistant_caches_identical_questions_before_using_more_ai_quota(monkeypatch) -> None:
    calls = 0

    def complete_text(**_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="Respuesta protegida", provider="cerebras")

    monkeypatch.setattr(routes_module, "get_assistant_analysis_context", lambda _match_id: None)
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(ai_gateway, "complete_text", complete_text)
    payload = {"question": "Que respalda esta señal?", "match_id": "api-football-123"}

    first = client.post("/api/v1/assistant/question", json=payload)
    second = client.post(
        "/api/v1/assistant/question",
        json={"question": "  QUE   RESPALDA ESTA SEÑAL?  ", "match_id": "api-football-123"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["source"] == "multi-ai-cerebras"
    assert calls == 1


def test_assistant_rate_limit_cannot_be_bypassed_with_malformed_forwarded_ips() -> None:
    for index in range(10):
        response = client.post(
            "/api/v1/assistant/question",
            headers={"X-Forwarded-For": f"not-an-ip-{index}"},
            json={"question": f"Consulta numero {index}"},
        )
        assert response.status_code == 200

    blocked = client.post(
        "/api/v1/assistant/question",
        headers={"X-Forwarded-For": "another-invalid-value"},
        json={"question": "Consulta adicional"},
    )

    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert blocked.json() == {
        "detail": "Demasiadas consultas al asistente. Intenta nuevamente en un momento."
    }
