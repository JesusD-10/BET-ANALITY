from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.catalog import slugify
from app.db.models import Competition, Country, Match, Team
from app.schemas.teams import (
    TeamCompetitionSummary,
    TeamCountry,
    TeamDetailResponse,
    TeamListItem,
    TeamMatchCompetition,
    TeamMatchItem,
    TeamMatchesResponse,
    TeamMatchSeason,
    TeamMatchSideItem,
    TeamSearchResponse,
    TeamStatistics,
)


HistoryScope = Literal["past", "upcoming", "all"]


def _total_pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total else 0


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _team_item(team: Team) -> TeamListItem:
    return TeamListItem(
        id=team.id,
        name=team.name,
        slug=team.slug,
        short_code=team.short_code,
        kind=team.kind,
        logo_url=team.logo_url,
        country=TeamCountry(
            code=team.country.code,
            name=team.country.name,
            flag_url=team.country.flag_url,
        ),
    )


def _match_side(team: Team) -> TeamMatchSideItem:
    return TeamMatchSideItem(
        id=team.id,
        name=team.name,
        slug=team.slug,
        logo_url=team.logo_url,
    )


def _kickoff_with_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _match_item(match: Match, team_id: int) -> TeamMatchItem:
    is_home = match.home_team_id == team_id
    own_score = match.home_score if is_home else match.away_score
    rival_score = match.away_score if is_home else match.home_score
    result = None
    if own_score is not None and rival_score is not None:
        result = "win" if own_score > rival_score else "loss" if own_score < rival_score else "draw"

    season = None
    if match.season is not None:
        season = TeamMatchSeason(
            id=match.season.id,
            label=match.season.label,
            start_year=match.season.start_year,
            end_year=match.season.end_year,
        )

    home_team = _match_side(match.home_team)
    away_team = _match_side(match.away_team)
    return TeamMatchItem(
        id=match.public_id,
        match_date=match.match_date,
        kickoff_at=_kickoff_with_timezone(match.kickoff_at),
        kickoff_precision=match.kickoff_precision,
        status=match.status,
        status_short=match.status_short,
        competition=TeamMatchCompetition(
            id=match.competition.id,
            name=match.competition.name,
            slug=match.competition.slug,
            kind=match.competition.kind,
            country_code=match.competition.country.code,
            logo_url=match.competition.logo_url,
        ),
        season=season,
        round=match.round,
        venue=match.venue,
        home_team=home_team,
        away_team=away_team,
        home_score=match.home_score,
        away_score=match.away_score,
        half_time_home_score=match.half_time_home_score,
        half_time_away_score=match.half_time_away_score,
        team_side="home" if is_home else "away",
        result=result,
        opponent=away_team if is_home else home_team,
    )


def team_exists(session: Session, team_id: int) -> bool:
    return session.scalar(select(Team.id).where(Team.id == team_id)) is not None


