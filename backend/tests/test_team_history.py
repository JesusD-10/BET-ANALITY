from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import repository
from app.db.models import Competition, Country, Match, MatchTeamStatistics, Season, Team
from app.db.session import Base
from app.db import team_repository
from app.main import app
from app.services import teams as teams_service


def _memory_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _seed_history(session_factory) -> tuple[int, int]:
    with session_factory() as session:
        spain = Country(code="ES", name="Spain", slug="spain")
        england = Country(code="ENG", name="England", slug="england")
        league = Competition(
            country=spain,
            name="La Liga",
            slug="la-liga",
            kind="league",
        )
        season = Season(
            competition=league,
            label="2025-2026",
            start_year=2025,
            end_year=2026,
        )
        real = Team(country=spain, name="Real Madrid", slug="real-madrid", kind="club")
        barcelona = Team(country=spain, name="Barcelona", slug="barcelona", kind="club")
        session.add_all(
            [
                spain,
                england,
                league,
                season,
                real,
                barcelona,
                Team(
                    country=england,
                    name="Manchester City",
                    slug="manchester-city",
                    kind="club",
                ),
            ]
        )
        session.flush()
        matches = [
            Match(
                public_id="match-1",
                fingerprint="fingerprint-1",
                competition=league,
                season=season,
                home_team=real,
                away_team=barcelona,
                match_date=date(2024, 1, 1),
                kickoff_at=datetime(2024, 1, 1, 20, tzinfo=timezone.utc),
                kickoff_precision="datetime",
                status="FINALIZADO",
                status_short="FT",
                home_score=2,
                away_score=1,
                half_time_home_score=1,
                half_time_away_score=0,
                source_provider="test",
            ),
            Match(
                public_id="match-2",
                fingerprint="fingerprint-2",
                competition=league,
                season=season,
                home_team=barcelona,
                away_team=real,
                match_date=date(2024, 2, 1),
                kickoff_at=datetime(2024, 2, 1, 20, tzinfo=timezone.utc),
                kickoff_precision="datetime",
                status="FINALIZADO",
                status_short="FT",
                home_score=0,
                away_score=0,
                source_provider="test",
            ),
            Match(
                public_id="match-3",
                fingerprint="fingerprint-3",
                competition=league,
                season=season,
                home_team=barcelona,
                away_team=real,
                match_date=date(2024, 3, 1),
                kickoff_at=datetime(2024, 3, 1, 20, tzinfo=timezone.utc),
                kickoff_precision="datetime",
                status="FINALIZADO",
                status_short="FT",
                home_score=3,
                away_score=1,
                source_provider="test",
            ),
            Match(
                public_id="match-4",
                fingerprint="fingerprint-4",
                competition=league,
                season=season,
                home_team=real,
                away_team=barcelona,
                match_date=date(2026, 9, 1),
                kickoff_at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
                kickoff_precision="datetime",
                status="PROGRAMADO",
                source_provider="test",
            ),
        ]
        session.add_all(matches)
        session.commit()
        return real.id, barcelona.id


def test_repository_search_detail_and_paginated_history() -> None:
    session_factory = _memory_session_factory()
    real_id, _ = _seed_history(session_factory)

    with session_factory() as session:
        search = team_repository.search_teams(
            session,
            query="madrid",
            country_code="es",
            kind="club",
            page=1,
            page_size=10,
        )
        detail = team_repository.get_team_detail(
            session,
            team_id=real_id,
            today=date(2026, 8, 26),
        )
        first_page = team_repository.get_team_matches(
            session,
            team_id=real_id,
            scope="past",
            today=date(2026, 8, 26),
            page=1,
            page_size=2,
            competition_id=None,
        )
        upcoming = team_repository.get_team_matches(
            session,
            team_id=real_id,
            scope="upcoming",
            today=date(2026, 8, 26),
            page=1,
            page_size=10,
            competition_id=None,
        )

    assert search.total == 1
    assert search.items[0].name == "Real Madrid"
    assert search.items[0].country.code == "ES"
    assert detail is not None
    assert detail.statistics.model_dump() == {
        "total_matches": 4,
        "completed_matches": 3,
        "upcoming_matches": 1,
        "wins": 1,
        "draws": 1,
        "losses": 1,
        "goals_for": 3,
        "goals_against": 4,
        "first_match_date": date(2024, 1, 1),
        "last_match_date": date(2024, 3, 1),
        "next_match_date": date(2026, 9, 1),
    }
    assert [(item.name, item.matches) for item in detail.competitions] == [("La Liga", 4)]
    assert [(item.label, item.matches) for item in detail.seasons] == [("2025-2026", 4)]
    assert first_page.total == 3
    assert first_page.total_pages == 2
    assert [item.id for item in first_page.items] == ["match-3", "match-2"]
    assert first_page.items[0].team_side == "away"
    assert first_page.items[0].result == "loss"
    assert first_page.items[0].opponent.name == "Barcelona"
    assert upcoming.total == 1
    assert upcoming.items[0].id == "match-4"
    assert upcoming.items[0].result is None


def test_repository_h2h_is_completed_and_paginated() -> None:
    session_factory = _memory_session_factory()
    real_id, barcelona_id = _seed_history(session_factory)

    with session_factory() as session:
        result = team_repository.get_head_to_head(
            session,
            team_id=real_id,
            opponent_id=barcelona_id,
            today=date(2026, 8, 26),
            page=2,
            page_size=2,
        )

    assert result.scope == "h2h"
    assert result.opponent_id == barcelona_id
    assert result.total == 3
    assert result.total_pages == 2
    assert [item.id for item in result.items] == ["match-1"]


