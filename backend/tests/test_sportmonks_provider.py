from datetime import date

import httpx

from app.services.matches import FootballDataProvider


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
