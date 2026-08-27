from datetime import date, datetime, timedelta, timezone
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import logging
from threading import Lock
import time
import unicodedata
from zoneinfo import ZoneInfo
import httpx

from app.core.config import settings
from app.db import load_match as load_stored_match
from app.db import load_matches as load_stored_matches
from app.db import persist_matches as persist_stored_matches
from app.schemas.matches import (
    DisciplineSummary,
    H2HMatchItem,
    InjuryItem,
    MatchAnalysisResponse,
    MatchSummary,
    Recommendation,
    RefereeInfo,
    TeamDisciplineAverage,
)
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.api_football import APIFootballAPIError, APIFootballProvider, BookmakerQuote
from app.services.match_evidence import build_match_evidence
from app.services.opportunities import enrich_analysis_with_opportunities
from app.services.sportmonks import SportmonksAPIError, SportmonksProvider

logger = logging.getLogger(__name__)
SPORTS_TIMEZONE = ZoneInfo("America/Lima")


class MockSportsDataProvider:
    """Datos demostrativos aislados para desarrollar la interfaz sin proveedor real."""

    def list_highlights(self, match_date: date | None = None) -> list[MatchSummary]:
        selected_date = match_date or datetime.now(SPORTS_TIMEZONE).date()
        return [
            MatchSummary(
                id="demo-arsenal-chelsea",
                competition="Premier League",
                kickoff_at=datetime(selected_date.year, selected_date.month, selected_date.day, 19, 30, tzinfo=timezone.utc),
                home_team="Arsenal",
                away_team="Chelsea",
                home_team_id="42",
                away_team_id="49",
                home_logo="https://media.api-sports.io/football/teams/42.png",
                away_logo="https://media.api-sports.io/football/teams/49.png",
                venue="Emirates Stadium",
                referee="Michael Oliver",
                home_form="W-W-D-W-L",
                away_form="D-W-L-W-W",
                data_quality=0.91,
                odds_available=False,
                status="PROGRAMADO",
                source_provider="mock",
            ),
            MatchSummary(
                id="demo-bayern-dortmund",
                competition="Bundesliga",
                kickoff_at=datetime(selected_date.year, selected_date.month, selected_date.day, 20, 0, tzinfo=timezone.utc),
                home_team="Bayern Munich",
                away_team="Borussia Dortmund",
                home_team_id="157",
                away_team_id="165",
                home_logo="https://media.api-sports.io/football/teams/157.png",
                away_logo="https://media.api-sports.io/football/teams/165.png",
                venue="Allianz Arena",
                referee="Felix Zwayer",
                home_form="W-W-W-D-W",
                away_form="W-L-W-D-L",
                data_quality=0.86,
                odds_available=False,
                status="PROGRAMADO",
                source_provider="mock",
            ),
            MatchSummary(
                id="demo-inter-milan",
                competition="Serie A",
                kickoff_at=datetime(selected_date.year, selected_date.month, selected_date.day, 21, 45, tzinfo=timezone.utc),
                home_team="Inter",
                away_team="AC Milan",
                home_team_id="505",
                away_team_id="489",
                home_logo="https://media.api-sports.io/football/teams/505.png",
                away_logo="https://media.api-sports.io/football/teams/489.png",
                venue="San Siro",
                referee="Daniele Orsato",
                home_form="W-D-W-W-W",
                away_form="L-W-D-W-D",
                data_quality=0.78,
                odds_available=False,
                status="PROGRAMADO",
                source_provider="mock",
            ),
        ]


