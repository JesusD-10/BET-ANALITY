from __future__ import annotations

from sqlalchemy import event, text

from app.db.session import Base, engine

# Importing registers every table on Base.metadata.
from app.db import models as _models  # noqa: F401,E402


_sqlite_listener_registered = False


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def init_database() -> None:
    """Create the current schema safely when it does not exist.

    ``create_all`` is idempotent and is useful for the first Render bootstrap.
    Future destructive/transformative schema changes should be performed with
    versioned Alembic migrations instead of changing existing tables here.
    """

    global _sqlite_listener_registered
    if engine.url.get_backend_name() == "sqlite":
        if not _sqlite_listener_registered:
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
            _sqlite_listener_registered = True
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(bind=engine)


def main() -> int:
    init_database()
    print("Database schema initialized successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
