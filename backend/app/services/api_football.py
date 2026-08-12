from datetime import date, datetime, timezone
from copy import deepcopy
import logging
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


class APIFootballAPIError(RuntimeError):
    """Error declarado por API-Football sin exponer el contenido de la respuesta."""


class APIFootballProvider:
    """Adaptador profesional para API-Football (v3.football.api-sports.io o RapidAPI)."""

    provider_name = "api-football"

    def __init__(self, key: str, base_url: str = "https://v3.football.api-sports.io", is_rapidapi: bool = False, timeout: int = 3) -> None:
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.is_rapidapi = is_rapidapi
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        if self.is_rapidapi:
            return {
                "x-rapidapi-key": self.key,
                "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
            }
        return {
            "x-apisports-key": self.key,
        }

    def _request(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = httpx.get(url, headers=self._get_headers(), params=params, timeout=self.timeout)
            response.raise_for_status()
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

    def list_fixtures(self, match_date: date | None = None) -> list[MatchSummary]:
        target_date = (match_date or date.today()).isoformat()
        data = self._request("fixtures", params={"date": target_date})
        return [self._to_match_summary(item) for item in data.get("response", [])]

    def get_fixture(self, fixture_id: str) -> MatchSummary | None:
        clean_id = fixture_id.removeprefix("api-football-")
        if not clean_id.isdigit():
            return None
        details = self.get_fixture_details(clean_id)
        return self._to_match_summary(details) if details is not None else None

    def get_fixture_details(self, fixture_id: str) -> dict | None:
        clean_id = fixture_id.removeprefix("api-football-")
        data = self._request("fixtures", params={"id": clean_id})
        res = data.get("response", [])
        return res[0] if res else None

    def get_team_last_matches(self, team_id: str, limit: int = 5) -> list[dict]:
        """Return up to five recent fixtures enriched with canonical statistics.

        The regular ``team`` lookup does not always embed statistics and player
        performance. API-Sports can return those blocks for multiple fixture
        IDs in one request, so use one batch instead of issuing one request per
        match. If that optional enrichment fails, the base fixture history is
        still useful and is returned unchanged.
        """
        bounded_limit = max(1, min(limit, 5))
        data = self._request("fixtures", params={"team": team_id, "last": str(bounded_limit)})
        raw_items = sorted(
            data.get("response", []),
            key=lambda item: str((item.get("fixture") or {}).get("date") or ""),
            reverse=True,
        )[:bounded_limit]

        fixture_ids = [
            str(fixture_id)
            for item in raw_items
            if (fixture_id := (item.get("fixture") or {}).get("id")) is not None
        ]
        needs_enrichment = any(
            not item.get("statistics") or not item.get("players")
            for item in raw_items
        )

        if raw_items and fixture_ids and needs_enrichment:
            try:
                batch = self._request("fixtures", params={"ids": "-".join(fixture_ids)})
                enriched_by_id = {
                    str(fixture_id): item
                    for item in batch.get("response", [])
                    if (fixture_id := (item.get("fixture") or {}).get("id")) is not None
                }
                raw_items = [
                    self._merge_payloads(
                        item,
                        enriched_by_id.get(str((item.get("fixture") or {}).get("id")), {}),
                    )
                    for item in raw_items
                ]
            except Exception as exc:
                logger.warning(
                    "No se pudo enriquecer el historial de API-Football; se conserva la respuesta base: %s",
                    exc,
                )

        return [self._normalize_history_payload(item) for item in raw_items]

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
            return 0
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
                        metrics[canonical_name] = cls._metric_value(metric.get("value"))
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
                            shots["total"] = cls._metric_value(raw_shots.get("total"))
                        if "on" in raw_shots:
                            shots["on_target"] = cls._metric_value(raw_shots.get("on"))
                        elif "on_target" in raw_shots:
                            shots["on_target"] = cls._metric_value(raw_shots.get("on_target"))
                        if shots:
                            item["shots"] = shots
                    raw_goals = statistic.get("goals")
                    if isinstance(raw_goals, dict) and "total" in raw_goals:
                        item["goals"] = {"total": cls._metric_value(raw_goals.get("total"))}
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

    def get_head_to_head(self, team1_id: str, team2_id: str, limit: int = 10) -> list[H2HMatchItem]:
        h2h_param = f"{team1_id}-{team2_id}"
        bounded_limit = max(1, min(limit, 10))
        data = self._request("fixtures/headtohead", params={"h2h": h2h_param, "last": str(bounded_limit)})
        return self.normalize_history(data.get("response", []), bounded_limit)

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
            injury_type = item.get("player", {}).get("type", "Lesión")

            items.append(
                InjuryItem(
                    player=player,
                    team=team,
                    reason=reason,
                    status="Baja confirmada",
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
            return LineupsSummary(confirmed=False, home=None, away=None)

        home_lineup: TeamLineup | None = None
        away_lineup: TeamLineup | None = None

        unmatched_lineups: list[TeamLineup] = []
        for idx, item in enumerate(response_list):
            team_data = item.get("team") or {}
            team_id = str(team_data["id"]) if team_data.get("id") is not None else None
            team_name = team_data.get("name", f"Equipo {idx+1}")
            formation = item.get("formation")
            coach = item.get("coach", {}).get("name")

            start_xi = [
                PlayerLineup(
                    id=p.get("player", {}).get("id"),
                    name=p.get("player", {}).get("name", "Jugador"),
                    number=p.get("player", {}).get("number"),
                    pos=p.get("player", {}).get("pos"),
                    grid=p.get("player", {}).get("grid"),
                )
                for p in item.get("startXI", [])
            ]

            substitutes = [
                PlayerLineup(
                    id=p.get("player", {}).get("id"),
                    name=p.get("player", {}).get("name", "Jugador"),
                    number=p.get("player", {}).get("number"),
                    pos=p.get("player", {}).get("pos"),
                    grid=p.get("player", {}).get("grid"),
                )
                for p in item.get("substitutes", [])
            ]

            t_lineup = TeamLineup(
                team_name=team_name,
                formation=formation,
                coach=coach,
                start_xi=start_xi,
                substitutes=substitutes,
            )

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

        return LineupsSummary(confirmed=True, home=home_lineup, away=away_lineup)

    @staticmethod
    def _normalize_status(short_status: str) -> str:
        status_map = {
            "NS": "PROGRAMADO",
            "TBD": "POR DEFINIR",
            "1H": "EN JUEGO (1T)",
            "HT": "ENTRETIEMPO",
            "2H": "EN JUEGO (2T)",
            "ET": "TIEMPO EXTRA",
            "P": "PENALES",
            "FT": "FINALIZADO",
            "AET": "FINALIZADO (ET)",
            "PEN": "FINALIZADO (PEN)",
            "PST": "POSPUESTO",
            "CANC": "CANCELADO",
            "ABD": "SUSPENDIDO",
        }
        return status_map.get(short_status.upper(), "PROGRAMADO")
