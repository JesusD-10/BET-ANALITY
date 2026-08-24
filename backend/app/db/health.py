from __future__ import annotations

from sqlalchemy import inspect, text

from app.db import models as _models  # noqa: F401
from app.db.session import engine


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
    except Exception:
        return False
    return EXPECTED_TABLES.issubset(existing_tables)