class FootballDataProvider:
    """Adaptador para football-data.org (v4 API) con partidos actuales, H2H y forma reciente."""

    provider_name = "football-data"

    def __init__(self, token: str, base_url: str, timeout: int) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self.token}

    def list_fixtures(
        self,
        match_date: date,
        *,
        timeout: float | None = None,
    ) -> list[MatchSummary]:
        endpoint = f"{self.base_url}/matches"
        from_date = match_date.isoformat()
        # football-data filters by UTC dates. A Lima match late at night can
        # belong to the following UTC day, so request both and then apply the
        # product's local-day boundary ourselves.
        to_date = (match_date + timedelta(days=1)).isoformat()

        response = httpx.get(
            endpoint,
            params={"dateFrom": from_date, "dateTo": to_date},
            headers=self._headers(),
            timeout=self.timeout if timeout is None else max(0.1, timeout),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise FootballDataAPIError(
                "Football-Data devolvió una respuesta sin una lista de partidos válida."
            )
        matches = [
            self._to_match(item, endpoint)
            for item in payload["matches"]
            if self._is_relevant_match(item)
        ]
        return [
            match
            for match in matches
            if match.kickoff_at.astimezone(SPORTS_TIMEZONE).date() == match_date
        ]

    def get_fixture(self, fixture_id: str) -> MatchSummary | None:
        """Resolve one fixture without depending on today's agenda."""
        clean_id = fixture_id.removeprefix("football-data-")
        if not clean_id.isdigit():
            return None

        endpoint = f"{self.base_url}/matches/{clean_id}"
        response = httpx.get(endpoint, headers=self._headers(), timeout=self.timeout)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("id") is None:
            raise FootballDataAPIError(
                "Football-Data devolvió un partido con formato inesperado."
            )
        return self._to_match(payload, endpoint)

    def get_head_to_head(self, match_id: str, limit: int = 10) -> list[H2HMatchItem]:
        clean_id = match_id.removeprefix("football-data-")
        if not clean_id.isdigit():
            return []
        endpoint = f"{self.base_url}/matches/{clean_id}/head2head"
        bounded_limit = max(1, min(limit, 10))
        res = httpx.get(
            endpoint,
            params={"limit": bounded_limit},
            headers=self._headers(),
            timeout=self.timeout,
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise FootballDataAPIError(
                "Football-Data devolvió un historial H2H con formato inesperado."
            )
        return self.normalize_history(payload["matches"], bounded_limit)

    def get_team_last_matches(self, team_id: str, limit: int = 5) -> list[dict]:
        clean_id = str(team_id).removeprefix("football-data-")
        if not clean_id.isdigit():
            return []
        endpoint = f"{self.base_url}/teams/{clean_id}/matches"
        bounded_limit = max(1, min(limit, 10))
        res = httpx.get(
            endpoint,
            params={"status": "FINISHED", "limit": bounded_limit},
            headers=self._headers(),
            timeout=self.timeout,
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise FootballDataAPIError(
                "Football-Data devolvió un historial de equipo con formato inesperado."
            )
        return sorted(
            payload["matches"],
            key=lambda item: str(item.get("utcDate") or ""),
            reverse=True,
        )[:bounded_limit]

    @staticmethod
    def _history_item(item: dict) -> H2HMatchItem | None:
        """Normalize real provider history without substituting absent fields."""
        raw_date = item.get("utcDate")
        competition = (item.get("competition") or {}).get("name")
        home = (item.get("homeTeam") or {}).get("name")
        away = (item.get("awayTeam") or {}).get("name")
        score = (item.get("score") or {}).get("fullTime") or {}
        home_goals = score.get("home")
        away_goals = score.get("away")
        if not raw_date or not competition or not home or not away or home_goals is None or away_goals is None:
            return None

        if home_goals > away_goals:
            winner = home
        elif away_goals > home_goals:
            winner = away
        else:
            winner = "Empate"

        return H2HMatchItem(
            date=str(raw_date)[:10],
            competition=str(competition),
            home_team=str(home),
            away_team=str(away),
            score=f"{home_goals} - {away_goals}",
            winner=winner,
        )

    @classmethod
    def normalize_history(cls, items: list[dict], limit: int = 5) -> list[H2HMatchItem]:
        normalized = [match for item in items if (match := cls._history_item(item)) is not None]
        normalized.sort(key=lambda match: match.date, reverse=True)
        return normalized[: max(0, limit)]

    def _to_match(self, item: dict, endpoint: str) -> MatchSummary:
        competition = item.get("competition") or {}
        area = item.get("area") or competition.get("area") or {}
        home = item.get("homeTeam") or {}
        away = item.get("awayTeam") or {}
        score = item.get("score") if isinstance(item.get("score"), dict) else {}
        full_time = score.get("fullTime") if isinstance(score.get("fullTime"), dict) else {}
        half_time = score.get("halfTime") if isinstance(score.get("halfTime"), dict) else {}
        referees = item.get("referees") or []
        referee_name = referees[0].get("name") if referees and isinstance(referees, list) else None

        kickoff = datetime.fromisoformat(item["utcDate"].replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return MatchSummary(
            id=f"football-data-{item['id']}",
            external_id=str(item["id"]),
            competition=competition.get("name") or "Competición sin nombre",
            country=area.get("name") if isinstance(area, dict) else None,
            country_code=(
                str(area.get("code"))
                if isinstance(area, dict) and area.get("code")
                else None
            ),
            competition_logo=competition.get("emblem"),
            kickoff_at=kickoff,
            home_team=home.get("name") or "Equipo local",
            away_team=away.get("name") or "Equipo visitante",
            home_team_id=str(home.get("id")) if home.get("id") else None,
            away_team_id=str(away.get("id")) if away.get("id") else None,
            home_logo=home.get("crest"),
            away_logo=away.get("crest"),
            referee=referee_name,
            data_quality=0.92,
            odds_available=False,
            home_score=self._optional_nonnegative_int(full_time.get("home")),
            away_score=self._optional_nonnegative_int(full_time.get("away")),
            halftime_home_score=self._optional_nonnegative_int(half_time.get("home")),
            halftime_away_score=self._optional_nonnegative_int(half_time.get("away")),
            elapsed=self._optional_nonnegative_int(item.get("minute")),
            status_short=str(item.get("status") or "UNKNOWN").upper(),
            status=self._normalize_status(item.get("status", "")),
            source_provider=self.provider_name,
            source_url=endpoint,
        )

    @staticmethod
    def _is_relevant_match(item: dict) -> bool:
        # The scoreboard must retain completed and interrupted fixtures so a
        # selected historical date renders the actual result instead of an
        # empty agenda. Only structurally unusable records are skipped.
        return bool(item.get("id") is not None and item.get("utcDate"))

    @staticmethod
    def _optional_nonnegative_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _normalize_status(status: str) -> str:
        status = (status or "").upper()
        if status in {"SCHEDULED", "TIMED"}:
            return "PROGRAMADO"
        if status in {"IN_PLAY", "LIVE"}:
            return "EN JUEGO"
        if status == "PAUSED":
            return "EN PAUSA"
        if status == "FINISHED":
            return "FINALIZADO"
        if status == "POSTPONED":
            return "POSPUESTO"
        if status in {"SUSPENDED", "CANCELLED"}:
            return "SUSPENDIDO"
        return status or "DESCONOCIDO"


class FootballDataAPIError(RuntimeError):
    """Football-Data returned a successful HTTP response with invalid data."""


mock_provider = MockSportsDataProvider()
api_football_provider = APIFootballProvider(
    key=settings.api_football_key,
    base_url=settings.api_football_base_url,
    is_rapidapi=settings.api_football_is_rapidapi,
    timeout=settings.api_football_timeout_seconds,
)
sportmonks_provider = SportmonksProvider(
    token=settings.sportmonks_api_token,
    base_url=settings.sportmonks_base_url,
    timeout=settings.sportmonks_timeout_seconds,
)
football_data_provider = FootballDataProvider(
    settings.football_data_api_token,
    settings.football_data_base_url,
    settings.football_data_timeout_seconds,
)


@dataclass(frozen=True)
class FixtureResult:
    date: date
    matches: list[MatchSummary]
    source: str
    notice: str | None = None


_FIXTURE_CACHE_TTL_SECONDS = 60
_LIVE_FIXTURE_CACHE_TTL_SECONDS = 15
_FIXTURE_STALE_TTL_SECONDS = 15 * 60
_MATCH_INDEX_TTL_SECONDS = 12 * 60 * 60
_PROVIDER_RETRY_COOLDOWN_SECONDS = 10
# Most complementary resources can be reused for ten minutes. Published
# lineups are different: a pre-window cache expires exactly when T-60 is
# crossed, and an unconfirmed response inside that window is retried every five
# minutes instead of on every page load.
_ANALYSIS_CACHE_TTL_SECONDS = 10 * 60
_UNCONFIRMED_LINEUP_CACHE_TTL_SECONDS = 5 * 60
_LINEUP_REFRESH_WINDOW_AFTER_KICKOFF = timedelta(hours=3)
_fixture_cache: dict[str, tuple[float, FixtureResult]] = {}
_fixture_by_id: dict[str, tuple[float, MatchSummary]] = {}
_provider_retry_after: dict[str, float] = {}
_analysis_cache: dict[tuple[str, bool], tuple[float, MatchAnalysisResponse]] = {}
_fixture_route_locks: dict[str, Lock] = {}
_fixture_route_locks_guard = Lock()
_recommendation_analysis_locks: dict[str, Lock] = {}
_recommendation_analysis_locks_guard = Lock()


class MatchProviderUnavailable(RuntimeError):
    """The configured upstream could not resolve a fixture right now."""


def _cache_get(cache: dict, key: object, ttl: int):
    cached = cache.get(key)
    if cached is None:
        return None
    stored_at, value = cached
    if time.monotonic() - stored_at > ttl:
        return None
    return value


def _cache_set(cache: dict, key: object, value: object) -> None:
    cache[key] = (time.monotonic(), value)


_LIVE_STATUS_SHORTS = {
    "1H",
    "HT",
    "2H",
    "BT",
    "ET",
    "P",
    "LIVE",
    "IN_PLAY",
    "PAUSED",
    "INPLAY_1ST_HALF",
    "INPLAY_2ND_HALF",
    "INPLAY_ET",
    "INPLAY_PENALTIES",
    "BREAK",
    "EXTRA_TIME_BREAK",
    "PEN_BREAK",
}


def _is_live_match(match: MatchSummary) -> bool:
    status_short = str(match.status_short or "").strip().upper()
    if status_short in _LIVE_STATUS_SHORTS:
        return True
    normalized = " ".join(str(match.status or "").upper().split())
    return normalized.startswith("EN JUEGO") or normalized in {
        "EN PAUSA",
        "ENTRETIEMPO",
        "DESCANSO",
        "TIEMPO EXTRA",
        "PENALES",
    }


def _fixture_cache_get(key: str) -> FixtureResult | None:
    cached = _fixture_cache.get(key)
    if cached is None:
        return None
    stored_at, result = cached
    if not isinstance(result, FixtureResult):
        return None
    ttl = (
        _LIVE_FIXTURE_CACHE_TTL_SECONDS
        if any(_is_live_match(match) for match in result.matches)
        else _FIXTURE_CACHE_TTL_SECONDS
    )
    if time.monotonic() - stored_at > ttl:
        return None
    return result


def _get_cached_analysis(
    key: tuple[str, bool],
    now: datetime | None = None,
) -> MatchAnalysisResponse | None:
    cached = _analysis_cache.get(key)
    if cached is None:
        return None

    stored_at, analysis = cached
    age_seconds = max(0.0, time.monotonic() - stored_at)
    if age_seconds > _ANALYSIS_CACHE_TTL_SECONDS:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    kickoff = analysis.match.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    lineup_window_start = kickoff - timedelta(minutes=60)
    stored_wall_time = current - timedelta(seconds=age_seconds)

    # A probable/pending result created before the publication window must not
    # survive after T-60 merely because its generic ten-minute TTL is active.
    if stored_wall_time < lineup_window_start <= current:
        return None

    lineups_confirmed = bool(analysis.lineups and analysis.lineups.confirmed)
    lineup_refresh_window = (
        lineup_window_start
        <= current
        <= kickoff + _LINEUP_REFRESH_WINDOW_AFTER_KICKOFF
    )
    if lineup_refresh_window and not lineups_confirmed:
        if age_seconds > _UNCONFIRMED_LINEUP_CACHE_TTL_SECONDS:
            return None

    return analysis


def _index_matches(matches: list[MatchSummary]) -> None:
    stored_at = time.monotonic()
    for match in matches:
        _fixture_by_id[match.id] = (stored_at, match)


def _persist_real_matches(matches: list[MatchSummary]) -> None:
    """Store provider fixtures without making database failures break livescores."""

    real_matches = [match for match in matches if match.source_provider != "mock"]
    if not real_matches:
        return
    try:
        persist_stored_matches(real_matches)
    except Exception:
        logger.exception("No se pudo persistir la agenda real en la base de datos.")


def _stored_fixture_result(
    selected_date: date,
    *,
    notice: str,
) -> FixtureResult | None:
    """Return the durable agenda for a date when upstream data is unavailable."""

    try:
        matches = load_stored_matches(selected_date)
    except Exception:
        logger.exception(
            "No se pudo consultar la agenda persistida para %s.",
            selected_date.isoformat(),
        )
        return None
    if not matches:
        return None
    result = FixtureResult(
        date=selected_date,
        matches=matches,
        source="database",
        notice=notice,
    )
    _index_matches(matches)
    return result


def _active_provider():
    provider_setting = settings.sports_data_provider.casefold()
    if provider_setting in {"sportmonks", "sport-monks", "monks"} and settings.sportmonks_api_token:
        return sportmonks_provider
    if provider_setting in {
        "api-football",
        "apifootball",
        "api-sports",
        "apisports",
    } and settings.api_football_key:
        return api_football_provider
    if provider_setting in {"football-data", "footballdata"} and settings.football_data_api_token:
        return football_data_provider
    # Keep a deterministic automatic priority when the named provider is not
    # configured. Football-Data remains the final real-data fallback.
    if settings.api_football_key:
        return api_football_provider
    if settings.sportmonks_api_token:
        return sportmonks_provider
    if settings.football_data_api_token:
        return football_data_provider
    return mock_provider


def _provider_name(provider: object) -> str:
    return str(getattr(provider, "provider_name", "mock"))


def _provider_chain() -> list[object]:
    """Return configured providers with Football-Data always in last place."""

    primary = _active_provider()
    if primary is mock_provider:
        return []
    providers: list[object] = []

    # A monkeypatched/custom primary is also retained, which keeps the routing
    # helper testable without relying on process-global credentials.
    if not isinstance(primary, FootballDataProvider):
        providers.append(primary)

    preferred = settings.sports_data_provider.casefold()
    first_two = (
        [sportmonks_provider, api_football_provider]
        if preferred in {"sportmonks", "sport-monks", "monks"}
        else [api_football_provider, sportmonks_provider]
    )
    for provider in first_two:
        configured = (
            settings.api_football_key
            if isinstance(provider, APIFootballProvider)
            else settings.sportmonks_api_token
        )
        if configured and all(
            _provider_name(provider) != _provider_name(existing)
            for existing in providers
        ):
            providers.append(provider)

    if settings.football_data_api_token:
        fallback = primary if isinstance(primary, FootballDataProvider) else football_data_provider
        if all(
            _provider_name(fallback) != _provider_name(existing)
            for existing in providers
        ):
            providers.append(fallback)
    elif isinstance(primary, FootballDataProvider):
        # Custom providers used in tests carry their credential internally.
        providers.append(primary)
    return providers


def _provider_for_match_id(
    match_id: str,
    *,
    source_provider: str | None = None,
) -> object | None:
    """Route fixture detail calls by their immutable provider namespace.

    The configured agenda provider can change between listing a match and
    opening it. Prefix/source routing keeps those already issued IDs usable.
    """

    active = _active_provider()
    source = (source_provider or "").casefold()
    wants_api_football = match_id.startswith("api-football-") or source == "api-football"
    wants_sportmonks = match_id.startswith("sportmonks-") or source == "sportmonks"
    wants_football_data = match_id.startswith("football-data-") or source == "football-data"

    if wants_api_football:
        if isinstance(active, APIFootballProvider):
            return active
        if settings.api_football_key:
            return api_football_provider
        return None
    if wants_sportmonks:
        if isinstance(active, SportmonksProvider):
            return active
        if settings.sportmonks_api_token:
            return sportmonks_provider
        return None
    if wants_football_data:
        if isinstance(active, FootballDataProvider):
            return active
        if settings.football_data_api_token:
            return football_data_provider
        return None
    if match_id.startswith("demo-") or source == "mock":
        return mock_provider if active is mock_provider else None
    return None


def _provider_cache_key(provider: object, selected_date: date) -> str:
    return f"provider:{_provider_name(provider)}:{selected_date.isoformat()}"


def _route_cache_key(providers: list[object], selected_date: date) -> str:
    route = ">".join(_provider_name(provider) for provider in providers) or "mock"
    return f"route:{route}:{selected_date.isoformat()}"


def _fixture_route_lock(cache_key: str) -> Lock:
    with _fixture_route_locks_guard:
        return _fixture_route_locks.setdefault(cache_key, Lock())


def _recommendation_analysis_lock(match_id: str) -> Lock:
    with _recommendation_analysis_locks_guard:
        return _recommendation_analysis_locks.setdefault(match_id, Lock())


def _provider_retry_delay(exc: Exception) -> int:
    """Back off longer for suspended, unauthorized or quota-limited accounts."""

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After")
            try:
                return max(10, min(int(float(retry_after or 0)), 60 * 60))
            except (TypeError, ValueError):
                return 5 * 60
        if status in {401, 403}:
            return 5 * 60
    if isinstance(exc, (APIFootballAPIError, SportmonksAPIError)):
        return 60
    return _PROVIDER_RETRY_COOLDOWN_SECONDS


def _list_fixtures_with_timeout(
    provider: object,
    selected_date: date,
    remaining: float,
) -> list[MatchSummary]:
    """Pass the route's remaining budget when the adapter supports it.

    Signature inspection also keeps lightweight test doubles and older custom
    adapters compatible: callables without a named ``timeout`` receive only the
    date argument.
    """

    method = provider.list_fixtures  # type: ignore[attr-defined]
    try:
        supports_timeout = "timeout" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        supports_timeout = False
    if supports_timeout:
        provider_timeout = float(getattr(provider, "timeout", remaining))
        return method(
            selected_date,
            timeout=max(0.1, min(provider_timeout, remaining)),
        )
    return method(selected_date)


def _latest_stale_result(
    providers: list[object],
    selected_date: date,
) -> FixtureResult | None:
    now = time.monotonic()
    candidates: list[tuple[float, FixtureResult]] = []
    for provider in providers:
        cached = _fixture_cache.get(_provider_cache_key(provider, selected_date))
        if cached is None:
            continue
        stored_at, result = cached
        if (
            now - stored_at <= _FIXTURE_STALE_TTL_SECONDS
            and result.source != "mock"
            and all(match.source_provider != "mock" for match in result.matches)
        ):
            candidates.append((stored_at, result))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _get_highlights_result_once(match_date: date | None = None) -> FixtureResult:
    selected_date = match_date or datetime.now(SPORTS_TIMEZONE).date()
    providers = _provider_chain()
    route_deadline = time.monotonic() + settings.sports_data_total_timeout_seconds
    route_cache_key = _route_cache_key(providers, selected_date)
    cached = _fixture_cache_get(route_cache_key)
    if cached is not None:
        _index_matches(cached.matches)
        return cached

    if not providers:
        stored = _stored_fixture_result(
            selected_date,
            notice=(
                "No hay un proveedor deportivo configurado; se muestra la agenda "
                "guardada en la base de datos."
            ),
        )
        if stored is not None:
            _cache_set(_fixture_cache, route_cache_key, stored)
            return stored
        matches = mock_provider.list_highlights(selected_date)
        result = FixtureResult(
            date=selected_date,
            matches=matches,
            source="mock",
            notice="Proveedor externo no configurado; se muestran fixtures demostrativos.",
        )
        _cache_set(_fixture_cache, route_cache_key, result)
        _index_matches(matches)
        return result

    failed_sources: list[str] = []
    empty_sources: list[str] = []
    for provider in providers:
        source = _provider_name(provider)
        provider_cache_key = _provider_cache_key(provider, selected_date)
        provider_cached = _fixture_cache_get(provider_cache_key)
        if provider_cached is not None:
            if not provider_cached.matches:
                empty_sources.append(provider_cached.source)
                continue
            notice = provider_cached.notice
            prior_results: list[str] = []
            if failed_sources:
                prior_results.append(f"{', '.join(failed_sources)} no respondió")
            if empty_sources:
                prior_results.append(f"{', '.join(empty_sources)} no devolvió partidos")
            if prior_results:
                notice = (
                    f"{'; '.join(prior_results)}; agenda real obtenida "
                    f"automáticamente desde {provider_cached.source}."
                )
            result = FixtureResult(
                selected_date,
                provider_cached.matches,
                provider_cached.source,
                notice,
            )
            _cache_set(_fixture_cache, route_cache_key, result)
            _index_matches(result.matches)
            return result
        retry_after = _provider_retry_after.get(provider_cache_key, 0.0)
        if time.monotonic() < retry_after:
            failed_sources.append(source)
            continue

        remaining = route_deadline - time.monotonic()
        if remaining <= 0:
            failed_sources.extend(
                _provider_name(p)
                for p in providers
                if _provider_name(p) not in failed_sources
            )
            logger.warning(
                "La cadena deportiva agotó su plazo de %ss antes de consultar %s.",
                settings.sports_data_total_timeout_seconds,
                source,
            )
            break

        provider_started_at = time.monotonic()
        try:
            matches = _list_fixtures_with_timeout(provider, selected_date, remaining)
            if not isinstance(matches, list):
                raise ValueError("El proveedor no devolvió una lista de partidos")
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - provider_started_at) * 1000)
            logger.error(
                "No se pudo actualizar la agenda desde %s tras %sms (%s).",
                source,
                elapsed_ms,
                type(exc).__name__,
            )
            failed_sources.append(source)
            _provider_retry_after[provider_cache_key] = (
                time.monotonic() + _provider_retry_delay(exc)
            )
            continue

        elapsed_ms = round((time.monotonic() - provider_started_at) * 1000)
        logger.info(
            "Agenda recibida desde %s en %sms (%s partidos).",
            source,
            elapsed_ms,
            len(matches),
        )

        # An empty 200/204 is not an outage, but it also cannot satisfy the
        # requested agenda. Cache it per provider and continue through the
        # configured chain, with Football-Data remaining the final fallback.
        _provider_retry_after.pop(provider_cache_key, None)
        provider_result = FixtureResult(
            date=selected_date,
            matches=matches,
            source=source,
        )
        _cache_set(_fixture_cache, provider_cache_key, provider_result)
        if not matches:
            empty_sources.append(source)
            continue

        notice = None
        prior_results: list[str] = []
        if failed_sources:
            prior_results.append(f"{', '.join(failed_sources)} no respondió")
        if empty_sources:
            prior_results.append(f"{', '.join(empty_sources)} no devolvió partidos")
        if prior_results:
            notice = (
                f"{'; '.join(prior_results)}; agenda real obtenida "
                f"automáticamente desde {source}."
            )
        result = FixtureResult(
            date=selected_date,
            matches=matches,
            source=source,
            notice=notice,
        )
        # Provider cache is route-neutral. A failover notice belongs only to
        # the route that experienced the failure, otherwise Football-Data used
        # later as primary would falsely retain an API-Football outage notice.
        _persist_real_matches(matches)
        _cache_set(_fixture_cache, route_cache_key, result)
        _index_matches(matches)
        return result

    # At least one provider answered correctly but none covered this date.
    # Return an honest empty real-data result only after trying the full chain.
    if empty_sources:
        observations: list[str] = []
        if failed_sources:
            observations.append(f"{', '.join(failed_sources)} no respondió")
        observations.append(f"{', '.join(empty_sources)} no devolvió partidos")
        stored = _stored_fixture_result(
            selected_date,
            notice=(
                f"{'; '.join(observations)}. Se muestra la agenda histórica o "
                "sincronizada guardada en la base de datos."
            ),
        )
        if stored is not None:
            _cache_set(_fixture_cache, route_cache_key, stored)
            return stored
        result = FixtureResult(
            date=selected_date,
            matches=[],
            source=empty_sources[-1],
            notice=(
                f"{'; '.join(observations)}. Se probaron todos los proveedores "
                "configurados y no se usaron partidos demo."
            ),
        )
        _cache_set(_fixture_cache, route_cache_key, result)
        return result

    # A live secondary response is always preferred over stale primary data.
    # Only consult stale real data after every configured source failed.
    stale = _latest_stale_result(providers, selected_date)
    if stale is not None:
        result = FixtureResult(
            date=selected_date,
            matches=stale.matches,
            source=stale.source,
            notice=(
                "Los proveedores de partidos están temporalmente indisponibles; "
                f"se muestra la última agenda real disponible desde {stale.source}."
            ),
        )
        _index_matches(result.matches)
        return result

    stored = _stored_fixture_result(
        selected_date,
        notice=(
            "Los proveedores de partidos no respondieron; se muestra la última "
            "agenda guardada en la base de datos."
        ),
    )
    if stored is not None:
        _cache_set(_fixture_cache, route_cache_key, stored)
        return stored

    source = _provider_name(providers[0])
    return FixtureResult(
        date=selected_date,
        matches=[],
        source=source,
        notice=(
            "Los proveedores de partidos no respondieron a tiempo. "
            "No se sustituyeron los datos reales por partidos demo."
        ),
    )


def get_highlights_result(match_date: date | None = None) -> FixtureResult:
    """Resolve one agenda refresh per provider route/date at a time."""

    selected_date = match_date or datetime.now(SPORTS_TIMEZONE).date()
    providers = _provider_chain()
    route_cache_key = _route_cache_key(providers, selected_date)
    cached = _fixture_cache_get(route_cache_key)
    if cached is not None:
        _index_matches(cached.matches)
        return cached
    with _fixture_route_lock(route_cache_key):
        # The first request populates the route envelope. Waiting page sections
        # reuse it, including its real source and failover notice.
        return _get_highlights_result_once(selected_date)


def get_highlights(match_date: date | None = None) -> list[MatchSummary]:
    return get_highlights_result(match_date).matches


def get_live_matches_result(match_date: date | None = None) -> FixtureResult:
    """Return only fixtures that are currently playing or temporarily paused."""

    result = get_highlights_result(match_date)
    return FixtureResult(
        date=result.date,
        matches=[match for match in result.matches if _is_live_match(match)],
        source=result.source,
        notice=result.notice,
    )


def search_matches_result(
    query: str | None = None,
    match_date: date | None = None,
) -> FixtureResult:
    result = get_highlights_result(match_date)
    if not query:
        return result
    needle = query.casefold().strip()
    matches = [
        match
        for match in result.matches
        if needle in f"{match.home_team} {match.away_team} {match.competition}".casefold()
    ]
    return FixtureResult(date=result.date, matches=matches, source=result.source, notice=result.notice)


def search_matches(
    query: str | None = None,
    match_date: date | None = None,
) -> list[MatchSummary]:
    return search_matches_result(query, match_date).matches



def get_match(match_id: str) -> MatchSummary | None:
    cached = _cache_get(_fixture_by_id, match_id, _MATCH_INDEX_TTL_SECONDS)
    if cached is not None:
        if cached.source_provider == "mock" and _active_provider() is not mock_provider:
            return None
        return cached

    try:
        stored = load_stored_match(match_id)
    except Exception:
        logger.exception("No se pudo consultar el partido %s en la base de datos.", match_id)
        stored = None
    if stored is not None:
        _index_matches([stored])
        return stored

    provider = _provider_for_match_id(match_id)
    if match_id.startswith("demo-"):
        if provider is not mock_provider:
            return None
        found = next((match for match in mock_provider.list_highlights() if match.id == match_id), None)
        if found is not None:
            _index_matches([found])
        return found

    try:
        if isinstance(provider, APIFootballProvider) and match_id.startswith("api-football-"):
            found = provider.get_fixture(match_id)
        elif isinstance(provider, SportmonksProvider) and match_id.startswith("sportmonks-"):
            found = provider.get_fixture(match_id)
        elif isinstance(provider, FootballDataProvider) and match_id.startswith("football-data-"):
            found = provider.get_fixture(match_id)
        else:
            return None
    except Exception as exc:
        logger.error("No se pudo resolver el partido %s desde %s: %s", match_id, _provider_name(provider), exc)
        raise MatchProviderUnavailable("El proveedor no pudo resolver el partido a tiempo") from exc

    if found is not None:
        _index_matches([found])
    return found



def _future_value(future: Future | None, default):
    if future is None:
        return default
    try:
        return future.result()
    except Exception as exc:
        logger.warning("Dato complementario no disponible: %s", exc)
        return default


def _future_result(future: Future | None, default) -> tuple[object, bool]:
    """Return a complementary value and preserve whether its request failed."""

    if future is None:
        return default, False
    try:
        return future.result(), False
    except Exception as exc:
        logger.warning("Dato complementario no disponible: %s", exc)
        return default, True


def _history_future_value(future: Future | None) -> tuple[list, bool]:
    """Return history plus an explicit transient-failure flag.

    An empty provider response is valid for teams without prior games. An
    exception is not: callers use the flag to avoid caching that temporary
    outage as an authoritative empty H2H for ten minutes.
    """

    if future is None:
        return [], False
    try:
        value = future.result()
        if not isinstance(value, list):
            raise TypeError("El proveedor no devolvió una lista de historial")
        return value, False
    except Exception as exc:
        logger.warning("Historial complementario no disponible: %s", exc)
        return [], True


def _history_team_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _history_sides(item: dict) -> tuple[tuple[str | None, str], tuple[str | None, str]] | None:
    """Extract home/away provider IDs and names from all supported payloads."""

    teams = item.get("teams")
    if isinstance(teams, dict):
        home = teams.get("home") or {}
        away = teams.get("away") or {}
    elif isinstance(item.get("homeTeam"), dict) and isinstance(item.get("awayTeam"), dict):
        home = item["homeTeam"]
        away = item["awayTeam"]
    else:
        participants = item.get("participants")
        if not isinstance(participants, list):
            return None
        by_location: dict[str, dict] = {}
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            location = str((participant.get("meta") or {}).get("location") or "").casefold()
            if location in {"home", "away"}:
                by_location[location] = participant
        home = by_location.get("home") or {}
        away = by_location.get("away") or {}

    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    if not home_name or not away_name:
        return None
    home_id = str(home["id"]) if home.get("id") is not None else None
    away_id = str(away["id"]) if away.get("id") is not None else None
    return (home_id, home_name), (away_id, away_name)


def _derive_h2h_from_team_histories(
    match: MatchSummary,
    provider: object,
    *histories: list[dict],
    limit: int = 10,
) -> list[H2HMatchItem]:
    """Recover direct meetings already present in either team's recent form."""

    normalize = getattr(provider, "normalize_history", None)
    if not callable(normalize):
        return []

    target_ids = {str(match.home_team_id), str(match.away_team_id)}
    has_target_ids = bool(match.home_team_id and match.away_team_id)
    target_names = {
        _history_team_key(match.home_team),
        _history_team_key(match.away_team),
    }
    recovered: list[H2HMatchItem] = []
    seen: set[tuple[str, str, str, str]] = set()

    for history in histories:
        for raw_item in history:
            if not isinstance(raw_item, dict):
                continue
            sides = _history_sides(raw_item)
            if sides is None:
                continue
            raw_ids = {side[0] for side in sides}
            raw_names = {_history_team_key(side[1]) for side in sides}
            ids_are_authoritative = has_target_ids and None not in raw_ids
            if ids_are_authoritative:
                if raw_ids != target_ids:
                    continue
            elif raw_names != target_names:
                continue
            try:
                normalized = normalize([raw_item], 1)
            except Exception as exc:
                logger.warning("No se pudo normalizar un respaldo H2H: %s", exc)
                continue
            if not normalized:
                continue
            item = normalized[0]
            key = (item.date, item.home_team, item.away_team, item.score)
            if key not in seen:
                seen.add(key)
                recovered.append(item)

    recovered.sort(key=lambda item: item.date, reverse=True)
    return recovered[: max(0, int(limit))]


def _should_fetch_published_lineups(
    match: MatchSummary,
    now: datetime | None = None,
) -> bool:
    """Avoid spending quota before API-Football normally publishes lineups."""

    current = now or datetime.now(timezone.utc)
    kickoff = match.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    time_to_kickoff = kickoff - current
    return timedelta(hours=-4) <= time_to_kickoff <= timedelta(minutes=60)


def _api_coverage_flag(raw: object, *path: str) -> bool | None:
    """Read one API-Football coverage flag without assuming it is present."""

    current = raw
    if isinstance(current, dict) and "coverage" in current:
        current = current.get("coverage")
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, bool) else None


def _apply_verified_market_odds(
    analysis: MatchAnalysisResponse,
    quotes: dict[str, BookmakerQuote],
) -> MatchAnalysisResponse:
    """Overlay only exact bookmaker selections returned by API-Football."""

    analysis.match.odds_available = bool(quotes)
    matched_quote = False
    for market in analysis.markets:
        quote = quotes.get(market.market_key)
        if quote is None or not _selection_matches_market_key(
            market.market_key,
            market.selection,
            analysis.match,
        ):
            continue
        market.best_odds = quote.odds
        market.bookmaker = quote.bookmaker
        market.expected_value = round(market.probability * quote.odds - 1.0, 3)
        matched_quote = True

    # Rebuild dream picks so a verified 3+ price can qualify a simple market.
    return enrich_analysis_with_opportunities(analysis) if matched_quote else analysis


def _selection_matches_market_key(
    market_key: str,
    selection: str,
    match: MatchSummary,
) -> bool:
    """Validate that an interpreted selection matches an exact bookmaker key.

    Quotes are indexed by the API-Football outcome (Over, Under, Home, Away,
    etc.). An AI label must never receive that quote if its human-readable
    selection describes the opposite side of the market.
    """

    normalized = unicodedata.normalize("NFKD", selection).casefold()
    text = "".join(character for character in normalized if not unicodedata.combining(character))
    compact = "".join(character for character in text if character.isalnum())
    home = _history_team_key(match.home_team)
    away = _history_team_key(match.away_team)

    if "_OVER_" in market_key:
        return "masde" in compact or "over" in compact
    if "_UNDER_" in market_key:
        return "menosde" in compact or "under" in compact
    if market_key == "BOTH_TEAMS_TO_SCORE":
        return compact in {"si", "yes", "ambosanotan", "ambosequiposanotan"}
    if market_key == "WINNER_HOME":
        return home in compact and not any(token in text for token in ("empate", "draw", " o "))
    if market_key == "WINNER_AWAY":
        return away in compact and not any(token in text for token in ("empate", "draw", " o "))
    if market_key == "WINNER_DRAW":
        return compact in {"empate", "draw", "x"}
    if market_key == "DOUBLE_CHANCE_HOME_DRAW":
        return home in compact and ("empate" in text or "draw" in text or compact.endswith("1x"))
    if market_key == "DOUBLE_CHANCE_AWAY_DRAW":
        return away in compact and ("empate" in text or "draw" in text or compact.endswith("x2"))
    if market_key == "DOUBLE_CHANCE_HOME_AWAY":
        return home in compact and away in compact
    if "_HOME_" in market_key or market_key.endswith("_HOME"):
        return home in compact or "local" in text
    if "_AWAY_" in market_key or market_key.endswith("_AWAY"):
        return away in compact or "visitante" in text
    return True


def _history_item(
    match_date: date,
    competition: str,
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
) -> H2HMatchItem:
    if home_goals > away_goals:
        winner = home_team
    elif away_goals > home_goals:
        winner = away_team
    else:
        winner = "Empate"
    return H2HMatchItem(
        date=match_date.isoformat(),
        competition=competition,
        home_team=home_team,
        away_team=away_team,
        score=f"{home_goals} - {away_goals}",
        winner=winner,
    )


def _mock_histories(match: MatchSummary) -> tuple[list[H2HMatchItem], list[H2HMatchItem], list[H2HMatchItem]]:
    """Build visibly marked sample histories only while the whole application is in demo mode."""
    anchor = match.kickoff_at.date()
    competition = f"{match.competition} · demo"
    h2h_scores = [(2, 1), (1, 1), (0, 2), (3, 1), (1, 0)]
    h2h: list[H2HMatchItem] = []
    for index, (first_goals, second_goals) in enumerate(h2h_scores):
        first_is_home = index % 2 == 0
        h2h.append(
            _history_item(
                anchor - timedelta(days=75 * (index + 1)),
                competition,
                match.home_team if first_is_home else match.away_team,
                match.away_team if first_is_home else match.home_team,
                first_goals,
                second_goals,
            )
        )

    def team_history(team: str, seed: int) -> list[H2HMatchItem]:
        score_pairs = [(2, 0), (1, 1), (1, 2), (3, 1), (0, 1)]
        result: list[H2HMatchItem] = []
        for index, (team_goals, rival_goals) in enumerate(score_pairs):
            team_is_home = (index + seed) % 2 == 0
            rival = f"Rival demo {index + 1}"
            result.append(
                _history_item(
                    anchor - timedelta(days=7 * (index + 1)),
                    competition,
                    team if team_is_home else rival,
                    rival if team_is_home else team,
                    team_goals if team_is_home else rival_goals,
                    rival_goals if team_is_home else team_goals,
                )
            )
        return result

    return h2h, team_history(match.home_team, 0), team_history(match.away_team, 1)


def _team_discipline_average(
    history: list[dict],
    *,
    team_id: str | None,
    team_name: str,
) -> TeamDisciplineAverage:
    """Average only verified team-level fixture statistics from the provider."""

    normalized_id = str(team_id) if team_id is not None else None
    normalized_name = team_name.strip().casefold()
    samples: dict[str, list[float]] = {
        "fouls": [],
        "yellow_cards": [],
        "red_cards": [],
    }
    fixture_samples = 0
    for fixture in history:
        statistics = fixture.get("statistics") or []
        if not isinstance(statistics, list):
            continue
        selected: dict | None = None
        for block in statistics:
            if not isinstance(block, dict):
                continue
            team = block.get("team") or {}
            block_id = str(team.get("id")) if isinstance(team, dict) and team.get("id") is not None else None
            if block_id is None and block.get("participant_id") is not None:
                block_id = str(block["participant_id"])
            block_name = str(team.get("name") or "").strip().casefold() if isinstance(team, dict) else ""
            if (normalized_id and block_id == normalized_id) or (
                normalized_name and block_name == normalized_name
            ):
                selected = block
                break
        if selected is None:
            continue
        found = False
        for metric in samples:
            value = selected.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                samples[metric].append(float(value))
                found = True
        if found:
            fixture_samples += 1

    def average(metric: str) -> float | None:
        values = samples[metric]
        return round(sum(values) / len(values), 2) if values else None

    return TeamDisciplineAverage(
        team_name=team_name,
        sample_size=fixture_samples,
        fouls_avg=average("fouls"),
        yellow_cards_avg=average("yellow_cards"),
        red_cards_avg=average("red_cards"),
    )


def _discipline_summary(
    match: MatchSummary,
    home_history: list[dict],
    away_history: list[dict],
) -> DisciplineSummary | None:
    home = _team_discipline_average(
        home_history,
        team_id=match.home_team_id,
        team_name=match.home_team,
    )
    away = _team_discipline_average(
        away_history,
        team_id=match.away_team_id,
        team_name=match.away_team,
    )
    if home.sample_size == 0 and away.sample_size == 0:
        return None
    return DisciplineSummary(
        home=home,
        away=away,
        note=(
            "Promedios por equipo calculados únicamente con partidos recientes que "
            "incluyen estadísticas verificadas. No representan el promedio histórico "
            "del árbitro."
        ),
    )


def get_analysis(match_id: str, use_external_ai: bool = True) -> MatchAnalysisResponse | None:
    cache_key = (match_id, use_external_ai)
    cached = _get_cached_analysis(cache_key)
    if cached is not None:
        return cached
    if not use_external_ai:
        enriched_cached = _get_cached_analysis((match_id, True))
        if enriched_cached is not None:
            return enriched_cached

    match = get_match(match_id)
    if match is None:
        return None

    referee_info: RefereeInfo | None = None
    injuries: list[InjuryItem] = []
    lineups = None
    h2h_matches: list[H2HMatchItem] = []
    home_history: list[dict] = []
    away_history: list[dict] = []
    home_recent_matches: list[H2HMatchItem] = []
    away_recent_matches: list[H2HMatchItem] = []
    odds_quotes: dict[str, BookmakerQuote] = {}
    discipline: DisciplineSummary | None = None
    league_coverage: dict | None = None
    standings_payload: dict | None = None
    home_team_statistics: dict | None = None
    away_team_statistics: dict | None = None
    provider_prediction: dict | None = None
    home_player_statistics: dict | None = None
    away_player_statistics: dict | None = None
    top_scorers: list[dict] = []
    top_assists: list[dict] = []
    top_yellow_cards: list[dict] = []
    top_red_cards: list[dict] = []
    injuries_failed = False
    lineups_failed = False
    odds_failed = False
    prediction_failed = False
    lineups_requested = False
    provider_unavailable_reason: str | None = None
    h2h_failed = False
    home_failed = False
    away_failed = False
    history_fetch_failed = False

    provider = _provider_for_match_id(
        match.id,
        source_provider=match.source_provider,
    )
    if isinstance(provider, APIFootballProvider) and match.id.startswith("api-football-"):
        fixture_id = match.external_id or match.id.replace("api-football-", "")
        # `/status` is documented as quota-free. Refresh it only while the
        # provider has no known daily allowance so a depleted account does not
        # trigger a fan-out of doomed requests.
        try:
            if provider.quota_snapshot.get("remaining") is None:
                provider.get_status()
        except Exception as exc:
            logger.info("No se pudo leer el estado de cuota de API-Football: %s", exc)

        remaining = provider.quota_snapshot.get("remaining")
        core_fetch_allowed = not isinstance(remaining, int) or remaining > 0
        if isinstance(remaining, int) and remaining <= 0:
            provider_unavailable_reason = (
                "Cuota diaria de API-Football agotada; el bloque se reintentará tras el reinicio de cuota."
            )
        reserve = settings.api_football_optional_quota_reserve
        optional_allowed = core_fetch_allowed and provider.can_fetch_optional(reserve=reserve)

        if optional_allowed and match.league_id and match.season:
            try:
                league_coverage = provider.get_league_coverage(match.league_id, match.season)
            except Exception as exc:
                logger.warning("Cobertura de liga no disponible: %s", exc)
            optional_allowed = provider.can_fetch_optional(reserve=reserve)

        remaining_after_coverage = provider.quota_snapshot.get("remaining")
        core_request_budget = (
            remaining_after_coverage if isinstance(remaining_after_coverage, int) else 10
        )
        h2h_request_allowed = core_fetch_allowed and core_request_budget >= 1
        history_bundle_allowed = core_fetch_allowed and core_request_budget >= 4
        odds_request_allowed = core_fetch_allowed and core_request_budget >= 5
        injuries_request_allowed = core_fetch_allowed and core_request_budget >= 6
        lineup_request_allowed = core_fetch_allowed and core_request_budget >= 7
        if core_fetch_allowed and core_request_budget < 6:
            provider_unavailable_reason = (
                "Cuota restante insuficiente para consultar todos los bloques sin agotar la reserva."
            )

        quota_limit = provider.quota_snapshot.get("limit")
        mode = settings.api_football_enrichment_mode
        full_enrichment = (
            optional_allowed
            and (
                not isinstance(remaining_after_coverage, int)
                or remaining_after_coverage >= reserve + 10
            )
            and (
                mode == "full"
                or (mode == "auto" and isinstance(quota_limit, int) and quota_limit > 100)
            )
        )
        player_enrichment = full_enrichment and (
            not isinstance(remaining_after_coverage, int)
            or remaining_after_coverage >= reserve + 12
        )
        rankings_enrichment = full_enrichment and (
            not isinstance(remaining_after_coverage, int)
            or remaining_after_coverage >= reserve + 16
        )

        lineups_requested = (
            lineup_request_allowed
            and _should_fetch_published_lineups(match)
            and _api_coverage_flag(league_coverage, "fixtures", "lineups") is not False
        )
        injuries_supported = _api_coverage_flag(league_coverage, "injuries") is not False
        odds_supported = _api_coverage_flag(league_coverage, "odds") is not False
        predictions_supported = _api_coverage_flag(league_coverage, "predictions") is not False
        standings_supported = _api_coverage_flag(league_coverage, "standings") is not False

        with ThreadPoolExecutor(max_workers=10) as executor:
            injuries_future = (
                executor.submit(provider.get_fixture_injuries, fixture_id)
                if injuries_request_allowed and injuries_supported
                else None
            )
            lineups_future = (
                executor.submit(
                    provider.get_fixture_lineups,
                    fixture_id,
                    match.home_team_id,
                    match.away_team_id,
                )
                if lineups_requested
                else None
            )
            odds_future = (
                executor.submit(provider.get_fixture_odds, fixture_id)
                if odds_request_allowed and odds_supported
                else None
            )
            h2h_future = None
            home_future = None
            away_future = None
            if h2h_request_allowed and match.home_team_id and match.away_team_id:
                h2h_future = executor.submit(provider.get_head_to_head, match.home_team_id, match.away_team_id, 10)
            if history_bundle_allowed and match.home_team_id and match.away_team_id:
                home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 10, False)
                away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 10, False)

            standings_future = (
                executor.submit(provider.get_standings, match.league_id, match.season)
                if full_enrichment and standings_supported and match.league_id and match.season
                else None
            )
            home_stats_future = (
                executor.submit(
                    provider.get_team_statistics,
                    match.home_team_id,
                    match.league_id,
                    match.season,
                    through_date=match.kickoff_at.astimezone(SPORTS_TIMEZONE).date(),
                )
                if full_enrichment and match.home_team_id and match.league_id and match.season
                else None
            )
            away_stats_future = (
                executor.submit(
                    provider.get_team_statistics,
                    match.away_team_id,
                    match.league_id,
                    match.season,
                    through_date=match.kickoff_at.astimezone(SPORTS_TIMEZONE).date(),
                )
                if full_enrichment and match.away_team_id and match.league_id and match.season
                else None
            )
            prediction_future = (
                executor.submit(provider.get_prediction, fixture_id)
                if full_enrichment and predictions_supported
                else None
            )
            players_supported = _api_coverage_flag(league_coverage, "players") is not False
            home_players_future = (
                executor.submit(
                    provider.get_player_context,
                    match.home_team_id,
                    match.league_id,
                    match.season,
                )
                if player_enrichment
                and players_supported
                and match.home_team_id
                and match.league_id
                and match.season
                else None
            )
            away_players_future = (
                executor.submit(
                    provider.get_player_context,
                    match.away_team_id,
                    match.league_id,
                    match.season,
                )
                if player_enrichment
                and players_supported
                and match.away_team_id
                and match.league_id
                and match.season
                else None
            )
            top_scorers_future = (
                executor.submit(provider.get_top_scorers, match.league_id, match.season)
                if rankings_enrichment
                and _api_coverage_flag(league_coverage, "top_scorers") is not False
                and match.league_id
                and match.season
                else None
            )
            top_assists_future = (
                executor.submit(provider.get_top_assists, match.league_id, match.season)
                if rankings_enrichment
                and _api_coverage_flag(league_coverage, "top_assists") is not False
                and match.league_id
                and match.season
                else None
            )
            top_yellow_future = (
                executor.submit(provider.get_top_yellow_cards, match.league_id, match.season)
                if rankings_enrichment
                and _api_coverage_flag(league_coverage, "top_cards") is not False
                and match.league_id
                and match.season
                else None
            )
            top_red_future = (
                executor.submit(provider.get_top_red_cards, match.league_id, match.season)
                if rankings_enrichment
                and _api_coverage_flag(league_coverage, "top_cards") is not False
                and match.league_id
                and match.season
                else None
            )

            injuries_result, injuries_failed = _future_result(injuries_future, [])
            injuries = injuries_result if isinstance(injuries_result, list) else []
            lineups, lineups_failed = _future_result(lineups_future, None)
            h2h_matches, h2h_failed = _history_future_value(h2h_future)
            home_history, home_failed = _history_future_value(home_future)
            away_history, away_failed = _history_future_value(away_future)
            history_fetch_failed = (
                not history_bundle_allowed or h2h_failed or home_failed or away_failed
            )
            odds_result, odds_failed = _future_result(odds_future, {})
            odds_quotes = odds_result if isinstance(odds_result, dict) else {}
            standings_result, _ = _future_result(standings_future, None)
            standings_payload = standings_result if isinstance(standings_result, dict) else None
            home_stats_result, _ = _future_result(home_stats_future, None)
            home_team_statistics = home_stats_result if isinstance(home_stats_result, dict) else None
            away_stats_result, _ = _future_result(away_stats_future, None)
            away_team_statistics = away_stats_result if isinstance(away_stats_result, dict) else None
            prediction_result, prediction_failed = _future_result(prediction_future, None)
            provider_prediction = prediction_result if isinstance(prediction_result, dict) else None
            home_players_result, _ = _future_result(home_players_future, None)
            home_player_statistics = home_players_result if isinstance(home_players_result, dict) else None
            away_players_result, _ = _future_result(away_players_future, None)
            away_player_statistics = away_players_result if isinstance(away_players_result, dict) else None
            top_scorers_result, _ = _future_result(top_scorers_future, [])
            top_scorers = top_scorers_result if isinstance(top_scorers_result, list) else []
            top_assists_result, _ = _future_result(top_assists_future, [])
            top_assists = top_assists_result if isinstance(top_assists_result, list) else []
            top_yellow_result, _ = _future_result(top_yellow_future, [])
            top_yellow_cards = top_yellow_result if isinstance(top_yellow_result, list) else []
            top_red_result, _ = _future_result(top_red_future, [])
            top_red_cards = top_red_result if isinstance(top_red_result, list) else []
            home_history, away_history = provider.enrich_fixture_histories(
                home_history,
                away_history,
            )
            home_recent_matches = provider.normalize_history(home_history, 10)
            away_recent_matches = provider.normalize_history(away_history, 10)
            discipline = _discipline_summary(match, home_history, away_history)
            probable_lineups = provider.get_probable_lineups(
                home_history,
                away_history,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_team_name=match.home_team,
                away_team_name=match.away_team,
                injuries=injuries,
            )
            lineups = provider.merge_lineups(lineups, probable_lineups)

    elif isinstance(provider, SportmonksProvider) and match.id.startswith("sportmonks-"):
        with ThreadPoolExecutor(max_workers=3) as executor:
            h2h_future = (
                executor.submit(
                    provider.get_head_to_head,
                    match.home_team_id,
                    match.away_team_id,
                    10,
                )
                if match.home_team_id and match.away_team_id
                else None
            )
            home_future = (
                executor.submit(provider.get_team_last_matches, match.home_team_id, 10)
                if match.home_team_id
                else None
            )
            away_future = (
                executor.submit(provider.get_team_last_matches, match.away_team_id, 10)
                if match.away_team_id
                else None
            )
            h2h_matches, h2h_failed = _history_future_value(h2h_future)
            home_history, home_failed = _history_future_value(home_future)
            away_history, away_failed = _history_future_value(away_future)
            history_fetch_failed = h2h_failed or home_failed or away_failed
            home_recent_matches = provider.normalize_history(home_history, 10)
            away_recent_matches = provider.normalize_history(away_history, 10)
            discipline = _discipline_summary(match, home_history, away_history)
    elif isinstance(provider, FootballDataProvider) and match.id.startswith("football-data-"):
        with ThreadPoolExecutor(max_workers=3) as executor:
            h2h_future = executor.submit(provider.get_head_to_head, match.id, 10)
            home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 10) if match.home_team_id else None
            away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 10) if match.away_team_id else None
            h2h_matches, h2h_failed = _history_future_value(h2h_future)
            home_history, home_failed = _history_future_value(home_future)
            away_history, away_failed = _history_future_value(away_future)
            history_fetch_failed = h2h_failed or home_failed or away_failed
            home_recent_matches = provider.normalize_history(home_history, 10)
            away_recent_matches = provider.normalize_history(away_history, 10)

    if not h2h_matches and (home_history or away_history):
        recovered_h2h = _derive_h2h_from_team_histories(
            match,
            provider,
            home_history,
            away_history,
            limit=10,
        )
        if recovered_h2h:
            h2h_matches = recovered_h2h
            # A successful reconstruction fully replaces only the failed H2H
            # call. Missing home/away form should still trigger a later retry.
            history_fetch_failed = home_failed or away_failed
    if match.source_provider == "mock":
        mock_h2h, mock_home_history, mock_away_history = _mock_histories(match)
        if not h2h_matches:
            h2h_matches = mock_h2h
        if not home_recent_matches:
            home_recent_matches = mock_home_history
        if not away_recent_matches:
            away_recent_matches = mock_away_history

    if match.source_provider == "mock" and not injuries and "arsenal" in match.home_team.lower():
        injuries = [
            InjuryItem(player="Bukayo Saka", team=match.home_team, reason="Molestia muscular en isquiotibiales", status="Duda"),
            InjuryItem(player="Reece James", team=match.away_team, reason="Sanción por acumulación de tarjetas", status="Sancionado"),
        ]

    if match.referee and match.source_provider == "mock":
        ref_hash = sum(ord(c) for c in match.referee)
        y_avg = round(3.4 + (ref_hash % 25) / 10.0, 1)  # Variación entre 3.4 y 5.8
        r_avg = round(0.12 + (ref_hash % 7) / 50.0, 2)
        f_avg = round(21.0 + (ref_hash % 9), 1)
        referee_info = RefereeInfo(
            name=match.referee,
            yellow_cards_avg=y_avg,
            red_cards_avg=r_avg,
            fouls_per_game=f_avg,
            tendency="Mantiene control riguroso en mediocampo" if y_avg > 4.5 else "Permite fluidez en transiciones",
        )
    elif match.referee:
        referee_info = RefereeInfo(
            name=match.referee,
            tendency="Sin métricas arbitrales verificadas",
        )

    evidence = None
    if isinstance(provider, APIFootballProvider) and match.id.startswith("api-football-"):
        evidence = build_match_evidence(
            match=match,
            provider_name=provider.provider_name,
            home_history=home_history,
            away_history=away_history,
            h2h=h2h_matches,
            injuries=injuries,
            lineups=lineups,
            odds_quotes=odds_quotes,
            league_coverage=league_coverage,
            standings=standings_payload,
            home_team_statistics=home_team_statistics,
            away_team_statistics=away_team_statistics,
            provider_prediction=provider_prediction,
            home_player_statistics=home_player_statistics,
            away_player_statistics=away_player_statistics,
            top_scorers=top_scorers,
            top_assists=top_assists,
            top_yellow_cards=top_yellow_cards,
            top_red_cards=top_red_cards,
            h2h_failed=h2h_failed,
            home_history_failed=home_failed,
            away_history_failed=away_failed,
            injuries_failed=injuries_failed,
            lineups_requested=lineups_requested,
            lineups_failed=lineups_failed,
            odds_failed=odds_failed,
            prediction_failed=prediction_failed,
            standings_requested=bool(
                full_enrichment and standings_supported and match.league_id and match.season
            ),
            prediction_requested=bool(full_enrichment and predictions_supported),
            provider_unavailable_reason=provider_unavailable_reason,
        )

    analysis = analyze_match_with_ai(
        match=match,
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        home_last_matches=home_history,
        away_last_matches=away_history,
        evidence=evidence,
        # Sports-provider latency no longer disables the requested four-model
        # interpretation. The frontend has a dedicated analysis deadline and
        # the AI calls share one bounded parallel window.
        allow_external_ai=use_external_ai,
    )
    analysis.discipline = discipline
    # Discipline is assembled from enriched recent fixtures, after the base
    # model has run. Rebuild opportunities now so card averages can participate
    # in evidence-led combinations; the odds overlay then attaches only exact
    # verified prices and performs one final rebuild if needed.
    analysis = enrich_analysis_with_opportunities(analysis)
    analysis = _apply_verified_market_odds(analysis, odds_quotes)
    analysis.home_recent_matches = home_recent_matches[:10]
    analysis.away_recent_matches = away_recent_matches[:10]
    if not history_fetch_failed:
        _cache_set(_analysis_cache, cache_key, analysis)
    else:
        logger.info(
            "No se cachea el análisis de %s porque falló al menos una consulta de historial.",
            match.id,
        )
    return analysis


