from app.core.config import settings
from app.services import matches


def test_api_sports_alias_selects_direct_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-sports")
    monkeypatch.setattr(settings, "api_football_key", "test-key")

    assert matches._active_provider() is matches.api_football_provider


def test_api_sports_without_secret_never_activates_external_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-sports")
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")

    assert matches._active_provider() is matches.mock_provider


def test_api_sports_without_key_uses_configured_football_data(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-sports")
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "fallback-token")

    assert matches._active_provider() is matches.football_data_provider


def test_sportmonks_alias_selects_sportmonks_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "sportmonks")
    monkeypatch.setattr(settings, "sportmonks_api_token", "monks-token")

    assert matches._active_provider() is matches.sportmonks_provider


def test_provider_chain_keeps_football_data_last(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-football")
    monkeypatch.setattr(settings, "api_football_key", "api-key")
    monkeypatch.setattr(settings, "sportmonks_api_token", "monks-token")
    monkeypatch.setattr(settings, "football_data_api_token", "fallback-token")

    assert [provider.provider_name for provider in matches._provider_chain()] == [
        "api-football",
        "sportmonks",
        "football-data",
    ]
