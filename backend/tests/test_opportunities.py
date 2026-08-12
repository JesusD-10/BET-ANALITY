from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import openai

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app
from app.schemas.matches import MatchSummary, RefereeInfo
from app.services.matches import _analysis_cache, _fixture_cache
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.opportunities import build_combinations, build_dream_picks


client = TestClient(app)


def _match() -> MatchSummary:
    return MatchSummary(
        id="test-opportunities",
        competition="Liga de prueba",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Equipo Local",
        away_team="Equipo Visitante",
        data_quality=0.9,
        odds_available=False,
        status="PROGRAMADO",
    )


def test_external_timeouts_stay_inside_interactive_budget() -> None:
    assert settings.openai_timeout_seconds <= 5
    assert settings.api_football_timeout_seconds <= 2
    assert settings.football_data_timeout_seconds <= 2
    assert settings.openai_max_retries == 0


def test_settings_clamp_slow_values_and_ignore_placeholders() -> None:
    configured = Settings(
        _env_file=None,
        openai_timeout_seconds=30,
        api_football_timeout_seconds=30,
        football_data_timeout_seconds=30,
        openai_max_retries=5,
        openai_api_key="OPENAI_API_KEY",
        api_football_key="your_api_football_key_here",
    )

    assert configured.openai_timeout_seconds == 5
    assert configured.api_football_timeout_seconds == 2
    assert configured.football_data_timeout_seconds == 2
    assert configured.openai_max_retries == 0
    assert configured.openai_api_key == ""
    assert configured.api_football_key == ""


def test_openai_timeout_falls_back_after_one_attempt(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = TimeoutError("slow model")
    client_factory = MagicMock(return_value=fake_client)
    monkeypatch.setattr(openai, "OpenAI", client_factory)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    analysis = analyze_match_with_ai(_match())

    assert analysis.model_version == "baseline-poisson-v0.3"
    assert fake_client.chat.completions.create.call_count == 1
    client_factory.assert_called_once_with(
        api_key="test-key",
        timeout=settings.openai_timeout_seconds,
        max_retries=0,
    )


def test_match_builders_include_high_probability_goal_and_cards() -> None:
    combinations = build_combinations(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=4.8),
    )

    assert len(combinations) == 2
    assert combinations[0].probability >= 0.70
    assert [leg.market_key for leg in combinations[0].legs] == [
        "TOTAL_GOALS_OVER_0_5",
        "TOTAL_CARDS_OVER_2_5",
    ]
    assert "independientes" in combinations[0].correlation_note


def test_each_dream_has_minimum_probability_and_reference_odds() -> None:
    dreams = build_dream_picks(_match(), None)

    assert len(dreams) == 2
    assert all(dream.probability >= 0.30 for dream in dreams)
    assert all(dream.fair_odds >= 3.0 for dream in dreams)
    assert all(len(dream.legs) >= 2 for dream in dreams)


def test_analysis_contract_exposes_combinations_and_match_dreams(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    _fixture_cache.clear()
    _analysis_cache.clear()

    response = client.get("/api/v1/matches/demo-arsenal-chelsea/analysis")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["combinations"]) >= 1
    assert len(payload["dream_picks"]) >= 1
    assert payload["dream_picks"][0]["probability"] >= 0.30


def test_daily_dreams_are_diversified_and_respect_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    _fixture_cache.clear()
    _analysis_cache.clear()

    response = client.get("/api/v1/recommendations/dreams", params={"limit": 3})

    assert response.status_code == 200
    items = response.json()["recommendations"]
    assert len(items) == 3
    assert len({item["match_id"] for item in items}) == 3
    assert all(item["probability"] >= 0.30 for item in items)
    assert all(item["fair_odds"] >= 3.0 for item in items)


def test_mock_highlights_follow_requested_date(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    _fixture_cache.clear()

    selected = date(2026, 9, 2)
    response = client.get("/api/v1/matches/highlights", params={"match_date": selected.isoformat()})

    assert response.status_code == 200
    assert all(item["kickoff_at"].startswith(selected.isoformat()) for item in response.json()["matches"])
