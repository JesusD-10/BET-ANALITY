from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.audit_database import audit_database
from app.db.import_historical import DEFAULT_DATA_PATH, ImportReport, import_historical
from app.db.init_db import init_database
from app.db.models import MatchOdds
from app.db.session import engine


class PostgreSQLLoadError(RuntimeError):
    """Raised when the guarded server load cannot start safely."""


def _require_postgresql() -> None:
    backend = engine.url.get_backend_name()
    if backend != "postgresql":
        raise PostgreSQLLoadError(
            "DATABASE_URL no apunta a PostgreSQL. Configura temporalmente la "
            "External Database URL antes de ejecutar la carga del servidor."
        )


def load_postgres(
    path: str | Path = DEFAULT_DATA_PATH,
    *,
    batch_size: int = 1_000,
    progress_every: int = 10_000,
    minimum_matches: int = 274_000,
    audit: bool = True,
    include_odds: bool = True,
    purge_odds: bool = False,
) -> tuple[ImportReport, dict[str, object] | None]:
    """Load historical sources into the PostgreSQL selected by DATABASE_URL.

    The historical importer commits small batches and records every source row,
    so running this command again safely resumes an interrupted upload.
    """

    source_path = Path(path)
    if not source_path.exists():
        raise PostgreSQLLoadError(f"No existe la ruta de datos: {source_path}")

    _require_postgresql()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    init_database()
    if purge_odds:
        with engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {MatchOdds.__tablename__}"))
    report = import_historical(
        source_path,
        dry_run=False,
        batch_size=max(1, batch_size),
        progress_every=max(0, progress_every),
        include_odds=include_odds,
    )
    if report.errors:
        return report, None

    audit_report = (
        audit_database(minimum_matches=max(0, minimum_matches)) if audit else None
    )
    return report, audit_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Carga idempotente y reanudable de los históricos en PostgreSQL. "
            "DATABASE_URL debe contener la External Database URL."
        )
    )
    parser.add_argument("path", nargs="?", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument("--minimum-matches", type=int, default=274_000)
    parser.add_argument(
        "--with-odds",
        action="store_true",
        help=(
            "incluye cuotas históricas; por defecto solo se cargan partidos y estadísticas"
        ),
    )
    parser.add_argument(
        "--purge-odds",
        action="store_true",
        help="elimina las cuotas existentes antes de cargar los históricos",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="omite la auditoría final; útil solo para una carga parcial que se reanudará",
    )
    args = parser.parse_args(argv)

    try:
        report, audit_report = load_postgres(
            args.path,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            minimum_matches=args.minimum_matches,
            audit=not args.skip_audit,
            include_odds=args.with_odds,
            purge_odds=args.purge_odds,
        )
    except KeyboardInterrupt:
        print(
            "Carga interrumpida. Los lotes confirmados se conservaron; ejecuta "
            "el mismo comando para reanudar.",
            file=sys.stderr,
        )
        return 130
    except PostgreSQLLoadError as error:
        print(str(error), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(
            "No se pudo conectar o escribir en PostgreSQL. Verifica que la URL "
            "externa esté completa, que TLS esté habilitado y que tu IP tenga acceso. "
            "La credencial no se imprimió.",
            file=sys.stderr,
        )
        return 3

    output = {
        "destination": "postgresql",
        "mode": "full" if args.with_odds else "history-only",
        "import": report.as_dict(),
        "audit": audit_report,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    if report.errors:
        return 1
    if audit_report is not None and audit_report.get("status") != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