def test_repository_merges_team_aliases_and_fills_missing_logo() -> None:
    session_factory = _memory_session_factory()

    with session_factory() as session:
        spain = Country(code="ES", name="Spain", slug="spain")
        league = Competition(country=spain, name="La Liga", slug="la-liga", kind="league")
        session.add(spain)
        session.add(league)

        legacy = Team(
            country=spain,
            name="Barcelona",
            slug="barcelona",
            kind="club",
            logo_url=None,
        )
        current = Team(
            country=spain,
            name="FC Barcelona",
            slug="fc-barcelona",
            kind="club",
            logo_url="https://example.com/barca.png",
        )
        reserve = Team(
            country=spain,
            name="Barcelona B",
            slug="barcelona-b",
            kind="club",
            logo_url=None,
        )
        session.add_all([legacy, current, reserve])
        session.flush()
        session.add_all(
            [
                Match(
                    public_id="legacy-barca-match",
                    fingerprint="legacy-barca-fingerprint",
                    competition=league,
                    home_team=legacy,
                    away_team=reserve,
                    match_date=date(2019, 5, 1),
                    kickoff_at=datetime(2019, 5, 1, 20, tzinfo=timezone.utc),
                    kickoff_precision="datetime",
                    status="FINALIZADO",
                    home_score=2,
                    away_score=0,
                    source_provider="historical",
                ),
                Match(
                    public_id="current-barca-match",
                    fingerprint="current-barca-fingerprint",
                    competition=league,
                    home_team=current,
                    away_team=reserve,
                    match_date=date(2026, 8, 31),
                    kickoff_at=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
                    kickoff_precision="datetime",
                    status="FINALIZADO",
                    home_score=3,
                    away_score=1,
                    source_provider="api-football",
                ),
            ]
        )
        session.commit()

        search = team_repository.search_teams(
            session,
            query="barcelona",
            country_code="ES",
            kind="club",
            page=1,
            page_size=20,
        )

        assert search.total == 1
        assert search.items[0].name == "FC Barcelona"
        assert search.items[0].logo_url is not None
        assert search.items[0].logo_url.startswith("http")
        detail = team_repository.get_team_detail(
            session, team_id=current.id, today=date(2026, 9, 1)
        )
        history = team_repository.get_team_matches(
            session,
            team_id=current.id,
            scope="past",
            today=date(2026, 9, 1),
            page=1,
            page_size=10,
            competition_id=None,
        )

        assert detail is not None
        assert detail.statistics.completed_matches == 2
        assert history.total == 2


def test_repository_loads_persisted_match_statistics(monkeypatch) -> None:
    session_factory = _memory_session_factory()
    real_id, _ = _seed_history(session_factory)
    monkeypatch.setattr(repository, "SessionLocal", session_factory)

    with session_factory() as session:
        match = session.scalar(select(Match).where(Match.public_id == "match-1"))
        assert match is not None
        session.add_all(
            [
                MatchTeamStatistics(
                    match=match,
                    team_id=match.home_team_id,
                    side="home",
                    shots=12,
                    shots_on_target=5,
                    corners=7,
                ),
                MatchTeamStatistics(
                    match=match,
                    team_id=match.away_team_id,
                    side="away",
                    shots=4,
                    shots_on_target=1,
                    corners=2,
                ),
            ]
        )
        session.commit()

        result = repository.load_match_statistics("match-1")

    assert result is not None
    assert result["goals"] == {"home": 2, "away": 1}
    assert result["statistics"][0]["shots"] == 12


def test_team_api_contract_and_errors(monkeypatch) -> None:
    session_factory = _memory_session_factory()
    real_id, barcelona_id = _seed_history(session_factory)
    monkeypatch.setattr(teams_service, "SessionLocal", session_factory)
    monkeypatch.setattr(teams_service, "_today_in_lima", lambda: date(2026, 8, 26))
    client = TestClient(app)

    search = client.get("/api/v1/teams", params={"q": "Madrid"})
    detail = client.get(f"/api/v1/teams/{real_id}")
    history = client.get(
        f"/api/v1/teams/{real_id}/matches",
        params={"scope": "past", "page": 1, "page_size": 2},
    )
    h2h = client.get(f"/api/v1/teams/{real_id}/h2h/{barcelona_id}")

    assert search.status_code == 200
    assert search.json()["items"][0]["country"]["code"] == "ES"
    assert detail.status_code == 200
    assert detail.json()["statistics"]["completed_matches"] == 3
    assert history.status_code == 200
    assert history.json()["total_pages"] == 2
    assert history.json()["items"][0]["opponent"]["name"] == "Barcelona"
    assert h2h.status_code == 200
    assert h2h.json()["scope"] == "h2h"
    assert client.get("/api/v1/teams/999999").status_code == 404
    assert client.get(f"/api/v1/teams/{real_id}/h2h/{real_id}").status_code == 400
    assert client.get("/api/v1/teams", params={"q": "x"}).status_code == 422
    assert (
        client.get(
            f"/api/v1/teams/{real_id}/matches",
            params={"page_size": 101},
        ).status_code
        == 422
    )
