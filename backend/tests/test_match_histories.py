from datetime import date, datetime, timedelta, timezone
import time
from unittest.mock import MagicMock

import httpx
import pytest

from app.schemas.matches import MatchSummary
from app.services import matches as matches_service
from app.services.api_football import APIFootballProvider
from app.services.matches import FootballDataProvider


def _api_football_fixture(day: int, home_goals: int | None = 2) -> dict:
    return {
        "fixture": {"date": f"2026-07-{day:02d}T20:00:00+00:00"},
        "league": {"name": "Liga de prueba"},
        "teams": {
            "home": {"name": f"Local {day}"},
            "away": {"name": f"Visitante {day}"},
        },
        "goals": {"home": home_goals, "away": 1},
    }


def _football_data_fixture(day: int, away_goals: int | None = 0) -> dict:
    return {
        "utcDate": f"2026-07-{day:02d}T20:00:00Z",
        "competition": {"name": "Liga de prueba"},
        "homeTeam": {"name": f"Local {day}"},
        "awayTeam": {"name": f"Visitante {day}"},
        "score": {"fullTime": {"home": 1, "away": away_goals}},
    }


def test_api_football_normalizes_recent_matches_and_caps_request_to_ten(monkeypatch) -> None:
    payload = {
        "response": [
            _api_football_fixture(2),
            _api_football_fixture(6),
            _api_football_fixture(4),
            _api_football_fixture(1, home_goals=None),
            _api_football_fixture(7),
            _api_football_fixture(5),
            _api_football_fixture(3),
        ]
    }
    calls: list[dict] = []

    def fake_request(self, endpoint: str, params: dict | None = None) -> dict:
        calls.append({"endpoint": endpoint, "params": params})
        return payload

    monkeypatch.setattr(APIFootballProvider, "_request", fake_request)
    provider = APIFootballProvider(key="test")
    raw_history = provider.get_team_last_matches("42", limit=20)
    history = provider.normalize_history(raw_history, 5)

    assert calls == [
        {
            "endpoint": "fixtures",
            "params": {"team": "42", "last": "10", "status": "FT-AET-PEN"},
        }
    ]
    assert len(history) == 5
    assert [item.date for item in history] == [
        "2026-07-07",
        "2026-07-06",
        "2026-07-05",
        "2026-07-04",
        "2026-07-03",
    ]
    assert history[0].score == "2 - 1"
    assert provider.normalize_history([_api_football_fixture(1, home_goals=None)], 5) == []


def test_api_football_recent_form_excludes_unfinished_fixtures(monkeypatch) -> None:
    finished = _api_football_fixture(5)
    finished["fixture"]["status"] = {"short": "FT"}
    scheduled = _api_football_fixture(6, home_goals=0)
    scheduled["fixture"]["status"] = {"short": "NS"}
    monkeypatch.setattr(
        APIFootballProvider,
        "_request",
        lambda self, endpoint, params=None: {"response": [scheduled, finished]},
    )

    provider = APIFootballProvider(key="test")
    history = provider.get_team_last_matches("42", enrich=False)

    assert history == [finished]


def test_published_lineups_are_requested_only_close_to_kickoff() -> None:
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    match = MatchSummary(
        id="api-football-10",
        competition="Liga",
        kickoff_at=now + timedelta(hours=2),
        home_team="Local",
        away_team="Visitante",
        status="PROGRAMADO",
        source_provider="api-football",
    )

    assert matches_service._should_fetch_published_lineups(match, now) is False
    match.kickoff_at = now + timedelta(minutes=61)
    assert matches_service._should_fetch_published_lineups(match, now) is False
    match.kickoff_at = now + timedelta(minutes=60)
    assert matches_service._should_fetch_published_lineups(match, now) is True


