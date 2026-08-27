from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.catalog import (
    CountrySpec,
    infer_competition_country,
    resolve_country,
    slugify,
)
from app.db.models import Competition, Country, Match, Season, Team
from app.db.session import SessionLocal
from app.schemas.matches import MatchSummary


def _stable_hash(*parts: object) -> str:
    canonical = "|".join(slugify(part) for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_match_fingerprint(
    competition: object,
    match_date: date,
    home_team: object,
    away_team: object,
) -> str:
    # The date rather than the exact kickoff makes a historical date-only row
    # converge with a later provider record that supplies the actual time.
    return _stable_hash(competition, match_date.isoformat(), home_team, away_team)


def _get_or_create_country(session: Session, spec: CountrySpec) -> Country:
    country = session.scalar(select(Country).where(Country.code == spec.code))
    if country is None:
        country = Country(code=spec.code, name=spec.name, slug=slugify(spec.name))
        session.add(country)
        session.flush()
    elif country.name == "Unknown" and spec.name != "Unknown":
        country.name = spec.name
        country.slug = slugify(spec.name)
    return country


def _get_or_create_competition(
    session: Session,
    *,
    country: Country,
    name: str,
    provider: str | None = None,
    external_id: str | None = None,
    kind: str = "league",
    logo_url: str | None = None,
) -> Competition:
    competition = None
    if provider and external_id:
        competition = session.scalar(
            select(Competition).where(
                Competition.source_provider == provider,
                Competition.external_id == str(external_id),
            )
        )
    if competition is None:
        competition = session.scalar(
            select(Competition).where(
                Competition.country_id == country.id,
                Competition.slug == slugify(name),
            )
        )
    if competition is None:
        competition = Competition(
            country=country,
            name=name,
            slug=slugify(name),
            kind=kind,
            source_provider=provider,
            external_id=str(external_id) if external_id else None,
            logo_url=logo_url,
        )
        session.add(competition)
        session.flush()
    else:
        if provider and external_id and not competition.external_id:
            competition.source_provider = provider
            competition.external_id = str(external_id)
        if logo_url:
            competition.logo_url = logo_url
    return competition


def _get_or_create_season(
    session: Session,
    *,
    competition: Competition,
    label: str,
    start_year: int,
    end_year: int,
) -> Season:
    season = session.scalar(
        select(Season).where(
            Season.competition_id == competition.id,
            Season.label == label,
        )
    )
    if season is None:
        season = Season(
            competition=competition,
            label=label,
            start_year=start_year,
            end_year=end_year,
        )
        session.add(season)
        session.flush()
    return season


def _get_or_create_team(
    session: Session,
    *,
    country: Country,
    name: str,
    provider: str | None = None,
    external_id: str | None = None,
    logo_url: str | None = None,
) -> Team:
    team = None
    if provider and external_id:
        team = session.scalar(
            select(Team).where(
                Team.source_provider == provider,
                Team.external_id == str(external_id),
            )
        )
    if team is None:
        team = session.scalar(
            select(Team).where(
                Team.country_id == country.id,
                Team.slug == slugify(name),
            )
        )
    if team is None:
        team = Team(
            country=country,
            name=name,
            slug=slugify(name),
            source_provider=provider,
            external_id=str(external_id) if external_id else None,
            logo_url=logo_url,
        )
        session.add(team)
        session.flush()
    else:
        if provider and external_id and not team.external_id:
            team.source_provider = provider
            team.external_id = str(external_id)
        if logo_url:
            team.logo_url = logo_url
    return team


def _normalized_kickoff(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_match_date(kickoff: datetime) -> date:
    try:
        from zoneinfo import ZoneInfo

        return kickoff.astimezone(ZoneInfo("America/Lima")).date()
    except Exception:
        return kickoff.date()


def _persist_matches(session: Session, matches: list[MatchSummary]) -> int:
    persisted = 0
    for summary in matches:
        provider = str(summary.source_provider or "unknown")
        kickoff = _normalized_kickoff(summary.kickoff_at)
        match_date = _local_match_date(kickoff)
        fingerprint = build_match_fingerprint(
            summary.competition, match_date, summary.home_team, summary.away_team
        )

        model = session.scalar(select(Match).where(Match.public_id == summary.id))
        if model is None and summary.external_id:
            model = session.scalar(
                select(Match).where(
                    Match.source_provider == provider,
                    Match.external_id == summary.external_id,
                )
            )
        if model is None:
            model = session.scalar(select(Match).where(Match.fingerprint == fingerprint))

        if summary.country_code:
            resolved = resolve_country(summary.country_code)
            country_spec = CountrySpec(
                resolved.code,
                summary.country or resolved.name,
            )
        elif summary.country:
            country_spec = resolve_country(summary.country)
        elif model is not None:
            country_spec = CountrySpec(
                model.competition.country.code,
                model.competition.country.name,
            )
        else:
            country_spec = infer_competition_country(summary.competition)
        country = _get_or_create_country(session, country_spec)
        competition = _get_or_create_competition(
            session,
            country=country,
            name=summary.competition,
            provider=provider if summary.league_id else None,
            external_id=summary.league_id,
            logo_url=summary.competition_logo,
        )
        season = None
        if summary.season is not None:
            year = int(summary.season)
            season = _get_or_create_season(
                session,
                competition=competition,
                label=str(year),
                start_year=year,
                end_year=year,
            )
        home = _get_or_create_team(
            session,
            country=country,
            name=summary.home_team,
            provider=provider if summary.home_team_id else None,
            external_id=summary.home_team_id,
            logo_url=summary.home_logo,
        )
        away = _get_or_create_team(
            session,
            country=country,
            name=summary.away_team,
            provider=provider if summary.away_team_id else None,
            external_id=summary.away_team_id,
            logo_url=summary.away_logo,
        )
        if model is None:
            model = Match(
                public_id=summary.id,
                fingerprint=fingerprint,
                competition=competition,
                season=season,
                home_team=home,
                away_team=away,
                match_date=match_date,
                kickoff_at=kickoff,
                status=summary.status,
                source_provider=provider,
            )
            session.add(model)

        model.public_id = summary.id
        model.competition = competition
        if season is not None:
            model.season = season
        model.home_team = home
        model.away_team = away
        model.match_date = match_date
        model.kickoff_at = kickoff
        model.kickoff_precision = "datetime"
        model.status = summary.status
        if summary.status_short is not None:
            model.status_short = summary.status_short
        if summary.elapsed is not None:
            model.live_minute = summary.elapsed
        if summary.home_score is not None:
            model.home_score = summary.home_score
        if summary.away_score is not None:
            model.away_score = summary.away_score
        if summary.halftime_home_score is not None:
            model.half_time_home_score = summary.halftime_home_score
        if summary.halftime_away_score is not None:
            model.half_time_away_score = summary.halftime_away_score
        if summary.round is not None:
            model.round = summary.round
        if summary.venue_id is not None:
            model.venue_id = summary.venue_id
        if summary.venue is not None:
            model.venue = summary.venue
        if summary.referee is not None:
            model.referee = summary.referee
        if summary.home_form is not None:
            model.home_form = summary.home_form
        if summary.away_form is not None:
            model.away_form = summary.away_form
        model.data_quality = summary.data_quality
        model.odds_available = bool(summary.odds_available or model.odds_available)
        model.source_provider = provider
        if summary.external_id is not None:
            model.external_id = summary.external_id
        if summary.source_url is not None:
            model.source_url = summary.source_url
        persisted += 1
    return persisted


def persist_matches(matches: list[MatchSummary]) -> int:
    """Insert or update provider matches and return the number processed."""

    if not matches:
        return 0
    with SessionLocal() as session:
        try:
            count = _persist_matches(session, matches)
            session.commit()
            return count
        except Exception:
            session.rollback()
            raise


def load_matches(match_date: date) -> list[MatchSummary]:
    """Load one application-local date ordered by kickoff."""

    with SessionLocal() as session:
        rows = session.scalars(
            select(Match)
            .where(Match.match_date == match_date)
            .options(
                joinedload(Match.competition),
                joinedload(Match.season),
                joinedload(Match.home_team),
                joinedload(Match.away_team),
            )
            .order_by(Match.kickoff_at, Match.id)
        ).all()

        return [_summary_from_row(row) for row in rows]


def _summary_from_row(row: Match) -> MatchSummary:
    kickoff = row.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return MatchSummary(
        id=row.public_id,
        competition=row.competition.name,
        country=row.competition.country.name,
        country_code=row.competition.country.code,
        competition_logo=row.competition.logo_url,
        kickoff_at=kickoff,
        home_team=row.home_team.name,
        away_team=row.away_team.name,
        home_team_id=str(row.home_team.id),
        away_team_id=str(row.away_team.id),
        league_id=row.competition.external_id,
        season=row.season.start_year if row.season else None,
        round=row.round,
        venue_id=row.venue_id,
        venue=row.venue,
        referee=row.referee,
        home_form=row.home_form,
        away_form=row.away_form,
        home_logo=row.home_team.logo_url,
        away_logo=row.away_team.logo_url,
        data_quality=row.data_quality,
        odds_available=row.odds_available,
        home_score=row.home_score,
        away_score=row.away_score,
        halftime_home_score=row.half_time_home_score,
        halftime_away_score=row.half_time_away_score,
        elapsed=row.live_minute,
        status_short=row.status_short,
        status=row.status,
        source_provider=row.source_provider,
        source_url=row.source_url,
        external_id=row.external_id,
    )


def load_match(public_id: str) -> MatchSummary | None:
    """Resolve a stored match before consulting the originating provider."""
    with SessionLocal() as session:
        row = session.scalar(
            select(Match)
            .where(Match.public_id == public_id)
            .options(
                joinedload(Match.competition).joinedload(Competition.country),
                joinedload(Match.season),
                joinedload(Match.home_team),
                joinedload(Match.away_team),
            )
        )
        return _summary_from_row(row) if row is not None else None
