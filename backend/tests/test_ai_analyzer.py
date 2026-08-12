from datetime import datetime, timezone
import json
from types import SimpleNamespace

import openai

from app.core.config import settings
from app.schemas.matches import MatchSummary, RefereeInfo, InjuryItem, H2HMatchItem
from app.services.ai_analyzer import (
    _available_market_families,
    _format_recent_history,
    _generate_local_fallback_analysis,
    analyze_match_with_ai,
)


def test_local_fallback_analysis_returns_rich_context():
    match = MatchSummary(
        id="demo-test-1",
        competition="La Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Real Madrid",
        away_team="Barcelona",
        venue="Santiago Bernabéu",
        referee="Mateu Lahoz",
        data_quality=0.9,
        odds_available=True,
        status="PROGRAMADO",
    )

    referee = RefereeInfo(name="Mateu Lahoz", yellow_cards_avg=5.1, red_cards_avg=0.3, tendency="Amonesta temprano")
    injuries = [InjuryItem(player="Courtois", team="Real Madrid", reason="Lesión de rodilla", status="Baja confirmada")]
    h2h = [H2HMatchItem(date="2025-10-26", competition="La Liga", home_team="Real Madrid", away_team="Barcelona", score="3 - 1", winner="Real Madrid")]

    analysis = _generate_local_fallback_analysis(match, referee, injuries, None, h2h)

    assert analysis.match.home_team == "Real Madrid"
    assert len(analysis.markets) >= 3
    assert analysis.referee_info.name == "Mateu Lahoz"
    assert len(analysis.injuries) == 1
    assert analysis.injuries[0].player == "Courtois"
    assert len(analysis.h2h_matches) == 1
    assert analysis.markets[0].fair_odds > 1.0


def test_openai_advanced_market_is_filtered_without_source_statistics(monkeypatch):
    match = MatchSummary(
        id="provider-1",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        data_quality=0.8,
        odds_available=False,
        status="PROGRAMADO",
    )
    content = json.dumps(
        {
            "markets": [
                {
                    "market_key": "TOTAL_CORNERS_OVER_9_5",
                    "label": "Córners",
                    "selection": "Más de 9.5",
                    "probability": 0.55,
                    "fair_odds": 1.82,
                    "best_odds": 2.0,
                    "expected_value": 0.1,
                    "confidence": "Media",
                    "data_quality": 0.8,
                    "factors_for": ["Supuesto no respaldado"],
                    "risks": ["Sin datos"],
                },
                {
                    "market_key": "TOTAL_GOALS_OVER_2_5",
                    "label": "Goles",
                    "selection": "Más de 2.5",
                    "probability": 0.52,
                    "fair_odds": 1.92,
                    "best_odds": 2.05,
                    "expected_value": 0.066,
                    "confidence": "Media",
                    "data_quality": 0.8,
                    "factors_for": ["Marcadores recientes"],
                    "risks": ["Varianza"],
                },
            ]
        }
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response)
        )
    )
    monkeypatch.setattr(openai, "OpenAI", lambda **_: fake_client)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    analysis = analyze_match_with_ai(match)

    assert [market.market_key for market in analysis.markets] == ["TOTAL_GOALS_OVER_2_5"]
    assert analysis.markets[0].best_odds is None
    assert analysis.markets[0].expected_value is None


def test_advanced_families_activate_only_from_explicit_statistics():
    families = _available_market_families(
        None,
        [
            {
                "statistics": [{"corners": 8, "total_shots": 14}],
                "player_statistics": [
                    {
                        "player": {"name": "Delantero"},
                        "shots": {"total": 3},
                        "goals": {"total": 1},
                    }
                ],
            }
        ],
        [],
    )

    assert {"corners", "team_shots", "player_shots", "player_goals"}.issubset(families)
    assert "player_shots_on_target" not in families


def test_player_shots_do_not_enable_team_shots_and_null_does_not_enable_market():
    families = _available_market_families(
        None,
        [
            {
                "statistics": [{"corners": None}],
                "player_statistics": [{"shots": {"total": 2, "on_target": None}}],
            }
        ],
        [],
    )

    assert "player_shots" in families
    assert "player_shots_on_target" not in families
    assert "team_shots" not in families
    assert "corners" not in families


def test_prompt_history_omits_duplicate_raw_player_payload():
    text = _format_recent_history(
        [
            {
                "fixture": {"date": "2026-08-10"},
                "players": [{"large": "raw-payload"}],
                "player_statistics": [{"player": {"name": "Nueve"}, "shots": {"total": 3}}],
            }
        ]
    )

    assert "player_statistics" in text
    assert "raw-payload" not in text


def test_normalized_recent_matches_are_accepted_without_enabling_advanced_markets():
    recent = H2HMatchItem(
        date="2026-08-10",
        competition="Liga",
        home_team="Local",
        away_team="Rival",
        score="2 - 1",
        winner="Local",
    )

    families = _available_market_families(None, [recent], [])

    assert families == {"result", "goals"}