def search_teams(
    session: Session,
    *,
    query: str | None,
    country_code: str | None,
    kind: str | None,
    page: int,
    page_size: int,
) -> TeamSearchResponse:
    filters = []
    rank = None
    if query and query.strip():
        cleaned_query = " ".join(query.split())
        query_slug = slugify(cleaned_query)
        escaped_slug = _escape_like(query_slug)
        escaped_name = _escape_like(cleaned_query.casefold())
        prefix_pattern = f"{escaped_slug}%"
        filters.append(
            or_(
                Team.slug.like(prefix_pattern, escape="\\"),
                func.lower(Team.name).like(f"%{escaped_name}%", escape="\\"),
            )
        )
        rank = case(
            (Team.slug == query_slug, 0),
            (Team.slug.like(prefix_pattern, escape="\\"), 1),
            else_=2,
        )
    if country_code:
        filters.append(Country.code == country_code.strip().upper())
    if kind:
        filters.append(Team.kind == kind)

    count_statement = select(func.count(Team.id)).join(Team.country)
    rows_statement = (
        select(Team)
        .join(Team.country)
        .options(joinedload(Team.country))
    )
    if filters:
        count_statement = count_statement.where(*filters)
        rows_statement = rows_statement.where(*filters)
    order_columns = [Team.name, Team.id] if rank is None else [rank, Team.name, Team.id]

    total = int(session.scalar(count_statement) or 0)
    rows = session.scalars(
        rows_statement
        .order_by(*order_columns)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TeamSearchResponse(
        items=[_team_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=_total_pages(total, page_size),
    )


def get_team_detail(session: Session, *, team_id: int, today: date) -> TeamDetailResponse | None:
    team = session.scalar(
        select(Team).where(Team.id == team_id).options(joinedload(Team.country))
    )
    if team is None:
        return None

    belongs_to_team = or_(Match.home_team_id == team_id, Match.away_team_id == team_id)
    completed = and_(Match.home_score.is_not(None), Match.away_score.is_not(None))
    upcoming = and_(
        or_(Match.home_score.is_(None), Match.away_score.is_(None)),
        Match.match_date >= today,
    )
    home = Match.home_team_id == team_id
    away = Match.away_team_id == team_id
    won = or_(
        and_(home, Match.home_score > Match.away_score),
        and_(away, Match.away_score > Match.home_score),
    )
    lost = or_(
        and_(home, Match.home_score < Match.away_score),
        and_(away, Match.away_score < Match.home_score),
    )
    drawn = Match.home_score == Match.away_score

    aggregate = session.execute(
        select(
            func.count(Match.id).label("total_matches"),
            func.coalesce(func.sum(case((completed, 1), else_=0)), 0).label(
                "completed_matches"
            ),
            func.coalesce(func.sum(case((upcoming, 1), else_=0)), 0).label(
                "upcoming_matches"
            ),
            func.coalesce(func.sum(case((and_(completed, won), 1), else_=0)), 0).label(
                "wins"
            ),
            func.coalesce(func.sum(case((and_(completed, drawn), 1), else_=0)), 0).label(
                "draws"
            ),
            func.coalesce(func.sum(case((and_(completed, lost), 1), else_=0)), 0).label(
                "losses"
            ),
            func.coalesce(
                func.sum(
                    case(
                        (and_(completed, home), Match.home_score),
                        (and_(completed, away), Match.away_score),
                        else_=0,
                    )
                ),
                0,
            ).label("goals_for"),
            func.coalesce(
                func.sum(
                    case(
                        (and_(completed, home), Match.away_score),
                        (and_(completed, away), Match.home_score),
                        else_=0,
                    )
                ),
                0,
            ).label("goals_against"),
            func.min(case((completed, Match.match_date), else_=None)).label(
                "first_match_date"
            ),
            func.max(case((completed, Match.match_date), else_=None)).label(
                "last_match_date"
            ),
            func.min(case((upcoming, Match.match_date), else_=None)).label(
                "next_match_date"
            ),
        ).where(belongs_to_team)
    ).one()

    match_count = func.count(Match.id)
    competition_rows = session.execute(
        select(
            Competition.id,
            Competition.name,
            Competition.slug,
            Competition.kind,
            Country.code.label("country_code"),
            Competition.logo_url,
            match_count.label("matches"),
        )
        .join(Match, Match.competition_id == Competition.id)
        .join(Country, Country.id == Competition.country_id)
        .where(belongs_to_team)
        .group_by(
            Competition.id,
            Competition.name,
            Competition.slug,
            Competition.kind,
            Country.code,
            Competition.logo_url,
        )
        .order_by(match_count.desc(), Competition.name, Competition.id)
        .limit(100)
    ).all()

    return TeamDetailResponse(
        team=_team_item(team),
        statistics=TeamStatistics(
            total_matches=int(aggregate.total_matches or 0),
            completed_matches=int(aggregate.completed_matches or 0),
            upcoming_matches=int(aggregate.upcoming_matches or 0),
            wins=int(aggregate.wins or 0),
            draws=int(aggregate.draws or 0),
            losses=int(aggregate.losses or 0),
            goals_for=int(aggregate.goals_for or 0),
            goals_against=int(aggregate.goals_against or 0),
            first_match_date=aggregate.first_match_date,
            last_match_date=aggregate.last_match_date,
            next_match_date=aggregate.next_match_date,
        ),
        competitions=[
            TeamCompetitionSummary(
                id=row.id,
                name=row.name,
                slug=row.slug,
                kind=row.kind,
                country_code=row.country_code,
                logo_url=row.logo_url,
                matches=int(row.matches),
            )
            for row in competition_rows
        ],
    )


def _matches_response(
    session: Session,
    *,
    team_id: int,
    scope: HistoryScope,
    today: date,
    page: int,
    page_size: int,
    competition_id: int | None,
) -> TeamMatchesResponse:
    filters = [or_(Match.home_team_id == team_id, Match.away_team_id == team_id)]
    completed = and_(Match.home_score.is_not(None), Match.away_score.is_not(None))
    if scope == "past":
        filters.extend((completed, Match.match_date <= today))
    elif scope == "upcoming":
        filters.extend(
            (
                or_(Match.home_score.is_(None), Match.away_score.is_(None)),
                Match.match_date >= today,
            )
        )
    if competition_id is not None:
        filters.append(Match.competition_id == competition_id)

    total = int(
        session.scalar(select(func.count(Match.id)).where(*filters)) or 0
    )
    ordering = (
        (Match.match_date.asc(), Match.kickoff_at.asc(), Match.id.asc())
        if scope == "upcoming"
        else (Match.match_date.desc(), Match.kickoff_at.desc(), Match.id.desc())
    )
    rows = session.scalars(
        select(Match)
        .where(*filters)
        .options(
            joinedload(Match.competition).joinedload(Competition.country),
            joinedload(Match.season),
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TeamMatchesResponse(
        team_id=team_id,
        scope=scope,
        items=[_match_item(row, team_id) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=_total_pages(total, page_size),
    )


def get_team_matches(
    session: Session,
    *,
    team_id: int,
    scope: HistoryScope,
    today: date,
    page: int,
    page_size: int,
    competition_id: int | None,
) -> TeamMatchesResponse:
    return _matches_response(
        session,
        team_id=team_id,
        scope=scope,
        today=today,
        page=page,
        page_size=page_size,
        competition_id=competition_id,
    )


def get_head_to_head(
    session: Session,
    *,
    team_id: int,
    opponent_id: int,
    today: date,
    page: int,
    page_size: int,
) -> TeamMatchesResponse:
    completed = and_(Match.home_score.is_not(None), Match.away_score.is_not(None))
    pairing = or_(
        and_(Match.home_team_id == team_id, Match.away_team_id == opponent_id),
        and_(Match.home_team_id == opponent_id, Match.away_team_id == team_id),
    )
    filters = (pairing, completed, Match.match_date <= today)
    total = int(session.scalar(select(func.count(Match.id)).where(*filters)) or 0)
    rows = session.scalars(
        select(Match)
        .where(*filters)
        .options(
            joinedload(Match.competition).joinedload(Competition.country),
            joinedload(Match.season),
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
        .order_by(Match.match_date.desc(), Match.kickoff_at.desc(), Match.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TeamMatchesResponse(
        team_id=team_id,
        scope="h2h",
        opponent_id=opponent_id,
        items=[_match_item(row, team_id) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=_total_pages(total, page_size),
    )
