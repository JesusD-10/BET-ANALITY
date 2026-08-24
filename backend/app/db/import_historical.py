from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.catalog import DIVISIONS, CountrySpec, resolve_country, slugify
from app.db.init_db import init_database
from app.db.models import ImportRecord, Match, MatchOdds, MatchTeamStatistics
from app.db.repository import (
    _get_or_create_competition,
    _get_or_create_country,
    _get_or_create_season,
    _get_or_create_team,
    build_match_fingerprint,
)
from app.db.session import SessionLocal


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_PATH = REPOSITORY_ROOT / "Base de datos"


@dataclass(frozen=True, slots=True)
class OddsValue:
    bookmaker: str
    market_key: str
    selection: str
    odds: float
    line: float | None = None
    is_closing: bool = False
    source_code: str | None = None


@dataclass(frozen=True, slots=True)
class HistoricalMatch:
    country: CountrySpec
    competition: str
    season_label: str
    season_start: int
    season_end: int
    match_date: date
    kickoff_at: datetime
    kickoff_precision: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    half_time_home_score: int | None
    half_time_away_score: int | None
    referee: str | None
    status: str
    statistics: dict[str, dict[str, float | int | None]]
    odds: tuple[OddsValue, ...]
    row_hash: str
    source_file: str
    file_hash: str
    sheet_name: str
    row_number: int

    @property
    def fingerprint(self) -> str:
        return build_match_fingerprint(
            self.competition,
            self.match_date,
            self.home_team,
            self.away_team,
        )


@dataclass(slots=True)
class ImportReport:
    dry_run: bool
    files_seen: int = 0
    sheets_seen: int = 0
    rows_seen: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    duplicate_rows: int = 0
    matches_inserted: int = 0
    matches_updated: int = 0
    rows_unchanged: int = 0
    statistics_upserted: int = 0
    odds_upserted: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        # Keep CLI/Render logs bounded even when a source file is malformed.
        if len(self.errors) < 100:
            self.errors.append(message)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _SourceRow:
    values: dict[str, object]
    source_file: str
    file_hash: str
    sheet_name: str
    row_number: int
    datemode: int | None = None


CORE_COLUMNS = {
    "Div",
    "Country",
    "League",
    "Season",
    "Date",
    "Time",
    "HomeTeam",
    "AwayTeam",
    "Home",
    "Away",
    "FTHG",
    "FTAG",
    "HG",
    "AG",
    "FTR",
    "Res",
    "HTHG",
    "HTAG",
    "HTR",
    "Referee",
    "HxG",
    "AxG",
    "HS",
    "AS",
    "HST",
    "AST",
    "HF",
    "AF",
    "HC",
    "AC",
    "HY",
    "AY",
    "HR",
    "AR",
}

BOOKMAKER_CODES = {
    "B365",
    "BF",
    "BFE",
    "BFD",
    "BMG",
    "BV",
    "BW",
    "CL",
    "GB",
    "IW",
    "LB",
    "P",
    "PS",
    "SB",
    "SJ",
    "VC",
    "WH",
    "Max",
    "Avg",
    "BbMx",
    "BbAv",
}

BOOKMAKER_NAMES = {
    "B365": "Bet365",
    "BF": "Betfair",
    "BFE": "Betfair Exchange",
    "BFD": "Betfair",
    "BMG": "BetMGM",
    "BV": "BetVictor",
    "BW": "Bwin",
    "CL": "Coral",
    "GB": "Gamebookers",
    "IW": "Interwetten",
    "LB": "Ladbrokes",
    "P": "Pinnacle",
    "PS": "Pinnacle",
    "SB": "Sportingbet",
    "SJ": "Stan James",
    "VC": "BetVictor",
    "WH": "William Hill",
    "Max": "Market maximum",
    "Avg": "Market average",
    "BbMx": "Market maximum",
    "BbAv": "Market average",
}


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _parse_date(value: object, datemode: int | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and datemode is not None:
        try:
            import xlrd

            return xlrd.xldate_as_datetime(value, datemode).date()
        except (ImportError, ValueError, OverflowError):
            return None
    text = _clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_time(value: object, datemode: int | None) -> time | None:
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)):
        if datemode is not None:
            try:
                import xlrd

                return xlrd.xldate_as_datetime(value, datemode).time()
            except (ImportError, ValueError, OverflowError):
                pass
        fraction = float(value) % 1
        seconds = round(fraction * 86400) % 86400
        return (datetime.min + timedelta(seconds=seconds)).time()
    text = _clean_text(value)
    if not text:
        return None
    normalized = text.replace(".", ":")
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(normalized, pattern).time()
        except ValueError:
            continue
    return None


