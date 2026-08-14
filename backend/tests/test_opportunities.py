from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app
from app.schemas.matches import (
    DisciplineSummary,
    MarketAnalysis,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
    TeamDisciplineAverage,
)
from app.services.api_football import BookmakerQuote
from app.services import matches as matches_service
from app.services.matches import (
    _analysis_cache,
    _apply_verified_market_odds,
    _fixture_cache,
)
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.ai_gateway import ai_gateway
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


def _named_match(match_id: str, home_team: str, away_team: str) -> MatchSummary:
    return _match().model_copy(
        update={
            "id": match_id,
            "home_team": home_team,
            "away_team": away_team,
        }
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


def test_external_timeouts_leave_room_inside_frontend_deadlines() -> None:
    assert settings.ai_provider_timeout_seconds <= 25
    assert settings.ai_total_timeout_seconds <= 30
    assert settings.api_football_timeout_seconds <= 15
    assert settings.sportmonks_timeout_seconds <= 25
    assert settings.football_data_timeout_seconds <= 15
    assert settings.sports_data_total_timeout_seconds <= 60
    assert (
        settings.api_football_timeout_seconds
        + settings.sportmonks_timeout_seconds
        + settings.football_data_timeout_seconds
        <= settings.sports_data_total_timeout_seconds
    )
    assert settings.ai_max_provider_attempts <= 4


def test_settings_clamp_slow_values_and_ignore_placeholders() -> None:
    configured = Settings(
        _env_file=None,
        ai_provider_timeout_seconds=30,
        ai_total_timeout_seconds=30,
        api_football_timeout_seconds=30,
        sportmonks_timeout_seconds=30,
        football_data_timeout_seconds=30,
        sports_data_total_timeout_seconds=120,
        ai_max_provider_attempts=30,
        xai_api_key="XAI_API_KEY",
        api_football_key="your_api_football_key_here",
        sportmonks_api_token="your_sportmonks_api_token_here",
    )

    assert configured.ai_provider_timeout_seconds == 25
    assert configured.ai_total_timeout_seconds == 30
    assert configured.api_football_timeout_seconds == 15
    assert configured.sportmonks_timeout_seconds == 25
    assert configured.football_data_timeout_seconds == 15
    assert configured.sports_data_total_timeout_seconds == 60
    assert configured.ai_max_provider_attempts == 4
    assert configured.xai_api_key == ""
    assert configured.api_football_key == ""
    assert configured.sportmonks_api_token == ""

    short_budget = Settings(
        _env_file=None,
        api_football_timeout_seconds=15,
        sportmonks_timeout_seconds=25,
        football_data_timeout_seconds=15,
        sports_data_total_timeout_seconds=10,
    )
    assert short_budget.sports_data_total_timeout_seconds == 55


def test_multi_ai_timeout_uses_local_fallback(monkeypatch) -> None:
    completion = MagicMock(side_effect=TimeoutError("slow model"))
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(ai_gateway, "complete_json_consensus", completion)

    analysis = analyze_match_with_ai(_match())

    assert analysis.model_version == "baseline-poisson-v0.3"
    assert completion.call_count == 1


def test_match_builders_include_high_probability_goal_and_cards() -> None:
    markets = [
        _advanced_market(
            "DOUBLE_CHANCE_HOME_DRAW",
            "Doble oportunidad",
            "Equipo Local o empate",
            0.82,
        ),
        _advanced_market(
            "TOTAL_GOALS_OVER_1_5",
            "Total de goles",
            "Más de 1.5 goles",
            0.80,
        ),
        _advanced_market(
            "TOTAL_CORNERS_OVER_7_5",
            "Total de córners",
            "Más de 7.5 córners",
            0.75,
        ),
    ]
    combinations = build_combinations(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=4.8),
        markets,
    )

    assert len(combinations) >= 4
    assert all(item.probability >= 0.40 for item in combinations)
    assert all(len(item.legs) >= 2 for item in combinations)
    card_builders = [
        item
        for item in combinations
        if any(leg.market_key == "TOTAL_CARDS_OVER_3_5" for leg in item.legs)
    ]
    assert card_builders
    assert any("4.8 amarillas" in factor for factor in card_builders[0].factors_for)
    assert "independencia" in combinations[0].correlation_note