def _quick_analysis(match: MatchSummary) -> MatchAnalysisResponse:
    return analyze_match_with_ai(match=match, allow_external_ai=False)


def _recommendation_analysis(match: MatchSummary) -> MatchAnalysisResponse:
    """Prefer already enriched match evidence without multiplying API calls.

    Opening a match populates H2H, form and verified odds in the analysis cache.
    Daily feeds reuse that exact interpretation. Fixtures not opened yet still
    receive the deterministic per-match model, but this helper deliberately
    does not fan out several upstream requests for every item in the agenda.
    """

    for cache_key in ((match.id, True), (match.id, False)):
        cached = _get_cached_analysis(cache_key)
        if cached is not None:
            return cached

    if match.source_provider == "mock":
        h2h, home_history, away_history = _mock_histories(match)
        return analyze_match_with_ai(
            match=match,
            h2h_matches=h2h,
            home_last_matches=home_history,
            away_last_matches=away_history,
            allow_external_ai=False,
        )

    # Recommendations and Soñadoras are often requested in parallel by the
    # home page. Serialize only the same fixture so they share the first
    # completed local analysis instead of doubling provider quota.
    with _recommendation_analysis_lock(match.id):
        for cache_key in ((match.id, True), (match.id, False)):
            cached = _get_cached_analysis(cache_key)
            if cached is not None:
                return cached
        try:
            enriched = get_analysis(match.id, use_external_ai=False)
        except Exception as exc:
            logger.warning(
                "No se pudo enriquecer la recomendación de %s: %s",
                match.id,
                exc,
            )
            enriched = None
        return enriched or _quick_analysis(match)


