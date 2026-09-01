from datetime import date, datetime, timezone
from copy import deepcopy
from dataclasses import dataclass
import logging
import math
import re
import time
from zoneinfo import ZoneInfo
import httpx

from app.schemas.matches import (
    H2HMatchItem,
    InjuryItem,
    LineupsSummary,
    MatchSummary,
    PlayerLineup,
    RefereeInfo,
    TeamLineup,
)

logger = logging.getLogger(__name__)
SPORTS_TIMEZONE = ZoneInfo("America/Lima")


class APIFootballAPIError(RuntimeError):
    """Error seguro del proveedor, serializable sin filtrar datos sensibles."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
        cooldown_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.cooldown_seconds = max(0, cooldown_seconds)

    def as_envelope(self) -> dict[str, object]:
        """Return a public error envelope; the upstream message is never included."""

        return {
            "provider": APIFootballProvider.provider_name,
            "endpoint": self.endpoint,
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "status_code": self.status_code,
            "cooldown_seconds": self.cooldown_seconds,
        }


@dataclass(frozen=True)
class BookmakerQuote:
    market_key: str
    odds: float
    bookmaker: str
    updated_at: str | None = None


class APIFootballProvider:
    """Adaptador profesional para API-Football (v3.football.api-sports.io o RapidAPI)."""

    provider_name = "api-football"
    _ODDS_CACHE_TTL_SECONDS = 3 * 60 * 60
    _ODDS_EMPTY_CACHE_TTL_SECONDS = 15 * 60
    _LIVE_FIXTURE_CACHE_TTL_SECONDS = 15
    _LIVE_STATUS_SHORTS = {"1H", "HT", "2H", "BT", "ET", "P", "LIVE"}

    # Static catalogues are intentionally long-lived. Match-dependent data is
    # short-lived so it can be refreshed without repeatedly spending quota.
    _CACHE_TTLS: dict[str, int] = {
        "status": 60,
        "timezone": 7 * 24 * 60 * 60,
        "countries": 7 * 24 * 60 * 60,
        "leagues": 6 * 60 * 60,
        "venues": 24 * 60 * 60,
        "fixtures/rounds": 6 * 60 * 60,
        "standings": 30 * 60,
        "teams": 24 * 60 * 60,
        "teams/statistics": 6 * 60 * 60,
        "fixtures": 10 * 60,
        "fixtures/headtohead": 6 * 60 * 60,
        "fixtures/statistics": 60 * 60,
        "fixtures/events": 60 * 60,
        "fixtures/lineups": 10 * 60,
        "fixtures/players": 60 * 60,
        "injuries": 10 * 60,
        "predictions": 6 * 60 * 60,
        "players": 6 * 60 * 60,
        "players/squads": 6 * 60 * 60,
        "players/topscorers": 6 * 60 * 60,
        "players/topassists": 6 * 60 * 60,
        "players/topyellowcards": 6 * 60 * 60,
        "players/topredcards": 6 * 60 * 60,
        "transfers": 6 * 60 * 60,
        "trophies": 24 * 60 * 60,
        "sidelined": 6 * 60 * 60,
        "coachs": 24 * 60 * 60,
        "odds/mapping": 24 * 60 * 60,
        "odds/bookmakers": 24 * 60 * 60,
        "odds/bets": 24 * 60 * 60,
        "odds/live/bets": 24 * 60 * 60,
    }

    def __init__(self, key: str, base_url: str = "https://v3.football.api-sports.io", is_rapidapi: bool = False, timeout: int = 3) -> None:
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.is_rapidapi = is_rapidapi
        self.timeout = timeout
        self._odds_cache: dict[str, tuple[float, dict[str, BookmakerQuote]]] = {}
        self._response_cache: dict[str, tuple[float, dict]] = {}
        self._error_cache: dict[str, tuple[float, APIFootballAPIError]] = {}
        self._provider_cooldown_until = 0.0
        self._quota_remaining: int | None = None
        self._quota_limit: int | None = None
        self._quota_captured_at: str | None = None

    @staticmethod
    def _request_key(endpoint: str, params: dict | None = None) -> str:
        normalized = tuple(
            sorted(
                (str(key), str(value))
                for key, value in (params or {}).items()
                if value is not None
            )
        )
        return f"{endpoint.lstrip('/')}|{normalized!r}"

    @staticmethod
    def _header_int(headers: object, name: str) -> int | None:
        try:
            value = headers.get(name)  # type: ignore[union-attr]
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return None
            return int(value)
        except (AttributeError, TypeError, ValueError):
            return None

    def _capture_quota(self, headers: object) -> None:
        remaining = self._header_int(headers, "x-ratelimit-requests-remaining")
        limit = self._header_int(headers, "x-ratelimit-requests-limit")
        if remaining is not None:
            self._quota_remaining = max(0, remaining)
        if limit is not None:
            self._quota_limit = max(0, limit)
        if remaining is not None or limit is not None:
            self._quota_captured_at = datetime.now(timezone.utc).isoformat()

    @property
    def quota_snapshot(self) -> dict[str, object]:
        cooldown_remaining = max(0, math.ceil(self._provider_cooldown_until - time.monotonic()))
        return {
            "remaining": self._quota_remaining,
            "limit": self._quota_limit,
            "captured_at": self._quota_captured_at,
            "cooldown_seconds": cooldown_remaining,
        }

    def can_fetch_optional(self, reserve: int = 10) -> bool:
        """Tell the orchestrator whether non-essential enrichment is affordable."""

        if time.monotonic() < self._provider_cooldown_until:
            return False
        return self._quota_remaining is None or self._quota_remaining > max(0, reserve)

    def _remember_error(self, key: str, error: APIFootballAPIError) -> None:
        if error.cooldown_seconds <= 0:
            return
        expires_at = time.monotonic() + error.cooldown_seconds
        self._error_cache[key] = (expires_at, error)
        if error.code in {"rate_limited", "quota_exhausted", "authentication_error"}:
            self._provider_cooldown_until = max(self._provider_cooldown_until, expires_at)

    def _cached_request(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        ttl: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Cache one provider envelope and return defensive copies to callers."""

        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        key = self._request_key(endpoint, clean_params)
        cached = self._response_cache.get(key)
        effective_ttl = self._CACHE_TTLS.get(endpoint.lstrip("/"), 5 * 60) if ttl is None else max(0, ttl)
        if (
            cached is not None
            and endpoint.lstrip("/") == "fixtures"
            and self._fixture_envelope_is_live(cached[1])
        ):
            effective_ttl = min(effective_ttl, self._LIVE_FIXTURE_CACHE_TTL_SECONDS)
        if cached is not None and time.monotonic() - cached[0] <= effective_ttl:
            return deepcopy(cached[1])

        try:
            payload = (
                self._request(endpoint, clean_params)
                if timeout is None
                else self._request(endpoint, clean_params, timeout=timeout)
            )
        except APIFootballAPIError as exc:
            self._remember_error(key, exc)
            raise
        self._response_cache[key] = (time.monotonic(), deepcopy(payload))
        return deepcopy(payload)

    @classmethod
    def _fixture_envelope_is_live(cls, payload: dict) -> bool:
        response = payload.get("response")
        if not isinstance(response, list):
            return False
        for item in response:
            if not isinstance(item, dict):
                continue
            fixture = item.get("fixture") if isinstance(item.get("fixture"), dict) else {}
            status = fixture.get("status") if isinstance(fixture.get("status"), dict) else {}
            if str(status.get("short") or "").upper() in cls._LIVE_STATUS_SHORTS:
                return True
        return False

    def _get_headers(self) -> dict[str, str]:
        if self.is_rapidapi:
            return {
                "x-rapidapi-key": self.key,
                "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
            }
        return {
            "x-apisports-key": self.key,
        }

    def _request(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        clean_endpoint = endpoint.lstrip("/")
        request_key = self._request_key(clean_endpoint, params)
        now = time.monotonic()
        cached_error = self._error_cache.get(request_key)
        if cached_error is not None:
            if now < cached_error[0]:
                raise cached_error[1]
            self._error_cache.pop(request_key, None)

        if clean_endpoint != "status" and now < self._provider_cooldown_until:
            remaining = max(1, math.ceil(self._provider_cooldown_until - now))
            raise APIFootballAPIError(
                "API-Football está temporalmente en espera para proteger la cuota.",
                endpoint=clean_endpoint,
                code="provider_cooldown",
                retryable=True,
                cooldown_seconds=remaining,
            )

        url = f"{self.base_url}/{clean_endpoint}"
        try:
            response = httpx.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout if timeout is None else max(0.1, timeout),
            )
            response.raise_for_status()
            self._capture_quota(getattr(response, "headers", {}))
            if response.status_code == 204:
                return {"errors": [], "results": 0, "response": []}
            data = response.json()
        except httpx.HTTPStatusError as err:
            status_code = err.response.status_code
            self._capture_quota(err.response.headers)
            retry_after = self._header_int(err.response.headers, "retry-after")
            if status_code == 429:
                code, retryable, cooldown = "rate_limited", True, retry_after or 60
            elif status_code in {401, 403}:
                code, retryable, cooldown = "authentication_error", False, 15 * 60
            elif status_code >= 500:
                code, retryable, cooldown = "upstream_unavailable", True, 15
            else:
                code, retryable, cooldown = "http_error", False, 0
            safe_error = APIFootballAPIError(
                "No se pudo completar la consulta a API-Football.",
                endpoint=clean_endpoint,
                code=code,
                retryable=retryable,
                status_code=status_code,
                cooldown_seconds=cooldown,
            )
            self._remember_error(request_key, safe_error)
            logger.warning("API-Football respondió HTTP %s en %s.", status_code, clean_endpoint)
            raise safe_error from err
        except httpx.TimeoutException as err:
            safe_error = APIFootballAPIError(
                "API-Football no respondió dentro del tiempo esperado.",
                endpoint=clean_endpoint,
                code="timeout",
                retryable=True,
                cooldown_seconds=5,
            )
            self._remember_error(request_key, safe_error)
            raise safe_error from err
        except httpx.HTTPError as err:
            safe_error = APIFootballAPIError(
                "No se pudo conectar con API-Football.",
                endpoint=clean_endpoint,
                code="network_error",
                retryable=True,
                cooldown_seconds=10,
            )
            self._remember_error(request_key, safe_error)
            raise safe_error from err
        except (TypeError, ValueError) as err:
            raise APIFootballAPIError(
                "API-Football devolvió una respuesta que no se pudo interpretar.",
                endpoint=clean_endpoint,
                code="invalid_json",
            ) from err

        if not isinstance(data, dict):
            raise APIFootballAPIError(
                "API-Football devolvió una respuesta con formato inesperado.",
                endpoint=clean_endpoint,
                code="invalid_envelope",
            )

        errors = data.get("errors")
        if errors:
            error_count = len(errors) if isinstance(errors, (dict, list, tuple, set)) else 1
            error_text = str(errors).casefold()
            quota_error = any(term in error_text for term in ("rate limit", "request limit", "quota"))
            auth_error = any(term in error_text for term in ("token", "api key", "apikey", "subscription"))
            code = "quota_exhausted" if quota_error else "authentication_error" if auth_error else "provider_rejected"
            cooldown = 5 * 60 if quota_error else 15 * 60 if auth_error else 0
            logger.warning(
                "API-Football rechazó la solicitud al endpoint %s con %s error(es).",
                clean_endpoint,
                error_count,
            )
            safe_error = APIFootballAPIError(
                f"API-Football rechazó la solicitud con {error_count} error(es).",
                endpoint=clean_endpoint,
                code=code,
                retryable=quota_error,
                cooldown_seconds=cooldown,
            )
            self._remember_error(request_key, safe_error)
            raise safe_error

        return data

    @staticmethod
    def _response_items(data: dict, endpoint: str) -> list[dict]:
        response = data.get("response")
        if not isinstance(response, list):
            raise APIFootballAPIError(
                "API-Football devolvió una lista de datos con formato inesperado.",
                endpoint=endpoint,
                code="invalid_envelope",
            )
        return [deepcopy(item) for item in response if isinstance(item, dict)]

    @staticmethod
    def _response_object(data: dict, endpoint: str) -> dict:
        response = data.get("response")
        if not isinstance(response, dict):
            raise APIFootballAPIError(
                "API-Football devolvió un objeto de datos con formato inesperado.",
                endpoint=endpoint,
                code="invalid_envelope",
            )
        return deepcopy(response)

    @staticmethod
    def _score_int(value: object) -> int | None:
        """Return a non-negative scoreboard value without trusting provider types."""

        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _to_match_summary(self, item: dict) -> MatchSummary:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        goals = item.get("goals") if isinstance(item.get("goals"), dict) else {}
        score = item.get("score") if isinstance(item.get("score"), dict) else {}
        halftime = score.get("halftime") if isinstance(score.get("halftime"), dict) else {}

        raw_fixture_id = fixture.get("id")
        if raw_fixture_id is None:
            raise ValueError("La respuesta de API-Football no contiene fixture.id.")
        fixture_id = str(raw_fixture_id)

        kickoff_raw = fixture.get("date")
        kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00")) if kickoff_raw else datetime.now(timezone.utc)
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        home_team_id = str(home["id"]) if home.get("id") is not None else None
        away_team_id = str(away["id"]) if away.get("id") is not None else None
        raw_status = fixture.get("status") if isinstance(fixture.get("status"), dict) else {}
        status_short = str(raw_status.get("short") or "NS").upper()

        return MatchSummary(
            id=f"api-football-{fixture_id}",
            external_id=fixture_id,
            competition=league.get("name") or "Competición",
            country=league.get("country"),
            country_code=league.get("country_code") or league.get("countryCode"),
            competition_logo=league.get("logo"),
            league_id=str(league["id"]) if league.get("id") is not None else None,
            season=league.get("season"),
            round=str(league.get("round")) if league.get("round") is not None else None,
            kickoff_at=kickoff,
            home_team=home.get("name") or "Local",
            away_team=away.get("name") or "Visitante",
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_logo=home.get("logo"),
            away_logo=away.get("logo"),
            venue_id=(
                str((fixture.get("venue") or {})["id"])
                if (fixture.get("venue") or {}).get("id") is not None
                else None
            ),
            venue=(fixture.get("venue") or {}).get("name"),
            referee=fixture.get("referee"),
            data_quality=0.95,
            # Fixtures do not include bookmaker quotes. A future odds provider
            # can flip this flag only after prices are actually retrieved.
            odds_available=False,
            home_score=self._score_int(goals.get("home")),
            away_score=self._score_int(goals.get("away")),
            halftime_home_score=self._score_int(halftime.get("home")),
            halftime_away_score=self._score_int(halftime.get("away")),
            elapsed=self._score_int(raw_status.get("elapsed")),
            status_short=status_short,
            status=self._normalize_status(status_short),
            source_provider=self.provider_name,
            source_url=f"{self.base_url}/fixtures?id={fixture_id}",
        )

    def list_fixtures(
        self,
        match_date: date | None = None,
        *,
        timeout: float | None = None,
    ) -> list[MatchSummary]:
        target_date = (match_date or datetime.now(SPORTS_TIMEZONE).date()).isoformat()
        data = self._cached_request(
            "fixtures",
            params={"date": target_date, "timezone": SPORTS_TIMEZONE.key},
            ttl=60,
            timeout=timeout,
        )
        response_items = data.get("response")
        if not isinstance(response_items, list):
            raise APIFootballAPIError(
                "API-Football devolvió una respuesta sin una lista de partidos válida."
            )
        return [self._to_match_summary(item) for item in response_items]

    def get_fixture(self, fixture_id: str) -> MatchSummary | None:
        clean_id = fixture_id.removeprefix("api-football-")
        if not clean_id.isdigit():
            return None
        details = self.get_fixture_details(clean_id)
        return self._to_match_summary(details) if details is not None else None

    def get_fixture_details(self, fixture_id: str) -> dict | None:
        clean_id = fixture_id.removeprefix("api-football-")
        data = self._cached_request("fixtures", params={"id": clean_id}, ttl=2 * 60)
        res = data.get("response")
        if not isinstance(res, list):
            raise APIFootballAPIError(
                "API-Football devolvió un detalle de partido con formato inesperado."
            )
        return res[0] if res else None

    @staticmethod
    def _clean_id(value: object, label: str = "id") -> str:
        clean = str(value or "").removeprefix("api-football-").strip()
        if not clean.isdigit():
            raise ValueError(f"{label} debe ser un identificador numérico de API-Football.")
        return clean

    @staticmethod
    def _entity(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        entity = deepcopy(raw)
        if entity.get("id") is not None:
            entity["id"] = str(entity["id"])
        return entity

    @classmethod
    def _split_record(cls, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        result: dict[str, object] = {}
        for key in ("home", "away", "total"):
            if key in raw:
                result[key] = cls._metric_value(raw.get(key))
        return result

    def get_status(self) -> dict:
        """Return account/quota status. API-Sports documents this call as free."""

        data = self._cached_request("status")
        status = self._response_object(data, "status")
        requests = status.get("requests") if isinstance(status.get("requests"), dict) else {}
        try:
            current = int(requests.get("current"))
            limit_day = int(requests.get("limit_day"))
        except (TypeError, ValueError):
            pass
        else:
            self._quota_limit = max(0, limit_day)
            self._quota_remaining = max(0, limit_day - max(0, current))
            self._quota_captured_at = datetime.now(timezone.utc).isoformat()
        return status

    def get_timezones(self) -> list[str]:
        data = self._cached_request("timezone")
        response = data.get("response")
        if not isinstance(response, list):
            raise APIFootballAPIError(
                "API-Football devolvió zonas horarias con formato inesperado.",
                endpoint="timezone",
                code="invalid_envelope",
            )
        return [str(item) for item in response if isinstance(item, str)]

    def get_countries(self, *, name: str | None = None, code: str | None = None, search: str | None = None) -> list[dict]:
        data = self._cached_request(
            "countries",
            params={"name": name, "code": code, "search": search},
        )
        return self._response_items(data, "countries")

    def get_venues(
        self,
        *,
        venue_id: str | int | None = None,
        name: str | None = None,
        city: str | None = None,
        country: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "venues",
            params={
                "id": self._clean_id(venue_id, "venue_id") if venue_id is not None else None,
                "name": name,
                "city": city,
                "country": country,
                "search": search,
            },
        )
        return self._response_items(data, "venues")

    def get_rounds(
        self,
        league_id: str | int,
        season: int,
        *,
        current: bool | None = None,
        include_dates: bool = False,
    ) -> list[object]:
        data = self._cached_request(
            "fixtures/rounds",
            params={
                "league": self._clean_id(league_id, "league_id"),
                "season": int(season),
                "current": str(current).lower() if current is not None else None,
                "dates": "true" if include_dates else None,
            },
        )
        response = data.get("response")
        if not isinstance(response, list):
            raise APIFootballAPIError(
                "API-Football devolvió jornadas con formato inesperado.",
                endpoint="fixtures/rounds",
                code="invalid_envelope",
            )
        return deepcopy(response)

    def get_leagues(
        self,
        *,
        league_id: str | int | None = None,
        team_id: str | int | None = None,
        country: str | None = None,
        code: str | None = None,
        season: int | None = None,
        current: bool | None = None,
        search: str | None = None,
        league_type: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "leagues",
            params={
                "id": self._clean_id(league_id, "league_id") if league_id is not None else None,
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "country": country,
                "code": code,
                "season": int(season) if season is not None else None,
                "current": str(current).lower() if current is not None else None,
                "search": search,
                "type": league_type,
            },
        )
        normalized: list[dict] = []
        for item in self._response_items(data, "leagues"):
            seasons = item.get("seasons") if isinstance(item.get("seasons"), list) else []
            normalized.append(
                {
                    "league": self._entity(item.get("league")),
                    "country": deepcopy(item.get("country") or {}),
                    "seasons": deepcopy(seasons),
                    "source_provider": self.provider_name,
                    "provider_payload": item,
                }
            )
        return normalized

    def get_league_coverage(self, league_id: str | int, season: int) -> dict | None:
        leagues = self.get_leagues(league_id=league_id, season=season)
        for item in leagues:
            for season_item in item.get("seasons") or []:
                if str(season_item.get("year")) == str(int(season)):
                    return {
                        "league": item["league"],
                        "country": item["country"],
                        "season": int(season),
                        "start": season_item.get("start"),
                        "end": season_item.get("end"),
                        "current": season_item.get("current"),
                        "coverage": deepcopy(season_item.get("coverage") or {}),
                        "source_provider": self.provider_name,
                    }
        return None

    @classmethod
    def _normalize_table_record(cls, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        goals = raw.get("goals") if isinstance(raw.get("goals"), dict) else {}
        return {
            "played": cls._metric_value(raw.get("played")),
            "wins": cls._metric_value(raw.get("win")),
            "draws": cls._metric_value(raw.get("draw")),
            "losses": cls._metric_value(raw.get("lose")),
            "goals_for": cls._metric_value(goals.get("for")),
            "goals_against": cls._metric_value(goals.get("against")),
        }

    def get_standings(
        self,
        league_id: str | int,
        season: int,
        *,
        team_id: str | int | None = None,
    ) -> dict:
        data = self._cached_request(
            "standings",
            params={
                "league": self._clean_id(league_id, "league_id"),
                "season": int(season),
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
            },
        )
        response = self._response_items(data, "standings")
        if not response:
            return {
                "league": {"id": self._clean_id(league_id), "season": int(season)},
                "groups": [],
                "source_provider": self.provider_name,
            }

        raw_item = response[0]
        raw_league = raw_item.get("league") if isinstance(raw_item.get("league"), dict) else {}
        raw_groups = raw_league.get("standings") if isinstance(raw_league.get("standings"), list) else []
        groups: list[dict] = []
        for index, raw_group in enumerate(raw_groups):
            if not isinstance(raw_group, list):
                continue
            table: list[dict] = []
            group_name: str | None = None
            for raw_row in raw_group:
                if not isinstance(raw_row, dict):
                    continue
                if group_name is None and raw_row.get("group"):
                    group_name = str(raw_row["group"])
                table.append(
                    {
                        "rank": self._metric_value(raw_row.get("rank")),
                        "team": self._entity(raw_row.get("team")),
                        "points": self._metric_value(raw_row.get("points")),
                        "goal_difference": self._metric_value(raw_row.get("goalsDiff")),
                        "form": raw_row.get("form"),
                        "status": raw_row.get("status"),
                        "description": raw_row.get("description"),
                        "overall": self._normalize_table_record(raw_row.get("all")),
                        "home": self._normalize_table_record(raw_row.get("home")),
                        "away": self._normalize_table_record(raw_row.get("away")),
                        "updated_at": raw_row.get("update"),
                    }
                )
            groups.append(
                {
                    "name": group_name or f"Grupo {index + 1}",
                    "table": table,
                }
            )

        league = {key: deepcopy(value) for key, value in raw_league.items() if key != "standings"}
        if league.get("id") is not None:
            league["id"] = str(league["id"])
        return {
            "league": league,
            "groups": groups,
            "source_provider": self.provider_name,
            "provider_payload": raw_item,
        }

    @classmethod
    def _normalize_goal_statistics(cls, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        return {
            "total": cls._split_record(raw.get("total")),
            "average": cls._split_record(raw.get("average")),
            "by_minute": deepcopy(raw.get("minute") or {}),
            "over_under": deepcopy(raw.get("under_over") or {}),
        }

    def get_team_statistics(
        self,
        team_id: str | int,
        league_id: str | int,
        season: int,
        *,
        through_date: date | str | None = None,
    ) -> dict | None:
        date_value = through_date.isoformat() if isinstance(through_date, date) else through_date
        data = self._cached_request(
            "teams/statistics",
            params={
                "team": self._clean_id(team_id, "team_id"),
                "league": self._clean_id(league_id, "league_id"),
                "season": int(season),
                "date": date_value,
            },
        )
        raw = data.get("response")
        if raw in (None, []):
            return None
        if not isinstance(raw, dict):
            raise APIFootballAPIError(
                "API-Football devolvió estadísticas de equipo con formato inesperado.",
                endpoint="teams/statistics",
                code="invalid_envelope",
            )
        fixture_stats = raw.get("fixtures") if isinstance(raw.get("fixtures"), dict) else {}
        goals = raw.get("goals") if isinstance(raw.get("goals"), dict) else {}
        penalties = raw.get("penalty") if isinstance(raw.get("penalty"), dict) else {}
        return {
            "team": self._entity(raw.get("team")),
            "league": self._entity(raw.get("league")),
            "form": raw.get("form"),
            "fixtures": {
                "played": self._split_record(fixture_stats.get("played")),
                "wins": self._split_record(fixture_stats.get("wins")),
                "draws": self._split_record(fixture_stats.get("draws")),
                "losses": self._split_record(fixture_stats.get("loses")),
            },
            "goals_for": self._normalize_goal_statistics(goals.get("for")),
            "goals_against": self._normalize_goal_statistics(goals.get("against")),
            "biggest": deepcopy(raw.get("biggest") or {}),
            "clean_sheets": self._split_record(raw.get("clean_sheet")),
            "failed_to_score": self._split_record(raw.get("failed_to_score")),
            "penalties": {
                "scored": deepcopy(penalties.get("scored")),
                "missed": deepcopy(penalties.get("missed")),
                "total": self._metric_value(penalties.get("total")),
            },
            "formations": deepcopy(raw.get("lineups") or []),
            "cards": deepcopy(raw.get("cards") or {}),
            "source_provider": self.provider_name,
            "provider_payload": deepcopy(raw),
        }

    @staticmethod
    def _percentage_value(value: object) -> float | None:
        if value is None:
            return None
        try:
            numeric = str(value).strip().removesuffix("%")
            parsed = float(numeric)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    def get_prediction(self, fixture_id: str | int) -> dict | None:
        clean_id = self._clean_id(fixture_id, "fixture_id")
        data = self._cached_request("predictions", params={"fixture": clean_id})
        response = self._response_items(data, "predictions")
        if not response:
            return None
        raw = response[0]
        predictions = raw.get("predictions") if isinstance(raw.get("predictions"), dict) else {}
        percentages = predictions.get("percent") if isinstance(predictions.get("percent"), dict) else {}
        goals = predictions.get("goals") if isinstance(predictions.get("goals"), dict) else {}
        comparison = raw.get("comparison") if isinstance(raw.get("comparison"), dict) else {}
        return {
            "fixture_id": clean_id,
            "winner": self._entity(predictions.get("winner")),
            "win_or_draw": predictions.get("win_or_draw"),
            "under_over": predictions.get("under_over"),
            "expected_goals": {
                "home": self._metric_value(goals.get("home")),
                "away": self._metric_value(goals.get("away")),
            },
            "advice": predictions.get("advice"),
            "percentages": {
                "home": self._percentage_value(percentages.get("home")),
                "draw": self._percentage_value(percentages.get("draw")),
                "away": self._percentage_value(percentages.get("away")),
            },
            "comparison": {
                key: {
                    side: self._percentage_value(value)
                    for side, value in values.items()
                }
                for key, values in comparison.items()
                if isinstance(values, dict)
            },
            "teams": deepcopy(raw.get("teams") or {}),
            "league": self._entity(raw.get("league")),
            "source_provider": self.provider_name,
            "provider_payload": raw,
        }

    @classmethod
    def _normalize_metric_tree(cls, value: object) -> object:
        if isinstance(value, dict):
            return {key: cls._normalize_metric_tree(child) for key, child in value.items()}
        if isinstance(value, list):
            return [cls._normalize_metric_tree(child) for child in value]
        return cls._metric_value(value)

    @staticmethod
    def _metric_key(value: object) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")
        aliases = {
            "corner_kicks": "corners",
            "shots_on_goal": "shots_on_target",
            "ball_possession": "possession_percentage",
        }
        return aliases.get(normalized, normalized)

    def get_fixtures_by_ids(self, fixture_ids: list[str | int]) -> list[dict]:
        """Fetch up to 20 IDs per documented batch and restore requested order."""

        clean_ids = list(dict.fromkeys(self._clean_id(item, "fixture_id") for item in fixture_ids))
        if not clean_ids:
            return []
        by_id: dict[str, dict] = {}
        for start in range(0, len(clean_ids), 20):
            chunk = clean_ids[start : start + 20]
            data = self._cached_request(
                "fixtures",
                params={"ids": "-".join(chunk), "timezone": SPORTS_TIMEZONE.key},
                ttl=10 * 60,
            )
            for item in self._response_items(data, "fixtures"):
                fixture_id = (item.get("fixture") or {}).get("id")
                if fixture_id is not None:
                    by_id[str(fixture_id)] = self._normalize_history_payload(item)
        return [by_id[fixture_id] for fixture_id in clean_ids if fixture_id in by_id]

    def get_fixture_statistics(
        self,
        fixture_id: str | int,
        *,
        team_id: str | int | None = None,
        statistic_type: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "fixtures/statistics",
            params={
                "fixture": self._clean_id(fixture_id, "fixture_id"),
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "type": statistic_type,
            },
        )
        normalized: list[dict] = []
        for item in self._response_items(data, "fixtures/statistics"):
            metrics: dict[str, object] = {}
            for metric in item.get("statistics") or []:
                if not isinstance(metric, dict):
                    continue
                key = self._metric_key(metric.get("type"))
                if key:
                    value = metric.get("value")
                    metrics[key] = (
                        self._percentage_value(value)
                        if isinstance(value, str) and value.strip().endswith("%")
                        else self._metric_value(value)
                    )
            normalized.append(
                {
                    "team": self._entity(item.get("team")),
                    "metrics": metrics,
                    "source_provider": self.provider_name,
                    "provider_payload": item,
                }
            )
        return normalized

    def get_fixture_events(
        self,
        fixture_id: str | int,
        *,
        team_id: str | int | None = None,
        player_id: str | int | None = None,
        event_type: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "fixtures/events",
            params={
                "fixture": self._clean_id(fixture_id, "fixture_id"),
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "player": self._clean_id(player_id, "player_id") if player_id is not None else None,
                "type": event_type,
            },
        )
        return [
            {
                "time": deepcopy(item.get("time") or {}),
                "team": self._entity(item.get("team")),
                "player": self._entity(item.get("player")),
                "assist": self._entity(item.get("assist")),
                "type": item.get("type"),
                "detail": item.get("detail"),
                "comments": item.get("comments"),
                "source_provider": self.provider_name,
                "provider_payload": item,
            }
            for item in self._response_items(data, "fixtures/events")
        ]

    def get_fixture_lineups_data(
        self,
        fixture_id: str | int,
        *,
        team_id: str | int | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "fixtures/lineups",
            params={
                "fixture": self._clean_id(fixture_id, "fixture_id"),
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
            },
        )
        return self._response_items(data, "fixtures/lineups")

    @classmethod
    def _normalize_player_rows(cls, raw_items: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for item in raw_items:
            # /fixtures/players wraps players by team; /players returns one
            # player plus a statistics array. Convert both to one row shape.
            if isinstance(item.get("players"), list):
                team = cls._entity(item.get("team"))
                player_items = item.get("players") or []
            else:
                team = {}
                player_items = [item]
            for player_item in player_items:
                if not isinstance(player_item, dict):
                    continue
                player = cls._entity(player_item.get("player"))
                statistics = player_item.get("statistics")
                if not isinstance(statistics, list):
                    statistics = []
                if not statistics:
                    rows.append(
                        {
                            "player": player,
                            "team": team,
                            "league": {},
                            "games": {},
                            "substitutes": {},
                            "shots": {},
                            "goals": {},
                            "passes": {},
                            "tackles": {},
                            "duels": {},
                            "dribbles": {},
                            "fouls": {},
                            "cards": {},
                            "penalty": {},
                            "provider_payload": deepcopy(player_item),
                        }
                    )
                    continue
                for statistic in statistics:
                    if not isinstance(statistic, dict):
                        continue
                    statistic_team = cls._entity(statistic.get("team")) or team
                    rows.append(
                        {
                            "player": player,
                            "team": statistic_team,
                            "league": cls._entity(statistic.get("league")),
                            "games": cls._normalize_metric_tree(statistic.get("games") or {}),
                            "substitutes": cls._normalize_metric_tree(statistic.get("substitutes") or {}),
                            "shots": cls._normalize_metric_tree(statistic.get("shots") or {}),
                            "goals": cls._normalize_metric_tree(statistic.get("goals") or {}),
                            "passes": cls._normalize_metric_tree(statistic.get("passes") or {}),
                            "tackles": cls._normalize_metric_tree(statistic.get("tackles") or {}),
                            "duels": cls._normalize_metric_tree(statistic.get("duels") or {}),
                            "dribbles": cls._normalize_metric_tree(statistic.get("dribbles") or {}),
                            "fouls": cls._normalize_metric_tree(statistic.get("fouls") or {}),
                            "cards": cls._normalize_metric_tree(statistic.get("cards") or {}),
                            "penalty": cls._normalize_metric_tree(statistic.get("penalty") or {}),
                            "provider_payload": deepcopy(player_item),
                        }
                    )
        return rows

    def get_fixture_players(
        self,
        fixture_id: str | int,
        *,
        team_id: str | int | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "fixtures/players",
            params={
                "fixture": self._clean_id(fixture_id, "fixture_id"),
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
            },
        )
        rows = self._normalize_player_rows(self._response_items(data, "fixtures/players"))
        for row in rows:
            row["source_provider"] = self.provider_name
        return rows

    def get_fixture_context(
        self,
        fixture_id: str | int,
        *,
        include_statistics: bool = True,
        include_events: bool = False,
        include_lineups: bool = False,
        include_players: bool = False,
        optional_reserve: int = 10,
    ) -> dict:
        """Build a selective context; optional blocks stop before quota reserve."""

        clean_id = self._clean_id(fixture_id, "fixture_id")
        result: dict[str, object] = {
            "fixture_id": clean_id,
            "fixture": self.get_fixture_details(clean_id),
            "statistics": [],
            "events": [],
            "lineups": [],
            "players": [],
            "skipped": [],
            "source_provider": self.provider_name,
        }
        requested = (
            ("statistics", include_statistics, self.get_fixture_statistics),
            ("events", include_events, self.get_fixture_events),
            ("lineups", include_lineups, self.get_fixture_lineups_data),
            ("players", include_players, self.get_fixture_players),
        )
        for key, enabled, fetcher in requested:
            if not enabled:
                continue
            if not self.can_fetch_optional(optional_reserve):
                result["skipped"].append(key)  # type: ignore[union-attr]
                continue
            result[key] = fetcher(clean_id)
        result["quota"] = self.quota_snapshot
        return result

    def get_injuries_by_fixture_ids(self, fixture_ids: list[str | int]) -> list[dict]:
        clean_ids = list(dict.fromkeys(self._clean_id(item, "fixture_id") for item in fixture_ids))
        injuries: list[dict] = []
        for start in range(0, len(clean_ids), 20):
            chunk = clean_ids[start : start + 20]
            if not chunk:
                continue
            data = self._cached_request("injuries", params={"ids": "-".join(chunk)})
            for item in self._response_items(data, "injuries"):
                fixture = item.get("fixture") if isinstance(item.get("fixture"), dict) else {}
                league = item.get("league") if isinstance(item.get("league"), dict) else {}
                injuries.append(
                    {
                        "fixture_id": str(fixture["id"]) if fixture.get("id") is not None else None,
                        "fixture": deepcopy(fixture),
                        "league": self._entity(league),
                        "team": self._entity(item.get("team")),
                        "player": self._entity(item.get("player")),
                        "source_provider": self.provider_name,
                        "provider_payload": item,
                    }
                )
        return injuries

    def get_squads(
        self,
        *,
        team_id: str | int | None = None,
        player_id: str | int | None = None,
    ) -> list[dict]:
        if team_id is None and player_id is None:
            raise ValueError("get_squads requiere team_id o player_id.")
        data = self._cached_request(
            "players/squads",
            params={
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "player": self._clean_id(player_id, "player_id") if player_id is not None else None,
            },
        )
        return [
            {
                "team": self._entity(item.get("team")),
                "players": [self._entity(player) for player in item.get("players") or [] if isinstance(player, dict)],
                "source_provider": self.provider_name,
                "provider_payload": item,
            }
            for item in self._response_items(data, "players/squads")
        ]

    def get_player_statistics(
        self,
        team_id: str | int,
        league_id: str | int,
        season: int,
        *,
        player_id: str | int | None = None,
        page: int = 1,
    ) -> dict:
        data = self._cached_request(
            "players",
            params={
                "team": self._clean_id(team_id, "team_id"),
                "league": self._clean_id(league_id, "league_id"),
                "season": int(season),
                "id": self._clean_id(player_id, "player_id") if player_id is not None else None,
                "page": max(1, int(page)),
            },
        )
        items = self._normalize_player_rows(self._response_items(data, "players"))
        for item in items:
            item["source_provider"] = self.provider_name
        return {
            "items": items,
            "paging": deepcopy(data.get("paging") or {}),
            "source_provider": self.provider_name,
        }

    def get_player_context(
        self,
        team_id: str | int,
        league_id: str | int,
        season: int,
        *,
        player_id: str | int | None = None,
        page: int = 1,
        include_squad: bool = False,
        optional_reserve: int = 10,
    ) -> dict:
        statistics = self.get_player_statistics(
            team_id,
            league_id,
            season,
            player_id=player_id,
            page=page,
        )
        squad: list[dict] = []
        skipped: list[str] = []
        if include_squad:
            if self.can_fetch_optional(optional_reserve):
                squad = self.get_squads(team_id=team_id, player_id=player_id)
            else:
                skipped.append("squad")
        return {
            "team_id": self._clean_id(team_id, "team_id"),
            "league_id": self._clean_id(league_id, "league_id"),
            "season": int(season),
            "players": statistics["items"],
            "paging": statistics["paging"],
            "squad": squad,
            "skipped": skipped,
            "quota": self.quota_snapshot,
            "source_provider": self.provider_name,
        }

    def get_teams(
        self,
        *,
        team_id: str | int | None = None,
        name: str | None = None,
        league_id: str | int | None = None,
        season: int | None = None,
        country: str | None = None,
        code: str | None = None,
        venue_id: str | int | None = None,
        search: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "teams",
            params={
                "id": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "name": name,
                "league": self._clean_id(league_id, "league_id") if league_id is not None else None,
                "season": int(season) if season is not None else None,
                "country": country,
                "code": code,
                "venue": self._clean_id(venue_id, "venue_id") if venue_id is not None else None,
                "search": search,
            },
        )
        return [
            {
                "team": self._entity(item.get("team")),
                "venue": self._entity(item.get("venue")),
                "source_provider": self.provider_name,
                "provider_payload": item,
            }
            for item in self._response_items(data, "teams")
        ]

    def get_team_seasons(self, team_id: str | int) -> list[int]:
        data = self._cached_request(
            "teams/seasons",
            params={"team": self._clean_id(team_id, "team_id")},
        )
        response = data.get("response")
        if not isinstance(response, list):
            raise APIFootballAPIError(
                "API-Football devolvió temporadas de equipo con formato inesperado.",
                endpoint="teams/seasons",
                code="invalid_envelope",
            )
        return [int(item) for item in response if isinstance(item, (int, str)) and str(item).isdigit()]

    def get_team_countries(self) -> list[dict]:
        data = self._cached_request("teams/countries", ttl=7 * 24 * 60 * 60)
        return self._response_items(data, "teams/countries")

    def get_player_seasons(self, player_id: str | int) -> list[int]:
        data = self._cached_request(
            "players/seasons",
            params={"player": self._clean_id(player_id, "player_id")},
            ttl=24 * 60 * 60,
        )
        response = data.get("response")
        if not isinstance(response, list):
            raise APIFootballAPIError(
                "API-Football devolvió temporadas de jugador con formato inesperado.",
                endpoint="players/seasons",
                code="invalid_envelope",
            )
        return [int(item) for item in response if isinstance(item, (int, str)) and str(item).isdigit()]

    def get_top_players(self, category: str, league_id: str | int, season: int) -> list[dict]:
        endpoints = {
            "scorers": "players/topscorers",
            "assists": "players/topassists",
            "yellow_cards": "players/topyellowcards",
            "red_cards": "players/topredcards",
        }
        try:
            endpoint = endpoints[category]
        except KeyError as exc:
            raise ValueError(f"Categoría top no soportada: {category}.") from exc
        data = self._cached_request(
            endpoint,
            params={
                "league": self._clean_id(league_id, "league_id"),
                "season": int(season),
            },
        )
        rows = self._normalize_player_rows(self._response_items(data, endpoint))
        for row in rows:
            row["category"] = category
            row["source_provider"] = self.provider_name
        return rows

    def get_top_scorers(self, league_id: str | int, season: int) -> list[dict]:
        return self.get_top_players("scorers", league_id, season)

    def get_top_assists(self, league_id: str | int, season: int) -> list[dict]:
        return self.get_top_players("assists", league_id, season)

    def get_top_yellow_cards(self, league_id: str | int, season: int) -> list[dict]:
        return self.get_top_players("yellow_cards", league_id, season)

    def get_top_red_cards(self, league_id: str | int, season: int) -> list[dict]:
        return self.get_top_players("red_cards", league_id, season)

    def get_transfers(
        self,
        *,
        team_id: str | int | None = None,
        player_id: str | int | None = None,
    ) -> list[dict]:
        if team_id is None and player_id is None:
            raise ValueError("get_transfers requiere team_id o player_id.")
        data = self._cached_request(
            "transfers",
            params={
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "player": self._clean_id(player_id, "player_id") if player_id is not None else None,
            },
        )
        return self._response_items(data, "transfers")

    def get_trophies(
        self,
        *,
        player_id: str | int | None = None,
        coach_id: str | int | None = None,
    ) -> list[dict]:
        if player_id is None and coach_id is None:
            raise ValueError("get_trophies requiere player_id o coach_id.")
        data = self._cached_request(
            "trophies",
            params={
                "player": self._clean_id(player_id, "player_id") if player_id is not None else None,
                "coach": self._clean_id(coach_id, "coach_id") if coach_id is not None else None,
            },
        )
        return self._response_items(data, "trophies")

    def get_sidelined(
        self,
        *,
        player_id: str | int | None = None,
        coach_id: str | int | None = None,
    ) -> list[dict]:
        if player_id is None and coach_id is None:
            raise ValueError("get_sidelined requiere player_id o coach_id.")
        data = self._cached_request(
            "sidelined",
            params={
                "player": self._clean_id(player_id, "player_id") if player_id is not None else None,
                "coach": self._clean_id(coach_id, "coach_id") if coach_id is not None else None,
            },
        )
        return self._response_items(data, "sidelined")

    def get_coaches(
        self,
        *,
        coach_id: str | int | None = None,
        team_id: str | int | None = None,
        search: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "coachs",
            params={
                "id": self._clean_id(coach_id, "coach_id") if coach_id is not None else None,
                "team": self._clean_id(team_id, "team_id") if team_id is not None else None,
                "search": search,
            },
        )
        return self._response_items(data, "coachs")

    def get_odds_mapping(self, *, page: int = 1) -> dict:
        data = self._cached_request("odds/mapping", params={"page": max(1, int(page))})
        return {
            "items": self._response_items(data, "odds/mapping"),
            "paging": deepcopy(data.get("paging") or {}),
            "source_provider": self.provider_name,
        }

    def get_odds_bookmakers(
        self,
        *,
        bookmaker_id: str | int | None = None,
        search: str | None = None,
    ) -> list[dict]:
        data = self._cached_request(
            "odds/bookmakers",
            params={
                "id": self._clean_id(bookmaker_id, "bookmaker_id") if bookmaker_id is not None else None,
                "search": search,
            },
        )
        return self._response_items(data, "odds/bookmakers")

    def get_odds_markets(
        self,
        *,
        live: bool = False,
        market_id: str | int | None = None,
        search: str | None = None,
    ) -> list[dict]:
        endpoint = "odds/live/bets" if live else "odds/bets"
        data = self._cached_request(
            endpoint,
            params={
                "id": self._clean_id(market_id, "market_id") if market_id is not None else None,
                "search": search,
            },
        )
        return self._response_items(data, endpoint)

    @staticmethod
    def _line_market_key(prefix: str, value: str) -> str | None:
        match = re.fullmatch(r"(Over|Under)\s+(\d+(?:\.\d+)?)", value.strip(), re.IGNORECASE)
        if match is None:
            return None
        side = match.group(1).upper()
        line = match.group(2).replace(".", "_")
        return f"{prefix}_{side}_{line}"

    @classmethod
    def _prematch_market_key(cls, bet_name: object, selection: object) -> str | None:
        """Map only exact full-time API-Football markets used by the model."""

        bet = " ".join(str(bet_name).split()).casefold()
        value = " ".join(str(selection).split())
        normalized_value = value.casefold()

        if bet == "match winner":
            return {
                "home": "WINNER_HOME",
                "draw": "WINNER_DRAW",
                "away": "WINNER_AWAY",
            }.get(normalized_value)
        if bet == "goals over/under":
            return cls._line_market_key("TOTAL_GOALS", value)
        if bet == "both teams score" and normalized_value == "yes":
            return "BOTH_TEAMS_TO_SCORE"
        if bet == "double chance":
            return {
                "home/draw": "DOUBLE_CHANCE_HOME_DRAW",
                "draw/away": "DOUBLE_CHANCE_AWAY_DRAW",
                "home/away": "DOUBLE_CHANCE_HOME_AWAY",
            }.get(normalized_value)
        if bet == "corners over under":
            return cls._line_market_key("TOTAL_CORNERS", value)
        if bet == "home corners over/under":
            return cls._line_market_key("TEAM_CORNERS_HOME", value)
        if bet == "away corners over/under":
            return cls._line_market_key("TEAM_CORNERS_AWAY", value)
        return None

    def get_fixture_odds(self, fixture_id: str) -> dict[str, BookmakerQuote]:
        """Return the best verified pre-match quote for each exact selection."""

        clean_id = fixture_id.removeprefix("api-football-")
        if not clean_id.isdigit():
            return {}

        cached = self._odds_cache.get(clean_id)
        if cached is not None:
            stored_at, quotes = cached
            ttl = self._ODDS_CACHE_TTL_SECONDS if quotes else self._ODDS_EMPTY_CACHE_TTL_SECONDS
            if time.monotonic() - stored_at <= ttl:
                return quotes.copy()

        data = self._request("odds", params={"fixture": clean_id})
        quotes: dict[str, BookmakerQuote] = {}
        for fixture_odds in data.get("response", []):
            updated_at = fixture_odds.get("update")
            for bookmaker_data in fixture_odds.get("bookmakers") or []:
                bookmaker = str(bookmaker_data.get("name") or "").strip()
                if not bookmaker:
                    continue
                for bet in bookmaker_data.get("bets") or []:
                    bet_name = bet.get("name")
                    for offered in bet.get("values") or []:
                        market_key = self._prematch_market_key(bet_name, offered.get("value"))
                        if market_key is None:
                            continue
                        try:
                            odds = float(offered.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(odds) or odds <= 1:
                            continue
                        current = quotes.get(market_key)
                        if current is None or odds > current.odds:
                            quotes[market_key] = BookmakerQuote(
                                market_key=market_key,
                                odds=odds,
                                bookmaker=bookmaker,
                                updated_at=str(updated_at) if updated_at else None,
                            )

        self._odds_cache[clean_id] = (time.monotonic(), quotes.copy())
        return quotes

    def get_team_last_matches(
        self,
        team_id: str,
        limit: int = 5,
        enrich: bool = True,
    ) -> list[dict]:
        """Return recent completed fixtures enriched with canonical statistics.

        The regular ``team`` lookup does not always embed statistics and player
        performance. API-Sports can return those blocks for multiple fixture
        IDs in one request, so use one batch instead of issuing one request per
        match. If that optional enrichment fails, the base fixture history is
        still useful and is returned unchanged.
        """
        bounded_limit = max(1, min(limit, 10))
        raw_items = self._request_completed_fixtures(
            "fixtures",
            params={"team": team_id, "last": str(bounded_limit), "status": "FT-AET-PEN"},
        )
        raw_items = sorted(
            raw_items,
            key=lambda item: str((item.get("fixture") or {}).get("date") or ""),
            reverse=True,
        )[:bounded_limit]

        if not enrich:
            return raw_items
        return self.enrich_fixture_histories(raw_items)[0]

    @staticmethod
    def _is_completed_fixture(item: dict) -> bool:
        """Keep only played fixtures in form history.

        API-Football applies the requested status filter. The score fallback
        keeps the adapter resilient to older/cached payloads without a status
        block while still excluding unplayed fixtures.
        """

        status = str(((item.get("fixture") or {}).get("status") or {}).get("short") or "").upper()
        if status:
            return status in {"FT", "AET", "PEN"}
        goals = item.get("goals") or {}
        if goals.get("home") is not None and goals.get("away") is not None:
            return True
        # Compatibility for detailed/cached completed-fixture payloads that
        # predate the status field in this adapter's tests or local cache.
        return bool(item.get("statistics") or item.get("players"))

    def _request_completed_fixtures(
        self,
        endpoint: str,
        *,
        params: dict[str, str],
    ) -> list[dict]:
        """Request played fixtures and optionally retry an empty successful response.

        Some API-Football plans/proxies have returned an empty response when
        ``last`` and the multi-value ``status`` filter are combined, even
        though the same request without ``status`` contains completed rows.
        We still filter locally, so the compatibility retry cannot leak
        scheduled fixtures into H2H or recent-form history.
        """

        def completed(payload: dict) -> list[dict]:
            response = payload.get("response")
            if not isinstance(response, list):
                raise APIFootballAPIError(
                    "API-Football devolvió un historial con formato inesperado."
                )
            return [
                item
                for item in response
                if isinstance(item, dict) and self._is_completed_fixture(item)
            ]

        # Provider failures (especially 403/429/quota exhaustion) must always
        # propagate. Retrying those without ``status`` only spends another
        # request and can hide the real operational problem.
        requested = completed(self._cached_request(endpoint, params=params, ttl=6 * 60 * 60))
        if requested or "status" not in params:
            return requested

        compatibility_params = {
            key: value for key, value in params.items() if key != "status"
        }
        return completed(
            self._cached_request(endpoint, params=compatibility_params, ttl=6 * 60 * 60)
        )

    def enrich_fixture_histories(
        self,
        *histories: list[dict],
    ) -> tuple[list[dict], ...]:
        """Enrich several team histories through one documented ``ids`` call."""

        fixture_ids: list[str] = []
        for history in histories:
            for item in history:
                fixture_id = (item.get("fixture") or {}).get("id")
                needs_enrichment = (
                    not item.get("statistics")
                    or not item.get("players")
                    or not item.get("lineups")
                )
                if fixture_id is not None and needs_enrichment:
                    clean_id = str(fixture_id)
                    if clean_id not in fixture_ids:
                        fixture_ids.append(clean_id)

        enriched_by_id: dict[str, dict] = {}
        if fixture_ids:
            try:
                batch = self._cached_request(
                    "fixtures",
                    params={"ids": "-".join(fixture_ids)},
                    ttl=60 * 60,
                )
                enriched_by_id = {
                    str(fixture_id): item
                    for item in batch.get("response", [])
                    if (fixture_id := (item.get("fixture") or {}).get("id")) is not None
                }
            except Exception as exc:
                logger.warning(
                    "No se pudo enriquecer el historial de API-Football; se conserva la respuesta base: %s",
                    exc,
                )

        return tuple(
            [
                self._normalize_history_payload(
                    self._merge_payloads(
                        item,
                        enriched_by_id.get(str((item.get("fixture") or {}).get("id")), {}),
                    )
                )
                for item in history
            ]
            for history in histories
        )

    @classmethod
    def _merge_payloads(cls, base: dict, enriched: dict) -> dict:
        """Recursively merge provider payloads without mutating either response."""

        merged = deepcopy(base)
        for key, value in enriched.items():
            # A detailed batch may omit a block that was already present in
            # the base fixture. Empty enrichment must never erase real data.
            if value in (None, [], {}) and merged.get(key):
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._merge_payloads(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    @staticmethod
    def _metric_value(value: object) -> object:
        """Keep provider meaning while converting simple numeric strings."""

        if value is None:
            return None
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        try:
            number = float(stripped)
        except ValueError:
            return value
        return int(number) if number.is_integer() else number

    @classmethod
    def _normalize_team_statistics(cls, raw_statistics: object) -> list[dict]:
        if not isinstance(raw_statistics, list):
            return []

        metric_names = {
            "corner kicks": "corners",
            "corners": "corners",
            "total shots": "total_shots",
            "shots on goal": "shots_on_target",
            "shots on target": "shots_on_target",
            "yellow cards": "yellow_cards",
            "red cards": "red_cards",
            "fouls": "fouls",
        }
        normalized: list[dict] = []
        for team_block in raw_statistics:
            if not isinstance(team_block, dict):
                continue
            team = deepcopy(team_block.get("team") or {})
            metrics: dict[str, object] = {}
            provider_metrics = team_block.get("statistics") or []
            if isinstance(provider_metrics, list):
                for metric in provider_metrics:
                    if not isinstance(metric, dict):
                        continue
                    raw_type = metric.get("type")
                    canonical_name = metric_names.get(str(raw_type).strip().casefold())
                    if canonical_name is not None:
                        metric_value = cls._metric_value(metric.get("value"))
                        if metric_value is not None:
                            metrics[canonical_name] = metric_value
            if metrics:
                normalized.append({"team": team, **metrics})
        return normalized

    @classmethod
    def _normalize_player_statistics(cls, raw_players: object) -> list[dict]:
        if not isinstance(raw_players, list):
            return []

        normalized: list[dict] = []
        for team_block in raw_players:
            if not isinstance(team_block, dict):
                continue
            team = deepcopy(team_block.get("team") or {})
            players = team_block.get("players") or []
            if not isinstance(players, list):
                continue
            for player_block in players:
                if not isinstance(player_block, dict):
                    continue
                player = deepcopy(player_block.get("player") or {})
                statistics = player_block.get("statistics") or []
                if not isinstance(statistics, list):
                    continue
                for statistic in statistics:
                    if not isinstance(statistic, dict):
                        continue
                    item: dict[str, object] = {"player": player, "team": team}
                    raw_shots = statistic.get("shots")
                    if isinstance(raw_shots, dict):
                        shots: dict[str, object] = {}
                        if "total" in raw_shots:
                            total_shots = cls._metric_value(raw_shots.get("total"))
                            if total_shots is not None:
                                shots["total"] = total_shots
                        if "on" in raw_shots:
                            shots_on_target = cls._metric_value(raw_shots.get("on"))
                            if shots_on_target is not None:
                                shots["on_target"] = shots_on_target
                        elif "on_target" in raw_shots:
                            shots_on_target = cls._metric_value(raw_shots.get("on_target"))
                            if shots_on_target is not None:
                                shots["on_target"] = shots_on_target
                        if shots:
                            item["shots"] = shots
                    raw_goals = statistic.get("goals")
                    if isinstance(raw_goals, dict) and "total" in raw_goals:
                        goals_total = cls._metric_value(raw_goals.get("total"))
                        if goals_total is not None:
                            item["goals"] = {"total": goals_total}
                    if "shots" in item or "goals" in item:
                        normalized.append(item)
        return normalized

    @classmethod
    def _normalize_history_payload(cls, item: dict) -> dict:
        normalized = deepcopy(item)
        raw_statistics = normalized.get("statistics")
        canonical_statistics = cls._normalize_team_statistics(raw_statistics)
        if canonical_statistics:
            normalized["provider_statistics"] = raw_statistics
            normalized["statistics"] = canonical_statistics

        raw_players = normalized.get("players")
        canonical_players = cls._normalize_player_statistics(raw_players)
        if canonical_players:
            normalized["player_statistics"] = canonical_players
        return normalized

    def get_head_to_head(self, team1_id: str, team2_id: str, limit: int = 5) -> list[H2HMatchItem]:
        """Return played meetings using API-Football's canonical H2H query.

        ``h2h`` is the only required parameter documented by API-Football and
        ``last`` is enough to request the recent meetings.  The additional
        multi-status filter introduced later caused valid H2H requests to
        return an empty result for some accounts/proxies, while the original
        ``h2h + last`` request still returned the fixtures.  Fetch the canonical
        payload once and enforce completed statuses locally so scheduled rows
        can never be displayed as history.
        """

        first_id = str(team1_id or "").removeprefix("api-football-").strip()
        second_id = str(team2_id or "").removeprefix("api-football-").strip()
        if not first_id or not second_id or first_id == second_id:
            return []

        h2h_param = f"{first_id}-{second_id}"
        bounded_limit = max(1, min(limit, 10))
        completed = self._request_completed_fixtures(
            "fixtures/headtohead",
            params={
                "h2h": h2h_param,
                "last": str(bounded_limit),
                "status": "FT-AET-PEN",
            },
        )
        return self.normalize_history(completed, bounded_limit)

    def get_upcoming_head_to_head(
        self, team1_id: str, team2_id: str, limit: int = 5
    ) -> list[MatchSummary]:
        first_id = self._clean_id(team1_id, "team1_id")
        second_id = self._clean_id(team2_id, "team2_id")
        if first_id == second_id:
            return []
        data = self._cached_request(
            "fixtures",
            params={
                "h2h": f"{first_id}-{second_id}",
                "next": str(max(1, min(limit, 5))),
                "timezone": SPORTS_TIMEZONE.key,
            },
            ttl=5 * 60,
        )
        rows = data.get("response")
        if not isinstance(rows, list):
            raise APIFootballAPIError("API-Football devolvió próximos cruces inválidos.")
        return [self._to_match_summary(row) for row in rows]

    @staticmethod
    def _history_item(item: dict) -> H2HMatchItem | None:
        """Normalize one real fixture, skipping incomplete records instead of fabricating values."""
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}

        raw_date = fixture.get("date")
        competition = league.get("name")
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")
        home_goals = goals.get("home")
        away_goals = goals.get("away")
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

    def get_fixture_injuries(self, fixture_id: str) -> list[InjuryItem]:
        clean_id = fixture_id.replace("api-football-", "")
        data = self._cached_request("injuries", params={"fixture": clean_id})
        items = []
        for item in data.get("response", []):
            player = item.get("player", {}).get("name", "Jugador")
            team = item.get("team", {}).get("name", "Equipo")
            reason = item.get("player", {}).get("reason", "Baja por lesión/sanción")
            injury_type = item.get("player", {}).get("type", "Sin clasificar")
            status = "Duda" if str(injury_type).casefold() == "questionable" else "Baja confirmada"

            items.append(
                InjuryItem(
                    player=player,
                    team=team,
                    reason=reason,
                    status=status,
                    type=injury_type,
                )
            )
        return items

    def get_fixture_lineups(
        self,
        fixture_id: str,
        home_team_id: str | None = None,
        away_team_id: str | None = None,
    ) -> LineupsSummary:
        clean_id = fixture_id.removeprefix("api-football-")
        data = self._cached_request("fixtures/lineups", params={"fixture": clean_id})
        response_list = data.get("response", [])

        if not response_list:
            return LineupsSummary(
                confirmed=False,
                home=None,
                away=None,
                status="pending",
                note="API-Football todavía no publicó los once iniciales.",
            )

        home_lineup: TeamLineup | None = None
        away_lineup: TeamLineup | None = None

        unmatched_lineups: list[TeamLineup] = []
        for idx, item in enumerate(response_list):
            team_data = item.get("team") or {}
            team_id = str(team_data["id"]) if team_data.get("id") is not None else None
            t_lineup = self._parse_team_lineup(item, idx, source="api_football")

            if home_team_id is not None and team_id == str(home_team_id):
                home_lineup = t_lineup
            elif away_team_id is not None and team_id == str(away_team_id):
                away_lineup = t_lineup
            else:
                unmatched_lineups.append(t_lineup)

        # API-Sports normally returns home then away. Keep that behavior as a
        # compatibility fallback when team IDs are unavailable.
        if home_lineup is None and unmatched_lineups:
            home_lineup = unmatched_lineups.pop(0)
        if away_lineup is None and unmatched_lineups:
            away_lineup = unmatched_lineups.pop(0)

        fully_confirmed = bool(
            home_lineup
            and home_lineup.confirmed
            and away_lineup
            and away_lineup.confirmed
        )
        partially_confirmed = bool(
            (home_lineup and home_lineup.confirmed)
            or (away_lineup and away_lineup.confirmed)
        )
        return LineupsSummary(
            confirmed=fully_confirmed,
            home=home_lineup,
            away=away_lineup,
            status="confirmed" if fully_confirmed else "partial" if partially_confirmed else "pending",
            note=(
                "Alineaciones confirmadas por API-Football."
                if fully_confirmed
                else "Solo se considera confirmado un equipo con formación y once titulares completos."
            ),
        )

    @staticmethod
    def _parse_player(raw_player: dict) -> PlayerLineup | None:
        player = raw_player.get("player") or {}
        name = str(player.get("name") or "").strip()
        if not name:
            return None
        return PlayerLineup(
            id=player.get("id"),
            name=name,
            number=player.get("number"),
            pos=player.get("pos"),
            grid=player.get("grid"),
        )

    @classmethod
    def _parse_team_lineup(
        cls,
        item: dict,
        index: int = 0,
        *,
        source: str,
        sample_size: int | None = None,
    ) -> TeamLineup:
        team_data = item.get("team") or {}
        team_name = str(team_data.get("name") or f"Equipo {index + 1}")
        formation = str(item.get("formation") or "").strip() or None
        coach = str((item.get("coach") or {}).get("name") or "").strip() or None
        start_xi = [
            player
            for raw_player in item.get("startXI") or []
            if isinstance(raw_player, dict) and (player := cls._parse_player(raw_player)) is not None
        ]
        substitutes = [
            player
            for raw_player in item.get("substitutes") or []
            if isinstance(raw_player, dict) and (player := cls._parse_player(raw_player)) is not None
        ]
        unique_starters = {
            f"id:{player.id}" if player.id is not None else f"name:{player.name.casefold()}"
            for player in start_xi
        }
        confirmed = (
            source == "api_football"
            and formation is not None
            and len(start_xi) == 11
            and len(unique_starters) == 11
        )
        return TeamLineup(
            team_name=team_name,
            formation=formation,
            coach=coach,
            start_xi=start_xi,
            substitutes=substitutes,
            confirmed=confirmed,
            source=source,
            sample_size=sample_size,
        )

    @classmethod
    def _probable_team_lineup(
        cls,
        history: list[dict],
        team_id: str | None,
        team_name: str,
        excluded_player_names: set[str] | None = None,
    ) -> TeamLineup | None:
        """Estimate the usual XI from starts in the five most recent fixtures."""

        normalized_team_id = str(team_id) if team_id is not None else None
        normalized_team_name = team_name.strip().casefold()
        excluded = {name.strip().casefold() for name in excluded_player_names or set()}
        formation_counts: dict[str, int] = {}
        formation_recency: dict[str, int] = {}
        player_counts: dict[str, int] = {}
        player_recency: dict[str, int] = {}
        players: dict[str, PlayerLineup] = {}
        latest_coach: str | None = None
        provider_team_name: str | None = None
        sample_size = 0

        for recency, fixture in enumerate(history[:5]):
            fixture_lineups = fixture.get("lineups") or []
            if not isinstance(fixture_lineups, list):
                continue
            matching_item: dict | None = None
            for item in fixture_lineups:
                if not isinstance(item, dict):
                    continue
                team = item.get("team") or {}
                item_id = str(team.get("id")) if team.get("id") is not None else None
                item_name = str(team.get("name") or "").strip().casefold()
                if (normalized_team_id and item_id == normalized_team_id) or (
                    normalized_team_name and item_name == normalized_team_name
                ):
                    matching_item = item
                    break
            if matching_item is None:
                continue

            parsed = cls._parse_team_lineup(matching_item, source="recent_form")
            if not parsed.formation and not parsed.start_xi:
                continue
            sample_size += 1
            provider_team_name = provider_team_name or parsed.team_name
            latest_coach = latest_coach or parsed.coach
            if parsed.formation:
                formation_counts[parsed.formation] = formation_counts.get(parsed.formation, 0) + 1
                formation_recency.setdefault(parsed.formation, recency)
            for player in parsed.start_xi:
                if player.name.strip().casefold() in excluded:
                    continue
                key = f"id:{player.id}" if player.id is not None else f"name:{player.name.casefold()}"
                player_counts[key] = player_counts.get(key, 0) + 1
                player_recency.setdefault(key, recency)
                players.setdefault(key, player)

        if sample_size == 0:
            return None

        formation = (
            max(
                formation_counts,
                key=lambda value: (formation_counts[value], -formation_recency[value]),
            )
            if formation_counts
            else "4-3-3"
        )
        ranked_player_keys = sorted(
            players,
            key=lambda key: (
                -player_counts[key],
                player_recency[key],
                players[key].number is None,
                players[key].number if players[key].number is not None else 999,
                players[key].name.casefold(),
            ),
        )
        probable_xi = [players[key] for key in ranked_player_keys[:11]]
        return TeamLineup(
            team_name=provider_team_name or team_name,
            formation=formation,
            coach=latest_coach,
            start_xi=probable_xi,
            substitutes=[],
            confirmed=False,
            source="recent_form",
            sample_size=sample_size,
        )

    @classmethod
    def get_probable_lineups(
        cls,
        home_history: list[dict],
        away_history: list[dict],
        *,
        home_team_id: str | None,
        away_team_id: str | None,
        home_team_name: str,
        away_team_name: str,
        injuries: list[InjuryItem] | None = None,
    ) -> LineupsSummary:
        injuries = injuries or []
        home_excluded = {
            injury.player
            for injury in injuries
            if injury.team.strip().casefold() == home_team_name.strip().casefold()
            and injury.status.casefold() != "duda"
        }
        away_excluded = {
            injury.player
            for injury in injuries
            if injury.team.strip().casefold() == away_team_name.strip().casefold()
            and injury.status.casefold() != "duda"
        }
        home = cls._probable_team_lineup(
            home_history,
            home_team_id,
            home_team_name,
            home_excluded,
        )
        away = cls._probable_team_lineup(
            away_history,
            away_team_id,
            away_team_name,
            away_excluded,
        )
        has_estimate = home is not None or away is not None
        return LineupsSummary(
            confirmed=False,
            home=home,
            away=away,
            status="probable" if has_estimate else "pending",
            note=(
                "Estimación basada en titulares y formaciones recientes; las bajas confirmadas se excluyen del once probable."
                if has_estimate
                else "No hay suficiente historial de alineaciones para estimar el once habitual."
            ),
        )

    @staticmethod
    def merge_lineups(
        published: LineupsSummary | None,
        probable: LineupsSummary,
    ) -> LineupsSummary:
        """Prefer complete published XIs team by team, otherwise keep estimates."""

        published_home = published.home if published else None
        published_away = published.away if published else None
        home = published_home if published_home and published_home.confirmed else probable.home or published_home
        away = published_away if published_away and published_away.confirmed else probable.away or published_away
        home_confirmed = bool(home and home.confirmed)
        away_confirmed = bool(away and away.confirmed)
        confirmed = home_confirmed and away_confirmed
        if confirmed:
            status = "confirmed"
            note = "Alineaciones confirmadas por API-Football."
        elif home_confirmed or away_confirmed:
            status = "partial"
            note = "Un equipo ya tiene XI confirmado; el otro se mantiene como probable o pendiente."
        elif home or away:
            status = "probable"
            note = probable.note
        else:
            status = "pending"
            note = probable.note
        return LineupsSummary(
            confirmed=confirmed,
            home=home,
            away=away,
            status=status,
            note=note,
        )

    @staticmethod
    def _normalize_status(short_status: str) -> str:
        status_map = {
            "NS": "PROGRAMADO",
            "TBD": "POR DEFINIR",
            "1H": "EN JUEGO (1T)",
            "HT": "ENTRETIEMPO",
            "2H": "EN JUEGO (2T)",
            "BT": "DESCANSO",
            "ET": "TIEMPO EXTRA",
            "P": "PENALES",
            "LIVE": "EN JUEGO",
            "FT": "FINALIZADO",
            "AET": "FINALIZADO (ET)",
            "PEN": "FINALIZADO (PEN)",
            "PST": "POSPUESTO",
            "CANC": "CANCELADO",
            "ABD": "SUSPENDIDO",
            "SUSP": "SUSPENDIDO",
            "INT": "INTERRUMPIDO",
            "AWD": "FINALIZADO (DECISIÓN TÉCNICA)",
            "WO": "FINALIZADO (WALKOVER)",
        }
        return status_map.get(short_status.upper(), "PROGRAMADO")
