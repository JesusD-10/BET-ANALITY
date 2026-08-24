from datetime import date, datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db import repository
from app.db.session import Base
from app.schemas.matches import MatchSummary


def _memory_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_provider_match_round_trip_and_live_update(monkeypatch) -> None:
    session_factory = _memory_session_factory()
    monkeypatch.setattr(repository, "SessionLocal", session_factory)
    live = MatchSummary(
        id="api-football-9001",
        external_id="9001",
        competition="Liga 1",
        country="Peru",
        country_code="PE",
        competition_logo="https://img.test/liga-1.png",
        league_id="239",
        season=2026,
        kickoff_at=datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
        home_team="Alianza Lima",
        away_team="Universitario",
        home_team_id="10",
        away_team_id="12",
        home_logo="https://img.test/alianza.png",
        away_logo="https://img.test/universitario.png",
        home_score=1,
        away_score=0,
        halftime_home_score=1,
        halftime_away_score=0,
        elapsed=64,
        status_short="2H",
        status="EN JUEGO (2T)",
        source_provider="api-football",
    )

    assert repository.persist_matches([live]) == 1
    stored = repository.load_matches(date(2026, 8, 24))

    assert len(stored) == 1
    assert stored[0].country == "Peru"
    assert stored[0].country_code == "PE"
    assert stored[0].competition_logo == "https://img.test/liga-1.png"
    assert (stored[0].home_score, stored[0].away_score) == (1, 0)
    assert stored[0].elapsed == 64
    assert stored[0].status_short == "2H"

    finished = live.model_copy(
        update={
            "home_score": 2,
            "away_score": 1,
            "elapsed": None,
            "status_short": "FT",
            "status": "FINALIZADO",
        }
    )
    assert repository.persist_matches([finished]) == 1

    updated = repository.load_matches(date(2026, 8, 24))
    assert len(updated) == 1
    assert (updated[0].home_score, updated[0].away_score) == (2, 1)
    assert updated[0].status_short == "FT"
    assert updated[0].status == "FINALIZADO"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Match)) == 1