def _recommendation_analyses(
    matches: list[MatchSummary],
) -> list[MatchAnalysisResponse]:
    if not matches:
        return []
    if len(matches) == 1:
        return [_recommendation_analysis(matches[0])]
    with ThreadPoolExecutor(max_workers=min(4, len(matches))) as executor:
        return list(executor.map(_recommendation_analysis, matches))


def _market_recommendation_score(market, use_count: int = 0) -> float:
    """Rank safety/evidence first and add only verified positive value."""

    verified_value = max(0.0, market.expected_value or 0.0) if market.best_odds else 0.0
    diversity_penalty = min(0.18, use_count * 0.055)
    return market.probability * market.data_quality + verified_value * 0.08 - diversity_penalty


def _recommendation_from_market(
    analysis_data: MatchAnalysisResponse,
    market,
) -> Recommendation:
    match = analysis_data.match
    return Recommendation(
        id=f"rec-{match.id}-{market.market_key.lower()}",
        match_id=match.id,
        match_label=f"{match.home_team} - {match.away_team}",
        market=market.label,
        selection=market.selection,
        probability=market.probability,
        fair_odds=market.fair_odds,
        best_odds=market.best_odds,
        expected_value=market.expected_value,
        kind="simple",
        rationale=(
            market.factors_for[0]
            if market.factors_for
            else "Selección priorizada por probabilidad y calidad de datos de este partido"
        ),
        confidence=market.confidence,
        data_quality=market.data_quality,
        home_logo=match.home_logo,
        away_logo=match.away_logo,
    )


