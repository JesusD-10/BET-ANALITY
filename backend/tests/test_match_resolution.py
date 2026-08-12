from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock

import pytest

from app.schemas.matches import MatchSummary
from app.services import matches as matches_service
from app.services.api_football import APIFootballProvider
from app.services.sportmonks import SportmonksProvider


SELECTED_DATE = date(2026, 8, 12)


def _external_match(
    fixture_id: str = "api-football-1001",
    home_team: str = "Millonarios",
    away_team: str = "Santa Fe",
) -> MatchSummary:
    return MatchSummary(
        id=fixture_id,
        external_id=fixture_id.removeprefix("api-football-"),
        competition="Liga BetPlay",
        kickoff_at=datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc),
        home_team=home_team,
        away_team=away_team,
        data_quality=0.95,
        odds_available=True,
        status="PROGRAMADO",
        source_provider="api-football",
    )


def _football_data_match(fixture_id: str = "football-data-2001") -> MatchSummary:
    return MatchSummary(
        id=fixture_id,
        external_id=fixture_id.removeprefix("football-data-"),
        competition="Liga real de respaldo",
        kickoff_at=datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc),
        home_team="Alianza Lima",
        away_team="Universitario",
        home_team_id="101",
        away_team_id="102",
        data_quality=0.92,
        odds_available=False,
        status="PROGRAMADO",
        source_provider="football-data",
    )


def _sportmonks_match(fixture_id: str = "sportmonks-1501") -> MatchSummary:
    return MatchSummary(
        id=fixture_id,
        external_id=fixture_id.removeprefix("sportmonks-"),
        competition="Liga real de Sportmonks",
        kickoff_at=datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc),
        home_team="Sporting Cristal",
        away_team="Melgar",
        home_team_id="201",
        away_team_id="202",
        data_quality=0.96,
        odds_available=False,
        status="PROGRAMADO",
        source_provider="sportmonks",
    )


@pytest.fixture(autouse=True)
def clear_match_resolution_state():
    matches_service._fixture_cache.clear()
    matches_service._fixture_by_id.clear()
    matches_service._provider_retry_after.clear()
    yield
    matches_service._fixture_cache.clear()
    matches_service._fixture_by_id.clear()
    matches_service._provider_retry_after.clear()


@pytest.fixture
def external_provider(monkeypatch) -> APIFootballProvider:
    provider = APIFootballProvider(key="test-key")
    monkeypatch.setattr(matches_service, "_active_provider", lambda: provider)
    return provider


