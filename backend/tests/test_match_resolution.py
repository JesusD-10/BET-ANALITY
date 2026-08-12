from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.schemas.matches import MatchSummary
from app.services import matches as matches_service
from app.services.api_football import APIFootballProvider


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
