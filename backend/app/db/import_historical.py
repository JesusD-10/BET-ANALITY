from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Iterable, Iterator
import zipfile

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db.catalog import DIVISIONS, CountrySpec, resolve_country, slugify
from app.db.football_txt import FootballMatch, parse_football_txt
from app.db.init_db import init_database
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
    round: str | None
    venue: str | None
    status: str
    competition_kind: str
    team_kind: str
    source_provider: str
    statistics: dict[str, dict[str, float | int | None]]
    odds: tuple[OddsValue, ...]
    row_hash: str
    legacy_row_hash: str
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
    archive_members_seen: int = 0
    statistics_seen: int = 0
    odds_seen: int = 0
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


@dataclass(slots=True)
class _ImportState:
    row_hashes: set[str]
    fingerprints: set[str]
    source_rows: set[tuple[str, str, int]] = field(default_factory=set)
    countries: dict[str, Country] = field(default_factory=dict)
    competitions: dict[tuple[int, str], Competition] = field(default_factory=dict)
    seasons: dict[tuple[int, str], Season] = field(default_factory=dict)
    teams: dict[tuple[int, str], Team] = field(default_factory=dict)


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


ZIP_MAX_MEMBERS = 5_000
ZIP_MAX_MEMBER_BYTES = 16 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 128 * 1024 * 1024
ZIP_MAX_COMPRESSION_RATIO = 300

KICKOFF_PRECISION_RANK = {
    "date-only": 0,
    "datetime-local-unknown": 1,
    "datetime": 2,
}

FOOTBALL_COMPETITION_ALIASES = {
    "world cup": "FIFA World Cup",
    "fifa world cup": "FIFA World Cup",
}

# The World Cup archive sometimes uses short/common labels while the broader
# internationals archive uses the names below. Canonicalizing only known source
# variants lets both archives enrich one match instead of creating a duplicate.
FOOTBALL_TEAM_ALIASES = {
    "bosnia-herzegovina": "Bosnia and Herzegovina",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "cote d'ivoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "czechia": "Czech Republic",
    "dutch east indies": "Indonesia",
    "east germany": "German DR",
    "ireland": "Republic of Ireland",
    "korea republic": "South Korea",
    "west germany": "Germany",
    "china": "China PR",
    "united states of america": "United States",
    "usa": "United States",
    "ussr": "Soviet Union",
    "zaire": "DR Congo",
    "zaïre": "DR Congo",
}


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _is_match_candidate(values: dict[str, object]) -> bool:
    """Ignore spreadsheet footers while retaining malformed match-like rows."""

    return any(
        _clean_text(values.get(column))
        for column in ("Date", "HomeTeam", "AwayTeam", "Home", "Away")
    )


def _canonical_football_competition(value: str) -> str:
    clean = _clean_text(value) or "International"
    return FOOTBALL_COMPETITION_ALIASES.get(clean.casefold(), clean)


def _canonical_football_team(value: str, *, match_date: date) -> str:
    clean = _clean_text(value) or value
    if clean.casefold() == "yugoslavia" and match_date.year >= 1994:
        return "FR Yugoslavia"
    return FOOTBALL_TEAM_ALIASES.get(clean.casefold(), clean)


def _decode_zip_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")


