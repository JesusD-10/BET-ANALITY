from datetime import date, datetime, timedelta, timezone
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import logging
from threading import Lock
import time
from zoneinfo import ZoneInfo
import httpx

from app.core.config import settings
from app.schemas.matches import (
    H2HMatchItem,
    InjuryItem,
    MatchAnalysisResponse,
    MatchSummary,
    Recommendation,
    RefereeInfo,
)
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.api_football import APIFootballAPIError, APIFootballProvider, BookmakerQuote
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
        clean_id = match_id.replace("football-data-", "")
        endpoint = f"{self.base_url}/matches/{clean_id}/head2head"
        try:
            bounded_limit = max(1, min(limit, 10))
            res = httpx.get(endpoint, params={"limit": bounded_limit}, headers=self._headers(), timeout=self.timeout)
            res.raise_for_status()
            return self.normalize_history(res.json().get("matches", []), bounded_limit)
        except Exception as exc:
            logger.warning("Fallo al obtener H2H en football-data: %s", exc)
            return []

    def get_team_last_matches(self, team_id: str, limit: int = 5) -> list[dict]:
        endpoint = f"{self.base_url}/teams/{team_id}/matches"
        try:
            bounded_limit = max(1, min(limit, 5))
            res = httpx.get(
                endpoint,
                params={"status": "FINISHED", "limit": bounded_limit},
                headers=self._headers(),
                timeout=self.timeout,
            )
            res.raise_for_status()
            raw_items = res.json().get("matches", [])
            return sorted(raw_items, key=lambda item: str(item.get("utcDate") or ""), reverse=True)[:bounded_limit]
        except Exception as exc:
            logger.warning("Fallo al obtener partidos del equipo %s en football-data: %s", team_id, exc)
            return []

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
        home = item.get("homeTeam") or {}
        away = item.get("awayTeam") or {}
        referees = item.get("referees") or []
        referee_name = referees[0].get("name") if referees and isinstance(referees, list) else None

        kickoff = datetime.fromisoformat(item["utcDate"].replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return MatchSummary(
            id=f"football-data-{item['id']}",
            external_id=str(item["id"]),
            competition=competition.get("name") or "Competición sin nombre",
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
            status=self._normalize_status(item.get("status", "")),
            source_provider=self.provider_name,
            source_url=endpoint,
        )

    @staticmethod
    def _is_relevant_match(item: dict) -> bool:
        status = (item.get("status") or "").upper()
        return status not in {"FINISHED", "POSTPONED", "SUSPENDED", "CANCELLED"}

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
    cached = _cache_get(_fixture_cache, route_cache_key, _FIXTURE_CACHE_TTL_SECONDS)
    if cached is not None:
        _index_matches(cached.matches)
        return cached

    if not providers:
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
        provider_cached = _cache_get(
            _fixture_cache,
            provider_cache_key,
            _FIXTURE_CACHE_TTL_SECONDS,
        )
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
    cached = _cache_get(_fixture_cache, route_cache_key, _FIXTURE_CACHE_TTL_SECONDS)
    if cached is not None:
        _index_matches(cached.matches)
        return cached
    with _fixture_route_lock(route_cache_key):
        # The first request populates the route envelope. Waiting page sections
        # reuse it, including its real source and failover notice.
        return _get_highlights_result_once(selected_date)


def get_highlights(match_date: date | None = None) -> list[MatchSummary]:
    return get_highlights_result(match_date).matches


def search_matches_result(query: str | None = None) -> FixtureResult:
    result = get_highlights_result()
    if not query:
        return result
    needle = query.casefold().strip()
    matches = [
        match
        for match in result.matches
        if needle in f"{match.home_team} {match.away_team} {match.competition}".casefold()
    ]
    return FixtureResult(date=result.date, matches=matches, source=result.source, notice=result.notice)


def search_matches(query: str | None = None) -> list[MatchSummary]:
    return search_matches_result(query).matches



def get_match(match_id: str) -> MatchSummary | None:
    cached = _cache_get(_fixture_by_id, match_id, _MATCH_INDEX_TTL_SECONDS)
    if cached is not None:
        if cached.source_provider == "mock" and _active_provider() is not mock_provider:
            return None
        return cached

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


def _should_fetch_published_lineups(
    match: MatchSummary,
    now: datetime | None = None,
) -> bool:
    """Avoid spending quota before API-Football normally publishes lineups."""

    current = now or datetime.now(timezone.utc)
    kickoff = match.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff - current <= timedelta(minutes=60)


def _apply_verified_market_odds(
    analysis: MatchAnalysisResponse,
    quotes: dict[str, BookmakerQuote],
) -> MatchAnalysisResponse:
    """Overlay only exact bookmaker selections returned by API-Football."""

    analysis.match.odds_available = bool(quotes)
    matched_quote = False
    for market in analysis.markets:
        quote = quotes.get(market.market_key)
        if quote is None:
            continue
        if market.market_key == "BOTH_TEAMS_TO_SCORE" and market.selection.casefold() not in {
            "sí",
            "si",
            "yes",
        }:
            continue
        market.best_odds = quote.odds
        market.bookmaker = quote.bookmaker
        market.expected_value = round(market.probability * quote.odds - 1.0, 3)
        matched_quote = True

    # Rebuild dream picks so a verified 3+ price can qualify a simple market.
    return enrich_analysis_with_opportunities(analysis) if matched_quote else analysis


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

    provider = _provider_for_match_id(
        match.id,
        source_provider=match.source_provider,
    )
    if isinstance(provider, APIFootballProvider) and match.id.startswith("api-football-"):
        fixture_id = match.external_id or match.id.replace("api-football-", "")
        with ThreadPoolExecutor(max_workers=6) as executor:
            injuries_future = executor.submit(provider.get_fixture_injuries, fixture_id)
            lineups_future = (
                executor.submit(
                    provider.get_fixture_lineups,
                    fixture_id,
                    match.home_team_id,
                    match.away_team_id,
                )
                if _should_fetch_published_lineups(match)
                else None
            )
            odds_future = executor.submit(provider.get_fixture_odds, fixture_id)
            h2h_future = None
            home_future = None
            away_future = None
            if match.home_team_id and match.away_team_id:
                h2h_future = executor.submit(provider.get_head_to_head, match.home_team_id, match.away_team_id, 5)
                home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 5, False)
                away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 5, False)

            injuries = _future_value(injuries_future, [])
            lineups = _future_value(lineups_future, None)
            h2h_matches = _future_value(h2h_future, [])
            home_history = _future_value(home_future, [])
            away_history = _future_value(away_future, [])
            odds_quotes = _future_value(odds_future, {})
            home_history, away_history = provider.enrich_fixture_histories(
                home_history,
                away_history,
            )
            home_recent_matches = provider.normalize_history(home_history, 5)
            away_recent_matches = provider.normalize_history(away_history, 5)
            probable_lineups = provider.get_probable_lineups(
                home_history,
                away_history,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_team_name=match.home_team,
                away_team_name=match.away_team,
            )
            lineups = provider.merge_lineups(lineups, probable_lineups)

    elif isinstance(provider, SportmonksProvider) and match.id.startswith("sportmonks-"):
        with ThreadPoolExecutor(max_workers=3) as executor:
            h2h_future = (
                executor.submit(
                    provider.get_head_to_head,
                    match.home_team_id,
                    match.away_team_id,
                    5,
                )
                if match.home_team_id and match.away_team_id
                else None
            )
            home_future = (
                executor.submit(provider.get_team_last_matches, match.home_team_id, 5)
                if match.home_team_id
                else None
            )
            away_future = (
                executor.submit(provider.get_team_last_matches, match.away_team_id, 5)
                if match.away_team_id
                else None
            )
            h2h_matches = _future_value(h2h_future, [])
            home_history = _future_value(home_future, [])
            away_history = _future_value(away_future, [])
            home_recent_matches = provider.normalize_history(home_history, 5)
            away_recent_matches = provider.normalize_history(away_history, 5)
    elif isinstance(provider, FootballDataProvider) and match.id.startswith("football-data-"):
        with ThreadPoolExecutor(max_workers=3) as executor:
            h2h_future = executor.submit(provider.get_head_to_head, match.id, 10)
            home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 5) if match.home_team_id else None
            away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 5) if match.away_team_id else None
            h2h_matches = _future_value(h2h_future, [])
            home_history = _future_value(home_future, [])
            away_history = _future_value(away_future, [])
            home_recent_matches = provider.normalize_history(home_history, 5)
            away_recent_matches = provider.normalize_history(away_history, 5)
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

    analysis = analyze_match_with_ai(
        match=match,
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        home_last_matches=home_history,
        away_last_matches=away_history,
        # Sports-provider latency no longer disables the requested four-model
        # interpretation. The frontend has a dedicated analysis deadline and
        # the AI calls share one bounded parallel window.
        allow_external_ai=use_external_ai,
    )
    analysis = _apply_verified_market_odds(analysis, odds_quotes)
    analysis.home_recent_matches = home_recent_matches[:5]
    analysis.away_recent_matches = away_recent_matches[:5]
    _cache_set(_analysis_cache, cache_key, analysis)
    return analysis