def test_each_dream_has_minimum_probability_and_reference_odds() -> None:
    markets = [
        _advanced_market("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", "Local o empate", 0.62),
        _advanced_market("TOTAL_GOALS_OVER_2_5", "Total de goles", "Más de 2.5", 0.60),
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Total de córners", "Más de 8.5", 0.60),
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Total de tarjetas", "Más de 3.5", 0.61),
    ]
    dreams = build_dream_picks(_match(), None, markets)

    assert len(dreams) > 2
    assert all(dream.probability >= 0.30 for dream in dreams)
    assert all(dream.fair_odds >= 3.0 for dream in dreams)
    assert all(len(dream.legs) >= 2 for dream in dreams)
    signatures = {
        tuple(sorted(leg.market_key for leg in dream.legs))
        for dream in dreams
    }
    assert len(signatures) == len(dreams)


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
    markets = [
        _advanced_market(
            "DOUBLE_CHANCE_HOME_DRAW",
            "Doble oportunidad",
            "Equipo Local o empate",
            0.62,
        ),
        _advanced_market(
            "TOTAL_GOALS_OVER_2_5",
            "Total de goles",
            "Más de 2.5 goles",
            0.60,
        ),
    ]
    dreams = build_dream_picks(
        _match(),
        None,
        markets,
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
            0.78,
            data_quality=0.91,
        ),
        _advanced_market(
            "PLAYER_SHOTS_ON_TARGET_OVER_0_5",
            "Remates al arco de jugador",
            "Delantero: más de 0.5 remates al arco",
            0.72,
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
    assert builder.probability == round(0.78 * 0.72 * 0.90, 4)
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


def test_eligible_advanced_builder_is_grounded_and_unpriced() -> None:
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

    assert len(dreams) == 1
    assert dreams[0].kind == "dream-builder"
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
        _advanced_market("TOTAL_CARDS_OVER_3_5", "Tarjetas", "Más de 3.5", 0.78),
        _advanced_market("TEAM_SHOTS_HOME_OVER_9_5", "Remates", "Más de 9.5", 0.72),
        _advanced_market("TOTAL_CORNERS_OVER_8_5", "Córners", "Más de 8.5", 0.62),
        _advanced_market(
            "PLAYER_SHOTS_ON_TARGET_OVER_0_5",
            "Remates al arco",
            "Delantero: más de 0.5",
            0.58,
        ),
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
    assert any(item.kind == "dream-builder" for item in enriched.dream_picks)


def test_dreams_are_interpreted_from_each_match_markets_instead_of_reusing_a_template() -> None:
    first_match = _named_match("halcones-leones", "Halcones", "Leones")
    first_markets = [
        _advanced_market(
            "DOUBLE_CHANCE_HOME_DRAW",
            "Doble oportunidad",
            "Halcones o empate",
            0.56,
        ),
        _advanced_market(
            "TOTAL_GOALS_OVER_2_5",
            "Total de goles",
            "Más de 2.5 goles",
            0.68,
        ),
    ]
    second_match = _named_match("tigres-condores", "Tigres", "Cóndores")
    second_markets = [
        _advanced_market(
            "DRAW_NO_BET_AWAY",
            "Empate no acción",
            "Cóndores",
            0.57,
        ),
        _advanced_market(
            "BOTH_TEAMS_TO_SCORE",
            "Ambos equipos anotan",
            "No",
            0.67,
        ),
    ]

    first_dream = build_dream_picks(first_match, None, first_markets)[0]
    second_dream = build_dream_picks(second_match, None, second_markets)[0]

    assert first_dream.label == "Soñadora interpretada del partido"
    assert second_dream.label == "Soñadora interpretada del partido"
    assert [(leg.market_key, leg.selection) for leg in first_dream.legs] == [
        ("DOUBLE_CHANCE_HOME_DRAW", "Halcones o empate"),
        ("TOTAL_GOALS_OVER_2_5", "Más de 2.5 goles"),
    ]
    assert [(leg.market_key, leg.selection) for leg in second_dream.legs] == [
        ("DRAW_NO_BET_AWAY", "Cóndores"),
        ("BOTH_TEAMS_TO_SCORE", "No"),
    ]
    assert first_dream.selection != second_dream.selection
    assert first_dream.id.startswith(f"{first_match.id}-")
    assert second_dream.id.startswith(f"{second_match.id}-")


def test_daily_safe_recommendations_use_the_best_market_of_each_match(monkeypatch) -> None:
    first_match = _named_match("norte-sur", "Norte", "Sur")
    second_match = _named_match("este-oeste", "Este", "Oeste")
    analyses = {
        first_match.id: MatchAnalysisResponse(
            match=first_match,
            model_version="per-match-test",
            updated_at=datetime.now(timezone.utc),
            notes=[],
            markets=[
                _advanced_market(
                    "DOUBLE_CHANCE_HOME_DRAW",
                    "Doble oportunidad",
                    "Norte o empate",
                    0.79,
                    data_quality=0.92,
                ),
                _advanced_market(
                    "TOTAL_GOALS_OVER_2_5",
                    "Total de goles",
                    "Más de 2.5 goles",
                    0.61,
                ),
            ],
        ),
        second_match.id: MatchAnalysisResponse(
            match=second_match,
            model_version="per-match-test",
            updated_at=datetime.now(timezone.utc),
            notes=[],
            markets=[
                _advanced_market(
                    "TOTAL_GOALS_UNDER_3_5",
                    "Total de goles",
                    "Menos de 3.5 goles",
                    0.76,
                    data_quality=0.91,
                ),
                _advanced_market(
                    "WINNER_HOME",
                    "Ganador del partido",
                    "Este",
                    0.58,
                ),
            ],
        ),
    }
    monkeypatch.setattr(matches_service, "get_highlights", lambda: [first_match, second_match])
    monkeypatch.setattr(
        matches_service,
        "_recommendation_analysis",
        lambda match: analyses[match.id],
    )

    recommendations = matches_service.get_recommendations(limit=2)

    assert [(item.match_id, item.selection) for item in recommendations] == [
        (first_match.id, "Norte o empate"),
        (second_match.id, "Menos de 3.5 goles"),
    ]
    assert [item.market for item in recommendations] == [
        "Doble oportunidad",
        "Total de goles",
    ]


def test_daily_safe_recommendations_never_repeat_a_match_to_fill_limit(monkeypatch) -> None:
    first_match = _named_match("uno", "Uno", "Rival Uno")
    second_match = _named_match("dos", "Dos", "Rival Dos")
    analyses = {}
    for match in (first_match, second_match):
        analyses[match.id] = MatchAnalysisResponse(
            match=match,
            model_version="per-match-test",
            updated_at=datetime.now(timezone.utc),
            notes=[],
            markets=[
                _advanced_market(
                    "TOTAL_GOALS_OVER_1_5",
                    "Total de goles",
                    "Más de 1.5 goles",
                    0.78,
                ),
                _advanced_market(
                    "TOTAL_GOALS_UNDER_3_5",
                    "Total de goles",
                    "Menos de 3.5 goles",
                    0.72,
                ),
            ],
        )
    monkeypatch.setattr(matches_service, "get_highlights", lambda: [first_match, second_match])
    monkeypatch.setattr(
        matches_service,
        "_recommendation_analysis",
        lambda match: analyses[match.id],
    )

    recommendations = matches_service.get_recommendations(limit=20)

    assert len(recommendations) == 2
    assert len({item.match_id for item in recommendations}) == 2


def test_daily_dreams_keep_the_market_interpretation_of_each_match(monkeypatch) -> None:
    first_match = _named_match("azules-rojos", "Azules", "Rojos")
    second_match = _named_match("verdes-dorados", "Verdes", "Dorados")
    first_analysis = enrich_analysis_with_opportunities(
        MatchAnalysisResponse(
            match=first_match,
            model_version="per-match-test",
            updated_at=datetime.now(timezone.utc),
            notes=[],
            markets=[
                _advanced_market(
                    "DOUBLE_CHANCE_HOME_DRAW",
                    "Doble oportunidad",
                    "Azules o empate",
                    0.56,
                ),
                _advanced_market(
                    "TOTAL_GOALS_OVER_2_5",
                    "Total de goles",
                    "Más de 2.5 goles",
                    0.68,
                ),
            ],
        )
    )
    second_analysis = enrich_analysis_with_opportunities(
        MatchAnalysisResponse(
            match=second_match,
            model_version="per-match-test",
            updated_at=datetime.now(timezone.utc),
            notes=[],
            markets=[
                _advanced_market(
                    "DRAW_NO_BET_AWAY",
                    "Empate no acción",
                    "Dorados",
                    0.57,
                ),
                _advanced_market(
                    "BOTH_TEAMS_TO_SCORE",
                    "Ambos equipos anotan",
                    "No",
                    0.67,
                ),
            ],
        )
    )
    analyses = {
        first_match.id: first_analysis,
        second_match.id: second_analysis,
    }
    monkeypatch.setattr(matches_service, "get_highlights", lambda: [first_match, second_match])
    monkeypatch.setattr(
        matches_service,
        "_recommendation_analysis",
        lambda match: analyses[match.id],
    )

    recommendations = matches_service.get_dream_recommendations(limit=2)

    assert [item.match_id for item in recommendations] == [first_match.id, second_match.id]
    assert [[leg.market_key for leg in item.legs] for item in recommendations] == [
        ["DOUBLE_CHANCE_HOME_DRAW", "TOTAL_GOALS_OVER_2_5"],
        ["DRAW_NO_BET_AWAY", "BOTH_TEAMS_TO_SCORE"],
    ]
    assert recommendations[0].selection == (
        "Doble oportunidad: Azules o empate + Total de goles: Más de 2.5 goles"
    )
    assert recommendations[1].selection == (
        "Empate no acción: Dorados + Ambos equipos anotan: No"
    )


def test_daily_dreams_do_not_fill_limit_with_repeated_leg_structure(monkeypatch) -> None:
    first_match = _named_match("primero", "Primero", "Rival A")
    second_match = _named_match("segundo", "Segundo", "Rival B")
    analyses = {}
    for match in (first_match, second_match):
        analyses[match.id] = enrich_analysis_with_opportunities(
            MatchAnalysisResponse(
                match=match,
                model_version="per-match-test",
                updated_at=datetime.now(timezone.utc),
                notes=[],
                markets=[
                    _advanced_market(
                        "DOUBLE_CHANCE_HOME_DRAW",
                        "Doble oportunidad",
                        f"{match.home_team} o empate",
                        0.56,
                    ),
                    _advanced_market(
                        "TOTAL_GOALS_OVER_2_5",
                        "Total de goles",
                        "Más de 2.5 goles",
                        0.68,
                    ),
                ],
            )
        )
    monkeypatch.setattr(matches_service, "get_highlights", lambda: [first_match, second_match])
    monkeypatch.setattr(
        matches_service,
        "_recommendation_analysis",
        lambda match: analyses[match.id],
    )

    recommendations = matches_service.get_dream_recommendations(limit=8)

    assert len(recommendations) == 1
    signatures = [tuple(sorted(leg.market_key for leg in item.legs)) for item in recommendations]
    assert len(signatures) == len(set(signatures))


def test_verified_odds_overlay_sets_bookmaker_ev_without_pricing_builders() -> None:
    markets = [
        MarketAnalysis(
            market_key="TOTAL_GOALS_OVER_2_5",
            label="Goles",
            selection="Más de 2.5",
            probability=0.58,
            fair_odds=1.72,
            confidence="Media",
            data_quality=0.8,
            factors_for=["Forma reciente"],
            risks=["Varianza"],
        ),
        MarketAnalysis(
            market_key="BOTH_TEAMS_TO_SCORE",
            label="Ambos anotan",
            selection="No",
            probability=0.45,
            fair_odds=2.22,
            confidence="Media",
            data_quality=0.8,
            factors_for=["Defensas"],
            risks=["Gol temprano"],
        ),
    ]
    analysis = MatchAnalysisResponse(
        match=_match(),
        model_version="test-model",
        updated_at=datetime.now(timezone.utc),
        markets=markets,
        notes=[],
    )
    quotes = {
        "TOTAL_GOALS_OVER_2_5": BookmakerQuote(
            market_key="TOTAL_GOALS_OVER_2_5",
            odds=1.95,
            bookmaker="Casa real",
        ),
        "BOTH_TEAMS_TO_SCORE": BookmakerQuote(
            market_key="BOTH_TEAMS_TO_SCORE",
            odds=2.10,
            bookmaker="Otra casa",
        ),
    }

    enriched = _apply_verified_market_odds(analysis, quotes)

    assert enriched.match.odds_available is True
    assert enriched.markets[0].best_odds == 1.95
    assert enriched.markets[0].bookmaker == "Casa real"
    assert enriched.markets[0].expected_value == round(0.58 * 1.95 - 1, 3)
    assert enriched.markets[1].best_odds is None
    assert all(item.best_odds is None for item in enriched.combinations)
    assert all(item.expected_value is None for item in enriched.combinations)


def test_verified_odds_overlay_rejects_selection_opposite_to_market_key() -> None:
    market = MarketAnalysis(
        market_key="TOTAL_GOALS_OVER_2_5",
        label="Goles",
        selection="Menos de 2.5 goles",
        probability=0.58,
        fair_odds=1.72,
        confidence="Media",
        data_quality=0.8,
        factors_for=["Forma reciente"],
        risks=["Varianza"],
    )
    analysis = MatchAnalysisResponse(
        match=_match(),
        model_version="test-model",
        updated_at=datetime.now(timezone.utc),
        markets=[market],
        notes=[],
    )
    quote = BookmakerQuote(
        market_key="TOTAL_GOALS_OVER_2_5",
        odds=2.05,
        bookmaker="Casa real",
    )

    enriched = _apply_verified_market_odds(
        analysis,
        {"TOTAL_GOALS_OVER_2_5": quote},
    )

    assert enriched.markets[0].best_odds is None
    assert enriched.markets[0].expected_value is None


def test_cards_are_omitted_from_generated_builders_without_referee_metrics() -> None:
    markets = [
        _advanced_market("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", "Local o empate", 0.76),
        _advanced_market("TOTAL_GOALS_OVER_1_5", "Total de goles", "Más de 1.5", 0.74),
    ]
    combinations = build_combinations(_match(), None, markets)
    dreams = build_dream_picks(_match(), None, markets)

    all_keys = {
        leg.market_key
        for item in [*combinations, *dreams]
        for leg in item.legs
    }
    assert not any("CARD" in key for key in all_keys)


def test_team_card_averages_create_a_grounded_total_card_leg() -> None:
    discipline = DisciplineSummary(
        home=TeamDisciplineAverage(
            team_name="Equipo Local",
            sample_size=6,
            yellow_cards_avg=2.4,
        ),
        away=TeamDisciplineAverage(
            team_name="Equipo Visitante",
            sample_size=5,
            yellow_cards_avg=2.1,
        ),
        note="Muestras recientes verificadas",
    )
    markets = [
        _advanced_market("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", "Local o empate", 0.78),
        _advanced_market("TOTAL_GOALS_OVER_1_5", "Total de goles", "Más de 1.5", 0.76),
    ]

    combinations = build_combinations(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=3.1),
        markets,
        discipline,
    )

    card_builders = [
        item
        for item in combinations
        if any(leg.market_key == "TOTAL_CARDS_OVER_2_5" for leg in item.legs)
    ]
    assert card_builders
    card_factors = {
        factor
        for item in card_builders
        for factor in item.factors_for
    }
    assert any("2.4 amarillas" in factor for factor in card_factors)
    assert any("2.1 amarillas" in factor for factor in card_factors)
    assert not any("3.1 amarillas" in factor for factor in card_factors)