def _season_from_value(value: object, source_file: str, match_date: date) -> tuple[str, int, int]:
    text = _clean_text(value)
    if not text:
        match = re.search(r"(20\d{2})[-_](20\d{2})", source_file)
        if match:
            return f"{match.group(1)}-{match.group(2)}", int(match.group(1)), int(match.group(2))
        return str(match_date.year), match_date.year, match_date.year
    years = [int(item) for item in re.findall(r"(?:19|20)\d{2}", text)]
    if len(years) >= 2:
        return f"{years[0]}-{years[1]}", years[0], years[1]
    if len(years) == 1:
        return str(years[0]), years[0], years[0]
    if text.isdigit() and len(text) == 4:
        year = int(text)
        return text, year, year
    return text, match_date.year, match_date.year


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _row_dict(headers: Iterable[object], values: Iterable[object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for index, (header, value) in enumerate(zip(headers, values), 1):
        name = _clean_text(header) or f"column_{index}"
        # Preserve the first occurrence if a malformed sheet duplicates a name.
        result.setdefault(name, value)
    return result


def _iter_xlsx(path: Path, digest: str) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    try:
        import openpyxl
    except ImportError as error:
        raise RuntimeError("openpyxl is required to import .xlsx files") from error

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    source_file = _source_path(path)
    try:
        for worksheet in workbook.worksheets:
            def rows(sheet=worksheet) -> Iterator[_SourceRow]:
                iterator = sheet.iter_rows(values_only=True)
                headers: tuple[object, ...] | None = None
                header_number = 0
                for row_number, values in enumerate(iterator, 1):
                    if headers is None:
                        if not any(_clean_text(value) for value in values):
                            continue
                        headers = tuple(values)
                        header_number = row_number
                        continue
                    if not any(value not in (None, "") for value in values):
                        continue
                    yield _SourceRow(
                        values=_row_dict(headers, values),
                        source_file=source_file,
                        file_hash=digest,
                        sheet_name=sheet.title,
                        row_number=row_number,
                    )
                if headers is None:
                    raise ValueError(f"{sheet.title}: no header row found")
                _ = header_number

            yield worksheet.title, rows()
    finally:
        workbook.close()


def _iter_xls(path: Path, digest: str) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    try:
        import xlrd
    except ImportError as error:
        raise RuntimeError("xlrd is required to import .xls files") from error

    workbook = xlrd.open_workbook(path, on_demand=True)
    source_file = _source_path(path)
    try:
        for sheet_name in workbook.sheet_names():
            worksheet = workbook.sheet_by_name(sheet_name)

            def rows(sheet=worksheet, name=sheet_name) -> Iterator[_SourceRow]:
                headers: list[object] | None = None
                for row_index in range(sheet.nrows):
                    values = sheet.row_values(row_index)
                    if headers is None:
                        if not any(_clean_text(value) for value in values):
                            continue
                        headers = values
                        continue
                    if not any(value not in (None, "") for value in values):
                        continue
                    yield _SourceRow(
                        values=_row_dict(headers, values),
                        source_file=source_file,
                        file_hash=digest,
                        sheet_name=name,
                        row_number=row_index + 1,
                        datemode=workbook.datemode,
                    )
                if headers is None:
                    raise ValueError(f"{name}: no header row found")

            yield sheet_name, rows()
            workbook.unload_sheet(sheet_name)
    finally:
        workbook.release_resources()


def _csv_encoding(path: Path) -> tuple[str, str]:
    sample = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return encoding, sample.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "latin-1", sample.decode("latin-1")


def _iter_csv(path: Path, digest: str) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    encoding, sample = _csv_encoding(path)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    source_file = _source_path(path)

    def rows() -> Iterator[_SourceRow]:
        with path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.DictReader(stream, dialect=dialect)
            if not reader.fieldnames:
                raise ValueError("no header row found")
            for row_number, values in enumerate(reader, 2):
                if not any(_clean_text(value) for value in values.values()):
                    continue
                yield _SourceRow(
                    values={str(key): value for key, value in values.items() if key is not None},
                    source_file=source_file,
                    file_hash=digest,
                    sheet_name=path.stem,
                    row_number=row_number,
                )

    yield path.stem, rows()


def _iter_file(path: Path) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    digest = _file_hash(path)
    if path.suffix.casefold() == ".xlsx":
        yield from _iter_xlsx(path, digest)
    elif path.suffix.casefold() == ".xls":
        yield from _iter_xls(path, digest)
    elif path.suffix.casefold() in {".csv", ".tsv"}:
        yield from _iter_csv(path, digest)
    else:
        raise ValueError(f"unsupported file type: {path.suffix}")


def _bookmaker_name(code: str) -> str:
    return BOOKMAKER_NAMES.get(code, code)


def _extract_odds(values: dict[str, object]) -> tuple[OddsValue, ...]:
    output: list[OddsValue] = []
    opening_handicap = _as_float(values.get("AHh"))
    closing_handicap = _as_float(values.get("AHCh"))

    for code, raw_value in values.items():
        if code in CORE_COLUMNS:
            continue
        price = _as_float(raw_value)
        if price is None or price <= 1:
            continue

        total_match = re.fullmatch(
            r"(?P<book>[A-Za-z0-9]+?)(?P<closing>C)?(?P<direction>[<>])(?P<line>\d+(?:\.\d+)?)",
            code,
        )
        if total_match and total_match.group("book") in BOOKMAKER_CODES:
            output.append(
                OddsValue(
                    bookmaker=_bookmaker_name(total_match.group("book")),
                    market_key="total_goals",
                    selection="over" if total_match.group("direction") == ">" else "under",
                    line=float(total_match.group("line")),
                    odds=price,
                    is_closing=bool(total_match.group("closing")),
                    source_code=code,
                )
            )
            continue

        handicap_match = re.fullmatch(
            r"(?P<book>[A-Za-z0-9]+?)(?P<closing>C)?AH(?P<side>H|A)", code
        )
        if handicap_match and handicap_match.group("book") in BOOKMAKER_CODES:
            closing = bool(handicap_match.group("closing"))
            output.append(
                OddsValue(
                    bookmaker=_bookmaker_name(handicap_match.group("book")),
                    market_key="asian_handicap",
                    selection="home" if handicap_match.group("side") == "H" else "away",
                    line=closing_handicap if closing else opening_handicap,
                    odds=price,
                    is_closing=closing,
                    source_code=code,
                )
            )
            continue

        result_match = re.fullmatch(
            r"(?P<book>[A-Za-z0-9]+?)(?P<closing>C)?(?P<selection>H|D|A)", code
        )
        if result_match and result_match.group("book") in BOOKMAKER_CODES:
            selection = {"H": "home", "D": "draw", "A": "away"}[
                result_match.group("selection")
            ]
            output.append(
                OddsValue(
                    bookmaker=_bookmaker_name(result_match.group("book")),
                    market_key="match_winner",
                    selection=selection,
                    odds=price,
                    is_closing=bool(result_match.group("closing")),
                    source_code=code,
                )
            )
    return tuple(output)


def _normalize_row(source: _SourceRow) -> HistoricalMatch:
    values = source.values
    match_date = _parse_date(values.get("Date"), source.datemode)
    if match_date is None:
        raise ValueError("missing or invalid Date")

    home = _clean_text(values.get("HomeTeam") or values.get("Home"))
    away = _clean_text(values.get("AwayTeam") or values.get("Away"))
    if not home or not away or home.casefold() == away.casefold():
        raise ValueError("missing or identical home/away teams")

    division = (_clean_text(values.get("Div")) or source.sheet_name).upper()
    division_info = DIVISIONS.get(division)
    if division_info:
        country, competition = division_info
    else:
        country = resolve_country(values.get("Country"))
        competition = _clean_text(values.get("League")) or division
    if not competition:
        raise ValueError("missing competition")

    season_label, season_start, season_end = _season_from_value(
        values.get("Season"), source.source_file, match_date
    )
    kickoff_time = _parse_time(values.get("Time"), source.datemode)
    precision = "datetime-local-unknown" if kickoff_time else "date-only"
    # Source files do not identify the venue timezone. Store a deterministic
    # UTC value and retain precision so consumers do not mistake it for a
    # provider-verified kickoff.
    kickoff = datetime.combine(match_date, kickoff_time or time.min, tzinfo=timezone.utc)

    home_score = _as_int(values.get("FTHG") if "FTHG" in values else values.get("HG"))
    away_score = _as_int(values.get("FTAG") if "FTAG" in values else values.get("AG"))
    status = "FINALIZADO" if home_score is not None and away_score is not None else "PROGRAMADO"

    statistics = {
        "home": {
            "expected_goals": _as_float(values.get("HxG")),
            "shots": _as_int(values.get("HS")),
            "shots_on_target": _as_int(values.get("HST")),
            "fouls": _as_int(values.get("HF")),
            "corners": _as_int(values.get("HC")),
            "yellow_cards": _as_int(values.get("HY")),
            "red_cards": _as_int(values.get("HR")),
        },
        "away": {
            "expected_goals": _as_float(values.get("AxG")),
            "shots": _as_int(values.get("AS")),
            "shots_on_target": _as_int(values.get("AST")),
            "fouls": _as_int(values.get("AF")),
            "corners": _as_int(values.get("AC")),
            "yellow_cards": _as_int(values.get("AY")),
            "red_cards": _as_int(values.get("AR")),
        },
    }
    odds = _extract_odds(values)
    canonical = {
        "country": country.code,
        "competition": competition,
        "season": season_label,
        "date": match_date,
        "time": kickoff_time,
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "half_time_home_score": _as_int(values.get("HTHG")),
        "half_time_away_score": _as_int(values.get("HTAG")),
        "referee": _clean_text(values.get("Referee")),
        "statistics": statistics,
        "odds": [asdict(item) for item in odds],
    }
    row_hash = _canonical_hash(canonical)
    return HistoricalMatch(
        country=country,
        competition=competition,
        season_label=season_label,
        season_start=season_start,
        season_end=season_end,
        match_date=match_date,
        kickoff_at=kickoff,
        kickoff_precision=precision,
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        half_time_home_score=_as_int(values.get("HTHG")),
        half_time_away_score=_as_int(values.get("HTAG")),
        referee=_clean_text(values.get("Referee")),
        status=status,
        statistics=statistics,
        odds=odds,
        row_hash=row_hash,
        source_file=source.source_file,
        file_hash=source.file_hash,
        sheet_name=source.sheet_name,
        row_number=source.row_number,
    )


def _upsert_statistic(
    session: Session,
    *,
    match: Match,
    team_id: int,
    side: str,
    values: dict[str, float | int | None],
) -> bool:
    if not any(value is not None for value in values.values()):
        return False
    statistic = session.scalar(
        select(MatchTeamStatistics).where(
            MatchTeamStatistics.match_id == match.id,
            MatchTeamStatistics.side == side,
        )
    )
    if statistic is None:
        statistic = MatchTeamStatistics(match=match, team_id=team_id, side=side)
        session.add(statistic)
    statistic.team_id = team_id
    for key, value in values.items():
        if value is not None:
            setattr(statistic, key, value)
    return True


def _upsert_odds(session: Session, match: Match, values: OddsValue) -> bool:
    odds_key = _canonical_hash(
        {
            "match": match.fingerprint,
            "bookmaker": values.bookmaker,
            "market": values.market_key,
            "selection": values.selection,
            "line": values.line,
            "closing": values.is_closing,
        }
    )
    quote = session.scalar(select(MatchOdds).where(MatchOdds.odds_key == odds_key))
    if quote is None:
        quote = MatchOdds(odds_key=odds_key, match=match)
        session.add(quote)
    quote.bookmaker = values.bookmaker
    quote.market_key = values.market_key
    quote.selection = values.selection
    quote.line = values.line
    quote.odds = values.odds
    quote.is_closing = values.is_closing
    quote.source_code = values.source_code
    return True


def _persist_record(session: Session, record: HistoricalMatch, report: ImportReport) -> str:
    prior_import = session.scalar(
        select(ImportRecord.id).where(ImportRecord.row_hash == record.row_hash)
    )
    if prior_import is not None:
        return "unchanged"

    country = _get_or_create_country(session, record.country)
    competition = _get_or_create_competition(
        session,
        country=country,
        name=record.competition,
        provider="historical",
    )
    season = _get_or_create_season(
        session,
        competition=competition,
        label=record.season_label,
        start_year=record.season_start,
        end_year=record.season_end,
    )
    home = _get_or_create_team(session, country=country, name=record.home_team)
    away = _get_or_create_team(session, country=country, name=record.away_team)

    model = session.scalar(select(Match).where(Match.fingerprint == record.fingerprint))
    outcome = "updated"
    if model is None:
        model = Match(
            public_id=f"historical-{record.fingerprint[:24]}",
            fingerprint=record.fingerprint,
            competition=competition,
            season=season,
            home_team=home,
            away_team=away,
            match_date=record.match_date,
            kickoff_at=record.kickoff_at,
            kickoff_precision=record.kickoff_precision,
            status=record.status,
            source_provider="historical",
        )
        session.add(model)
        session.flush()
        outcome = "inserted"

    model.competition = competition
    model.season = season
    model.home_team = home
    model.away_team = away
    model.match_date = record.match_date
    if model.kickoff_precision != "datetime":
        model.kickoff_at = record.kickoff_at
        model.kickoff_precision = record.kickoff_precision
    model.status = record.status
    model.status_short = "FT" if record.status == "FINALIZADO" else "NS"
    model.home_score = record.home_score
    model.away_score = record.away_score
    model.half_time_home_score = record.half_time_home_score
    model.half_time_away_score = record.half_time_away_score
    model.referee = record.referee or model.referee
    model.source_hash = record.row_hash
    model.odds_available = bool(record.odds) or model.odds_available

    if _upsert_statistic(
        session,
        match=model,
        team_id=home.id,
        side="home",
        values=record.statistics["home"],
    ):
        report.statistics_upserted += 1
    if _upsert_statistic(
        session,
        match=model,
        team_id=away.id,
        side="away",
        values=record.statistics["away"],
    ):
        report.statistics_upserted += 1
    for quote in record.odds:
        if _upsert_odds(session, model, quote):
            report.odds_upserted += 1

    session.add(
        ImportRecord(
            row_hash=record.row_hash,
            file_hash=record.file_hash,
            match=model,
            source_file=record.source_file,
            sheet_name=record.sheet_name,
            row_number=record.row_number,
        )
    )
    return outcome


def _discover_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.casefold() in {".xls", ".xlsx", ".csv", ".tsv"} else []
    if not path.exists():
        return []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.casefold() in {".xls", ".xlsx", ".csv", ".tsv"}
    )


def import_historical(
    path: str | Path = DEFAULT_DATA_PATH,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    batch_size: int = 500,
) -> ImportReport:
    """Import supported historical files idempotently.

    Dry-run is the safe default. ZIP archives are intentionally not expanded;
    provide extracted CSV files explicitly when they have a documented schema.
    """

    source_path = Path(path)
    report = ImportReport(dry_run=dry_run)
    files = _discover_files(source_path)
    if not files:
        report.add_error(f"No supported .xls/.xlsx/.csv/.tsv files found under {source_path}")
        return report
    if not dry_run:
        init_database()

    seen_hashes: set[str] = set()
    session = None if dry_run else SessionLocal()
    pending = 0
    stop = False
    try:
        for file_path in files:
            if stop:
                break
            report.files_seen += 1
            try:
                sheets = _iter_file(file_path)
                for sheet_name, rows in sheets:
                    report.sheets_seen += 1
                    for source in rows:
                        if limit is not None and report.rows_seen >= max(0, limit):
                            stop = True
                            break
                        report.rows_seen += 1
                        try:
                            record = _normalize_row(source)
                        except Exception as error:
                            report.rows_invalid += 1
                            report.add_error(
                                f"{source.source_file}:{sheet_name}:{source.row_number}: {error}"
                            )
                            continue
                        if record.row_hash in seen_hashes:
                            report.duplicate_rows += 1
                            continue
                        seen_hashes.add(record.row_hash)
                        report.rows_valid += 1
                        if dry_run:
                            continue
                        assert session is not None
                        try:
                            outcome = _persist_record(session, record, report)
                            if outcome == "inserted":
                                report.matches_inserted += 1
                            elif outcome == "updated":
                                report.matches_updated += 1
                            else:
                                report.rows_unchanged += 1
                            pending += 1
                            if pending >= max(1, batch_size):
                                session.commit()
                                pending = 0
                        except Exception as error:
                            session.rollback()
                            pending = 0
                            report.add_error(
                                f"{source.source_file}:{sheet_name}:{source.row_number}: database error: {error}"
                            )
                    if stop:
                        break
            except Exception as error:
                report.add_error(f"{_source_path(file_path)}: {type(error).__name__}: {error}")
        if session is not None and pending:
            session.commit()
    finally:
        if session is not None:
            session.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import BET ANALIZADOR historical match files")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_DATA_PATH))
    parser.add_argument(
        "--commit",
        action="store_true",
        help="write to DATABASE_URL; without this flag the command is a dry-run",
    )
    parser.add_argument("--limit", type=int, default=None, help="maximum source rows to inspect")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    report = import_historical(
        args.path,
        dry_run=not args.commit,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
