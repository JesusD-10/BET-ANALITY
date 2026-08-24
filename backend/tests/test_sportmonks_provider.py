from datetime import date

import httpx
import pytest

from app.services.matches import FootballDataAPIError, FootballDataProvider
from app.services.sportmonks import SportmonksAPIError, SportmonksProvider


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


def test_football_data_keeps_finished_fixture_and_result(monkeypatch) -> None:
    payload = {
        "matches": [
            {
                "id": 124,
                "area": {"name": "Peru", "code": "PER"},
                "competition": {
                    "name": "Liga 1",
                    "emblem": "https://img.test/liga-1.svg",
                },
                "status": "FINISHED",
                "utcDate": "2026-07-24T19:30:00Z",
                "homeTeam": {"name": "Alianza Lima"},
                "awayTeam": {"name": "Universitario"},
                "score": {
                    "fullTime": {"home": 3, "away": 1},
                    "halfTime": {"home": 1, "away": 0},
                },
            }
        ]
    }

    def fake_get(*args, **kwargs):
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", "https://example.test"),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    matches = FootballDataProvider(
        "token", "https://api.football-data.org/v4", 15
    ).list_fixtures(date(2026, 7, 24))

    assert len(matches) == 1
    match = matches[0]
    assert match.status == "FINALIZADO"
    assert match.status_short == "FINISHED"
    assert (match.home_score, match.away_score) == (3, 1)
    assert (match.halftime_home_score, match.halftime_away_score) == (1, 0)
    assert match.country == "Peru"
    assert match.country_code == "PER"
    assert match.competition_logo == "https://img.test/liga-1.svg"


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


def _sportmonks_fixture(
    fixture_id: int = 9001,
    *,
    state: str = "NS",
    state_id: int = 1,
) -> dict:
    return {
        "id": fixture_id,
        "league": {"name": "Liga 1"},
        "state_id": state_id,
        "state": {"state": state},
        "venue": {"name": "Estadio Nacional"},
        "referees": [{"referee": {"name": "Kevin Ortega"}}],
        "starting_at_timestamp": 1786582800,
        "has_odds": True,
        # Deliberately reversed: roles must come from meta.location.
        "participants": [
            {
                "id": 22,
                "name": "Universitario",
                "image_path": "https://img.test/away.png",
                "meta": {"location": "away"},
            },
            {
                "id": 11,
                "name": "Alianza Lima",
                "image_path": "https://img.test/home.png",
                "meta": {"location": "home"},
            },
        ],
    }


