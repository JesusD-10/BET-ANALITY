from __future__ import annotations

from sqlalchemy import func, inspect, select, text

from app.db import models as _models  # noqa: F401
from app.db.models import Match, Team
from app.db.session import SessionLocal, engine


EXPECTED_TABLES = frozenset(
    {
        "countries",
        "competitions",
        "seasons",
        "teams",
        "players",
        "squad_memberships",
        "matches",
        "match_team_statistics",
        "match_lineups",
        "match_lineup_players",
        "match_events",
        "match_odds",
        "import_records",
    }
)


def database_is_ready() -> bool:
    """Return whether the connection works and the complete schema exists.

    Connection exceptions deliberately collapse to ``False``. Callers should
    expose only a generic readiness state and never serialize exception text.
    """

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            existing_tables = frozenset(inspect(connection).get_table_names())
            if not EXPECTED_TABLES.issubset(existing_tables):
                return False
        with SessionLocal() as session:
            teams = int(session.scalar(select(func.count(Team.id))) or 0)
            matches = int(session.scalar(select(func.count(Match.id))) or 0)
    except Exception:
        return False
    return teams > 0 and matches > 0