def test_football_data_normalizes_recent_matches_and_caps_request(monkeypatch) -> None:
    payload = {
        "matches": [
            _football_data_fixture(3),
            _football_data_fixture(6),
            _football_data_fixture(1, away_goals=None),
            _football_data_fixture(7),
            _football_data_fixture(5),
            _football_data_fixture(2),
            _football_data_fixture(4),
        ]
    }
    captured: dict = {}

    def fake_get(endpoint: str, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json=payload, request=httpx.Request("GET", endpoint))

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = FootballDataProvider("token", "https://example.test", 2)
    raw_history = provider.get_team_last_matches("7", limit=30)
    history = provider.normalize_history(raw_history, 5)

    assert captured["params"] == {"status": "FINISHED", "limit": 10}
    assert len(history) == 5
    assert [item.date for item in history] == [
        "2026-07-07",
        "2026-07-06",
        "2026-07-05",
        "2026-07-04",
        "2026-07-03",
    ]
    assert provider.normalize_history([_football_data_fixture(1, away_goals=None)], 5) == []


def test_team_discipline_average_uses_only_verified_available_statistics() -> None:
    history = [
        {
            "statistics": [
                {
                    "team": {"id": 10, "name": "Local"},
                    "fouls": 12,
                    "yellow_cards": 3,
                    "red_cards": 1,
                }
            ]
        },
        {
            "statistics": [
                {
                    "team": {"id": 10, "name": "Local"},
                    "fouls": 8,
                    "yellow_cards": 1,
                }
            ]
        },
        {"statistics": [{"team": {"id": 99, "name": "Rival"}, "fouls": 30}]},
    ]

    result = matches_service._team_discipline_average(
        history,
        team_id="10",
        team_name="Local",
    )

    assert result.sample_size == 2
    assert result.fouls_avg == 10
    assert result.yellow_cards_avg == 2
    assert result.red_cards_avg == 1


@pytest.fixture(autouse=True)
def clear_analysis_cache():
    matches_service._analysis_cache.clear()
    matches_service._fixture_by_id.clear()
    yield
    matches_service._analysis_cache.clear()
    matches_service._fixture_by_id.clear()


def _indexed_match(kickoff_at: datetime, match_id: str = "api-football-991") -> MatchSummary:
    return MatchSummary(
        id=match_id,
        external_id=match_id.removeprefix("api-football-"),
        competition="Liga real",
        kickoff_at=kickoff_at,
        home_team="Local",
        away_team="Visitante",
        home_team_id="1",
        away_team_id="2",
        status="PROGRAMADO",
        source_provider="api-football",
    )


def test_analysis_cached_before_t60_expires_when_lineup_window_is_crossed(monkeypatch) -> None:
    kickoff = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
    analysis = matches_service._quick_analysis(_indexed_match(kickoff))
    key = (analysis.match.id, False)
    clock = [200.0]
    monkeypatch.setattr(matches_service.time, "monotonic", lambda: clock[0])
    matches_service._analysis_cache[key] = (100.0, analysis)

    assert matches_service._get_cached_analysis(
        key,
        now=kickoff - timedelta(minutes=60),
    ) is None


def test_unconfirmed_analysis_inside_t60_uses_five_minute_ttl(monkeypatch) -> None:
    kickoff = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
    analysis = matches_service._quick_analysis(_indexed_match(kickoff))
    key = (analysis.match.id, False)
    clock = [399.0]
    monkeypatch.setattr(matches_service.time, "monotonic", lambda: clock[0])
    matches_service._analysis_cache[key] = (100.0, analysis)
    inside_window = kickoff - timedelta(minutes=30)

    assert matches_service._get_cached_analysis(key, now=inside_window) is analysis
    clock[0] = 401.0
    assert matches_service._get_cached_analysis(key, now=inside_window) is None


def test_assistant_context_reuses_cached_analysis_without_quick_rebuild(monkeypatch) -> None:
    match = _indexed_match(datetime.now(timezone.utc) + timedelta(hours=4))
    analysis = matches_service._quick_analysis(match)
    matches_service._analysis_cache[(match.id, True)] = (time.monotonic(), analysis)
    monkeypatch.setattr(
        matches_service,
        "_quick_analysis",
        lambda match: pytest.fail("No debe reconstruir un análisis que ya está en caché"),
    )

    assert matches_service.get_assistant_analysis_context(match.id) is analysis


