from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import date
import json
import re
import sys
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.catalog import CountrySpec, resolve_country, slugify
from app.db.init_db import init_database
from app.db.models import (
    Competition,
    Country,
    Player,
    Season,
    SquadMembership,
    Team,
)
from app.db.session import SessionLocal
from app.services.api_football import APIFootballAPIError, APIFootballProvider


PROVIDER = APIFootballProvider.provider_name


class CatalogSyncError(RuntimeError):
    """A safe, user-facing catalog synchronization failure."""


@dataclass(frozen=True, slots=True)
class SyncOptions:
    league_id: str
    season: int
    competition_name: str
    country: str
    country_code: str
    team_id: str | None = None
    dry_run: bool = False


@dataclass(slots=True)
class SyncReport:
    dry_run: bool
    league_id: str
    season: int
    competition_name: str
    teams_received: int = 0
    teams_upserted: int = 0
    teams_created: int = 0
    squads_requested: int = 0
    squads_skipped: int = 0
    players_upserted: int = 0
    players_created: int = 0
    memberships_upserted: int = 0
    memberships_created: int = 0
    quota_remaining: int | None = None
    quota_limit: int | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _text(value: object) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _external_id(value: object) -> str | None:
    clean = _text(value)
    return clean if clean and clean.isdigit() else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _date(value: object) -> date | None:
    clean = _text(value)
    if clean is None:
        return None
    try:
        return date.fromisoformat(clean[:10])
    except ValueError:
        return None


def _country_spec(name: str, code: str | None = None) -> CountrySpec:
    clean_name = _text(name) or "Unknown"
    clean_code = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())[:8]
    if clean_code:
        return CountrySpec(clean_code, clean_name)
    resolved = resolve_country(clean_name)
    return CountrySpec(resolved.code, clean_name if resolved.name == "Unknown" else resolved.name)


