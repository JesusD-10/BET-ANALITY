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
    """Error declarado por API-Football sin exponer el contenido de la respuesta."""


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

    def __init__(self, key: str, base_url: str = "https://v3.football.api-sports.io", is_rapidapi: bool = False, timeout: int = 3) -> None:
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.is_rapidapi = is_rapidapi
        self.timeout = timeout
        self._odds_cache: dict[str, tuple[float, dict[str, BookmakerQuote]]] = {}

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
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = httpx.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout if timeout is None else max(0.1, timeout),
            )
            response.raise_for_status()
            if response.status_code == 204:
                return {"errors": [], "results": 0, "response": []}
            data = response.json()
        except Exception as err:
            logger.error("Error al consultar API-Football en %s: %s", url, err)
            raise

        if not isinstance(data, dict):
            raise APIFootballAPIError("API-Football devolvió una respuesta con formato inesperado.")

        errors = data.get("errors")
        if errors:
            error_count = len(errors) if isinstance(errors, (dict, list, tuple, set)) else 1
            logger.warning(
                "API-Football rechazó la solicitud al endpoint %s con %s error(es).",
                endpoint,
                error_count,
            )
            raise APIFootballAPIError(
                f"API-Football rechazó la solicitud con {error_count} error(es)."
            )

        return data

    def _to_match_summary(self, item: dict) -> MatchSummary:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}

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
        status_short = (fixture.get("status") or {}).get("short", "NS")

        return MatchSummary(
            id=f"api-football-{fixture_id}",
            external_id=fixture_id,
            competition=league.get("name") or "Competición",
            kickoff_at=kickoff,
            home_team=home.get("name") or "Local",
            away_team=away.get("name") or "Visitante",
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_logo=home.get("logo"),
            away_logo=away.get("logo"),
            venue=(fixture.get("venue") or {}).get("name"),
            referee=fixture.get("referee"),
            data_quality=0.95,
            # Fixtures do not include bookmaker quotes. A future odds provider
            # can flip this flag only after prices are actually retrieved.
            odds_available=False,
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
        data = self._request(
            "fixtures",
            params={"date": target_date, "timezone": SPORTS_TIMEZONE.key},
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
        data = self._request("fixtures", params={"id": clean_id})
        res = data.get("response")
        if not isinstance(res, list):
            raise APIFootballAPIError(
                "API-Football devolvió un detalle de partido con formato inesperado."
            )
        return res[0] if res else None

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
        data = self._request(
            "fixtures",
            params={"team": team_id, "last": str(bounded_limit), "status": "FT-AET-PEN"},
        )
        raw_items = sorted(
            [item for item in data.get("response", []) if self._is_completed_fixture(item)],
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
                batch = self._request("fixtures", params={"ids": "-".join(fixture_ids)})
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
        h2h_param = f"{team1_id}-{team2_id}"
        bounded_limit = max(1, min(limit, 10))
        data = self._request(
            "fixtures/headtohead",
            params={
                "h2h": h2h_param,
                "last": str(bounded_limit),
                "status": "FT-AET-PEN",
            },
        )
        completed = [
            item
            for item in data.get("response", [])
            if self._is_completed_fixture(item)
        ]
        return self.normalize_history(completed, bounded_limit)

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
        data = self._request("injuries", params={"fixture": clean_id})
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
        data = self._request("fixtures/lineups", params={"fixture": clean_id})
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
