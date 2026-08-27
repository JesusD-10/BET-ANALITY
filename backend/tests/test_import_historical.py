from __future__ import annotations

from datetime import datetime, timezone
import zipfile

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import audit_database as auditor
from app.db import import_historical as importer
from app.db import models
from app.db.session import Base


def _memory_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _write_zip(path, member: str, text: str) -> None:
    _write_zip_members(path, {member: text})


def _write_zip_members(path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, text in members.items():
            archive.writestr(member, text)


def test_imports_spreadsheet_and_both_archives_idempotently(tmp_path, monkeypatch) -> None:
    (tmp_path / "domestic.csv").write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\n"
        "E0,20/08/2026,Arsenal,Chelsea,2,1,2.10,3.40,3.20\n"
        ",,,,,,,,23\n",
        encoding="utf-8",
    )
    _write_zip(
        tmp_path / "internationals-master.zip",
        "internationals-master/fifa_world_cup/2026_fifa_world_cup.txt",
        """= FIFA World Cup 2026

▪ Final
Sun Jul 19
  Spain 1-0 Argentina @ New York/New Jersey, United States
""",
    )
    _write_zip(
        tmp_path / "worldcup-master.zip",
        "worldcup-master/2026--canada-usa-mexico/cup.txt",
        """= World Cup 2026

▪ Final
Sun Jul 19
  15:00 UTC-4 Argentina v Spain 0-1 (0-0) @ New York/New Jersey
""",
    )

    session_factory = _memory_session_factory()
    monkeypatch.setattr(importer, "SessionLocal", session_factory)
    monkeypatch.setattr(importer, "init_database", lambda: None)

    first = importer.import_historical(tmp_path, dry_run=False, batch_size=2)

    assert first.errors == []
    assert first.files_seen == 3
    assert first.archive_members_seen == 2
    assert first.rows_seen == 3
    assert first.rows_valid == 3
    assert first.matches_inserted == 2
    assert first.matches_updated == 1
    assert first.odds_upserted == 3

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Match)) == 2
        assert session.scalar(select(func.count()).select_from(models.ImportRecord)) == 3
        assert session.scalar(select(func.count()).select_from(models.MatchOdds)) == 3
        world_cup = session.scalar(
            select(models.Match).where(models.Match.source_provider == "football-txt")
        )
        assert world_cup is not None
        assert world_cup.competition.name == "FIFA World Cup"
        assert world_cup.competition.kind == "cup"
        assert world_cup.home_team.kind == "national"
        assert world_cup.round == "Final"
        assert world_cup.venue == "New York/New Jersey"
        kickoff = world_cup.kickoff_at
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        assert kickoff == datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc)
        assert world_cup.kickoff_precision == "datetime"
        assert (world_cup.home_score, world_cup.away_score) == (1, 0)
        assert (world_cup.half_time_home_score, world_cup.half_time_away_score) == (0, 0)

    monkeypatch.setattr(auditor, "SessionLocal", session_factory)
    monkeypatch.setattr(auditor, "engine", session_factory.kw["bind"])
    audit = auditor.audit_database(minimum_matches=2)
    assert audit["status"] == "ok"
    assert audit["counts"]["matches"] == 2
    assert audit["archive_import_records"] == 2
    assert audit["coverage"]["matches_with_odds"] == 1

    second = importer.import_historical(tmp_path, dry_run=False, batch_size=2)

    assert second.errors == []
    assert second.matches_inserted == 0
    assert second.matches_updated == 0
    assert second.rows_unchanged == 3
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Match)) == 2
        assert session.scalar(select(func.count()).select_from(models.ImportRecord)) == 3


def test_rejects_unsafe_zip_members(tmp_path) -> None:
    archive_path = tmp_path / "internationals-master.zip"
    _write_zip(
        archive_path,
        "../2026_friendly.txt",
        "= Friendly 2026\nSun Jul 19\nA 1-0 B @ Somewhere\n",
    )

    report = importer.import_historical(archive_path, dry_run=True)

    assert report.rows_seen == 0
    assert report.errors
    assert "unsafe ZIP member path" in report.errors[0]


