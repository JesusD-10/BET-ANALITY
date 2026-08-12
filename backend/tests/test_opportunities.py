from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import openai

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app
from app.schemas.matches import (
    MarketAnalysis,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)
from app.services.matches import _analysis_cache, _fixture_cache
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.opportunities import (
    MARKET_TAXONOMY,
    build_combinations,
    build_dream_picks,
    enrich_analysis_with_opportunities,
    market_family,
)


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


def _advanced_market(
    market_key: str,
    label: str,
    selection: str,
    probability: float,
    *,
    data_quality: float = 0.88,
) -> MarketAnalysis:
    return MarketAnalysis(
        market_key=market_key,
        label=label,
        selection=selection,
        probability=probability,
        fair_odds=round(1 / probability, 2),
        best_odds=2.10,
        expected_value=0.04,
        confidence="Media-alta",
        data_quality=data_quality,
        factors_for=[f"Datos verificados para {label.lower()}"],
        risks=[f"Varianza del mercado de {label.lower()}"],
    )


def test_external_timeouts_stay_inside_interactive_budget() -> None:
    assert settings.openai_timeout_seconds <= 5
    assert settings.api_football_timeout_seconds <= 3
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
    assert configured.api_football_timeout_seconds == 3
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


def test_dream_can_be_a_single_selection_when_the_whole_market_reaches_three() -> None:
    market = MarketAnalysis(
        market_key="WINNER_AWAY",
        label="Ganador del partido",
        selection="Equipo Visitante",
        probability=0.40,
        fair_odds=2.50,
        best_odds=3.20,
        expected_value=0.024,
        confidence="Media",
        data_quality=0.82,
        factors_for=["Precio verificado por encima de la cuota objetivo"],
        risks=["Selección de alta varianza"],
    )

    dreams = build_dream_picks(_match(), None, [market])

    assert dreams[0].kind == "dream-single"
    assert len(dreams[0].legs) == 1
    assert dreams[0].best_odds == 3.20
    assert dreams[0].probability == 0.40


def test_dream_threshold_applies_to_complete_combination_not_each_leg() -> None:
    dreams = build_dream_picks(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=4.8),
    )

    combined = next(item for item in dreams if item.kind == "dream-builder")
    assert len(combined.legs) == 2
    assert combined.fair_odds >= 3.0
    assert combined.best_odds is None
    assert all("ODDS" not in leg.market_key for leg in combined.legs)


def test_advanced_market_taxonomy_is_ready_but_not_fabricated() -> None:
    assert {
        "corners",
        "team_shots",
        "player_shots",
        "player_shots_on_target",
        "player_goals",
    }.issubset(MARKET_TAXONOMY)

    dreams_without_advanced_data = build_dream_picks(_match(), None)
    keys = {leg.market_key for dream in dreams_without_advanced_data for leg in dream.legs}
    assert not any("CORNER" in key or "SHOT" in key or "GOALSCORER" in key for key in keys)


def test_advanced_markets_form_one_adjusted_builder_from_distinct_families() -> None:
    markets = [
        _advanced_market(
            "TOTAL_CORNERS_OVER_8_5",
            "Total de córners",
            "Más de 8.5 córners",
            0.62,
            data_quality=0.91,
        ),
        _advanced_market(
            "PLAYER_SHOTS_ON_TARGET_OVER_0_5",
            "Remates al arco de jugador",
            "Delantero: más de 0.5 remates al arco",
            0.58,
            data_quality=0.87,
        ),
    ]

    combinations = build_combinations(_match(), None, markets)
    advanced = [item for item in combinations if item.kind == "advanced-builder"]

    assert len(advanced) == 1
    builder = advanced[0]
    assert len(builder.legs) == 2
    assert {
        market_family(builder.legs[0].market_key),
        market_family(builder.legs[1].market_key),
    } == {"corners", "player_shots_on_target"}
    assert builder.probability == round(0.62 * 0.58 * 0.90, 4)
    assert builder.fair_odds == round(1 / builder.probability, 2)
    assert builder.best_odds is None
    assert builder.expected_value is None
    assert "0.90" in builder.correlation_note
    assert "independencia" in builder.correlation_note


def test_advanced_builder_rejects_low_probability_or_same_family_legs() -> None:
    same_family = [
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Córners", "Más de 8.5", 0.70),
        _advanced_market("TEAM_CORNERS_HOME_OVER_3_5", "Córners local", "Más de 3.5", 0.66),
    ]
    low_probability = [
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Tarjetas", "Más de 3.5", 0.54),
        _advanced_market("TEAM_SHOTS_HOME_OVER_9_5", "Remates", "Más de 9.5", 0.80),
    ]

    assert not any(
        item.kind == "advanced-builder"
        for item in build_combinations(_match(), None, same_family)
    )
    assert not any(
        item.kind == "advanced-builder"
        for item in build_combinations(_match(), None, low_probability)
    )


def test_eligible_advanced_builder_has_priority_in_dreams_and_keeps_limit() -> None:
    markets = [
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Córners", "Más de 8.5", 0.62),
        _advanced_market(
            "PLAYER_SHOTS_OVER_1_5",
            "Remates de jugador",
            "Delantero: más de 1.5 remates",
            0.58,
        ),
    ]

    dreams = build_dream_picks(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=4.8),
        markets,
    )

    assert len(dreams) == 2
    assert dreams[0].kind == "advanced-builder"
    assert dreams[0].probability >= 0.30
    assert dreams[0].fair_odds >= 3.0
    assert dreams[0].best_odds is None
    assert dreams[0].expected_value is None


def test_advanced_dream_requires_probability_and_fair_odds_thresholds() -> None:
    below_probability = [
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Tarjetas", "Más de 3.5", 0.55),
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Córners", "Más de 8.5", 0.55),
    ]
    below_reference_odds = [
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Tarjetas", "Más de 3.5", 0.68),
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Córners", "Más de 8.5", 0.65),
    ]

    for markets in (below_probability, below_reference_odds):
        dreams = build_dream_picks(_match(), None, markets)
        assert not any(item.kind == "advanced-builder" for item in dreams)


def test_enrichment_passes_gated_markets_to_advanced_builders() -> None:
    markets = [
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Tarjetas", "Más de 3.5", 0.62),
        _advanced_market("TEAM_SHOTS_HOME_OVER_9_5", "Remates", "Más de 9.5", 0.58),
    ]
    analysis = MatchAnalysisResponse(
        match=_match(),
        model_version="test-model",
        updated_at=datetime.now(timezone.utc),
        markets=markets,
        notes=[],
    )

    enriched = enrich_analysis_with_opportunities(analysis)

    assert any(item.kind == "advanced-builder" for item in enriched.combinations)
    assert any(item.kind == "advanced-builder" for item in enriched.dream_picks)


def test_cards_are_omitted_from_generated_builders_without_referee_metrics() -> None:
    combinations = build_combinations(_match(), None)
    dreams = build_dream_picks(_match(), None)

    all_keys = {
        leg.market_key
        for item in [*combinations, *dreams]
        for leg in item.legs
    }
    assert not any("CARD" in key for key in all_keys)


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
