from __future__ import annotations

import argparse
from datetime import date, datetime
import json

from sqlalchemy import case, func, select, text

from app.db.models import (
    Competition,
    Country,
    ImportRecord,
    Match,
    MatchOdds,
    MatchTeamStatistics,
    Season,
    Team,
)
from app.db.session import SessionLocal, engine


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def audit_database(*, minimum_matches: int = 0) -> dict[str, object]:
    tables = {
        "countries": Country,
        "competitions": Competition,
        "seasons": Season,
        "teams": Team,
        "matches": Match,
        "match_team_statistics": MatchTeamStatistics,
        "match_odds": MatchOdds,
        "import_records": ImportRecord,
    }
    with SessionLocal() as session:
        counts = {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in tables.items()
        }
        source_counts = {
            str(provider): int(count)
            for provider, count in session.execute(
                select(Match.source_provider, func.count(Match.id))
                .group_by(Match.source_provider)
                .order_by(Match.source_provider)
            )
        }
        status_counts = {
            str(status): int(count)
            for status, count in session.execute(
                select(Match.status, func.count(Match.id))
                .group_by(Match.status)
                .order_by(Match.status)
            )
        }
        earliest, latest = session.execute(
            select(func.min(Match.match_date), func.max(Match.match_date))
        ).one()
        archive_records = int(
            session.scalar(
                select(func.count(ImportRecord.id)).where(
                    ImportRecord.source_file.like("%.zip!/%")
                )
            )
            or 0
        )
        bad_team_pairs = int(
            session.scalar(
                select(func.count(Match.id)).where(
                    Match.home_team_id == Match.away_team_id
                )
            )
            or 0
        )
        duplicate_fingerprints = int(
            session.scalar(
                select(func.count()).select_from(
                    select(Match.fingerprint)
                    .group_by(Match.fingerprint)
                    .having(func.count(Match.id) > 1)
                    .subquery()
                )
            )
            or 0
        )
        first_team = case(
            (Match.home_team_id < Match.away_team_id, Match.home_team_id),
            else_=Match.away_team_id,
        )
        second_team = case(
            (Match.home_team_id < Match.away_team_id, Match.away_team_id),
            else_=Match.home_team_id,
        )
        reversed_duplicate_pairs = int(
            session.scalar(
                select(func.count()).select_from(
                    select(
                        Match.competition_id,
                        Match.match_date,
                        first_team.label("first_team"),
                        second_team.label("second_team"),
                    )
                    .join(Competition, Competition.id == Match.competition_id)
                    .join(Country, Country.id == Competition.country_id)
                    .where(Country.code == "INT")
                    .group_by(
                        Match.competition_id,
                        Match.match_date,
                        first_team,
                        second_team,
                    )
                    .having(func.count(Match.id) > 1)
                    .subquery()
                )
            )
            or 0
        )
        matches_with_statistics = int(
            session.scalar(
                select(func.count(func.distinct(MatchTeamStatistics.match_id)))
            )
            or 0
        )
        matches_with_odds = int(
            session.scalar(select(func.count(func.distinct(MatchOdds.match_id))))
            or 0
        )

    integrity = "database-managed"
    foreign_key_violations = 0
    if engine.url.get_backend_name() == "sqlite":
        with engine.connect() as connection:
            integrity = str(connection.execute(text("PRAGMA integrity_check")).scalar_one())
            foreign_key_violations = len(
                connection.execute(text("PRAGMA foreign_key_check")).all()
            )

    checks = {
        "minimum_matches": counts["matches"] >= max(0, minimum_matches),
        "integrity": integrity.casefold() in {"ok", "database-managed"},
        "foreign_keys": foreign_key_violations == 0,
        "different_teams": bad_team_pairs == 0,
        "unique_fingerprints": duplicate_fingerprints == 0,
        "unique_international_pair_dates": reversed_duplicate_pairs == 0,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "backend": engine.url.get_backend_name(),
        "counts": counts,
        "source_counts": source_counts,
        "status_counts": status_counts,
        "coverage": {
            "matches_with_statistics": matches_with_statistics,
            "matches_with_odds": matches_with_odds,
        },
        "date_range": {"earliest": earliest, "latest": latest},
        "archive_import_records": archive_records,
        "integrity": integrity,
        "foreign_key_violations": foreign_key_violations,
        "duplicate_fingerprints": duplicate_fingerprints,
        "reversed_duplicate_pairs": reversed_duplicate_pairs,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the BET ANALIZADOR database")
    parser.add_argument("--minimum-matches", type=int, default=0)
    args = parser.parse_args(argv)
    report = audit_database(minimum_matches=args.minimum_matches)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
