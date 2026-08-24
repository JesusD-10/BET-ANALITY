from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api import routes
from app.core.config import settings
from app.db import health as database_health_module
from app.db.models import Base, MatchOdds
from app.main import app
from app.services.matches import _fixture_by_id, _fixture_cache

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bet-analizador-api"}


def test_database_health_returns_ready_without_connection_details(monkeypatch) -> None:
    monkeypatch.setattr(routes, "database_is_ready", lambda: True)

    response = client.get("/api/v1/health/database")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "connection": "ok",
        "schema": "complete",
    }
    assert "url" not in response.text.casefold()
    assert "host" not in response.text.casefold()
    assert "user" not in response.text.casefold()


def test_database_health_hides_internal_errors(monkeypatch) -> None:
    def fail_with_secret() -> bool:
        raise RuntimeError("postgresql://private-user:private-password@private-host/db")

    monkeypatch.setattr(routes, "database_is_ready", fail_with_secret)

    response = client.get("/api/v1/health/database")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "connection": "unavailable",
        "schema": "unavailable",
    }
    assert "private" not in response.text.casefold()
    assert "postgresql" not in response.text.casefold()


def test_database_readiness_requires_every_expected_table(monkeypatch) -> None:
    test_engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(test_engine)
        monkeypatch.setattr(database_health_module, "engine", test_engine)
        assert database_health_module.database_is_ready() is True

        MatchOdds.__table__.drop(test_engine)
        assert database_health_module.database_is_ready() is False
    finally:
        test_engine.dispose()


def test_highlights_returns_matches(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")
    monkeypatch.setattr(settings, "sportmonks_api_token", "")
    monkeypatch.setattr(settings, "football_data_api_token", "")
    _fixture_cache.clear()
    _fixture_by_id.clear()
    response = client.get("/api/v1/matches/highlights")

    assert response.status_code == 200
    assert len(response.json()["matches"]) > 0
    assert "source" in response.json()

