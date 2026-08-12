import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from app.services.api_football import APIFootballAPIError, APIFootballProvider
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
    assert mock_get.call_args.kwargs["params"] == {
        "date": "2026-08-07",
        "timezone": "America/Lima",
    }


@patch("httpx.get")
def test_request_treats_http_204_as_an_empty_valid_response(mock_get):
    mock_response = MagicMock(status_code=204)
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")

    assert provider._request("fixtures", {"date": "2026-08-07"})["response"] == []
    mock_response.json.assert_not_called()


@patch("httpx.get")
def test_get_fixture_uses_individual_lookup_and_shared_mapping(mock_get):
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
                    "home": {"id": 10, "name": "Millonarios", "logo": "home.png"},
                    "away": {"id": 12, "name": "Santa Fe", "logo": "away.png"},
                },
            }
        ]
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")
    fixture = provider.get_fixture("api-football-1001")

    assert fixture is not None
    assert fixture.id == "api-football-1001"
    assert fixture.external_id == "1001"
    assert fixture.home_team == "Millonarios"
    assert fixture.away_team == "Santa Fe"
    assert fixture.home_team_id == "10"
    assert fixture.away_team_id == "12"
    assert fixture.source_provider == "api-football"
    assert fixture.odds_available is False
    assert fixture.source_url == "https://v3.football.api-sports.io/fixtures?id=1001"
    assert mock_get.call_args.kwargs["params"] == {"id": "1001"}


@patch("httpx.get")
def test_get_fixture_returns_none_when_lookup_has_no_response(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": []}
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")

    assert provider.get_fixture("1001") is None


@patch("httpx.get")
def test_get_fixture_rejects_invalid_identifier_without_request(mock_get):
    provider = APIFootballProvider(key="dummy_key")

    assert provider.get_fixture("api-football-invalid") is None
    mock_get.assert_not_called()


@patch("httpx.get")
def test_payload_errors_raise_without_exposing_provider_message(mock_get):
    secret = "sensitive-provider-detail"
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "errors": {"token": secret},
        "response": [],
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")

    with pytest.raises(APIFootballAPIError, match="API-Football rechazó la solicitud") as exc_info:
        provider.list_fixtures(date(2026, 8, 7))

    assert secret not in str(exc_info.value)


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
                "player": {"name": "Radamel Falcao", "reason": "Molestia muscular", "type": "Missing Fixture"},
                "team": {"name": "Millonarios"},
            },
            {
                "player": {"name": "Jugador en duda", "reason": "Golpe", "type": "Questionable"},
                "team": {"name": "Santa Fe"},
            }
        ]
    }
    mock_get.return_value = mock_response

    provider = APIFootballProvider(key="dummy_key")
    injuries = provider.get_fixture_injuries("1001")

    assert len(injuries) == 2
    assert injuries[0].player == "Radamel Falcao"
    assert injuries[0].team == "Millonarios"
    assert injuries[0].status == "Baja confirmada"
    assert injuries[1].status == "Duda"


def test_get_fixture_lineups_uses_exact_api_sports_endpoint(monkeypatch):
    calls = []

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {
            "response": [
                {
                    "team": {"id": 10, "name": "Millonarios"},
                    "formation": "4-2-3-1",
                    "coach": {"name": "Entrenador local"},
                    "startXI": [
                        {"player": {"id": 7, "name": "Titular", "number": 9, "pos": "F", "grid": "1:1"}}
                    ],
                    "substitutes": [],
                },
                {
                    "team": {"id": 12, "name": "Santa Fe"},
                    "formation": "4-3-3",
                    "coach": {"name": "Entrenador visitante"},
                    "startXI": [],
                    "substitutes": [],
                },
            ]
        }

    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(provider, "_request", fake_request)

    lineups = provider.get_fixture_lineups("api-football-1001")

    assert calls == [("fixtures/lineups", {"fixture": "1001"})]
    assert lineups.confirmed is True
    assert lineups.home is not None
    assert lineups.home.team_name == "Millonarios"
    assert lineups.home.start_xi[0].name == "Titular"
    assert lineups.away is not None
    assert lineups.away.team_name == "Santa Fe"


def test_get_fixture_lineups_uses_team_ids_when_provider_order_is_reversed(monkeypatch):
    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(
        provider,
        "_request",
        lambda endpoint, params=None: {
            "response": [
                {"team": {"id": 12, "name": "Visitante"}, "startXI": [], "substitutes": []},
                {"team": {"id": 10, "name": "Local"}, "startXI": [], "substitutes": []},
            ]
        },
    )

    lineups = provider.get_fixture_lineups("1001", home_team_id="10", away_team_id="12")

    assert lineups.home is not None
    assert lineups.home.team_name == "Local"
    assert lineups.away is not None
    assert lineups.away.team_name == "Visitante"