def _safe_zip_member_name(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe ZIP member path: {value!r}")
    return path.as_posix()


def _is_football_member(archive_name: str, member_name: str) -> bool:
    archive_key = archive_name.casefold()
    member_key = member_name.casefold()
    if "internationals" in archive_key:
        return bool(re.search(r"(?:^|/)\d{4}_[^/]+\.txt$", member_key))
    if "worldcup" in archive_key:
        # The archive repeats each tournament under min/, more/, Wikipedia and
        # RSSSF. The edition-level cup.txt is the one canonical representation.
        return bool(
            re.search(
                r"(?:^|/)\d{4}--[^/]+/cup(?:_finals)?\.txt$",
                member_key,
            )
        )
    return False


def _football_values(match: FootballMatch) -> dict[str, object]:
    return {
        "Country": "INT",
        "League": _canonical_football_competition(match.competition),
        "Season": match.season_label,
        "Date": match.match_date,
        "Time": match.kickoff_time,
        "HomeTeam": _canonical_football_team(
            match.home_team,
            match_date=match.match_date,
        ),
        "AwayTeam": _canonical_football_team(
            match.away_team,
            match_date=match.match_date,
        ),
        "FTHG": match.home_score,
        "FTAG": match.away_score,
        "HTHG": match.half_time_home_score,
        "HTAG": match.half_time_away_score,
        "Round": match.round,
        "Venue": match.venue,
        "_KickoffUtcOffset": match.kickoff_utc_offset,
        "_KickoffPrecision": match.kickoff_precision,
        "_CompetitionKind": "cup",
        "_TeamKind": "national",
        "_SourceProvider": "football-txt",
    }


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
                    mapped = _row_dict(headers, values)
                    if not _is_match_candidate(mapped):
                        continue
                    yield _SourceRow(
                        values=mapped,
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
                    mapped = _row_dict(headers, values)
                    if not _is_match_candidate(mapped):
                        continue
                    yield _SourceRow(
                        values=mapped,
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
                mapped = {str(key): value for key, value in values.items() if key is not None}
                if not _is_match_candidate(mapped):
                    continue
                yield _SourceRow(
                    values=mapped,
                    source_file=source_file,
                    file_hash=digest,
                    sheet_name=path.stem,
                    row_number=row_number,
                )

    yield path.stem, rows()


def _sheet_label(value: str) -> str:
    if len(value) <= 180:
        return value
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[:160]}-{suffix}"


def _iter_zip(path: Path, digest: str) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    archive_source = _source_path(path)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"invalid ZIP archive: {error}") from error

    with archive:
        infos = archive.infolist()
        if len(infos) > ZIP_MAX_MEMBERS:
            raise ValueError(
                f"ZIP has {len(infos)} members; maximum is {ZIP_MAX_MEMBERS}"
            )

        selected: list[tuple[zipfile.ZipInfo, str]] = []
        total_size = 0
        for info in infos:
            if info.is_dir():
                continue
            member_name = _safe_zip_member_name(info.filename)
            if not _is_football_member(path.name, member_name):
                continue
            if info.flag_bits & 0x1:
                raise ValueError(f"encrypted ZIP member is not supported: {member_name}")
            if info.file_size > ZIP_MAX_MEMBER_BYTES:
                raise ValueError(
                    f"ZIP member {member_name} is {info.file_size} bytes; "
                    f"maximum is {ZIP_MAX_MEMBER_BYTES}"
                )
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > ZIP_MAX_COMPRESSION_RATIO:
                raise ValueError(
                    f"ZIP member {member_name} compression ratio {ratio:.1f} is unsafe"
                )
            total_size += info.file_size
            if total_size > ZIP_MAX_TOTAL_BYTES:
                raise ValueError(
                    f"selected ZIP data exceeds {ZIP_MAX_TOTAL_BYTES} uncompressed bytes"
                )
            selected.append((info, member_name))

        if not selected:
            raise ValueError("no supported Football.TXT members found")

        parsed_members = 0
        for info, member_name in selected:
            with archive.open(info, "r") as stream:
                payload = stream.read(ZIP_MAX_MEMBER_BYTES + 1)
            if len(payload) > ZIP_MAX_MEMBER_BYTES:
                raise ValueError(f"ZIP member grew beyond its declared limit: {member_name}")
            matches = list(
                parse_football_txt(
                    _decode_zip_text(payload),
                    member_name=member_name,
                )
            )
            if not matches:
                # Some upstream archives contain placeholder/empty tournament
                # files. One such member must not hide later valid members.
                continue

            parsed_members += 1

            label = _sheet_label(member_name)
            source_file = f"{archive_source}!/{member_name}"

            def rows(
                parsed=matches,
                member_source=source_file,
                sheet=label,
            ) -> Iterator[_SourceRow]:
                for index, match in enumerate(parsed, 1):
                    yield _SourceRow(
                        values=_football_values(match),
                        source_file=member_source,
                        file_hash=digest,
                        sheet_name=sheet,
                        row_number=int(getattr(match, "source_line", index)),
                    )

            yield label, rows()

        if not parsed_members:
            raise ValueError("no match rows parsed from supported Football.TXT members")


def _iter_file(path: Path) -> Iterator[tuple[str, Iterator[_SourceRow]]]:
    digest = _file_hash(path)
    if path.suffix.casefold() == ".xlsx":
        yield from _iter_xlsx(path, digest)
    elif path.suffix.casefold() == ".xls":
        yield from _iter_xls(path, digest)
    elif path.suffix.casefold() in {".csv", ".tsv"}:
        yield from _iter_csv(path, digest)
    elif path.suffix.casefold() == ".zip":
        yield from _iter_zip(path, digest)
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
    offset_text = _clean_text(values.get("_KickoffUtcOffset"))
    precision = "datetime-local-unknown" if kickoff_time else "date-only"
    kickoff_zone = timezone.utc
    if kickoff_time and offset_text:
        offset_match = re.fullmatch(
            r"(?:UTC)?(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?",
            offset_text.upper(),
        )
        if offset_match:
            offset = timedelta(
                hours=int(offset_match.group("hours")),
                minutes=int(offset_match.group("minutes") or 0),
            )
            if offset_match.group("sign") == "-":
                offset = -offset
            kickoff_zone = timezone(offset)
            precision = "datetime"
    # Local spreadsheet times have no venue timezone. They remain explicitly
    # marked as unknown; Football.TXT rows with UTC offsets are converted.
    kickoff = datetime.combine(
        match_date,
        kickoff_time or time.min,
        tzinfo=kickoff_zone,
    ).astimezone(timezone.utc)

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
    competition_kind = _clean_text(values.get("_CompetitionKind")) or "league"
    team_kind = _clean_text(values.get("_TeamKind")) or "club"
    source_provider = _clean_text(values.get("_SourceProvider")) or "historical"
    legacy_canonical = {
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
        "round": _clean_text(values.get("Round")),
        "venue": _clean_text(values.get("Venue")),
        "statistics": statistics,
        "odds": [asdict(item) for item in odds],
    }
    canonical = {
        **legacy_canonical,
        "kickoff_utc_offset": offset_text,
        "kickoff_precision": precision,
        "competition_kind": competition_kind,
        "team_kind": team_kind,
        "source_provider": source_provider,
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
        round=_clean_text(values.get("Round")),
        venue=_clean_text(values.get("Venue")),
        status=status,
        competition_kind=competition_kind,
        team_kind=team_kind,
        source_provider=source_provider,
        statistics=statistics,
        odds=odds,
        row_hash=row_hash,
        legacy_row_hash=_canonical_hash(legacy_canonical),
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
    existing: MatchTeamStatistics | None = None,
) -> bool:
    if not any(value is not None for value in values.values()):
        return False
    statistic = existing
    if statistic is None:
        statistic = MatchTeamStatistics(match=match, team_id=team_id, side=side)
        session.add(statistic)
    statistic.team_id = team_id
    for key, value in values.items():
        if value is not None:
            setattr(statistic, key, value)
    return True


def _odds_key(match: Match, values: OddsValue) -> str:
    return _canonical_hash(
        {
            "match": match.fingerprint,
            "bookmaker": values.bookmaker,
            "market": values.market_key,
            "selection": values.selection,
            "line": values.line,
            "closing": values.is_closing,
        }
    )


def _odds_mapping(match: Match, values: OddsValue) -> dict[str, object]:
    return {
        "odds_key": _odds_key(match, values),
        "match_id": match.id,
        "bookmaker": values.bookmaker,
        "market_key": values.market_key,
        "selection": values.selection,
        "line": values.line,
        "odds": values.odds,
        "is_closing": values.is_closing,
        "captured_at": None,
        "source_code": values.source_code,
    }


def _upsert_odds(
    session: Session,
    match: Match,
    values: OddsValue,
    existing: dict[str, MatchOdds] | None = None,
) -> bool:
    odds_key = _odds_key(match, values)
    quote = existing.get(odds_key) if existing is not None else None
    if quote is None:
        quote = MatchOdds(odds_key=odds_key, match=match)
        session.add(quote)
        if existing is not None:
            existing[odds_key] = quote
    quote.bookmaker = values.bookmaker
    quote.market_key = values.market_key
    quote.selection = values.selection
    quote.line = values.line
    quote.odds = values.odds
    quote.is_closing = values.is_closing
    quote.source_code = values.source_code
    return True


def _cached_country(
    session: Session,
    state: _ImportState,
    spec: CountrySpec,
) -> Country:
    country = state.countries.get(spec.code)
    if country is None:
        country = _get_or_create_country(session, spec)
        state.countries[spec.code] = country
    return country


def _cached_competition(
    session: Session,
    state: _ImportState,
    *,
    country: Country,
    record: HistoricalMatch,
) -> Competition:
    key = (country.id, slugify(record.competition))
    competition = state.competitions.get(key)
    if competition is None:
        competition = _get_or_create_competition(
            session,
            country=country,
            name=record.competition,
            provider=record.source_provider,
            kind=record.competition_kind,
        )
        state.competitions[key] = competition
    return competition


def _cached_season(
    session: Session,
    state: _ImportState,
    *,
    competition: Competition,
    record: HistoricalMatch,
) -> Season:
    key = (competition.id, record.season_label)
    season = state.seasons.get(key)
    if season is None:
        season = _get_or_create_season(
            session,
            competition=competition,
            label=record.season_label,
            start_year=record.season_start,
            end_year=record.season_end,
        )
        state.seasons[key] = season
    return season


def _cached_team(
    session: Session,
    state: _ImportState,
    *,
    country: Country,
    name: str,
    kind: str,
) -> Team:
    key = (country.id, slugify(name))
    team = state.teams.get(key)
    if team is None:
        team = _get_or_create_team(session, country=country, name=name)
        state.teams[key] = team
    team.kind = kind
    return team


def _persist_record(
    session: Session,
    record: HistoricalMatch,
    report: ImportReport,
    state: _ImportState,
    bulk_odds: dict[str, dict[str, object]],
) -> str:
    source_row = (record.file_hash, record.sheet_name, record.row_number)
    if record.row_hash in state.row_hashes or (
        record.legacy_row_hash in state.row_hashes
        and source_row in state.source_rows
    ):
        return "unchanged"

    country = _cached_country(session, state, record.country)
    competition = _cached_competition(
        session,
        state,
        country=country,
        record=record,
    )
    season = _cached_season(
        session,
        state,
        competition=competition,
        record=record,
    )
    home = _cached_team(
        session,
        state,
        country=country,
        name=record.home_team,
        kind=record.team_kind,
    )
    away = _cached_team(
        session,
        state,
        country=country,
        name=record.away_team,
        kind=record.team_kind,
    )

    model = None
    reversed_match = False
    if record.fingerprint in state.fingerprints:
        model = session.scalar(
            select(Match).where(Match.fingerprint == record.fingerprint)
        )
    if model is None and record.team_kind == "national":
        reverse_fingerprint = build_match_fingerprint(
            record.competition,
            record.match_date,
            record.away_team,
            record.home_team,
        )
        if reverse_fingerprint in state.fingerprints:
            model = session.scalar(
                select(Match).where(Match.fingerprint == reverse_fingerprint)
            )
            reversed_match = model is not None
    outcome = "updated"
    if model is None:
        model = Match(
            public_id=f"{slugify(record.source_provider)}-{record.fingerprint[:24]}",
            fingerprint=record.fingerprint,
            competition=competition,
            season=season,
            home_team=home,
            away_team=away,
            match_date=record.match_date,
            kickoff_at=record.kickoff_at,
            kickoff_precision=record.kickoff_precision,
            status=record.status,
            source_provider=record.source_provider,
        )
        session.add(model)
        session.flush()
        state.fingerprints.add(record.fingerprint)
        outcome = "inserted"

    model.competition = competition
    model.season = season
    if not reversed_match:
        model.home_team = home
        model.away_team = away
    model.match_date = record.match_date
    current_precision_rank = KICKOFF_PRECISION_RANK.get(model.kickoff_precision, -1)
    incoming_precision_rank = KICKOFF_PRECISION_RANK.get(record.kickoff_precision, -1)
    if incoming_precision_rank >= current_precision_rank:
        model.kickoff_at = record.kickoff_at
        model.kickoff_precision = record.kickoff_precision
    if record.status == "FINALIZADO" or model.status != "FINALIZADO":
        model.status = record.status
    model.status_short = "FT" if model.status == "FINALIZADO" else "NS"
    home_score = record.away_score if reversed_match else record.home_score
    away_score = record.home_score if reversed_match else record.away_score
    half_time_home_score = (
        record.half_time_away_score if reversed_match else record.half_time_home_score
    )
    half_time_away_score = (
        record.half_time_home_score if reversed_match else record.half_time_away_score
    )
    if home_score is not None:
        model.home_score = home_score
    if away_score is not None:
        model.away_score = away_score
    if half_time_home_score is not None:
        model.half_time_home_score = half_time_home_score
    if half_time_away_score is not None:
        model.half_time_away_score = half_time_away_score
    model.referee = record.referee or model.referee
    model.round = record.round or model.round
    model.venue = record.venue or model.venue
    model.source_hash = record.row_hash
    model.odds_available = bool(record.odds) or model.odds_available

    existing_statistics: dict[str, MatchTeamStatistics] = {}
    existing_odds: dict[str, MatchOdds] = {}
    if outcome == "updated":
        existing_statistics = {
            item.side: item
            for item in session.scalars(
                select(MatchTeamStatistics).where(
                    MatchTeamStatistics.match_id == model.id
                )
            )
        }
        if record.odds:
            existing_odds = {
                item.odds_key: item
                for item in session.scalars(
                    select(MatchOdds).where(MatchOdds.match_id == model.id)
                )
            }

    home_statistics = (
        record.statistics["away"] if reversed_match else record.statistics["home"]
    )
    away_statistics = (
        record.statistics["home"] if reversed_match else record.statistics["away"]
    )
    home_team_id = model.home_team_id if reversed_match else home.id
    away_team_id = model.away_team_id if reversed_match else away.id

    if _upsert_statistic(
        session,
        match=model,
        team_id=home_team_id,
        side="home",
        values=home_statistics,
        existing=existing_statistics.get("home"),
    ):
        report.statistics_upserted += 1
    if _upsert_statistic(
        session,
        match=model,
        team_id=away_team_id,
        side="away",
        values=away_statistics,
        existing=existing_statistics.get("away"),
    ):
        report.statistics_upserted += 1
    if outcome == "inserted":
        unique_mappings: dict[str, dict[str, object]] = {}
        for quote in record.odds:
            mapping = _odds_mapping(model, quote)
            unique_mappings[str(mapping["odds_key"])] = mapping
        bulk_odds.update(unique_mappings)
        report.odds_upserted += len(unique_mappings)
    else:
        for quote in record.odds:
            mapping = _odds_mapping(model, quote)
            odds_key = str(mapping["odds_key"])
            if odds_key in bulk_odds:
                bulk_odds[odds_key] = mapping
                report.odds_upserted += 1
            elif _upsert_odds(session, model, quote, existing=existing_odds):
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
    state.row_hashes.add(record.row_hash)
    state.source_rows.add(source_row)
    return outcome


def _discover_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.casefold() in {".xls", ".xlsx", ".csv", ".tsv", ".zip"} else []
    if not path.exists():
        return []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and item.suffix.casefold() in {".xls", ".xlsx", ".csv", ".tsv", ".zip"}
    )


