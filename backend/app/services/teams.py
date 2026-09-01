from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.db import persist_matches
from app.db.session import SessionLocal
from app.db.team_repository import (
    get_head_to_head as load_head_to_head,
    get_team_detail as load_team_detail,
    get_team_matches as load_team_matches,
    provider_team_id,
    search_teams as search_team_rows,
    team_exists,
)
from app.core.config import settings
from app.services.api_football import APIFootballAPIError, APIFootballProvider
from app.schemas.teams import TeamDetailResponse, TeamMatchesResponse, TeamSearchResponse


TeamHistoryScope = Literal["past", "upcoming", "all"]


class TeamNotFoundError(LookupError):
    pass


class InvalidTeamPairError(ValueError):
    pass


def _today_in_lima() -> date:
    return datetime.now(ZoneInfo("America/Lima")).date()


def search_teams(
    *,
    query: str | None,
    country_code: str | None,
    kind: str | None,
    page: int,
    page_size: int,
) -> TeamSearchResponse:
    with SessionLocal() as session:
        return search_team_rows(
            session,
            query=query,
            country_code=country_code,
            kind=kind,
            page=page,
            page_size=page_size,
        )


def get_team_detail(team_id: int) -> TeamDetailResponse:
    with SessionLocal() as session:
        result = load_team_detail(session, team_id=team_id, today=_today_in_lima())
    if result is None:
        raise TeamNotFoundError(team_id)
    return result


def get_team_matches(
    team_id: int,
    *,
    scope: TeamHistoryScope,
    page: int,
    page_size: int,
    competition_id: int | None,
    season_id: int | None = None,
) -> TeamMatchesResponse:
    with SessionLocal() as session:
        if not team_exists(session, team_id):
            raise TeamNotFoundError(team_id)
        return load_team_matches(
            session,
            team_id=team_id,
            scope=scope,
            today=_today_in_lima(),
            page=page,
            page_size=page_size,
            competition_id=competition_id,
            season_id=season_id,
        )


def get_head_to_head(
    team_id: int,
    opponent_id: int,
    *,
    page: int,
    page_size: int,
) -> TeamMatchesResponse:
    if team_id == opponent_id:
        raise InvalidTeamPairError("Los equipos del historial H2H deben ser distintos")
    with SessionLocal() as session:
        if not team_exists(session, team_id):
            raise TeamNotFoundError(team_id)
        if not team_exists(session, opponent_id):
            raise TeamNotFoundError(opponent_id)

        return load_head_to_head(
            session,
            team_id=team_id,
            opponent_id=opponent_id,
            today=_today_in_lima(),
            page=page,
            page_size=page_size,
        )


def refresh_head_to_head_upcoming(team_id: int, opponent_id: int) -> None:
    """Fetch a future meeting only after the historical database result is shown."""
    with SessionLocal() as session:
        first_provider_id = provider_team_id(
            session, team_id, APIFootballProvider.provider_name
        )
        second_provider_id = provider_team_id(
            session, opponent_id, APIFootballProvider.provider_name
        )
    if first_provider_id and second_provider_id and settings.api_football_key.strip():
        try:
            provider = APIFootballProvider(
                key=settings.api_football_key,
                base_url=settings.api_football_base_url,
                is_rapidapi=settings.api_football_is_rapidapi,
                timeout=settings.api_football_timeout_seconds,
            )
            persist_matches(provider.get_upcoming_head_to_head(first_provider_id, second_provider_id))
        except (APIFootballAPIError, ValueError):
            pass