def test_successful_external_highlights_index_every_fixture(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    fixtures = [
        _external_match(),
        _external_match(
            fixture_id="api-football-1002",
            home_team="Alianza Lima",
            away_team="Universitario",
        ),
    ]
    list_fixtures = MagicMock(return_value=fixtures)
    monkeypatch.setattr(external_provider, "list_fixtures", list_fixtures)

    resolved = matches_service.get_highlights(SELECTED_DATE)

    assert resolved == fixtures
    assert set(matches_service._fixture_by_id) == {fixture.id for fixture in fixtures}
    assert all(
        matches_service._fixture_by_id[fixture.id][1] is fixture
        for fixture in fixtures
    )
    list_fixtures.assert_called_once_with(SELECTED_DATE)


def test_external_failure_returns_stale_real_fixtures_never_mock(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(matches_service.time, "monotonic", lambda: clock["now"])
    real_fixture = _external_match()
    list_fixtures = MagicMock(return_value=[real_fixture])
    monkeypatch.setattr(external_provider, "list_fixtures", list_fixtures)

    assert matches_service.get_highlights(SELECTED_DATE) == [real_fixture]

    clock["now"] += matches_service._FIXTURE_CACHE_TTL_SECONDS + 1
    list_fixtures.side_effect = TimeoutError("upstream temporarily slow")

    stale = matches_service.get_highlights_result(SELECTED_DATE)

    assert stale.matches == [real_fixture]
    assert stale.source == "api-football"
    assert stale.notice is not None
    assert all(match.source_provider != "mock" for match in stale.matches)
    assert list_fixtures.call_count == 2


def test_external_failure_without_stale_returns_empty_and_never_calls_mock(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    list_fixtures = MagicMock(side_effect=TimeoutError("upstream unavailable"))
    monkeypatch.setattr(
        external_provider,
        "list_fixtures",
        list_fixtures,
    )
    mock_list = MagicMock(side_effect=AssertionError("mock fallback must not run"))
    monkeypatch.setattr(matches_service.mock_provider, "list_highlights", mock_list)

    result = matches_service.get_highlights_result(SELECTED_DATE)
    cooldown_result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == []
    assert cooldown_result.matches == []
    assert result.source == "api-football"
    assert result.notice is not None
    assert matches_service._fixture_by_id == {}
    assert matches_service._fixture_cache == {}
    assert list_fixtures.call_count == 1
    mock_list.assert_not_called()


def test_search_without_external_match_never_substitutes_demo_results(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    external_fixture = _external_match()
    monkeypatch.setattr(
        external_provider,
        "list_fixtures",
        MagicMock(return_value=[external_fixture]),
    )
    mock_list = MagicMock(side_effect=AssertionError("search must not consult demos"))
    monkeypatch.setattr(matches_service.mock_provider, "list_highlights", mock_list)

    result = matches_service.search_matches("Arsenal")

    assert result == []
    mock_list.assert_not_called()


def test_get_match_returns_indexed_fixture_without_provider_lookup(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    indexed = _external_match()
    matches_service._index_matches([indexed])
    individual_lookup = MagicMock(
        side_effect=AssertionError("indexed fixtures must not hit the provider")
    )
    monkeypatch.setattr(external_provider, "get_fixture", individual_lookup)

    resolved = matches_service.get_match(indexed.id)

    assert resolved is indexed
    individual_lookup.assert_not_called()


def test_get_match_uses_individual_lookup_without_relisting_agenda(
    monkeypatch,
    external_provider: APIFootballProvider,
) -> None:
    fetched = _external_match(fixture_id="api-football-9001")
    individual_lookup = MagicMock(return_value=fetched)
    list_fixtures = MagicMock(
        side_effect=AssertionError("individual resolution must not relist fixtures")
    )
    monkeypatch.setattr(external_provider, "get_fixture", individual_lookup)
    monkeypatch.setattr(external_provider, "list_fixtures", list_fixtures)

    resolved = matches_service.get_match(fetched.id)

    assert resolved is fetched
    individual_lookup.assert_called_once_with(fetched.id)
    list_fixtures.assert_not_called()
    assert matches_service._fixture_by_id[fetched.id][1] is fetched


def test_api_football_failure_uses_live_football_data_and_preserves_envelope_cache(
    monkeypatch,
) -> None:
    primary = APIFootballProvider(key="suspended-key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    fallback_fixture = _football_data_match()
    primary_list = MagicMock(side_effect=TimeoutError("primary suspended"))
    secondary_list = MagicMock(return_value=[fallback_fixture])
    active = {"provider": primary}
    monkeypatch.setattr(primary, "list_fixtures", primary_list)
    monkeypatch.setattr(secondary, "list_fixtures", secondary_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: active["provider"])
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")

    first = matches_service.get_highlights_result(SELECTED_DATE)
    cached = matches_service.get_highlights_result(SELECTED_DATE)

    assert first.matches == [fallback_fixture]
    assert first.source == "football-data"
    assert first.notice is not None
    assert "api-football" in first.notice
    assert cached == first
    assert cached.source == "football-data"
    assert cached.notice == first.notice
    primary_list.assert_called_once_with(SELECTED_DATE)
    secondary_list.assert_called_once_with(SELECTED_DATE)

    active["provider"] = secondary
    direct = matches_service.get_highlights_result(SELECTED_DATE)
    assert direct.source == "football-data"
    assert direct.notice is None
    secondary_list.assert_called_once_with(SELECTED_DATE)


def test_valid_empty_primary_agenda_does_not_trigger_fallback(monkeypatch) -> None:
    primary = APIFootballProvider(key="valid-key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    primary_list = MagicMock(return_value=[])
    secondary_list = MagicMock(
        side_effect=AssertionError("Una lista vacía válida no activa fallback")
    )
    monkeypatch.setattr(primary, "list_fixtures", primary_list)
    monkeypatch.setattr(secondary, "list_fixtures", secondary_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == []
    assert result.source == "api-football"
    assert result.notice is None
    secondary_list.assert_not_called()


def test_api_football_failure_uses_sportmonks_before_football_data(monkeypatch) -> None:
    primary = APIFootballProvider(key="api-key")
    secondary = SportmonksProvider("monks-token")
    final_fallback = matches_service.FootballDataProvider(
        "football-token", "https://football-data.test/v4", 10
    )
    expected = _sportmonks_match()
    primary_list = MagicMock(side_effect=TimeoutError("api-football down"))
    secondary_list = MagicMock(return_value=[expected])
    final_list = MagicMock(side_effect=AssertionError("final fallback must not run"))
    monkeypatch.setattr(primary, "list_fixtures", primary_list)
    monkeypatch.setattr(secondary, "list_fixtures", secondary_list)
    monkeypatch.setattr(final_fallback, "list_fixtures", final_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "sportmonks_provider", secondary)
    monkeypatch.setattr(matches_service, "football_data_provider", final_fallback)
    monkeypatch.setattr(matches_service.settings, "sports_data_provider", "api-football")
    monkeypatch.setattr(matches_service.settings, "sportmonks_api_token", "monks-token")
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "football-token")

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == [expected]
    assert result.source == "sportmonks"
    assert result.notice is not None and "api-football" in result.notice
    primary_list.assert_called_once_with(SELECTED_DATE)
    secondary_list.assert_called_once_with(SELECTED_DATE)
    final_list.assert_not_called()


def test_third_provider_is_used_after_api_football_and_sportmonks_fail(monkeypatch) -> None:
    primary = APIFootballProvider(key="api-key")
    secondary = SportmonksProvider("monks-token")
    final_fallback = matches_service.FootballDataProvider(
        "football-token", "https://football-data.test/v4", 10
    )
    expected = _football_data_match("football-data-3001")
    primary_list = MagicMock(side_effect=TimeoutError("api-football down"))
    secondary_list = MagicMock(side_effect=TimeoutError("sportmonks down"))
    final_list = MagicMock(return_value=[expected])
    monkeypatch.setattr(primary, "list_fixtures", primary_list)
    monkeypatch.setattr(secondary, "list_fixtures", secondary_list)
    monkeypatch.setattr(final_fallback, "list_fixtures", final_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "sportmonks_provider", secondary)
    monkeypatch.setattr(matches_service, "football_data_provider", final_fallback)
    monkeypatch.setattr(matches_service.settings, "sports_data_provider", "api-football")
    monkeypatch.setattr(matches_service.settings, "sportmonks_api_token", "monks-token")
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "football-token")

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == [expected]
    assert result.source == "football-data"
    assert result.notice is not None
    assert "api-football" in result.notice and "sportmonks" in result.notice
    primary_list.assert_called_once_with(SELECTED_DATE)
    secondary_list.assert_called_once_with(SELECTED_DATE)
    final_list.assert_called_once_with(SELECTED_DATE)


def test_live_secondary_wins_over_stale_primary(monkeypatch) -> None:
    clock = {"now": 100.0}
    primary = APIFootballProvider(key="key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    stale_primary = _external_match()
    live_secondary = _football_data_match()
    primary_list = MagicMock(return_value=[stale_primary])
    secondary_list = MagicMock(return_value=[live_secondary])
    monkeypatch.setattr(matches_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(primary, "list_fixtures", primary_list)
    monkeypatch.setattr(secondary, "list_fixtures", secondary_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")

    assert matches_service.get_highlights_result(SELECTED_DATE).matches == [stale_primary]
    clock["now"] += matches_service._FIXTURE_CACHE_TTL_SECONDS + 1
    primary_list.side_effect = TimeoutError("primary down")

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == [live_secondary]
    assert result.source == "football-data"
    assert result.notice is not None
    secondary_list.assert_called_once_with(SELECTED_DATE)


def test_both_providers_failing_return_latest_stale_real_source(monkeypatch) -> None:
    clock = {"now": 100.0}
    primary = APIFootballProvider(key="key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    stale_primary = _external_match()
    stale_secondary = _football_data_match()
    monkeypatch.setattr(matches_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")
    matches_service._cache_set(
        matches_service._fixture_cache,
        matches_service._provider_cache_key(primary, SELECTED_DATE),
        matches_service.FixtureResult(
            SELECTED_DATE, [stale_primary], "api-football"
        ),
    )
    clock["now"] += 5
    matches_service._cache_set(
        matches_service._fixture_cache,
        matches_service._provider_cache_key(secondary, SELECTED_DATE),
        matches_service.FixtureResult(
            SELECTED_DATE, [stale_secondary], "football-data"
        ),
    )
    clock["now"] += matches_service._FIXTURE_CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(primary, "list_fixtures", MagicMock(side_effect=TimeoutError()))
    monkeypatch.setattr(secondary, "list_fixtures", MagicMock(side_effect=TimeoutError()))

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == [stale_secondary]
    assert result.source == "football-data"
    assert result.notice is not None
    assert "última agenda real" in result.notice
    assert all(match.source_provider != "mock" for match in result.matches)


def test_get_match_routes_football_data_prefix_even_when_api_football_is_active(
    monkeypatch,
) -> None:
    primary = APIFootballProvider(key="key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    expected = _football_data_match("football-data-918")
    primary_lookup = MagicMock(side_effect=AssertionError("Proveedor equivocado"))
    secondary_lookup = MagicMock(return_value=expected)
    monkeypatch.setattr(primary, "get_fixture", primary_lookup)
    monkeypatch.setattr(secondary, "get_fixture", secondary_lookup)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")

    result = matches_service.get_match(expected.id)

    assert result is expected
    secondary_lookup.assert_called_once_with(expected.id)
    primary_lookup.assert_not_called()


def test_get_match_routes_sportmonks_namespace(monkeypatch) -> None:
    primary = APIFootballProvider(key="key")
    secondary = SportmonksProvider("monks-token")
    expected = _sportmonks_match("sportmonks-918")
    primary_lookup = MagicMock(side_effect=AssertionError("Proveedor equivocado"))
    secondary_lookup = MagicMock(return_value=expected)
    monkeypatch.setattr(primary, "get_fixture", primary_lookup)
    monkeypatch.setattr(secondary, "get_fixture", secondary_lookup)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "sportmonks_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "sportmonks_api_token", "monks-token")

    result = matches_service.get_match(expected.id)

    assert result is expected
    secondary_lookup.assert_called_once_with(expected.id)
    primary_lookup.assert_not_called()


def test_both_live_providers_failing_without_stale_never_use_demo(monkeypatch) -> None:
    primary = APIFootballProvider(key="suspended-key")
    secondary = matches_service.FootballDataProvider(
        "fallback-token", "https://football-data.test/v4", 2
    )
    monkeypatch.setattr(
        primary,
        "list_fixtures",
        MagicMock(side_effect=TimeoutError("primary down")),
    )
    monkeypatch.setattr(
        secondary,
        "list_fixtures",
        MagicMock(side_effect=TimeoutError("secondary down")),
    )
    mock_list = MagicMock(side_effect=AssertionError("No debe usar demos"))
    monkeypatch.setattr(matches_service.mock_provider, "list_highlights", mock_list)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service, "football_data_provider", secondary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "fallback-token")

    result = matches_service.get_highlights_result(SELECTED_DATE)

    assert result.matches == []
    assert result.source == "api-football"
    assert result.notice is not None
    assert "demo" in result.notice
    mock_list.assert_not_called()


def test_concurrent_agenda_requests_share_one_provider_refresh(monkeypatch) -> None:
    primary = APIFootballProvider(key="key")
    fixture = _external_match()
    request_started = Event()
    allow_response = Event()

    def delayed_list(selected_date):
        request_started.set()
        assert allow_response.wait(timeout=2)
        return [fixture]

    provider_call = MagicMock(side_effect=delayed_list)
    monkeypatch.setattr(primary, "list_fixtures", provider_call)
    monkeypatch.setattr(matches_service, "_active_provider", lambda: primary)
    monkeypatch.setattr(matches_service.settings, "football_data_api_token", "")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(matches_service.get_highlights_result, SELECTED_DATE)
        assert request_started.wait(timeout=2)
        second = executor.submit(matches_service.get_highlights_result, SELECTED_DATE)
        allow_response.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert all(result.matches == [fixture] for result in results)
    assert provider_call.call_count == 1
