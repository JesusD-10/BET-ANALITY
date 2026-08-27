from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Column declarations are deliberately explicit instead of relying on
# PostgreSQL-only types so the same schema works in local SQLite tests.
class Country(Base):
    __tablename__ = "countries"

    id = Column(Integer, primary_key=True)
    code = Column(String(8), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(140), nullable=False, unique=True)
    flag_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    competitions = relationship("Competition", back_populates="country")
    teams = relationship("Team", back_populates="country")


class Competition(Base):
    __tablename__ = "competitions"
    __table_args__ = (
        UniqueConstraint("country_id", "slug", name="uq_competition_country_slug"),
        UniqueConstraint("source_provider", "external_id", name="uq_competition_provider_external"),
    )

    id = Column(Integer, primary_key=True)
    country_id = Column(
        ForeignKey("countries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name = Column(String(180), nullable=False)
    slug = Column(String(200), nullable=False)
    kind = Column(String(24), nullable=False, default="league")
    source_provider = Column(String(64), nullable=True)
    external_id = Column(String(128), nullable=True)
    logo_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    country = relationship("Country", back_populates="competitions")
    seasons = relationship("Season", back_populates="competition", cascade="all, delete-orphan")
    matches = relationship("Match", back_populates="competition")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("competition_id", "label", name="uq_season_competition_label"),
        CheckConstraint("end_year >= start_year", name="ck_season_year_order"),
    )

    id = Column(Integer, primary_key=True)
    competition_id = Column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label = Column(String(32), nullable=False)
    start_year = Column(Integer, nullable=False)
    end_year = Column(Integer, nullable=False)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    competition = relationship("Competition", back_populates="seasons")
    matches = relationship("Match", back_populates="season")
    squad_memberships = relationship("SquadMembership", back_populates="season")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("country_id", "slug", name="uq_team_country_slug"),
        UniqueConstraint("source_provider", "external_id", name="uq_team_provider_external"),
        Index("ix_teams_slug", "slug"),
        Index("ix_teams_name", "name"),
    )

    id = Column(Integer, primary_key=True)
    country_id = Column(
        ForeignKey("countries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name = Column(String(180), nullable=False)
    slug = Column(String(200), nullable=False)
    short_code = Column(String(16), nullable=True)
    kind = Column(String(24), nullable=False, default="club")
    source_provider = Column(String(64), nullable=True)
    external_id = Column(String(128), nullable=True)
    logo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    country = relationship("Country", back_populates="teams")
    squad_memberships = relationship("SquadMembership", back_populates="team")


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("source_provider", "external_id", name="uq_player_provider_external"),
    )

    id = Column(Integer, primary_key=True)
    identity_key = Column(String(128), nullable=False, unique=True)
    name = Column(String(180), nullable=False)
    slug = Column(String(200), nullable=False, index=True)
    short_name = Column(String(120), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    nationality_country_id = Column(
        ForeignKey("countries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    preferred_position = Column(String(40), nullable=True)
    source_provider = Column(String(64), nullable=True)
    external_id = Column(String(128), nullable=True)
    photo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    nationality = relationship("Country")
    squad_memberships = relationship("SquadMembership", back_populates="player")


class SquadMembership(Base):
    __tablename__ = "squad_memberships"
    __table_args__ = (
        UniqueConstraint("player_id", "team_id", "season_id", name="uq_squad_player_team_season"),
        CheckConstraint("shirt_number IS NULL OR shirt_number >= 0", name="ck_squad_shirt_number"),
    )

    id = Column(Integer, primary_key=True)
    player_id = Column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id = Column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_id = Column(
        ForeignKey("seasons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    shirt_number = Column(Integer, nullable=True)
    position = Column(String(40), nullable=True)
    joined_on = Column(Date, nullable=True)
    left_on = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    player = relationship("Player", back_populates="squad_memberships")
    team = relationship("Team", back_populates="squad_memberships")
    season = relationship("Season", back_populates="squad_memberships")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("source_provider", "external_id", name="uq_match_provider_external"),
        CheckConstraint("home_team_id <> away_team_id", name="ck_match_different_teams"),
        CheckConstraint("home_score IS NULL OR home_score >= 0", name="ck_match_home_score"),
        CheckConstraint("away_score IS NULL OR away_score >= 0", name="ck_match_away_score"),
        Index("ix_matches_date_status", "match_date", "status"),
        Index("ix_matches_competition_kickoff", "competition_id", "kickoff_at"),
        Index("ix_matches_home_date", "home_team_id", "match_date"),
        Index("ix_matches_away_date", "away_team_id", "match_date"),
    )

    id = Column(Integer, primary_key=True)
    public_id = Column(String(160), nullable=False, unique=True)
    fingerprint = Column(String(64), nullable=False, unique=True)
    competition_id = Column(
        ForeignKey("competitions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    season_id = Column(
        ForeignKey("seasons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    home_team_id = Column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    away_team_id = Column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    match_date = Column(Date, nullable=False, index=True)
    kickoff_at = Column(DateTime(timezone=True), nullable=False, index=True)
    kickoff_precision = Column(String(24), nullable=False, default="datetime")
    status = Column(String(64), nullable=False, index=True)
    status_short = Column(String(24), nullable=True, index=True)
    live_minute = Column(Integer, nullable=True)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    half_time_home_score = Column(Integer, nullable=True)
    half_time_away_score = Column(Integer, nullable=True)
    round = Column(String(120), nullable=True)
    venue_id = Column(String(128), nullable=True)
    venue = Column(String(240), nullable=True)
    referee = Column(String(180), nullable=True)
    home_form = Column(String(80), nullable=True)
    away_form = Column(String(80), nullable=True)
    data_quality = Column(Float, nullable=False, default=0.9)
    odds_available = Column(Boolean, nullable=False, default=False)
    source_provider = Column(String(64), nullable=False)
    external_id = Column(String(128), nullable=True)
    source_url = Column(String(1000), nullable=True)
    source_hash = Column(String(64), nullable=True, index=True)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    competition = relationship("Competition", back_populates="matches")
    season = relationship("Season", back_populates="matches")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    team_statistics = relationship(
        "MatchTeamStatistics", back_populates="match", cascade="all, delete-orphan"
    )
    odds = relationship("MatchOdds", back_populates="match", cascade="all, delete-orphan")
    import_records = relationship("ImportRecord", back_populates="match", cascade="all, delete-orphan")


class MatchTeamStatistics(Base):
    __tablename__ = "match_team_statistics"
    __table_args__ = (
        UniqueConstraint("match_id", "side", name="uq_match_statistics_side"),
        CheckConstraint("side IN ('home', 'away')", name="ck_match_statistics_side"),
    )

    id = Column(Integer, primary_key=True)
    match_id = Column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id = Column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    side = Column(String(8), nullable=False)
    expected_goals = Column(Float, nullable=True)
    shots = Column(Integer, nullable=True)
    shots_on_target = Column(Integer, nullable=True)
    fouls = Column(Integer, nullable=True)
    corners = Column(Integer, nullable=True)
    yellow_cards = Column(Integer, nullable=True)
    red_cards = Column(Integer, nullable=True)
    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    match = relationship("Match", back_populates="team_statistics")
    team = relationship("Team")


class MatchOdds(Base):
    __tablename__ = "match_odds"
    __table_args__ = (
        CheckConstraint("odds > 1", name="ck_match_odds_price"),
        Index("ix_match_odds_market", "match_id", "market_key", "is_closing"),
    )

    id = Column(Integer, primary_key=True)
    odds_key = Column(String(64), nullable=False, unique=True)
    match_id = Column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bookmaker = Column(String(80), nullable=False)
    market_key = Column(String(64), nullable=False)
    selection = Column(String(64), nullable=False)
    line = Column(Float, nullable=True)
    odds = Column(Float, nullable=False)
    is_closing = Column(Boolean, nullable=False, default=False)
    captured_at = Column(DateTime(timezone=True), nullable=True)
    source_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    match = relationship("Match", back_populates="odds")


class ImportRecord(Base):
    __tablename__ = "import_records"
    __table_args__ = (
        Index("ix_import_records_source", "source_file", "sheet_name", "row_number"),
    )

    id = Column(Integer, primary_key=True)
    row_hash = Column(String(64), nullable=False, unique=True)
    file_hash = Column(String(64), nullable=False, index=True)
    match_id = Column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file = Column(Text, nullable=False)
    sheet_name = Column(String(180), nullable=False)
    row_number = Column(Integer, nullable=False)
    imported_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    match = relationship("Match", back_populates="import_records")
