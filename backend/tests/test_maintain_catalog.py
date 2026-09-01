from datetime import date, datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.maintain_catalog import enrich_missing_logos, maintain_catalog
from app.db.models import Competition, Country, Match, Season, Team
from app.db.session import Base


def test_maintain_catalog_merges_la_liga_rebuilds_season_and_copies_logo() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        country = Country(code="ESP", name="Spain", slug="spain")
        la_liga = Competition(country=country, name="La Liga", slug="la-liga")
        primera = Competition(country=country, name="Primera Division", slug="primera-division")
        old_season = Season(competition=primera, label="2005-2026", start_year=2005, end_year=2026)
        historical = Team(country=country, name="Barcelona", slug="barcelona")
        current = Team(country=country, name="FC Barcelona", slug="fc-barcelona", logo_url="https://img.test/barca.png")
        rival = Team(country=country, name="Celta", slug="celta")
        session.add_all([country, la_liga, primera, old_season, historical, current, rival])
        session.flush()
        session.add(Match(public_id="barca-celta", fingerprint="barca-celta", competition=primera, season=old_season, home_team=historical, away_team=rival, match_date=date(2021, 5, 16), kickoff_at=datetime(2021, 5, 16, tzinfo=timezone.utc), status="FINALIZADO", source_provider="historical"))
        session.commit()

    report = maintain_catalog(factory)

    assert report == {"competitions_merged": 1, "seasons_reassigned": 1, "logos_propagated": 1}
    with factory() as session:
        match = session.scalar(select(Match))
        historical = session.scalar(select(Team).where(Team.slug == "barcelona"))
        assert match is not None
        assert match.competition.name == "La Liga"
        assert match.season.label == "2020-2021"
        assert historical is not None and historical.logo_url == "https://img.test/barca.png"


def test_enrich_missing_logos_only_assigns_canonical_provider_matches() -> None:
    class Provider:
        def get_teams(self, *, search: str):
            return [{"team": {"id": "529", "name": "FC Barcelona", "logo": "https://img.test/barca.png"}}]

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        country = Country(code="ESP", name="Spain", slug="spain")
        session.add_all([country, Team(country=country, name="Barcelona", slug="barcelona")])
        session.commit()

    report = enrich_missing_logos(limit=10, provider=Provider(), session_factory=factory)  # type: ignore[arg-type]

    assert report == {"looked_up": 1, "logos_found": 1}
    with factory() as session:
        team = session.scalar(select(Team))
        assert team is not None
        assert team.logo_url == "https://img.test/barca.png"
        assert team.external_id == "529"