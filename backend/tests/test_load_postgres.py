from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db import load_postgres as loader
from app.db.import_historical import ImportReport


class _Connection:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement) -> None:
        self.statements.append(statement)


class _Engine:
    def __init__(self, backend: str) -> None:
        self.url = SimpleNamespace(get_backend_name=lambda: backend)
        self.connection = _Connection()

    def connect(self) -> _Connection:
        return self.connection


def test_load_refuses_non_postgresql_destination(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "engine", _Engine("sqlite"))

    with pytest.raises(loader.PostgreSQLLoadError, match="DATABASE_URL"):
        loader.load_postgres(tmp_path)


def test_load_initializes_imports_and_audits_postgresql(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    fake_engine = _Engine("postgresql")
    report = ImportReport(dry_run=False, matches_inserted=274_201)
    monkeypatch.setattr(loader, "engine", fake_engine)
    monkeypatch.setattr(loader, "init_database", lambda: calls.append("init"))

    def fake_import(path, **kwargs):
        calls.append((path, kwargs))
        return report

    monkeypatch.setattr(loader, "import_historical", fake_import)
    monkeypatch.setattr(
        loader,
        "audit_database",
        lambda **kwargs: {"status": "ok", "minimum": kwargs["minimum_matches"]},
    )

    result, audit = loader.load_postgres(
        tmp_path,
        batch_size=250,
        progress_every=50,
        minimum_matches=274_000,
    )

    assert result is report
    assert audit == {"status": "ok", "minimum": 274_000}
    assert calls[0] == "init"
    assert calls[1] == (
        tmp_path,
        {
            "dry_run": False,
            "batch_size": 250,
            "progress_every": 50,
            "include_odds": True,
        },
    )
    assert len(fake_engine.connection.statements) == 1


def test_load_skips_audit_when_import_reports_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "engine", _Engine("postgresql"))
    monkeypatch.setattr(loader, "init_database", lambda: None)
    monkeypatch.setattr(
        loader,
        "import_historical",
        lambda *_args, **_kwargs: ImportReport(
            dry_run=False,
            errors=["source failed"],
        ),
    )
    monkeypatch.setattr(
        loader,
        "audit_database",
        lambda **_kwargs: pytest.fail("audit must not run after an import error"),
    )

    report, audit = loader.load_postgres(tmp_path)

    assert report.errors == ["source failed"]
    assert audit is None
