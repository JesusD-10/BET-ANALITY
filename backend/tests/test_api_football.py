import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from app.services.api_football import APIFootballProvider
from app.schemas.matches import LineupsSummary, H2HMatchItem, InjuryItem


def test_api_football_headers():
    provider_direct = APIFootballProvider(key="test_key", is_rapidapi=False)
    assert provider_direct._get_headers() == {"x-apisports-key": "test_key"}

    provider_rapid = APIFootballProvider(key="rapid_key", is_rapidapi=True)
    assert provider_rapid._get_headers() == {
        "x-rapidapi-key": "rapid_key",
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
    }


@patch("httpx.get")
def test_list_fixtures_mapping(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": [
            {
                "fixture": {
                    "id": 1001,
                    "date": "2026-08-07T20:00:00+00:00",
                    "status": {"short": "NS"},
                    "referee": "Wilmar Roldán",
                    "venue": {"name": "Estadio El Campín"},
                },
                "league": {"name": "Liga BetPlay"},
                "teams": {
                    "home": {"id": 10, "name": "Millonarios"},
                    "away": {"id": 12, "name": "Santa Fe"},
                },
            }
        ]
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")
    fixtures = provider.list_fixtures(date(2026, 8, 7))

    assert len(fixtures) == 1
    m = fixtures[0]
    assert m.id == "api-football-1001"
    assert m.home_team == "Millonarios"
    assert m.away_team == "Santa Fe"
    assert m.competition == "Liga BetPlay"
    assert m.referee == "Wilmar Roldán"
    assert m.status == "PROGRAMADO"


@patch("httpx.get")
def test_get_head_to_head(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": [
            {
                "fixture": {"date": "2025-10-10T18:00:00+00:00"},
                "league": {"name": "Liga BetPlay"},
                "teams": {
                    "home": {"name": "Millonarios"},
                    "away": {"name": "Santa Fe"},
                },
                "goals": {"home": 2, "away": 0},
            }
        ]
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")
    h2h = provider.get_head_to_head("10", "12")

    assert len(h2h) == 1
    assert h2h[0].score == "2 - 0"
    assert h2h[0].winner == "Millonarios"


@patch("httpx.get")
def test_get_fixture_injuries(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": [
            {
                "player": {"name": "Radamel Falcao", "reason": "Molestia muscular", "type": "Lesión"},
                "team": {"name": "Millonarios"},
            }
        ]
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")
    injuries = provider.get_fixture_injuries("1001")

    assert len(injuries) == 1
    assert injuries[0].player == "Radamel Falcao"
    assert injuries[0].team == "Millonarios"