def test_sportmonks_skips_malformed_fixtures_without_aborting_the_agenda(monkeypatch) -> None:
    valid = _sportmonks_fixture(9001)
    malformed = {
        "id": 9002,
        "league": {"name": "Liga 1"},
        "state_id": 1,
        "state": {"state": "NS"},
        "participants": [
            {
                "id": 19,
                "name": "Equipo raro",
                "image_path": "https://img.test/raro.png",
            }
        ],
    }

    def fake_get(url: str, **kwargs):
        return httpx.Response(
            200,
            json={"data": [valid, malformed], "pagination": {"has_more": False}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    fixtures = SportmonksProvider("token", timeout=15).list_fixtures(date(2026, 8, 12))

    assert [fixture.id for fixture in fixtures] == ["sportmonks-9001"]


def test_sportmonks_uses_bearer_header_and_maps_fixture(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"data": [_sportmonks_fixture()], "pagination": {"has_more": False}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = SportmonksProvider("private-token", timeout=15)

    fixtures = provider.list_fixtures(date(2026, 8, 12))

    assert captured["url"] == "https://api.sportmonks.com/v3/football/fixtures/date/2026-08-12"
    assert captured["headers"] == {
        "Authorization": "private-token",
        "Accept": "application/json",
    }
    assert "private-token" not in captured["url"]
    assert captured["params"]["timezone"] == "America/Lima"
    assert captured["params"]["per_page"] == 50
    assert captured["params"]["include"] == "participants;league.country;state;scores"
    assert fixtures[0].id == "sportmonks-9001"
    assert fixtures[0].external_id == "9001"
    assert fixtures[0].home_team == "Alianza Lima"
    assert fixtures[0].away_team == "Universitario"
    assert fixtures[0].home_team_id == "11"
    assert fixtures[0].away_team_id == "22"
    assert fixtures[0].source_provider == "sportmonks"
    assert fixtures[0].status == "PROGRAMADO"
    assert fixtures[0].odds_available is True
    assert fixtures[0].referee == "Kevin Ortega"


def test_sportmonks_maps_live_score_clock_and_halftime() -> None:
    raw = _sportmonks_fixture(
        9003,
        state="INPLAY_2ND_HALF",
        state_id=22,
    )
    raw["minute"] = 67
    raw["league"] = {
        "name": "Liga 1",
        "image_path": "https://img.test/liga-1.png",
        "country": {"name": "Peru", "iso2": "PE"},
    }
    raw["scores"] = [
        {
            "description": "CURRENT",
            "participant_id": 11,
            "score": {"goals": 2, "participant": "home"},
        },
        {
            "description": "CURRENT",
            "participant_id": 22,
            "score": {"goals": 1, "participant": "away"},
        },
        {
            "description": "1ST_HALF",
            "participant_id": 11,
            "score": {"goals": 1, "participant": "home"},
        },
        {
            "description": "1ST_HALF",
            "participant_id": 22,
            "score": {"goals": 1, "participant": "away"},
        },
    ]

    match = SportmonksProvider("token")._to_match_summary(raw)

    assert match.status == "EN JUEGO"
    assert match.status_short == "INPLAY_2ND_HALF"
    assert match.elapsed == 67
    assert (match.home_score, match.away_score) == (2, 1)
    assert (match.halftime_home_score, match.halftime_away_score) == (1, 1)
    assert match.country == "Peru"
    assert match.country_code == "PE"
    assert match.competition_logo == "https://img.test/liga-1.png"


def test_sportmonks_follows_pagination_without_duplicates(monkeypatch) -> None:
    requested_pages: list[int] = []

    def fake_get(url: str, **kwargs):
        page = kwargs["params"]["page"]
        requested_pages.append(page)
        data = [_sportmonks_fixture(9001)]
        if page == 2:
            data = [_sportmonks_fixture(9001), _sportmonks_fixture(9002)]
        return httpx.Response(
            200,
            json={"data": data, "pagination": {"has_more": page == 1}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    fixtures = SportmonksProvider("token", timeout=15).list_fixtures(
        date(2026, 8, 12)
    )

    assert requested_pages == [1, 2]
    assert [fixture.id for fixture in fixtures] == [
        "sportmonks-9001",
        "sportmonks-9002",
    ]


def test_sportmonks_get_fixture_routes_own_namespace(monkeypatch) -> None:
    captured: dict = {}
    finished = _sportmonks_fixture(77, state="FT", state_id=5)
    finished["minute"] = 90
    finished["scores"] = [
        {
            "description": "CURRENT",
            "participant_id": 11,
            "score": {"goals": 3, "participant": "home"},
        },
        {
            "description": "CURRENT",
            "participant_id": 22,
            "score": {"goals": 1, "participant": "away"},
        },
        {
            "description": "1ST_HALF",
            "participant_id": 11,
            "score": {"goals": 2, "participant": "home"},
        },
        {
            "description": "1ST_HALF",
            "participant_id": 22,
            "score": {"goals": 0, "participant": "away"},
        },
    ]

    def fake_get(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"data": finished},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = SportmonksProvider("token")

    assert provider.get_fixture("invalid") is None
    fixture = provider.get_fixture("sportmonks-77")

    assert fixture is not None
    assert fixture.id == "sportmonks-77"
    assert captured["url"].endswith("/fixtures/77")
    assert captured["params"]["timezone"] == "America/Lima"
    assert captured["params"]["include"] == (
        "participants;league.country;state;scores;venue;referees.type"
    )
    assert fixture.status == "FINALIZADO"
    assert fixture.status_short == "FT"
    assert fixture.elapsed == 90
    assert (fixture.home_score, fixture.away_score) == (3, 1)
    assert (fixture.halftime_home_score, fixture.halftime_away_score) == (2, 0)


def test_sportmonks_invalid_agenda_payload_is_typed_failure(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        return httpx.Response(
            200,
            json={"data": {}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(SportmonksAPIError):
        SportmonksProvider("token").list_fixtures(date(2026, 8, 12))


def test_sportmonks_history_uses_current_scores_and_discards_unfinished() -> None:
    finished = _sportmonks_fixture(51, state="FT", state_id=5)
    finished["scores"] = [
        {
            "description": "CURRENT",
            "participant_id": 22,
            "score": {"goals": 1, "participant": "away"},
        },
        {
            "description": "1ST_HALF",
            "participant_id": 11,
            "score": {"goals": 0, "participant": "home"},
        },
        {
            "description": "CURRENT",
            "participant_id": 11,
            "score": {"goals": 2, "participant": "home"},
        },
    ]
    scheduled = _sportmonks_fixture(52)
    scheduled["scores"] = finished["scores"]

    history = SportmonksProvider.normalize_history([scheduled, finished], 5)

    assert len(history) == 1
    assert history[0].score == "2 - 1"
    assert history[0].winner == "Alianza Lima"


def test_sportmonks_204_is_valid_empty_agenda(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        return httpx.Response(204, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert SportmonksProvider("token").list_fixtures(date(2026, 8, 12)) == []


def test_sportmonks_204_detail_is_not_found(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        return httpx.Response(204, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert SportmonksProvider("token").get_fixture("9001") is None


def test_sportmonks_skips_placeholder_without_discarding_valid_page(monkeypatch) -> None:
    placeholder = {"id": 1, "placeholder": True, "participants": []}

    def fake_get(url: str, **kwargs):
        return httpx.Response(
            200,
            json={
                "data": [placeholder, _sportmonks_fixture(2)],
                "pagination": {"has_more": False},
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    matches = SportmonksProvider("token").list_fixtures(date(2026, 8, 12))

    assert [match.id for match in matches] == ["sportmonks-2"]


def test_sportmonks_statistics_are_normalized_for_betting_evidence() -> None:
    fixture = _sportmonks_fixture(61, state="FT", state_id=5)
    fixture["statistics"] = [
        {
            "type_id": 34,
            "participant_id": 11,
            "location": "home",
            "data": {"value": 7},
        },
        {
            "type_id": 42,
            "participant_id": 11,
            "location": "home",
            "data": {"value": 14},
        },
        {
            "type_id": 86,
            "participant_id": 22,
            "location": "away",
            "data": {"value": 4},
        },
        {
            "type_id": 84,
            "participant_id": 22,
            "location": "away",
            "data": {"value": "3"},
        },
    ]

    normalized = SportmonksProvider._normalize_history_payload(fixture)

    assert normalized["fixture"]["date"].endswith("+00:00")
    assert normalized["competition"] == "Liga 1"
    assert normalized["teams"]["home"]["name"] == "Alianza Lima"
    assert normalized["teams"]["away"]["name"] == "Universitario"
    assert normalized["provider_statistics"] == fixture["statistics"]
    assert normalized["statistics"] == [
        {
            "participant_id": "11",
            "location": "home",
            "corners": 7,
            "total_shots": 14,
        },
        {
            "participant_id": "22",
            "location": "away",
            "shots_on_target": 4,
            "yellow_cards": 3,
        },
    ]


def test_sportmonks_team_history_respects_maximum_100_day_range(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(url: str, **kwargs):
        captured["url"] = url
        return httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    SportmonksProvider("token").get_team_last_matches("11")

    parts = captured["url"].split("/fixtures/between/", 1)[1].split("/")
    start = date.fromisoformat(parts[0])
    end = date.fromisoformat(parts[1])
    assert (end - start).days == 99


@pytest.mark.parametrize(
    ("state", "state_id", "expected"),
    [
        ("HT", 3, "EN PAUSA"),
        ("AET", 7, "FINALIZADO"),
        ("FT_PEN", 8, "FINALIZADO"),
        ("POSTPONED", 10, "POSPUESTO"),
        ("AWARDED", 17, "FINALIZADO"),
        ("DELAYED", 16, "RETRASADO"),
        ("AWAITING_UPDATES", 19, "PENDIENTE"),
        ("PENDING", 26, "PENDIENTE"),
        ("DELETED", 20, "ELIMINADO"),
        ("INPLAY_2ND_HALF", 22, "EN JUEGO"),
    ],
)
def test_sportmonks_uses_official_fixture_states(
    state: str,
    state_id: int,
    expected: str,
) -> None:
    assert SportmonksProvider._normalize_status(state, state_id) == expected


def test_sportmonks_selects_head_referee_even_when_not_first() -> None:
    referees = [
        {
            "name": "Assistant Official",
            "type": {"developer_name": "ASSISTANT_REFEREE"},
        },
        {
            "name": "Head Official",
            "type": {"developer_name": "HEAD_REFEREE"},
        },
    ]

    assert SportmonksProvider._referee_name(referees) == "Head Official"


def test_sportmonks_extracts_safe_subscription_plan_names() -> None:
    subscription = [
        {
            "plans": [
                {"plan": "Football Free Plan", "sport": "Football"},
                {"plan": "Football Free Plan", "sport": "Football"},
            ]
        }
    ]

    assert SportmonksProvider._subscription_plan_names(subscription) == [
        "Football Free Plan"
    ]
