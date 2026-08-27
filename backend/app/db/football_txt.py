from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from pathlib import PurePosixPath
import re


@dataclass(frozen=True, slots=True)
class FootballMatch:
    """One fixture normalized from a Football.TXT tournament document."""

    competition: str
    season_label: str
    season_start: int
    season_end: int
    match_date: date
    kickoff_time: time | None
    kickoff_utc_offset: str | None
    kickoff_precision: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    half_time_home_score: int | None
    half_time_away_score: int | None
    round: str | None
    venue: str | None
    status: str
    source_line: int


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

_SEASON_RE = re.compile(
    r"(?<!\d)(?P<start>(?:18|19|20)\d{2})"
    r"(?:\s*[-/]\s*(?P<end>\d{2}|(?:18|19|20)\d{2}))?(?!\d)"
)
_TEXT_DATE_PATTERNS = (
    re.compile(
        rf"^(?:[A-Za-z]{{2,9}}\.?\s+)?"
        rf"(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}})"
        r"(?:,?\s+(?P<year>\d{2,4})(?=\s|$))?\b(?P<rest>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:[A-Za-z]{{2,9}}\.?\s+)?"
        rf"(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_PATTERN})"
        r"(?:,?\s+(?P<year>\d{2,4})(?=\s|$))?\b(?P<rest>.*)$",
        re.IGNORECASE,
    ),
)
_NUMERIC_DATE_RE = re.compile(
    r"^(?:[A-Za-z]{2,9}\.?\s+)?"
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{2,4})"
    r"\b(?P<rest>.*)$"
)
_ISO_DATE_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"\b(?P<rest>.*)$"
)
_STAGE_RE = re.compile(r"^[▪•◾]\s*(?P<stage>.+?)\s*$")
_MATCH_NUMBER_RE = re.compile(r"^\(\d{1,3}\)\s*")
_TIME_RE = re.compile(
    r"^\[?(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)"
    r"(?:\s*/?\s*(?P<zone>UTC(?:[+\-−]\d{1,2}(?::?\d{2})?)?|GMT|CET|CEST|BST))?"
    r"\]?\s+",
    re.IGNORECASE,
)
_EXTRA_TIME = r"a\.?\s*e\.?\s*t\.?"
_RESULT_META = (
    rf"(?:\s+{_EXTRA_TIME})?"
    r"(?:\s*\([^)]*\))?"
    r"(?:\s*,?\s*\d{1,2}\s*-\s*\d{1,2}\s*(?:pen\.?|pens\.?))?"
)
_SCORE_BETWEEN_RE = re.compile(
    r"^(?P<home>.+?)\s+(?P<home_score>\d{1,2})\s*-\s*"
    r"(?P<away_score>\d{1,2})"
    rf"(?P<meta>{_RESULT_META})\s+(?P<away>.+?)\s*$",
    re.IGNORECASE,
)
_PENALTY_FIRST_RE = re.compile(
    r"^(?P<home>.+?)\s+\d{1,2}\s*-\s*\d{1,2}\s*pen\.?\s+"
    r"(?P<home_score>\d{1,2})\s*-\s*(?P<away_score>\d{1,2})"
    rf"(?P<meta>{_RESULT_META})\s+(?P<away>.+?)\s*$",
    re.IGNORECASE,
)
_VERSUS_RE = re.compile(r"^(?P<home>.+?)\s+v(?:s\.?)?\s+(?P<rest>.+?)\s*$", re.IGNORECASE)
_TRAILING_RESULT_RE = re.compile(
    r"^(?P<away>.+?)\s+(?P<home_score>\d{1,2})\s*-\s*"
    r"(?P<away_score>\d{1,2})"
    rf"(?P<meta>{_RESULT_META})\s*$",
    re.IGNORECASE,
)
_HALF_TIME_RE = re.compile(r"(?P<home>\d{1,2})\s*-\s*(?P<away>\d{1,2})")
_TRAILING_NOTE_RE = re.compile(r"\s+\[[^\]]+\]\s*$")


def _clean(value: str) -> str:
    return " ".join(value.split())


