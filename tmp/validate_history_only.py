import json
import os
from pathlib import Path


root = Path(__file__).resolve().parents[1]
database_path = root / "tmp" / "history_server_validation.db"
os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

from app.db.audit_database import audit_database  # noqa: E402
from app.db.import_historical import import_historical  # noqa: E402


report = import_historical(
    root / "Base de datos",
    dry_run=False,
    batch_size=1_000,
    progress_every=25_000,
    include_odds=False,
)
audit = audit_database(minimum_matches=274_000) if not report.errors else None
print(
    json.dumps(
        {
            "database_bytes": database_path.stat().st_size,
            "import": report.as_dict(),
            "audit": audit,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
)
