from app.core.config import settings
from app.services import matches


def test_api_sports_alias_selects_direct_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-sports")
    monkeypatch.setattr(settings, "api_football_key", "test-key")

    assert matches._active_provider() is matches.api_football_provider


def test_api_sports_without_secret_never_activates_external_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sports_data_provider", "api-sports")
    monkeypatch.setattr(settings, "api_football_key", "")

    assert matches._active_provider() is matches.mock_provider