def _expanded_end_year(start: int, raw_end: str | None) -> int:
    if not raw_end:
        return start
    end = int(raw_end)
    if len(raw_end) == 2:
        end += (start // 100) * 100
        if end < start:
            end += 100
    return end


def _season_from_text(value: str) -> tuple[int, int, tuple[int, int]] | None:
    matches = list(_SEASON_RE.finditer(value))
    if not matches:
        return None
    match = matches[-1]
    start = int(match.group("start"))
    end = _expanded_end_year(start, match.group("end"))
    return start, end, match.span()


def _member_metadata(member_name: str) -> tuple[str, int | None, int | None]:
    normalized = member_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    season = _season_from_text(normalized)
    start = season[0] if season else None
    end = season[1] if season else None

    stem = path.stem
    stem = _SEASON_RE.sub(" ", stem)
    stem = _clean(re.sub(r"[_-]+", " ", stem)).strip()
    if stem.casefold() in {"cup", "cup finals", "quali playoffs", ""}:
        folded = normalized.casefold()
        if "fifa_world_cup" in folded:
            stem = "FIFA World Cup"
        elif "worldcup" in folded:
            stem = "World Cup"
    return stem, start, end


def _document_metadata(text: str, member_name: str) -> tuple[str, str, int, int] | None:
    member_competition, member_start, member_end = _member_metadata(member_name)
    header_value: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("="):
            header_value = stripped.lstrip("=").split("#", 1)[0].strip()
            break

    competition = member_competition
    start = member_start
    end = member_end
    if header_value:
        season = _season_from_text(header_value)
        if season:
            start, end, span = season
            competition = _clean(header_value[: span[0]] + " " + header_value[span[1] :])
        else:
            competition = _clean(header_value)

    if not competition or start is None or end is None or end < start:
        return None
    label = str(start) if start == end else f"{start}-{end}"
    return competition, label, start, end


def _stage(line: str) -> str | None:
    match = _STAGE_RE.match(line)
    if not match:
        return None
    stage = match.group("stage").split("|", 1)[0]
    return _clean(stage).strip(" :-") or None


def _expanded_date_year(raw_year: str, season_start: int) -> int:
    year = int(raw_year)
    if len(raw_year) == 2:
        year += (season_start // 100) * 100
        if year < season_start - 50:
            year += 100
        elif year > season_start + 50:
            year -= 100
    return year


def _inferred_date_year(month: int, season_start: int, season_end: int) -> int:
    if season_start == season_end:
        return season_start
    # Football seasons that span two calendar years normally turn over between
    # June and July. Explicit years in the document always take precedence.
    return season_start if month >= 7 else season_end


def _make_date(
    *,
    day: int,
    month: int,
    raw_year: str | None,
    season_start: int,
    season_end: int,
) -> date | None:
    year = (
        _expanded_date_year(raw_year, season_start)
        if raw_year
        else _inferred_date_year(month, season_start, season_end)
    )
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_prefix(
    line: str,
    *,
    season_start: int,
    season_end: int,
) -> tuple[date, str] | None:
    iso = _ISO_DATE_RE.match(line)
    if iso:
        parsed = _make_date(
            day=int(iso.group("day")),
            month=int(iso.group("month")),
            raw_year=iso.group("year"),
            season_start=season_start,
            season_end=season_end,
        )
        return (parsed, iso.group("rest").strip()) if parsed else None

    numeric = _NUMERIC_DATE_RE.match(line)
    if numeric:
        parsed = _make_date(
            day=int(numeric.group("day")),
            month=int(numeric.group("month")),
            raw_year=numeric.group("year"),
            season_start=season_start,
            season_end=season_end,
        )
        return (parsed, numeric.group("rest").strip()) if parsed else None

    for pattern in _TEXT_DATE_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        month = _MONTHS[match.group("month").casefold()]
        parsed = _make_date(
            day=int(match.group("day")),
            month=month,
            raw_year=match.group("year"),
            season_start=season_start,
            season_end=season_end,
        )
        return (parsed, match.group("rest").strip()) if parsed else None
    return None


def _normalized_offset(zone: str | None) -> str | None:
    if not zone:
        return None
    normalized = zone.upper().replace("−", "-")
    named = {
        "UTC": "+00:00",
        "GMT": "+00:00",
        "CET": "+01:00",
        "CEST": "+02:00",
        "BST": "+01:00",
    }
    if normalized in named:
        return named[normalized]
    match = re.fullmatch(r"UTC(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?", normalized)
    if not match:
        return None
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes") or 0)
    if hours > 14 or minutes > 59 or (hours == 14 and minutes):
        return None
    return f"{match.group('sign')}{hours:02d}:{minutes:02d}"


def _kickoff_prefix(line: str) -> tuple[time | None, str | None, str]:
    body = _MATCH_NUMBER_RE.sub("", line, count=1)
    match = _TIME_RE.match(body)
    if not match:
        return None, None, body.strip()
    kickoff = time(int(match.group("hour")), int(match.group("minute")))
    offset = _normalized_offset(match.group("zone"))
    return kickoff, offset, body[match.end() :].strip()


def _half_time(meta: str) -> tuple[int | None, int | None]:
    parenthesized = re.search(r"\((?P<scores>[^)]*)\)", meta)
    if not parenthesized:
        return None, None
    scores = list(_HALF_TIME_RE.finditer(parenthesized.group("scores")))
    if not scores:
        return None, None
    # In a.e.t. notation the first pair is the score after 90 minutes and the
    # final pair is the half-time score, e.g. ``(1-1, 0-1)``.
    score = scores[-1]
    return int(score.group("home")), int(score.group("away"))


def _valid_team(value: str) -> bool:
    if not 1 < len(value) <= 120 or not any(character.isalpha() for character in value):
        return False
    if not value[0].isalpha() or any(character in value for character in "@|"):
        return False
    folded = value.casefold()
    return not any(
        folded.startswith(prefix)
        for prefix in ("group ", "matchday ", "matches ", "teams ", "date ")
    )


def _teams_are_valid(home: str, away: str) -> bool:
    return _valid_team(home) and _valid_team(away) and home.casefold() != away.casefold()


def _fixture_parts(line: str) -> tuple[str, str | None]:
    without_comment = line.split("##", 1)[0].strip()
    if "@" not in without_comment:
        return _TRAILING_NOTE_RE.sub("", without_comment).strip(), None
    fixture, venue = without_comment.split("@", 1)
    venue = _TRAILING_NOTE_RE.sub("", venue).strip()
    return _TRAILING_NOTE_RE.sub("", fixture).strip(), _clean(venue) or None


def _match_values(
    line: str,
) -> tuple[str, str, int | None, int | None, int | None, int | None, str | None] | None:
    fixture, venue = _fixture_parts(line)
    penalty_first = _PENALTY_FIRST_RE.match(fixture)
    if penalty_first and venue is not None:
        home = _clean(penalty_first.group("home"))
        away = _clean(penalty_first.group("away"))
        if _teams_are_valid(home, away):
            half_home, half_away = _half_time(penalty_first.group("meta"))
            return (
                home,
                away,
                int(penalty_first.group("home_score")),
                int(penalty_first.group("away_score")),
                half_home,
                half_away,
                venue,
            )

    score_between = _SCORE_BETWEEN_RE.match(fixture)
    if score_between and venue is not None:
        # This flexible grammar could otherwise mistake a standings row for a
        # match. The canonical score-between-teams documents include a venue.
        home = _clean(score_between.group("home"))
        away = _clean(score_between.group("away"))
        if _teams_are_valid(home, away):
            half_home, half_away = _half_time(score_between.group("meta"))
            return (
                home,
                away,
                int(score_between.group("home_score")),
                int(score_between.group("away_score")),
                half_home,
                half_away,
                venue,
            )

    versus = _VERSUS_RE.match(fixture)
    if not versus:
        return None
    home = _clean(versus.group("home"))
    rest = versus.group("rest").strip()
    trailing_result = _TRAILING_RESULT_RE.match(rest)
    if trailing_result:
        away = _clean(trailing_result.group("away"))
        if not _teams_are_valid(home, away):
            return None
        half_home, half_away = _half_time(trailing_result.group("meta"))
        return (
            home,
            away,
            int(trailing_result.group("home_score")),
            int(trailing_result.group("away_score")),
            half_home,
            half_away,
            venue,
        )

    away = _clean(rest)
    if not _teams_are_valid(home, away):
        return None
    return home, away, None, None, None, None, venue


def parse_football_txt(text: str, *, member_name: str) -> list[FootballMatch]:
    """Parse conservative fixture records from a Football.TXT document.

    A record is accepted only after a valid document/member season and a valid
    date have been established. Score-between-teams records additionally need
    an ``@ venue`` delimiter; ``home v away`` supplies its own safe boundary and
    may represent either a played or a scheduled match.
    """

    metadata = _document_metadata(text, member_name)
    if metadata is None:
        return []
    competition, season_label, season_start, season_end = metadata

    output: list[FootballMatch] = []
    current_date: date | None = None
    current_round: str | None = None
    for source_line, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith(("#", "=")):
            continue
        if line.startswith("(") and _MATCH_NUMBER_RE.match(line) is None:
            continue

        stage = _stage(line)
        if stage is not None:
            current_round = stage
            continue

        dated = _date_prefix(
            line,
            season_start=season_start,
            season_end=season_end,
        )
        if dated is not None:
            current_date, line = dated
            if not line or line.startswith("-"):
                continue
        if current_date is None:
            continue

        kickoff, utc_offset, line = _kickoff_prefix(line)
        values = _match_values(line)
        if values is None:
            continue
        home, away, home_score, away_score, half_home, half_away, venue = values
        if kickoff is None:
            precision = "date-only"
        elif utc_offset is None:
            precision = "datetime-local-unknown"
        else:
            precision = "datetime-offset"
        output.append(
            FootballMatch(
                competition=competition,
                season_label=season_label,
                season_start=season_start,
                season_end=season_end,
                match_date=current_date,
                kickoff_time=kickoff,
                kickoff_utc_offset=utc_offset,
                kickoff_precision=precision,
                home_team=home,
                away_team=away,
                home_score=home_score,
                away_score=away_score,
                half_time_home_score=half_home,
                half_time_away_score=half_away,
                round=current_round,
                venue=venue,
                status="FINALIZADO" if home_score is not None else "PROGRAMADO",
                source_line=source_line,
            )
        )
    return output


__all__ = ["FootballMatch", "parse_football_txt"]
