from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


TeamKind = Literal["club", "national"]
TeamMatchScope = Literal["past", "upcoming", "all", "h2h"]
TeamMatchSide = Literal["home", "away"]
TeamMatchResult = Literal["win", "draw", "loss"]


class TeamCountry(BaseModel):
    code: str
    name: str
    flag_url: str | None = None


class TeamListItem(BaseModel):
    id: int
    name: str
    slug: str
    short_code: str | None = None
    kind: str
    logo_url: str | None = None
    country: TeamCountry


class TeamSearchResponse(BaseModel):
    items: list[TeamListItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class TeamStatistics(BaseModel):
    total_matches: int = Field(ge=0)
    completed_matches: int = Field(ge=0)
    upcoming_matches: int = Field(ge=0)
    wins: int = Field(ge=0)
    draws: int = Field(ge=0)
    losses: int = Field(ge=0)
    goals_for: int = Field(ge=0)
    goals_against: int = Field(ge=0)
    first_match_date: date | None = None
    last_match_date: date | None = None
    next_match_date: date | None = None


class TeamCompetitionSummary(BaseModel):
    id: int
    name: str
    slug: str
    kind: str
    country_code: str
    logo_url: str | None = None
    matches: int = Field(ge=1)


class TeamSeasonSummary(BaseModel):
    id: int
    label: str
    start_year: int
    end_year: int
    matches: int = Field(ge=1)


class TeamDetailResponse(BaseModel):
    team: TeamListItem
    statistics: TeamStatistics
    competitions: list[TeamCompetitionSummary]
    seasons: list[TeamSeasonSummary]


class TeamMatchSideItem(BaseModel):
    id: int
    name: str
    slug: str
    logo_url: str | None = None


class TeamMatchCompetition(BaseModel):
    id: int
    name: str
    slug: str
    kind: str
    country_code: str
    logo_url: str | None = None


class TeamMatchSeason(BaseModel):
    id: int
    label: str
    start_year: int
    end_year: int


class TeamMatchItem(BaseModel):
    id: str
    match_date: date
    kickoff_at: datetime
    kickoff_precision: str
    status: str
    status_short: str | None = None
    competition: TeamMatchCompetition
    season: TeamMatchSeason | None = None
    round: str | None = None
    venue: str | None = None
    home_team: TeamMatchSideItem
    away_team: TeamMatchSideItem
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    half_time_home_score: int | None = Field(default=None, ge=0)
    half_time_away_score: int | None = Field(default=None, ge=0)
    team_side: TeamMatchSide
    result: TeamMatchResult | None = None
    opponent: TeamMatchSideItem


class TeamMatchesResponse(BaseModel):
    team_id: int
    scope: TeamMatchScope
    opponent_id: int | None = None
    items: list[TeamMatchItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    upcoming: list[TeamMatchItem] = Field(default_factory=list)