def _canonical_team_key(name: str) -> str:
    raw = " ".join(str(name or "").split())
    without_prefix = re.sub(
        r"^(fc|cf|club|c\.f\.|ac|sc|rc|us|ud|cd|sv|sl|deportivo|athletic|atletico|real)\s+",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    without_reserve = re.sub(
        r"\s+(b|ii|iii|iv|reserve|reserves|academy)$",
        "",
        without_prefix or raw,
        flags=re.IGNORECASE,
    )
    return slugify(without_reserve)


def _upsert_country(session: Session, spec: CountrySpec) -> tuple[Country, bool]:
    country = session.scalar(select(Country).where(Country.code == spec.code))
    if country is None:
        country = session.scalar(select(Country).where(Country.slug == slugify(spec.name)))
    created = country is None
    if country is None:
        country = Country(code=spec.code, name=spec.name, slug=slugify(spec.name))
        session.add(country)
        session.flush()
    else:
        country.name = spec.name
    return country, created


def _upsert_competition(
    session: Session,
    *,
    country: Country,
    external_id: str,
    name: str,
) -> tuple[Competition, bool]:
    competition = session.scalar(
        select(Competition).where(
            Competition.source_provider == PROVIDER,
            Competition.external_id == external_id,
        )
    )
    if competition is None:
        competition = session.scalar(
            select(Competition).where(
                Competition.country_id == country.id,
                Competition.slug == slugify(name),
            )
        )
    created = competition is None
    if competition is None:
        competition = Competition(
            country=country,
            name=name,
            slug=slugify(name),
            source_provider=PROVIDER,
            external_id=external_id,
        )
        session.add(competition)
        session.flush()
    else:
        competition.country = country
        competition.name = name
        competition.slug = slugify(name)
        if not competition.external_id:
            competition.source_provider = PROVIDER
            competition.external_id = external_id
        competition.is_active = True
    return competition, created


def _upsert_season(
    session: Session,
    *,
    competition: Competition,
    year: int,
) -> tuple[Season, bool]:
    label = str(year)
    season = session.scalar(
        select(Season).where(
            Season.competition_id == competition.id,
            Season.label == label,
        )
    )
    created = season is None
    if season is None:
        season = Season(
            competition=competition,
            label=label,
            start_year=year,
            end_year=year,
        )
        session.add(season)
        session.flush()
    return season, created


def _upsert_team(
    session: Session,
    *,
    country: Country,
    payload: dict,
) -> tuple[Team, bool] | None:
    external_id = _external_id(payload.get("id"))
    name = _text(payload.get("name"))
    if external_id is None or name is None:
        return None
    team = session.scalar(
        select(Team).where(
            Team.source_provider == PROVIDER,
            Team.external_id == external_id,
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
        canonical = _canonical_team_key(name)
        team = next(
            (
                candidate
                for candidate in session.scalars(
                    select(Team).where(Team.country_id == country.id)
                )
                if _canonical_team_key(candidate.name) == canonical
            ),
            None,
        )
    created = team is None
    if team is None:
        team = Team(
            country=country,
            name=name,
            slug=slugify(name),
            kind="national" if payload.get("national") is True else "club",
            source_provider=PROVIDER,
            external_id=external_id,
        )
        session.add(team)
        session.flush()
    else:
        team.country = country
        team.name = name
        team.slug = slugify(name)
        if not team.external_id:
            team.source_provider = PROVIDER
            team.external_id = external_id
    team.short_code = _text(payload.get("code")) or team.short_code
    team.logo_url = _text(payload.get("logo")) or team.logo_url
    return team, created


def _upsert_player(
    session: Session,
    payload: dict,
) -> tuple[Player, bool] | None:
    external_id = _external_id(payload.get("id"))
    name = _text(payload.get("name"))
    if external_id is None or name is None:
        return None
    identity_key = f"{PROVIDER}:{external_id}"
    player = session.scalar(
        select(Player).where(
            Player.source_provider == PROVIDER,
            Player.external_id == external_id,
        )
    )
    if player is None:
        player = session.scalar(select(Player).where(Player.identity_key == identity_key))
    created = player is None
    if player is None:
        player = Player(
            identity_key=identity_key,
            name=name,
            slug=slugify(name),
            source_provider=PROVIDER,
            external_id=external_id,
        )
        session.add(player)
        session.flush()
    else:
        player.name = name
        player.slug = slugify(name)
        player.source_provider = PROVIDER
        player.external_id = external_id
    player.short_name = _text(payload.get("short_name")) or player.short_name
    player.preferred_position = _text(payload.get("position")) or player.preferred_position
    player.photo_url = _text(payload.get("photo")) or player.photo_url
    birth = payload.get("birth") if isinstance(payload.get("birth"), dict) else {}
    player.date_of_birth = _date(birth.get("date")) or player.date_of_birth
    return player, created


def _upsert_membership(
    session: Session,
    *,
    player: Player,
    team: Team,
    season: Season,
    payload: dict,
) -> tuple[SquadMembership, bool]:
    membership = session.scalar(
        select(SquadMembership).where(
            SquadMembership.player_id == player.id,
            SquadMembership.team_id == team.id,
            SquadMembership.season_id == season.id,
        )
    )
    created = membership is None
    if membership is None:
        membership = SquadMembership(player=player, team=team, season=season)
        session.add(membership)
    membership.shirt_number = _nonnegative_int(payload.get("number"))
    membership.position = _text(payload.get("position")) or membership.position
    membership.is_active = True
    return membership, created


def _provider_from_settings() -> APIFootballProvider:
    key = settings.api_football_key.strip()
    if not key:
        raise CatalogSyncError(
            "API_FOOTBALL_KEY no está configurada; no se realizó ninguna consulta."
        )
    return APIFootballProvider(
        key=key,
        base_url=settings.api_football_base_url,
        is_rapidapi=settings.api_football_is_rapidapi,
        timeout=settings.api_football_timeout_seconds,
    )


def _validate_options(options: SyncOptions) -> SyncOptions:
    league_id = _external_id(options.league_id)
    team_id = _external_id(options.team_id) if options.team_id is not None else None
    if league_id is None:
        raise CatalogSyncError("--league-id debe ser un identificador numérico.")
    if options.team_id is not None and team_id is None:
        raise CatalogSyncError("--team-id debe ser un identificador numérico.")
    if options.season < 1800 or options.season > 2200:
        raise CatalogSyncError("--season debe ser un año válido.")
    competition_name = _text(options.competition_name)
    country = _text(options.country)
    country_code = re.sub(r"[^A-Z0-9]", "", options.country_code.upper())[:8]
    if competition_name is None or country is None or not country_code:
        raise CatalogSyncError(
            "--competition-name, --country y --country-code no pueden estar vacíos."
        )
    return SyncOptions(
        league_id=league_id,
        season=options.season,
        competition_name=competition_name,
        country=country,
        country_code=country_code,
        team_id=team_id,
        dry_run=options.dry_run,
    )


def sync_catalog(
    options: SyncOptions,
    *,
    provider: APIFootballProvider | None = None,
    session_factory: Callable[[], Session] | None = None,
    initialize_schema: bool = True,
) -> SyncReport:
    """Fetch and idempotently persist one API-Football competition catalog."""

    options = _validate_options(options)
    provider = provider or _provider_from_settings()
    report = SyncReport(
        dry_run=options.dry_run,
        league_id=options.league_id,
        season=options.season,
        competition_name=options.competition_name,
    )

    # The free status call seeds quota information before the N squad calls.
    provider.get_status()
    if options.team_id:
        team_rows = provider.get_teams(team_id=options.team_id)
    else:
        team_rows = provider.get_teams(
            league_id=options.league_id,
            season=options.season,
        )
    report.teams_received = len(team_rows)

    if not options.dry_run and initialize_schema:
        init_database()

    session = (session_factory or SessionLocal)()
    try:
        competition_country, _ = _upsert_country(
            session,
            _country_spec(options.country, options.country_code),
        )
        competition, _ = _upsert_competition(
            session,
            country=competition_country,
            external_id=options.league_id,
            name=options.competition_name,
        )
        season, _ = _upsert_season(
            session,
            competition=competition,
            year=options.season,
        )

        squads_enabled = True
        for row in team_rows:
            raw_team = row.get("team") if isinstance(row, dict) else None
            if not isinstance(raw_team, dict):
                report.warnings.append("Se ignoró un equipo con formato inválido.")
                continue
            raw_country = _text(raw_team.get("country"))
            if raw_country and raw_country.casefold() != options.country.casefold():
                team_country_spec = _country_spec(raw_country)
            else:
                team_country_spec = _country_spec(options.country, options.country_code)
            team_country, _ = _upsert_country(session, team_country_spec)
            upserted_team = _upsert_team(
                session,
                country=team_country,
                payload=raw_team,
            )
            if upserted_team is None:
                report.warnings.append("Se ignoró un equipo sin id o nombre válido.")
                continue
            team, team_created = upserted_team
            report.teams_upserted += 1
            report.teams_created += int(team_created)

            reserve = settings.api_football_optional_quota_reserve
            if not squads_enabled or not provider.can_fetch_optional(reserve):
                report.squads_skipped += 1
                report.warnings.append(
                    f"Plantilla omitida para team_id={team.external_id}: reserva de cuota alcanzada."
                )
                continue
            try:
                squad_rows = provider.get_squads(team_id=team.external_id)
            except APIFootballAPIError as exc:
                report.squads_skipped += 1
                report.warnings.append(
                    f"Plantilla no disponible para team_id={team.external_id} ({exc.code})."
                )
                if exc.code in {
                    "authentication_error",
                    "quota_exhausted",
                    "rate_limited",
                    "provider_cooldown",
                }:
                    squads_enabled = False
                continue
            report.squads_requested += 1

            players_by_id: dict[str, dict] = {}
            for squad_row in squad_rows:
                if not isinstance(squad_row, dict):
                    continue
                squad_team = squad_row.get("team")
                squad_team_id = (
                    _external_id(squad_team.get("id"))
                    if isinstance(squad_team, dict)
                    else None
                )
                if squad_team_id and squad_team_id != team.external_id:
                    continue
                for raw_player in squad_row.get("players") or []:
                    if not isinstance(raw_player, dict):
                        continue
                    player_id = _external_id(raw_player.get("id"))
                    if player_id:
                        players_by_id[player_id] = raw_player

            for raw_player in players_by_id.values():
                upserted_player = _upsert_player(session, raw_player)
                if upserted_player is None:
                    continue
                player, player_created = upserted_player
                report.players_upserted += 1
                report.players_created += int(player_created)
                _, membership_created = _upsert_membership(
                    session,
                    player=player,
                    team=team,
                    season=season,
                    payload=raw_player,
                )
                report.memberships_upserted += 1
                report.memberships_created += int(membership_created)

        if options.dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    quota = provider.quota_snapshot
    remaining = quota.get("remaining")
    limit = quota.get("limit")
    report.quota_remaining = remaining if isinstance(remaining, int) else None
    report.quota_limit = limit if isinstance(limit, int) else None
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincroniza clubes, logos, jugadores y plantillas desde API-Football."
    )
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--competition-name", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--country-code", required=True)
    parser.add_argument("--team-id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="consulta la API y valida los upserts, pero revierte la transacción",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = SyncOptions(
        league_id=args.league_id,
        season=args.season,
        competition_name=args.competition_name,
        country=args.country,
        country_code=args.country_code,
        team_id=args.team_id,
        dry_run=args.dry_run,
    )
    try:
        report = sync_catalog(options)
    except (CatalogSyncError, APIFootballAPIError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "La sincronización del catálogo falló.",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