def test_explicit_markets_never_trigger_generic_or_hash_fallbacks() -> None:
    only_market = _advanced_market(
        "TOTAL_GOALS_OVER_1_5",
        "Total de goles",
        "Más de 1.5 goles",
        0.78,
    )

    assert build_combinations(_match(), None, [only_market]) == []
    assert build_dream_picks(_match(), None, [only_market]) == []


def test_explicit_card_market_takes_precedence_over_derived_average() -> None:
    markets = [
        _advanced_market("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", "Local o empate", 0.82),
        _advanced_market("TOTAL_CARDS_OVER_4_5", "Total de tarjetas", "Más de 4.5", 0.72),
    ]

    combinations = build_combinations(
        _match(),
        RefereeInfo(name="Árbitro", yellow_cards_avg=4.8),
        markets,
    )

    card_keys = {
        leg.market_key
        for item in combinations
        for leg in item.legs
        if "CARD" in leg.market_key
    }
    assert card_keys == {"TOTAL_CARDS_OVER_4_5"}


def test_analysis_contract_exposes_combinations_and_match_dreams(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(ai_gateway, "is_available", lambda: False)
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
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    monkeypatch.setattr(ai_gateway, "is_available", lambda: False)
    _fixture_cache.clear()
    _analysis_cache.clear()

    response = client.get("/api/v1/recommendations/dreams", params={"limit": 3})

    assert response.status_code == 200
    items = response.json()["recommendations"]
    # When only two distinct evidence structures qualify, the endpoint is
    # intentionally shorter than the requested limit instead of repeating one.
    assert 1 <= len(items) <= 3
    signatures = [tuple(sorted(leg["market_key"] for leg in item["legs"])) for item in items]
    assert len(signatures) == len(set(signatures))
    assert all(item["probability"] >= 0.30 for item in items)
    assert all(item["fair_odds"] >= 3.0 for item in items)


def test_mock_highlights_follow_requested_date(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    _fixture_cache.clear()

    selected = date(2026, 9, 2)
    response = client.get("/api/v1/matches/highlights", params={"match_date": selected.isoformat()})

    assert response.status_code == 200
    assert all(item["kickoff_at"].startswith(selected.isoformat()) for item in response.json()["matches"])