def test_assistant_context_builds_local_analysis_only_from_match_index(monkeypatch) -> None:
    match = _indexed_match(datetime.now(timezone.utc) + timedelta(hours=4))
    matches_service._fixture_by_id[match.id] = (time.monotonic(), match)
    monkeypatch.setattr(
        matches_service,
        "get_match",
        lambda match_id: pytest.fail("El asistente no debe resolver el partido externamente"),
    )
    monkeypatch.setattr(
        matches_service,
        "_active_provider",
        lambda: pytest.fail("El asistente no debe consultar un proveedor"),
    )

    context = matches_service.get_assistant_analysis_context(match.id)

    assert context is not None
    assert context.match.id == match.id


def test_assistant_context_returns_none_without_cached_or_indexed_match() -> None:
    assert matches_service.get_assistant_analysis_context("api-football-missing") is None
    assert matches_service.get_assistant_analysis_context(None) is None


def test_real_analysis_does_not_fill_missing_histories_with_demo_data(monkeypatch) -> None:
    match = MatchSummary(
        id="football-data-88",
        external_id="88",
        competition="Liga real",
        kickoff_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        home_team="Equipo real A",
        away_team="Equipo real B",
        home_team_id="1",
        away_team_id="2",
        referee="Árbitro real",
        status="PROGRAMADO",
        source_provider="football-data",
    )
    provider = FootballDataProvider("token", "https://example.test", 2)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: provider)
    monkeypatch.setattr(matches_service, "get_match", lambda match_id: match)
    monkeypatch.setattr(provider, "get_head_to_head", lambda *args, **kwargs: [])
    monkeypatch.setattr(provider, "get_team_last_matches", lambda *args, **kwargs: [])

    analysis = matches_service.get_analysis(match.id, use_external_ai=False)

    assert analysis is not None
    assert analysis.h2h_matches == []
    assert analysis.home_recent_matches == []
    assert analysis.away_recent_matches == []
    assert analysis.injuries == []
    assert analysis.referee_info is not None
    assert analysis.referee_info.name == "Árbitro real"
    assert analysis.referee_info.yellow_cards_avg is None


def test_football_data_analysis_routes_by_match_source_when_api_football_is_active(
    monkeypatch,
) -> None:
    match = MatchSummary(
        id="football-data-188",
        external_id="188",
        competition="Liga real",
        kickoff_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        home_team="Equipo real A",
        away_team="Equipo real B",
        home_team_id="11",
        away_team_id="12",
        status="PROGRAMADO",
        source_provider="football-data",
    )
    primary = APIFootballProvider(key="key")
    secondary = FootballDataProvider("token", "https://example.test", 2)
    h2h = secondary.normalize_history([_football_data_fixture(1)], 5)
    home_raw = [_football_data_fixture(20)]
    away_raw = [_football_data_fixture(21)]
    monkeypatch.setattr(matches_service, "get_match", lambda match_id: match)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "token")
    h2h_call = MagicMock(return_value=h2h)
    home_call = MagicMock(return_value=home_raw)
    away_call = MagicMock(return_value=away_raw)
    monkeypatch.setattr(secondary, "get_head_to_head", h2h_call)

    def team_history(team_id: str, limit: int = 5):
        return home_call(team_id, limit) if team_id == "11" else away_call(team_id, limit)

    monkeypatch.setattr(secondary, "get_team_last_matches", team_history)

    analysis = matches_service.get_analysis(match.id, use_external_ai=False)

    assert analysis is not None
    assert analysis.h2h_matches == h2h
    h2h_call.assert_called_once_with(match.id, 10)
    home_call.assert_called_once_with("11", 10)
    away_call.assert_called_once_with("12", 10)


def test_mock_analysis_exposes_three_visibly_demo_history_sections(monkeypatch) -> None:
    match = matches_service.mock_provider.list_highlights(date(2026, 8, 20))[0]
    monkeypatch.setattr(matches_service, "_active_provider", lambda: matches_service.mock_provider)
    monkeypatch.setattr(matches_service, "get_match", lambda match_id: match)

    analysis = matches_service.get_analysis(match.id, use_external_ai=False)

    assert analysis is not None
    assert len(analysis.h2h_matches) == 5
    assert len(analysis.home_recent_matches) == 5
    assert len(analysis.away_recent_matches) == 5
    all_history = analysis.h2h_matches + analysis.home_recent_matches + analysis.away_recent_matches
    assert all(item.competition.endswith("· demo") for item in all_history)
