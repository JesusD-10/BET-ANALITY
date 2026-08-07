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
