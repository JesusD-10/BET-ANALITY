from datetime import date

import httpx
import pytest

from app.services.matches import FootballDataAPIError, FootballDataProvider


def test_football_data_maps_confirmed_fixture(monkeypatch) -> None:
    payload = {
        "matches": [{
            "id": 123,
            "competition": {"name": "Premier League"},
            "status": "SCHEDULED",
            "utcDate": "2026-07-24T19:30:00Z",
            "homeTeam": {"name": "Arsenal"},
            "awayTeam": {"name": "Chelsea"},
        }],
    }

    def fake_get(*args, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))

    monkeypatch.setattr(httpx, "get", fake_get)
    matches = FootballDataProvider("token", "https://api.football-data.org/v4", 15).list_fixtures(date(2026, 7, 24))

    assert matches[0].source_provider == "football-data"
    assert matches[0].external_id == "123"
    assert matches[0].home_team == "Arsenal"
    assert matches[0].away_team == "Chelsea"


def test_football_data_resolves_individual_fixture(monkeypatch) -> None:
    payload = {
        "id": 456,
        "competition": {"name": "Liga 1"},
        "status": "SCHEDULED",
        "utcDate": "2026-08-12T23:00:00Z",
        "homeTeam": {"id": 1, "name": "Alianza Lima"},
        "awayTeam": {"id": 2, "name": "Universitario"},
    }

    def fake_get(*args, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))

    monkeypatch.setattr(httpx, "get", fake_get)
    match = FootballDataProvider("token", "https://api.football-data.org/v4", 2).get_fixture(
        "football-data-456"
    )

    assert match is not None
    assert match.id == "football-data-456"
    assert match.home_team == "Alianza Lima"
    assert match.away_team == "Universitario"


def test_football_data_requests_utc_spillover_and_filters_to_lima_day(monkeypatch) -> None:
    payload = {
        "matches": [
            {
                "id": 1,
                "competition": {"name": "Liga 1"},
                "status": "SCHEDULED",
                # 23:30 on Aug 12 in Lima, although UTC is Aug 13.
                "utcDate": "2026-08-13T04:30:00Z",
                "homeTeam": {"name": "Local Lima"},
                "awayTeam": {"name": "Visita Lima"},
            },
            {
                "id": 2,
                "competition": {"name": "Liga 1"},
                "status": "SCHEDULED",
                # 23:30 on Aug 11 in Lima and must be excluded.
                "utcDate": "2026-08-12T04:30:00Z",
                "homeTeam": {"name": "Día anterior"},
                "awayTeam": {"name": "Día anterior 2"},
            },
        ]
    }
    captured: dict = {}

    def fake_get(endpoint: str, **kwargs):
        captured.update(kwargs)
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", endpoint),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    matches = FootballDataProvider(
        "token", "https://api.football-data.org/v4", 2
    ).list_fixtures(date(2026, 8, 12))

    assert captured["params"] == {"dateFrom": "2026-08-12", "dateTo": "2026-08-13"}
    assert [match.id for match in matches] == ["football-data-1"]


def test_football_data_missing_matches_field_is_typed_failure(monkeypatch) -> None:
    def fake_get(endpoint: str, **kwargs):
        return httpx.Response(
            200,
            json={"count": 0},
            request=httpx.Request("GET", endpoint),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(FootballDataAPIError):
        FootballDataProvider(
            "token", "https://api.football-data.org/v4", 2
        ).list_fixtures(date(2026, 8, 12))
