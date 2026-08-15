import pytest
import httpx
from unittest.mock import MagicMock, patch
from datetime import date

from app.services.api_football import APIFootballAPIError, APIFootballProvider
from app.schemas.matches import H2HMatchItem, InjuryItem, LineupsSummary, PlayerLineup, TeamLineup


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
    assert secret not in repr(exc_info.value.as_envelope())
    assert exc_info.value.as_envelope()["endpoint"] == "fixtures"


@patch("httpx.get")
def test_rate_limit_is_cached_and_blocks_duplicate_network_call(mock_get):
    request = httpx.Request("GET", "https://v3.football.api-sports.io/fixtures")
    rate_response = httpx.Response(
        429,
        headers={
            "retry-after": "120",
            "x-ratelimit-requests-remaining": "0",
            "x-ratelimit-requests-limit": "100",
        },
        request=request,
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "provider detail must stay private",
        request=request,
        response=rate_response,
    )
    mock_get.return_value = mock_response
    provider = APIFootballProvider(key="dummy_key")

    with pytest.raises(APIFootballAPIError) as first_error:
        provider._request("fixtures", {"id": "1001"})
    with pytest.raises(APIFootballAPIError) as second_error:
        provider._request("fixtures", {"id": "1001"})

    assert mock_get.call_count == 1
    assert first_error.value is second_error.value
    assert first_error.value.code == "rate_limited"
    assert first_error.value.retryable is True
    assert first_error.value.cooldown_seconds == 120
    assert provider.quota_snapshot["remaining"] == 0
    assert provider.can_fetch_optional() is False


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
    assert mock_get.call_args.kwargs["params"] == {
        "h2h": "10-12",
        "last": "5",
    }


def test_head_to_head_excludes_unfinished_provider_rows(monkeypatch):
    finished = {
        "fixture": {"date": "2026-08-01T20:00:00+00:00", "status": {"short": "FT"}},
        "league": {"name": "Liga"},
        "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
        "goals": {"home": 2, "away": 1},
    }
    scheduled_with_placeholder_score = {
        "fixture": {"date": "2026-09-01T20:00:00+00:00", "status": {"short": "NS"}},
        "league": {"name": "Liga"},
        "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
        "goals": {"home": 0, "away": 0},
    }
    provider = APIFootballProvider(key="dummy_key")
    monkeypatch.setattr(
        provider,
        "_request",
        lambda endpoint, params=None: {"response": [scheduled_with_placeholder_score, finished]},
    )

    history = provider.get_head_to_head("10", "12")

    assert len(history) == 1
    assert history[0].date == "2026-08-01"


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
    assert lineups.confirmed is False
    assert lineups.status == "pending"
    assert lineups.home is not None
    assert lineups.home.team_name == "Millonarios"
    assert lineups.home.start_xi[0].name == "Titular"
    assert lineups.home.confirmed is False
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


def test_fixture_lineups_are_confirmed_only_with_formation_and_eleven_valid_starters(monkeypatch):
    provider = APIFootballProvider(key="dummy_key")

    def full_lineup(team_id: int, team_name: str) -> dict:
        return {
            "team": {"id": team_id, "name": team_name},
            "formation": "4-3-3",
            "startXI": [
                {"player": {"id": team_id * 100 + index, "name": f"{team_name} {index}", "pos": "M"}}
                for index in range(1, 12)
            ],
            "substitutes": [],
        }

    monkeypatch.setattr(
        provider,
        "_request",
        lambda endpoint, params=None: {
            "response": [full_lineup(10, "Local"), full_lineup(12, "Visitante")]
        },
    )

    lineups = provider.get_fixture_lineups("1001", home_team_id="10", away_team_id="12")

    assert lineups.confirmed is True
    assert lineups.status == "confirmed"
    assert lineups.home is not None and lineups.home.confirmed is True
    assert lineups.away is not None and lineups.away.confirmed is True
    assert len(lineups.home.start_xi) == 11


