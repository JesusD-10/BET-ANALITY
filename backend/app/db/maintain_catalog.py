from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.catalog import canonical_competition_name, season_from_match_date, slugify
from app.db.models import Competition, Country, Match, Season, Team
from app.db.session import SessionLocal
from app.db.team_repository import _canonical_team_key_for_row
from app.services.api_football import APIFootballAPIError, APIFootballProvider


def maintain_catalog(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    batch_size: int = 1_000,
    progress_every: int = 10_000,
) -> dict[str, int]:
    """Consolidate known catalog aliases with short transactions per batch."""
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
                session.commit()
                continue
            for source_season in session.scalars(
                select(Season).where(Season.competition_id == source.id)
            ):
                target_season = session.scalar(
                    select(Season).where(
                        Season.competition_id == target.id,
                        Season.label == source_season.label,
                    )
                )
                if target_season is None:
                    source_season.competition_id = target.id
                else:
                    session.execute(
                        update(Match)
                        .where(Match.season_id == source_season.id)
                        .values(season_id=target_season.id)
                    )
                    session.delete(source_season)
            moved = session.execute(
                update(Match)
                .where(Match.competition_id == source.id)
                .values(competition_id=target.id)
            )
            session.delete(source)
            session.commit()
            if moved.rowcount:
                report["competitions_merged"] += 1
                continue
            report["competitions_merged"] += 1

        anomalous_seasons = session.execute(
            select(
                Season.id,
                Season.competition_id,
                Country.code.label("country_code"),
                func.min(Match.match_date).label("first_match_date"),
                func.max(Match.match_date).label("last_match_date"),
            )
            .join(Competition, Season.competition_id == Competition.id)
            .join(Country, Competition.country_id == Country.id)
            .join(Match, Match.season_id == Season.id)
            .where(Competition.kind == "league", Season.end_year > Season.start_year + 1)
            .group_by(Season.id, Season.competition_id, Country.code)
        ).all()
        processed_matches = 0
        for source in anomalous_seasons:
            if source.first_match_date is None or source.last_match_date is None:
                continue
            first = season_from_match_date(source.country_code, source.first_match_date)
            last = season_from_match_date(source.country_code, source.last_match_date)
            if first is None or last is None:
                continue
            for start_year in range(first[1], last[1] + 1):
                label = f"{start_year}-{start_year + 1}"
                target = session.scalar(
                    select(Season).where(
                        Season.competition_id == source.competition_id,
                        Season.label == label,
                    )
                )
                if target is None:
                    target = Season(
                        competition_id=source.competition_id,
                        label=label,
                        start_year=start_year,
                        end_year=start_year + 1,
                    )
                    session.add(target)
                    session.flush()
                moved = session.execute(
                    update(Match)
                    .where(
                        Match.season_id == source.id,
                        Match.match_date >= date(start_year, 8, 1),
                        Match.match_date < date(start_year + 1, 8, 1),
                    )
                    .values(season_id=target.id)
                )
                moved_count = int(moved.rowcount or 0)
                report["seasons_reassigned"] += moved_count
                processed_matches += moved_count
                session.commit()
                if processed_matches and processed_matches % max(1, progress_every) < moved_count:
                    print(
                        json.dumps(
                            {
                                "progress_matches": processed_matches,
                                "seasons_reassigned": report["seasons_reassigned"],
                            }
                        ),
                        flush=True,
                    )

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
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--progress-every", type=int, default=10_000)
    args = parser.parse_args(argv)
    report: dict[str, object] = {
        "catalog": maintain_catalog(
            batch_size=max(1, args.batch_size),
            progress_every=max(1, args.progress_every),
        )
    }
    if args.fetch_logos:
        report["logos"] = enrich_missing_logos(limit=max(0, args.logo_limit))
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())