def test_recent_matches_use_one_batch_and_normalize_statistics(monkeypatch):
    calls = []
    base_items = [
        {
            "fixture": {"id": 1001, "date": "2026-08-01T20:00:00+00:00"},
            "league": {"name": "Liga", "base_only": True},
            "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
            "goals": {"home": 1, "away": 0},
        },
        {
            "fixture": {"id": 1002, "date": "2026-08-03T20:00:00+00:00"},
            "league": {"name": "Liga", "base_only": True},
            "teams": {"home": {"name": "C"}, "away": {"name": "A"}},
            "goals": {"home": 2, "away": 2},
        },
    ]
    batch_items = [
        {
            "fixture": {"id": 1001},
            "events": [{"type": "Goal"}],
            "statistics": [],
            "players": [],
        },
        {
            "fixture": {"id": 1002},
            "events": [{"type": "Card"}],
            "statistics": [
                {
                    "team": {"id": 30, "name": "C"},
                    "statistics": [
                        {"type": "Corner Kicks", "value": "7"},
                        {"type": "Total Shots", "value": 14},
                        {"type": "Shots on Goal", "value": 6},
                        {"type": "Yellow Cards", "value": 3},
                        {"type": "Red Cards", "value": None},
                        {"type": "Fouls", "value": 11},
                        {"type": "Ball Possession", "value": "55%"},
                    ],
                }
            ],
            "players": [
                {
                    "team": {"id": 30, "name": "C"},
                    "players": [
                        {
                            "player": {"id": 99, "name": "Delantero"},
                            "statistics": [
                                {"shots": {"total": "4", "on": 2}, "goals": {"total": 1, "assists": 0}}
                            ],
                        }
                    ],
                }
            ],
        },
    ]

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        if params and "team" in params:
            return {"response": base_items}
        return {"response": batch_items}

    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(provider, "_request", fake_request)

    history = provider.get_team_last_matches("10", limit=20)

    assert calls == [
        ("fixtures", {"team": "10", "last": "5"}),
        ("fixtures", {"ids": "1002-1001"}),
    ]
    assert [item["fixture"]["id"] for item in history] == [1002, 1001]
    assert history[0]["league"]["base_only"] is True
    assert history[0]["events"] == [{"type": "Card"}]
    assert history[0]["statistics"] == [
        {
            "team": {"id": 30, "name": "C"},
            "corners": 7,
            "total_shots": 14,
            "shots_on_target": 6,
            "yellow_cards": 3,
            "fouls": 11,
        }
    ]
    assert history[0]["provider_statistics"] == batch_items[1]["statistics"]
    assert history[0]["player_statistics"] == [
        {
            "player": {"id": 99, "name": "Delantero"},
            "team": {"id": 30, "name": "C"},
            "shots": {"total": 4, "on_target": 2},
            "goals": {"total": 1},
        }
    ]
    assert history[0]["players"] == batch_items[1]["players"]


def test_recent_matches_skip_batch_when_base_is_already_enriched(monkeypatch):
    calls = []
    enriched = {
        "fixture": {"id": 1001, "date": "2026-08-01T20:00:00+00:00"},
        "statistics": [
            {
                "team": {"id": 10, "name": "A"},
                "statistics": [{"type": "Corner Kicks", "value": 5}],
            }
        ],
        "players": [
            {
                "team": {"id": 10, "name": "A"},
                "players": [
                    {
                        "player": {"id": 8, "name": "Atacante"},
                        "statistics": [{"shots": {"total": 2, "on": 1}, "goals": {"total": 0}}],
                    }
                ],
            }
        ],
    }

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {"response": [enriched]}

    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(provider, "_request", fake_request)

    history = provider.get_team_last_matches("10")

    assert calls == [("fixtures", {"team": "10", "last": "5"})]
    assert history[0]["statistics"][0]["corners"] == 5
    assert history[0]["player_statistics"][0]["shots"]["on_target"] == 1


def test_recent_matches_fall_back_to_base_when_batch_fails(monkeypatch):
    calls = []
    base = {
        "fixture": {"id": 1001, "date": "2026-08-01T20:00:00+00:00"},
        "league": {"name": "Liga"},
        "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
        "goals": {"home": 1, "away": 0},
    }

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        if params and "ids" in params:
            raise APIFootballAPIError("fallo controlado")
        return {"response": [base]}

    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(provider, "_request", fake_request)

    history = provider.get_team_last_matches("10")

    assert calls == [
        ("fixtures", {"team": "10", "last": "5"}),
        ("fixtures", {"ids": "1001"}),
    ]
    assert history == [base]


def test_two_team_histories_share_one_batch_request(monkeypatch):
    calls = []
    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(
        provider,
        "_request",
        lambda endpoint, params=None: (
            calls.append((endpoint, params))
            or {
                "response": [
                    {"fixture": {"id": 1001}, "statistics": [], "players": []},
                    {"fixture": {"id": 2001}, "statistics": [], "players": []},
                ]
            }
        ),
    )
    home = [{"fixture": {"id": 1001, "date": "2026-08-01T20:00:00+00:00"}}]
    away = [{"fixture": {"id": 2001, "date": "2026-08-02T20:00:00+00:00"}}]

    enriched_home, enriched_away = provider.enrich_fixture_histories(home, away)

    assert calls == [("fixtures", {"ids": "1001-2001"})]
    assert enriched_home[0]["fixture"]["id"] == 1001
    assert enriched_away[0]["fixture"]["id"] == 2001