def import_historical(
    path: str | Path = DEFAULT_DATA_PATH,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    batch_size: int = 500,
    progress_every: int | None = None,
    include_odds: bool = True,
) -> ImportReport:
    """Import supported historical files idempotently.

    Dry-run is the safe default. Known Football.TXT ZIP archives are streamed
    directly and are never extracted to disk.
    """

    source_path = Path(path)
    report = ImportReport(dry_run=dry_run)
    files = _discover_files(source_path)
    if not files:
        report.add_error(
            f"No supported .xls/.xlsx/.csv/.tsv/.zip files found under {source_path}"
        )
        return report
    if not dry_run:
        init_database()

    seen_hashes: set[str] = set()
    session = None if dry_run else SessionLocal()
    state = None
    if session is not None:
        session.expire_on_commit = False
        state = _ImportState(
            row_hashes=set(session.scalars(select(ImportRecord.row_hash))),
            fingerprints=set(session.scalars(select(Match.fingerprint))),
            source_rows={
                (str(file_hash), str(sheet_name), int(row_number))
                for file_hash, sheet_name, row_number in session.execute(
                    select(
                        ImportRecord.file_hash,
                        ImportRecord.sheet_name,
                        ImportRecord.row_number,
                    )
                )
            },
        )
    pending = 0
    pending_odds: dict[str, dict[str, object]] = {}
    committed_persist_counts = {
        "matches_inserted": 0,
        "matches_updated": 0,
        "statistics_upserted": 0,
        "odds_upserted": 0,
    }
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
                    if file_path.suffix.casefold() == ".zip":
                        report.archive_members_seen += 1
                    for source in rows:
                        if limit is not None and report.rows_seen >= max(0, limit):
                            stop = True
                            break
                        report.rows_seen += 1
                        if (
                            progress_every is not None
                            and progress_every > 0
                            and report.rows_seen % progress_every == 0
                        ):
                            print(
                                json.dumps(
                                    {
                                        "progress_rows": report.rows_seen,
                                        "files_seen": report.files_seen,
                                        "matches_inserted": report.matches_inserted,
                                        "matches_updated": report.matches_updated,
                                        "rows_unchanged": report.rows_unchanged,
                                        "odds_upserted": report.odds_upserted,
                                    },
                                    ensure_ascii=False,
                                ),
                                file=sys.stderr,
                                flush=True,
                            )
                        try:
                            record = _normalize_row(source)
                            if not include_odds and record.odds:
                                record = replace(
                                    record,
                                    odds=(),
                                    row_hash=_canonical_hash(
                                        {
                                            "content": record.row_hash,
                                            "scope": "history-without-odds",
                                        }
                                    ),
                                )
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
                        report.statistics_seen += sum(
                            1
                            for values in record.statistics.values()
                            if any(value is not None for value in values.values())
                        )
                        report.odds_seen += len(record.odds)
                        if dry_run:
                            continue
                        assert session is not None
                        assert state is not None
                        try:
                            outcome = _persist_record(
                                session,
                                record,
                                report,
                                state,
                                pending_odds,
                            )
                            if outcome == "inserted":
                                report.matches_inserted += 1
                            elif outcome == "updated":
                                report.matches_updated += 1
                            else:
                                report.rows_unchanged += 1
                            if outcome != "unchanged":
                                pending += 1
                            if pending >= max(1, batch_size):
                                if pending_odds:
                                    session.execute(insert(MatchOdds), list(pending_odds.values()))
                                session.commit()
                                pending = 0
                                pending_odds.clear()
                                committed_persist_counts = {
                                    key: int(getattr(report, key))
                                    for key in committed_persist_counts
                                }
                        except Exception as error:
                            session.rollback()
                            pending = 0
                            pending_odds.clear()
                            for key, value in committed_persist_counts.items():
                                setattr(report, key, value)
                            report.add_error(
                                f"{source.source_file}:{sheet_name}:{source.row_number}: database error: {error}"
                            )
                            stop = True
                            break
                    if stop:
                        break
            except Exception as error:
                report.add_error(f"{_source_path(file_path)}: {type(error).__name__}: {error}")
        if session is not None and pending:
            try:
                if pending_odds:
                    session.execute(insert(MatchOdds), list(pending_odds.values()))
                session.commit()
                pending_odds.clear()
            except Exception as error:
                session.rollback()
                pending_odds.clear()
                for key, value in committed_persist_counts.items():
                    setattr(report, key, value)
                report.add_error(f"final database batch error: {error}")
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
    parser.add_argument(
        "--skip-odds",
        action="store_true",
        help="importa clubes, partidos y estadísticas sin las cuotas históricas",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10_000,
        help="emit a compact progress line after this many source rows; 0 disables it",
    )
    args = parser.parse_args(argv)
    report = import_historical(
        args.path,
        dry_run=not args.commit,
        limit=args.limit,
        batch_size=args.batch_size,
        progress_every=args.progress_every,
        include_odds=not args.skip_odds,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