def get_assistant_analysis_context(match_id: str | None) -> MatchAnalysisResponse | None:
    """Return assistant context without resolving any external sports data.

    A previously computed analysis is preferred. If none is cached, an already
    indexed fixture can still produce the deterministic local analysis. This
    helper deliberately never calls ``get_match`` or an upstream provider.
    """

    if not match_id:
        return None
    for cache_key in ((match_id, True), (match_id, False)):
        cached = _get_cached_analysis(cache_key)
        if cached is not None:
            return cached
    indexed_match = _cache_get(_fixture_by_id, match_id, _MATCH_INDEX_TTL_SECONDS)
    if indexed_match is None:
        return None
    return _quick_analysis(indexed_match)


def get_recommendations(limit: int | None = None) -> list[Recommendation]:
    """Return exactly one match-specific safe signal per fixture."""

    matches = get_highlights()
    candidate_count = min(len(matches), max(1, limit or len(matches)))
    analyses = _recommendation_analyses(matches[:candidate_count])
    result: list[Recommendation] = []
    family_use_count: dict[str, int] = {}

    for analysis_data in analyses:
        if not analysis_data.markets:
            continue
        market = max(
            analysis_data.markets,
            key=lambda item: (
                _market_recommendation_score(
                    item,
                    family_use_count.get(_recommendation_market_signature(item), 0),
                ),
                item.probability,
            ),
        )
        result.append(_recommendation_from_market(analysis_data, market))
        signature = _recommendation_market_signature(market)
        family_use_count[signature] = family_use_count.get(signature, 0) + 1
    return result


