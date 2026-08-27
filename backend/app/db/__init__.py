from __future__ import annotations

from datetime import date

from app.schemas.matches import MatchSummary


def init_database() -> None:
    from app.db.init_db import init_database as initialize

    initialize()


def persist_matches(matches: list[MatchSummary]) -> int:
    from app.db.repository import persist_matches as persist

    return persist(matches)


def load_matches(match_date: date) -> list[MatchSummary]:
    from app.db.repository import load_matches as load

    return load(match_date)


def load_match(public_id: str) -> MatchSummary | None:
    from app.db.repository import load_match as load

    return load(public_id)

__all__ = ["init_database", "load_match", "load_matches", "persist_matches"]