def test_probable_lineup_uses_most_common_formation_and_regular_starters():
    provider = APIFootballProvider(key="dummy_key")

    def historical_lineup(formation: str, player_ids: list[int]) -> dict:
        return {
            "lineups": [
                {
                    "team": {"id": 10, "name": "Local"},
                    "formation": formation,
                    "coach": {"name": "DT habitual"},
                    "startXI": [
                        {
                            "player": {
                                "id": player_id,
                                "name": f"Jugador {player_id}",
                                "number": player_id,
                                "pos": "M",
                            }
                        }
                        for player_id in player_ids
                    ],
                }
            ]
        }

    home_history = [
        historical_lineup("4-3-3", list(range(1, 12))),
        historical_lineup("4-4-2", list(range(1, 11)) + [20]),
        historical_lineup("4-3-3", list(range(1, 12))),
    ]

    lineups = provider.get_probable_lineups(
        home_history,
        [],
        home_team_id="10",
        away_team_id="12",
        home_team_name="Local",
        away_team_name="Visitante",
    )

    assert lineups.confirmed is False
    assert lineups.status == "probable"
    assert lineups.home is not None
    assert lineups.home.formation == "4-3-3"
    assert lineups.home.sample_size == 3
    assert lineups.home.source == "recent_form"
    assert [player.id for player in lineups.home.start_xi] == list(range(1, 12))
    assert lineups.away is None


def test_probable_lineup_excludes_confirmed_absence_and_uses_next_regular_starter():
    provider = APIFootballProvider(key="dummy_key")

    def historical_lineup(player_ids: list[int]) -> dict:
        return {
            "lineups": [
                {
                    "team": {"id": 10, "name": "Local"},
                    "formation": "4-3-3",
                    "startXI": [
                        {
                            "player": {
                                "id": player_id,
                                "name": f"Jugador {player_id}",
                                "number": player_id,
                                "pos": "M",
                            }
                        }
                        for player_id in player_ids
                    ],
                }
            ]
        }

    history = [
        historical_lineup(list(range(1, 12))),
        historical_lineup(list(range(1, 11)) + [12]),
    ]
    lineups = provider.get_probable_lineups(
        history,
        [],
        home_team_id="10",
        away_team_id="12",
        home_team_name="Local",
        away_team_name="Visitante",
        injuries=[
            InjuryItem(
                player="Jugador 1",
                team="Local",
                reason="Lesión muscular",
                status="Baja confirmada",
            )
        ],
    )

    assert lineups.home is not None
    selected_ids = [player.id for player in lineups.home.start_xi]
    assert 1 not in selected_ids
    assert 12 in selected_ids
    assert len(selected_ids) == 11


def test_merge_lineups_keeps_team_level_confirmation_and_probable_opponent():
    provider = APIFootballProvider(key="dummy_key")
    confirmed_home = TeamLineup(
        team_name="Local",
        formation="4-3-3",
        start_xi=[PlayerLineup(name=f"Local {index}") for index in range(11)],
        confirmed=True,
        source="api_football",
    )
    probable_away = TeamLineup(
        team_name="Visitante",
        formation="4-4-2",
        start_xi=[PlayerLineup(name=f"Visitante {index}") for index in range(11)],
        source="recent_form",
        sample_size=5,
    )

    merged = provider.merge_lineups(
        LineupsSummary(home=confirmed_home, status="partial"),
        LineupsSummary(away=probable_away, status="probable"),
    )

    assert merged.confirmed is False
    assert merged.status == "partial"
    assert merged.home is confirmed_home
    assert merged.away is probable_away


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
        ("fixtures", {"team": "10", "last": "10", "status": "FT-AET-PEN"}),
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
        "lineups": [{"team": {"id": 10}, "formation": "4-3-3", "startXI": []}],
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

    assert calls == [("fixtures", {"team": "10", "last": "5", "status": "FT-AET-PEN"})]
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
        ("fixtures", {"team": "10", "last": "5", "status": "FT-AET-PEN"}),
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