def test_empty_zip_member_does_not_hide_later_valid_members(tmp_path) -> None:
    archive_path = tmp_path / "internationals-master.zip"
    _write_zip_members(
        archive_path,
        {
            "internationals-master/2025_friendly.txt": (
                "= Friendly 2025\nFri Jan 10\n  Peru 2-1 Chile @ Lima\n"
            ),
            "internationals-master/2026_placeholder.txt": "= Friendly 2026\n",
            "internationals-master/2027_friendly.txt": (
                "= Friendly 2027\nSun Jan 10\n  Japan 1-0 China @ Tokyo\n"
            ),
        },
    )

    report = importer.import_historical(archive_path, dry_run=True)

    assert report.errors == []
    assert report.rows_seen == 2
    assert report.rows_valid == 2
    assert report.archive_members_seen == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("_KickoffUtcOffset", "+02:00"),
        ("_SourceProvider", "alternate-provider"),
        ("_CompetitionKind", "cup"),
        ("_TeamKind", "national"),
    ],
)
def test_row_hash_includes_persisted_metadata(field, value) -> None:
    values = {
        "Div": "E0",
        "Date": "20/08/2026",
        "Time": "18:30",
        "HomeTeam": "Arsenal",
        "AwayTeam": "Chelsea",
        "FTHG": 2,
        "FTAG": 1,
    }
    base = importer._normalize_row(
        importer._SourceRow(values=values, source_file="sample.csv", file_hash="a", sheet_name="E0", row_number=2)
    )
    changed = importer._normalize_row(
        importer._SourceRow(
            values={**values, field: value},
            source_file="sample.csv",
            file_hash="a",
            sheet_name="E0",
            row_number=3,
        )
    )

    assert changed.row_hash != base.row_hash


def test_merge_never_downgrades_result_or_kickoff_and_deduplicates_pending_odds(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "duplicate.csv"
    source.write_text(
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365H\n"
        "E0,20/08/2026,18:30,Arsenal,Chelsea,2,1,2.10\n"
        "E0,20/08/2026,,Arsenal,Chelsea,,,2.20\n",
        encoding="utf-8",
    )
    session_factory = _memory_session_factory()
    monkeypatch.setattr(importer, "SessionLocal", session_factory)
    monkeypatch.setattr(importer, "init_database", lambda: None)

    report = importer.import_historical(source, dry_run=False, batch_size=10)

    assert report.errors == []
    assert report.matches_inserted == 1
    assert report.matches_updated == 1
    with session_factory() as session:
        match = session.scalar(select(models.Match))
        assert match is not None
        assert match.status == "FINALIZADO"
        assert match.status_short == "FT"
        assert (match.home_score, match.away_score) == (2, 1)
        assert match.kickoff_precision == "datetime-local-unknown"
        assert (match.kickoff_at.hour, match.kickoff_at.minute) == (18, 30)
        quotes = list(session.scalars(select(models.MatchOdds)))
        assert len(quotes) == 1
        assert quotes[0].odds == pytest.approx(2.20)


def test_exact_offset_enriches_less_precise_kickoff(tmp_path, monkeypatch) -> None:
    source = tmp_path / "kickoff.csv"
    source.write_text(
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,_KickoffUtcOffset\n"
        "E0,20/08/2026,18:30,Arsenal,Chelsea,2,1,\n"
        "E0,20/08/2026,18:30,Arsenal,Chelsea,2,1,+02:00\n",
        encoding="utf-8",
    )
    session_factory = _memory_session_factory()
    monkeypatch.setattr(importer, "SessionLocal", session_factory)
    monkeypatch.setattr(importer, "init_database", lambda: None)

    report = importer.import_historical(source, dry_run=False, batch_size=10)

    assert report.errors == []
    assert report.matches_inserted == 1
    assert report.matches_updated == 1
    with session_factory() as session:
        match = session.scalar(select(models.Match))
        assert match is not None
        kickoff = match.kickoff_at
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        assert kickoff == datetime(2026, 8, 20, 16, 30, tzinfo=timezone.utc)
        assert match.kickoff_precision == "datetime"


def test_history_only_load_can_be_enriched_with_odds_later(tmp_path, monkeypatch) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H\n"
        "E0,20/08/2026,Arsenal,Chelsea,2,1,2.10\n",
        encoding="utf-8",
    )
    session_factory = _memory_session_factory()
    monkeypatch.setattr(importer, "SessionLocal", session_factory)
    monkeypatch.setattr(importer, "init_database", lambda: None)

    history = importer.import_historical(
        source,
        dry_run=False,
        include_odds=False,
    )

    assert history.errors == []
    assert history.matches_inserted == 1
    assert history.odds_seen == 0
    assert history.odds_upserted == 0
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.MatchOdds)) == 0

    full = importer.import_historical(source, dry_run=False, include_odds=True)

    assert full.errors == []
    assert full.matches_inserted == 0
    assert full.matches_updated == 1
    assert full.odds_upserted == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.MatchOdds)) == 1
        assert session.scalar(select(func.count()).select_from(models.ImportRecord)) == 2

    repeated = importer.import_historical(source, dry_run=False, include_odds=True)
    assert repeated.rows_unchanged == 1
    assert repeated.matches_updated == 0