def _quick_analysis(match: MatchSummary) -> MatchAnalysisResponse:
    return analyze_match_with_ai(match=match, allow_external_ai=False)


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
    """Build the daily simple list without N external AI detail calls."""
    result: list[Recommendation] = []
    for match in get_highlights():
        analysis_data = _quick_analysis(match)
        for index, market in enumerate(analysis_data.markets[:2]):
            result.append(
                Recommendation(
                    id=f"rec-{analysis_data.match.id}-{index}",
                    match_id=analysis_data.match.id,
                    match_label=f"{analysis_data.match.home_team} - {analysis_data.match.away_team}",
                    market=market.label,
                    selection=market.selection,
                    probability=market.probability,
                    fair_odds=market.fair_odds,
                    best_odds=market.best_odds,
                    expected_value=market.expected_value,
                    kind="simple",
                    rationale=market.factors_for[0] if market.factors_for else "Respaldo estadístico de forma",
                    confidence=market.confidence,
                    data_quality=market.data_quality,
                    home_logo=match.home_logo,
                    away_logo=match.away_logo,
                )
            )
    result.sort(key=lambda item: (item.probability * item.data_quality), reverse=True)
    return result[:limit] if limit is not None else result


def get_dream_recommendations(limit: int = 6) -> list[Recommendation]:
    """Return diversified same-match dream builders for today's fixtures."""
    analyses = [_quick_analysis(match) for match in get_highlights()]
    result: list[Recommendation] = []

    # Round-robin keeps the home page from being dominated by one match.
    for pick_index in range(2):
        for analysis_data in analyses:
            if pick_index >= len(analysis_data.dream_picks):
                continue
            dream = analysis_data.dream_picks[pick_index]
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
            if len(result) >= limit:
                return result
    return result