def test_completed_history_never_retries_provider_error_without_status(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def rejected(endpoint, params=None):
        calls.append((endpoint, params))
        raise APIFootballAPIError(
            "cuota agotada",
            endpoint=endpoint,
            code="quota_exhausted",
            cooldown_seconds=300,
        )

    monkeypatch.setattr(provider, "_request", rejected)

    with pytest.raises(APIFootballAPIError, match="cuota agotada"):
        provider.get_team_last_matches("10")

    assert calls == [
        ("fixtures", {"team": "10", "last": "5", "status": "FT-AET-PEN"})
    ]


def test_quota_headers_are_exposed_and_league_catalog_is_cached(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None, timeout=None):
        calls.append((endpoint, params))
        provider._capture_quota(
            {
                "x-ratelimit-requests-remaining": "19",
                "x-ratelimit-requests-limit": "100",
            }
        )
        return {
            "response": [
                {
                    "league": {"id": 71, "name": "Serie A"},
                    "country": {"name": "Brazil"},
                    "seasons": [
                        {
                            "year": 2026,
                            "start": "2026-01-01",
                            "end": "2026-12-01",
                            "current": True,
                            "coverage": {
                                "fixtures": {
                                    "statistics_fixtures": True,
                                    "events": True,
                                    "lineups": True,
                                    "statistics_players": True,
                                },
                                "standings": True,
                                "players": True,
                                "injuries": True,
                                "predictions": True,
                                "odds": True,
                            },
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    first = provider.get_league_coverage("71", 2026)
    second = provider.get_league_coverage("71", 2026)

    assert calls == [("leagues", {"id": "71", "season": 2026})]
    assert first == second
    assert first is not None
    assert first["league"]["id"] == "71"
    assert first["coverage"]["fixtures"]["statistics_players"] is True
    assert provider.quota_snapshot["remaining"] == 19
    assert provider.quota_snapshot["limit"] == 100
    assert provider.can_fetch_optional(reserve=10) is True
    assert provider.can_fetch_optional(reserve=20) is False


def test_free_status_call_updates_daily_remaining_quota_and_is_cached(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {
            "response": {
                "account": {"email": "hidden@example.invalid"},
                "subscription": {"active": True},
                "requests": {"current": 99, "limit_day": 100},
            }
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    first = provider.get_status()
    second = provider.get_status()

    assert first == second
    assert calls == [("status", {})]
    assert provider.quota_snapshot["remaining"] == 1
    assert provider.quota_snapshot["limit"] == 100
    assert provider.can_fetch_optional(reserve=1) is False


def test_team_statistics_normalize_full_prematch_blocks_and_date(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")
    raw = {
        "league": {"id": 71, "name": "Serie A", "season": 2026},
        "team": {"id": 10, "name": "Local"},
        "form": "WWDLW",
        "fixtures": {
            "played": {"home": 4, "away": 3, "total": 7},
            "wins": {"home": 3, "away": 1, "total": 4},
            "draws": {"home": 1, "away": 1, "total": 2},
            "loses": {"home": 0, "away": 1, "total": 1},
        },
        "goals": {
            "for": {
                "total": {"home": 8, "away": 4, "total": 12},
                "average": {"home": "2.0", "away": "1.3", "total": "1.7"},
                "minute": {"0-15": {"total": 2, "percentage": "16.67%"}},
                "under_over": {"2.5": {"over": 4, "under": 3}},
            },
            "against": {
                "total": {"home": 2, "away": 4, "total": 6},
                "average": {"home": "0.5", "away": "1.3", "total": "0.9"},
                "minute": {},
                "under_over": {},
            },
        },
        "biggest": {"streak": {"wins": 3, "draws": 1, "loses": 1}},
        "clean_sheet": {"home": 2, "away": 1, "total": 3},
        "failed_to_score": {"home": 0, "away": 1, "total": 1},
        "penalty": {"scored": {"total": 2}, "missed": {"total": 1}, "total": 3},
        "lineups": [{"formation": "4-3-3", "played": 5}],
        "cards": {"yellow": {"0-15": {"total": None, "percentage": None}}},
    }

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {"response": raw}

    monkeypatch.setattr(provider, "_request", fake_request)

    stats = provider.get_team_statistics("10", "71", 2026, through_date=date(2026, 8, 20))

    assert calls == [
        (
            "teams/statistics",
            {"team": "10", "league": "71", "season": 2026, "date": "2026-08-20"},
        )
    ]
    assert stats is not None
    assert stats["team"]["id"] == "10"
    assert stats["fixtures"]["losses"]["total"] == 1
    assert stats["goals_for"]["average"]["total"] == 1.7
    assert stats["goals_for"]["over_under"]["2.5"]["over"] == 4
    assert stats["clean_sheets"]["total"] == 3
    assert stats["cards"]["yellow"]["0-15"]["total"] is None


def test_standings_and_prediction_have_stable_canonical_shapes(monkeypatch) -> None:
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None):
        if endpoint == "standings":
            return {
                "response": [
                    {
                        "league": {
                            "id": 71,
                            "name": "Serie A",
                            "season": 2026,
                            "standings": [
                                [
                                    {
                                        "rank": 1,
                                        "team": {"id": 10, "name": "Local"},
                                        "points": 33,
                                        "goalsDiff": 12,
                                        "group": "Serie A",
                                        "form": "WWDLW",
                                        "status": "same",
                                        "description": None,
                                        "all": {
                                            "played": 15,
                                            "win": 10,
                                            "draw": 3,
                                            "lose": 2,
                                            "goals": {"for": 30, "against": 18},
                                        },
                                        "home": None,
                                        "away": None,
                                        "update": "2026-08-10",
                                    }
                                ]
                            ],
                        }
                    }
                ]
            }
        return {
            "response": [
                {
                    "predictions": {
                        "winner": {"id": 10, "name": "Local", "comment": "Win or draw"},
                        "win_or_draw": True,
                        "under_over": "-3.5",
                        "goals": {"home": "2.1", "away": None},
                        "advice": "Double chance : Local or draw",
                        "percent": {"home": "45%", "draw": "40%", "away": "15%"},
                    },
                    "comparison": {
                        "form": {"home": "60%", "away": "40%"},
                        "total": {"home": "65.4%", "away": "34.6%"},
                    },
                    "league": {"id": 71, "name": "Serie A"},
                    "teams": {"home": {"id": 10}, "away": {"id": 12}},
                }
            ]
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    standings = provider.get_standings("71", 2026)
    prediction = provider.get_prediction("1001")

    row = standings["groups"][0]["table"][0]
    assert row["rank"] == 1
    assert row["goal_difference"] == 12
    assert row["overall"] == {
        "played": 15,
        "wins": 10,
        "draws": 3,
        "losses": 2,
        "goals_for": 30,
        "goals_against": 18,
    }
    assert row["home"] is None
    assert prediction is not None
    assert prediction["winner"]["id"] == "10"
    assert prediction["expected_goals"] == {"home": 2.1, "away": None}
    assert prediction["percentages"] == {"home": 45.0, "draw": 40.0, "away": 15.0}
    assert prediction["comparison"]["total"]["home"] == 65.4


def test_fixture_and_player_context_normalize_all_available_metrics(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        if endpoint == "fixtures/statistics":
            return {
                "response": [
                    {
                        "team": {"id": 10, "name": "Local"},
                        "statistics": [
                            {"type": "Shots on Goal", "value": 7},
                            {"type": "Ball Possession", "value": "58%"},
                            {"type": "Red Cards", "value": None},
                        ],
                    }
                ]
            }
        if endpoint == "players":
            return {
                "paging": {"current": 1, "total": 1},
                "response": [
                    {
                        "player": {"id": 9, "name": "Delantero"},
                        "statistics": [
                            {
                                "team": {"id": 10, "name": "Local"},
                                "league": {"id": 71, "name": "Serie A"},
                                "games": {"appearences": 12, "minutes": 900, "rating": "7.25"},
                                "shots": {"total": 30, "on": 18},
                                "goals": {"total": 8, "assists": 4},
                                "cards": {"yellow": 2, "red": None},
                            }
                        ],
                    }
                ],
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(provider, "_request", fake_request)

    fixture_stats = provider.get_fixture_statistics("1001")
    players = provider.get_player_statistics("10", "71", 2026)

    assert fixture_stats[0]["metrics"] == {
        "shots_on_target": 7,
        "possession_percentage": 58.0,
        "red_cards": None,
    }
    player = players["items"][0]
    assert player["player"]["id"] == "9"
    assert player["games"]["rating"] == 7.25
    assert player["goals"] == {"total": 8, "assists": 4}
    assert player["cards"]["red"] is None
    assert calls == [
        ("fixtures/statistics", {"fixture": "1001"}),
        ("players", {"team": "10", "league": "71", "season": 2026, "page": 1}),
    ]


def test_fixture_batch_chunks_ids_and_odds_catalogs_are_cacheable(monkeypatch) -> None:
    calls = []
    provider = APIFootballProvider(key="dummy_key")

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        if endpoint == "fixtures":
            ids = params["ids"].split("-")
            return {"response": [{"fixture": {"id": int(item)}} for item in ids]}
        return {"response": [{"id": 1, "name": "Match Winner"}]}

    monkeypatch.setattr(provider, "_request", fake_request)
    ids = list(range(1, 22))

    fixtures = provider.get_fixtures_by_ids(ids)
    first_markets = provider.get_odds_markets()
    second_markets = provider.get_odds_markets()

    assert len(fixtures) == 21
    assert first_markets == second_markets
    fixture_calls = [call for call in calls if call[0] == "fixtures"]
    assert len(fixture_calls) == 2
    assert fixture_calls[0][1]["ids"] == "-".join(str(item) for item in range(1, 21))
    assert fixture_calls[1][1]["ids"] == "21"
    assert [call for call in calls if call[0] == "odds/bets"] == [
        ("odds/bets", {})
    ]
