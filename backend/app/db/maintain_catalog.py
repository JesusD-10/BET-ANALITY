from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.catalog import canonical_competition_name, season_from_match_date, slugify
from app.db.models import Competition, Match, Season, Team
from app.db.session import SessionLocal
from app.db.team_repository import _canonical_team_key_for_row
from app.services.api_football import APIFootballAPIError, APIFootballProvider


def maintain_catalog(
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """Consolidate known catalog aliases without deleting match history."""
    report = {"competitions_merged": 0, "seasons_reassigned": 0, "logos_propagated": 0}
    with session_factory() as session:
        competitions = list(session.scalars(select(Competition)))
        for source in competitions:
            canonical_name = canonical_competition_name(source.country.code, source.name)
            if canonical_name == source.name:
                continue
            target = session.scalar(
                select(Competition).where(
                    Competition.country_id == source.country_id,
                    Competition.slug == slugify(canonical_name),
                )
            )
            if target is None:
                source.name = canonical_name
                source.slug = slugify(canonical_name)
                report["competitions_merged"] += 1
                continue
            for match in session.scalars(select(Match).where(Match.competition_id == source.id)):
                match.competition = target
            session.flush()
            session.delete(source)
            report["competitions_merged"] += 1

        season_cache: dict[tuple[int, str], Season] = {}
        matches = session.scalars(
            select(Match).join(Match.competition).join(Competition.country)
            .where(Competition.kind == "league")
        )
        for match in matches:
            derived = season_from_match_date(match.competition.country.code, match.match_date)
            if derived is None:
                continue
            label, start_year, end_year = derived
            key = (match.competition_id, label)
            season = season_cache.get(key)
            if season is None:
                season = session.scalar(
                    select(Season).where(
                        Season.competition_id == match.competition_id,
                        Season.label == label,
                    )
                )
                if season is None:
                    season = Season(
                        competition_id=match.competition_id,
                        label=label,
                        start_year=start_year,
                        end_year=end_year,
                    )
                    session.add(season)
                    session.flush()
                season_cache[key] = season
            if match.season_id != season.id:
                match.season = season
                report["seasons_reassigned"] += 1

        teams_by_identity: dict[tuple[int, str], list[Team]] = defaultdict(list)
        for team in session.scalars(select(Team)):
            teams_by_identity[(team.country_id, _canonical_team_key_for_row(team.name))].append(team)
        for aliases in teams_by_identity.values():
            logo = next((team.logo_url for team in aliases if team.logo_url), None)
            if logo is None:
                continue
            for team in aliases:
                if not team.logo_url:
                    team.logo_url = logo
                    report["logos_propagated"] += 1

        session.commit()
    return report


def enrich_missing_logos(
    *,
    limit: int,
    provider: APIFootballProvider | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """Look up a bounded number of missing official crests by team name."""
    if limit < 1:
        return {"looked_up": 0, "logos_found": 0}
    if provider is None:
        if not settings.api_football_key.strip():
            raise RuntimeError("API_FOOTBALL_KEY no está configurada.")
        provider = APIFootballProvider(
            key=settings.api_football_key,
            base_url=settings.api_football_base_url,
            is_rapidapi=settings.api_football_is_rapidapi,
            timeout=settings.api_football_timeout_seconds,
        )
    report = {"looked_up": 0, "logos_found": 0}
    with session_factory() as session:
        teams = list(session.scalars(select(Team).where(Team.logo_url.is_(None)).limit(limit)))
        for team in teams:
            report["looked_up"] += 1
            try:
                candidates = provider.get_teams(search=team.name)
            except APIFootballAPIError:
                continue
            matched = next(
                (
                    item.get("team", {})
                    for item in candidates
                    if _canonical_team_key_for_row(str(item.get("team", {}).get("name", "")))
                    == _canonical_team_key_for_row(team.name)
                ),
                None,
            )
            if not isinstance(matched, dict) or not matched.get("logo"):
                continue
            team.logo_url = str(matched["logo"])
            if not team.external_id and matched.get("id") is not None:
                team.source_provider = APIFootballProvider.provider_name
                team.external_id = str(matched["id"])
            report["logos_found"] += 1
        session.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repara competencias, temporadas y logos del catálogo histórico.")
    parser.add_argument("--fetch-logos", action="store_true")
    parser.add_argument("--logo-limit", type=int, default=50)
    args = parser.parse_args(argv)
    report: dict[str, object] = {"catalog": maintain_catalog()}
    if args.fetch_logos:
        report["logos"] = enrich_missing_logos(limit=max(0, args.logo_limit))
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())