def test_batch_empty_blocks_do_not_erase_existing_statistics(monkeypatch):
    base = {
        "fixture": {"id": 1001, "date": "2026-08-01T20:00:00+00:00"},
        "statistics": [
            {
                "team": {"id": 10, "name": "A"},
                "statistics": [{"type": "Total Shots", "value": 9}],
            }
        ],
        "players": [],
    }

    def fake_request(endpoint, params=None):
        if params and "ids" in params:
            return {"response": [{"fixture": {"id": 1001}, "statistics": [], "players": []}]}
        return {"response": [base]}

    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(provider, "_request", fake_request)

    history = provider.get_team_last_matches("10")

    assert history[0]["statistics"][0]["total_shots"] == 9


def test_null_statistics_remain_unavailable_instead_of_becoming_zero() -> None:
    payload = {
        "statistics": [
            {
                "team": {"id": 10, "name": "A"},
                "statistics": [
                    {"type": "Corner Kicks", "value": None},
                    {"type": "Total Shots", "value": 8},
                ],
            }
        ],
        "players": [
            {
                "team": {"id": 10, "name": "A"},
                "players": [
                    {
                        "player": {"id": 7, "name": "Atacante"},
                        "statistics": [{"shots": {"total": None, "on": 2}, "goals": {"total": None}}],
                    }
                ],
            }
        ],
    }

    normalized = APIFootballProvider._normalize_history_payload(payload)

    assert normalized["statistics"] == [{"team": {"id": 10, "name": "A"}, "total_shots": 8}]
    assert normalized["player_statistics"] == [
        {
            "player": {"id": 7, "name": "Atacante"},
            "team": {"id": 10, "name": "A"},
            "shots": {"on_target": 2},
        }
    ]


def test_all_documented_fixture_statuses_are_not_misreported_as_scheduled() -> None:
    expected = {
        "BT": "DESCANSO",
        "SUSP": "SUSPENDIDO",
        "INT": "INTERRUMPIDO",
        "AWD": "FINALIZADO (DECISIÓN TÉCNICA)",
        "WO": "FINALIZADO (WALKOVER)",
        "LIVE": "EN JUEGO",
    }

    assert {
        status: APIFootballProvider._normalize_status(status)
        for status in expected
    } == expected


def test_prematch_odds_keep_best_exact_quote_and_cache_response(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {
            "response": [
                {
                    "update": "2026-08-12T12:00:00+00:00",
                    "bookmakers": [
                        {
                            "name": "Casa A",
                            "bets": [
                                {
                                    "name": "Goals Over/Under",
                                    "values": [
                                        {"value": "Over 2.5", "odd": "1.90"},
                                        {"value": "Over 3.5", "odd": "2.80"},
                                    ],
                                },
                                {
                                    "name": "Both Teams Score",
                                    "values": [
                                        {"value": "Yes", "odd": "1.75"},
                                        {"value": "No", "odd": "2.00"},
                                    ],
                                },
                            ],
                        },
                        {
                            "name": "Casa B",
                            "bets": [
                                {
                                    "name": "Goals Over/Under",
                                    "values": [
                                        {"value": "Over 2.5", "odd": "2.05"},
                                        {"value": "Over 3.5", "odd": "NaN"},
                                    ],
                                },
                                {
                                    "name": "Mercado desconocido",
                                    "values": [{"value": "Algo", "odd": "9.0"}],
                                },
                            ],
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    first = provider.get_fixture_odds("api-football-1001")
    second = provider.get_fixture_odds("1001")

    assert calls == [("odds", {"fixture": "1001"})]
    assert first == second
    assert first["TOTAL_GOALS_OVER_2_5"].odds == 2.05
    assert first["TOTAL_GOALS_OVER_2_5"].bookmaker == "Casa B"
    assert first["TOTAL_GOALS_OVER_3_5"].odds == 2.8
    assert first["BOTH_TEAMS_TO_SCORE"].odds == 1.75
    assert len(first) == 3


@pytest.mark.parametrize(
    ("bet", "selection", "expected"),
    [
        ("Match Winner", "Home", "WINNER_HOME"),
        ("Double Chance", "Draw/Away", "DOUBLE_CHANCE_AWAY_DRAW"),
        ("Corners Over Under", "Over 9.5", "TOTAL_CORNERS_OVER_9_5"),
        ("Home Corners Over/Under", "Under 4.5", "TEAM_CORNERS_HOME_UNDER_4_5"),
        ("Goals Over/Under - First Half", "Over 0.5", None),
        ("Goals Over/Under", "Over invalid", None),
    ],
)
def test_prematch_market_mapping_is_exact(bet, selection, expected) -> None:
    assert APIFootballProvider._prematch_market_key(bet, selection) == expected
