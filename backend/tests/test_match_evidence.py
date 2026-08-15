from datetime import datetime, timezone

from app.schemas.matches import H2HMatchItem, MatchSummary
from app.services.api_football import BookmakerQuote
from app.services.match_evidence import build_match_evidence


def _match() -> MatchSummary:
    return MatchSummary(
        id="api-football-900",
        external_id="900",
        competition="Liga de prueba",
        league_id="39",
        season=2026,
        round="Regular Season - 3",
        kickoff_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        home_team="Local FC",
        away_team="Visitante FC",
        home_team_id="10",
        away_team_id="20",
        status="PROGRAMADO",
        source_provider="api-football",
    )


def _history(fixture_id: int, home_id: int, away_id: int, home_goals: int, away_goals: int):
    return {
        "fixture": {"id": fixture_id, "date": "2026-08-01T20:00:00+00:00"},
        "league": {"id": 39, "name": "Liga de prueba"},
        "teams": {
            "home": {"id": home_id, "name": "Local FC" if home_id == 10 else "Rival"},
            "away": {"id": away_id, "name": "Visitante FC" if away_id == 20 else "Rival"},
        },
        "goals": {"home": home_goals, "away": away_goals},
        "statistics": [
            {
                "team": {"id": home_id, "name": "Local FC" if home_id == 10 else "Rival"},
                "corners": 7,
                "total_shots": 14,
                "shots_on_target": 6,
                "fouls": 11,
                "yellow_cards": 2,
                "red_cards": 0,
            },
            {
                "team": {"id": away_id, "name": "Visitante FC" if away_id == 20 else "Rival"},
                "corners": 4,
                "total_shots": 9,
                "shots_on_target": 3,
                "fouls": 13,
                "yellow_cards": 3,
                "red_cards": 0,
            },
        ],
        "player_statistics": [
            {
                "player": {"id": 101, "name": "Delantero Local"},
                "team": {"id": home_id, "name": "Local FC" if home_id == 10 else "Rival"},
                "shots": {"total": 4, "on_target": 2},
                "goals": {"total": 1},
            }
        ],
    }


def test_build_match_evidence_normalizes_all_predictive_blocks() -> None:
    match = _match()
    home_history = [_history(1, 10, 30, 2, 1)]
    away_history = [_history(2, 30, 20, 1, 1)]
    h2h = [
        H2HMatchItem(
            date="2026-01-01",
            competition="Liga de prueba",
            home_team="Local FC",
            away_team="Visitante FC",
            score="2 - 0",
            winner="Local FC",
        )
    ]
    season_home = {
        "team": {"id": "10", "name": "Local FC"},
        "form": "WWDLW",
        "fixtures": {
            "played": {"total": 20},
            "wins": {"total": 12},
            "draws": {"total": 5},
            "losses": {"total": 3},
        },
        "goals_for": {"total": {"total": 38}, "average": {"total": "1.9"}},
        "goals_against": {"total": {"total": 18}, "average": {"total": "0.9"}},
        "clean_sheets": {"total": 9},
        "failed_to_score": {"total": 2},
    }
    standings = {
        "groups": [
            {
                "table": [
                    {
                        "rank": 1,
                        "team": {"id": "10", "name": "Local FC"},
                        "points": 41,
                        "goal_difference": 20,
                        "form": "WWDLW",
                        "overall": {"played": 20, "wins": 12, "draws": 5, "losses": 3, "goals_for": 38, "goals_against": 18},
                    },
                    {
                        "rank": 5,
                        "team": {"id": "20", "name": "Visitante FC"},
                        "points": 32,
                        "goal_difference": 6,
                        "form": "DWLWW",
                        "overall": {"played": 20, "wins": 9, "draws": 5, "losses": 6, "goals_for": 30, "goals_against": 24},
                    },
                ]
            }
        ]
    }
    prediction = {
        "winner": {"id": "10", "name": "Local FC", "comment": "Win or draw"},
        "win_or_draw": True,
        "under_over": "+1.5",
        "expected_goals": {"home": "2", "away": "1"},
        "percentages": {"home": 55, "draw": 27, "away": 18},
        "advice": "Double chance: Local FC or draw",
    }
    evidence = build_match_evidence(
        match=match,
        provider_name="api-football",
        home_history=home_history,
        away_history=away_history,
        h2h=h2h,
        injuries=[],
        lineups=None,
        odds_quotes={
            "TOTAL_GOALS_OVER_2_5": BookmakerQuote(
                market_key="TOTAL_GOALS_OVER_2_5",
                odds=1.95,
                bookmaker="Casa verificada",
                updated_at="2026-08-13T12:00:00+00:00",
            )
        },
        league_coverage={
            "coverage": {
                "fixtures": {"lineups": True, "statistics_players": True},
                "standings": True,
                "injuries": True,
                "predictions": True,
                "odds": True,
            }
        },
        standings=standings,
        home_team_statistics=season_home,
        provider_prediction=prediction,
        lineups_requested=False,
    )

    assert evidence.statistics_summary is not None
    assert evidence.statistics_summary.home is not None
    assert evidence.statistics_summary.home.goals_for_avg == 1.9
    assert evidence.statistics_summary.home.averages["corners"] == 7
    assert evidence.standings is not None
    assert evidence.standings.home is not None and evidence.standings.home.rank == 1
    assert evidence.standings.away is not None and evidence.standings.away.rank == 5
    assert evidence.provider_prediction is not None
    assert evidence.provider_prediction.percent_home == 0.55
    assert evidence.player_context is not None
    assert evidence.player_context.home[0].player_name == "Delantero Local"
    assert evidence.verified_odds[0].bookmaker == "Casa verificada"
    assert {item.section: item.status for item in evidence.data_coverage}["h2h"] == "available"


def test_build_match_evidence_reports_exhausted_quota_instead_of_valid_empty_data() -> None:
    evidence = build_match_evidence(
        match=_match(),
        provider_name="api-football",
        home_history=[],
        away_history=[],
        h2h=[],
        injuries=[],
        lineups=None,
        odds_quotes={},
        standings_requested=False,
        prediction_requested=False,
        provider_unavailable_reason="Cuota diaria agotada.",
    )

    sections = {item.section: item for item in evidence.data_coverage}
    assert sections["h2h"].status == "unavailable"
    assert sections["h2h"].reason == "Cuota diaria agotada."
    assert sections["injuries"].status == "unavailable"
    assert sections["standings"].status == "not_requested"
    assert sections["provider_prediction"].status == "not_requested"