def _recommendation_market_signature(market) -> str:
    key = market.market_key.strip().upper()
    family = key.split("_OVER_", 1)[0].split("_UNDER_", 1)[0]
    if key == "BOTH_TEAMS_TO_SCORE":
        side = _history_team_key(market.selection)
    elif "_OVER_" in key:
        side = "over"
    elif "_UNDER_" in key:
        side = "under"
    else:
        side = key
    return f"{family}:{side}"


def get_dream_recommendations(limit: int = 6) -> list[Recommendation]:
    """Return several evidence-led dreams using fair cross-match rotation."""

    matches = get_highlights()
    analyses = _recommendation_analyses(matches[: min(len(matches), max(1, limit))])
    result: list[Recommendation] = []
    remaining: dict[str, list] = {
        analysis.match.id: list(analysis.dream_picks) for analysis in analyses
    }
    used_signatures: set[tuple[str, ...]] = set()

    # Round-robin permits more than one pick per fixture without allowing the
    # first match to consume the feed. Within each turn prefer a leg signature
    # not already used globally. If the evidence contains no new structure we
    # return fewer cards instead of filling the page with a repeated template.
    while len(result) < limit and any(remaining.values()):
        added = False
        for analysis_data in analyses:
            candidates = [
                item
                for item in remaining[analysis_data.match.id]
                if tuple(sorted(leg.market_key for leg in item.legs)) not in used_signatures
            ]
            if not candidates:
                continue
            dream = max(
                candidates,
                key=lambda item: (
                    item.data_quality,
                    -abs(item.probability - 0.315),
                ),
            )
            remaining[analysis_data.match.id].remove(dream)
            match = analysis_data.match
            result.append(
                Recommendation(
                    id=dream.id,
                    match_id=match.id,
                    match_label=f"{match.home_team} - {match.away_team}",
                    market=dream.label,
                    selection=dream.selection,
                    probability=dream.probability,
                    fair_odds=dream.fair_odds,
                    best_odds=dream.best_odds,
                    expected_value=dream.expected_value,
                    kind=dream.kind,
                    rationale=dream.factors_for[0],
                    legs=dream.legs,
                    confidence=dream.confidence,
                    data_quality=dream.data_quality,
                    risk_note=dream.correlation_note,
                    home_logo=match.home_logo,
                    away_logo=match.away_logo,
                )
            )
            signature = tuple(sorted(leg.market_key for leg in dream.legs))
            used_signatures.add(signature)
            added = True
            if len(result) >= limit:
                return result
        if not added:
            break
    return result

