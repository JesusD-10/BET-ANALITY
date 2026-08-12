from datetime import date, datetime, timezone
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

    def __init__(self, key: str, base_url: str = "https://v3.football.api-sports.io", is_rapidapi: bool = False, timeout: int = 2) -> None:
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
        """Keep at most five raw fixtures so richer provider statistics remain available to analysis."""
        bounded_limit = max(1, min(limit, 5))
        data = self._request("fixtures", params={"team": team_id, "last": str(bounded_limit)})
        raw_items = data.get("response", [])
        return sorted(
            raw_items,
            key=lambda item: str((item.get("fixture") or {}).get("date") or ""),
            reverse=True,
        )[:bounded_limit]

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

    def get_fixture_lineups(self, fixture_id: str) -> LineupsSummary:
        clean_id = fixture_id.replace("api-football-", "")
        data = self._request("lineups", params={"fixture": clean_id})
        response_list = data.get("response", [])

        if not response_list:
            return LineupsSummary(confirmed=False, home=None, away=None)

        home_lineup: TeamLineup | None = None
        away_lineup: TeamLineup | None = None

        for idx, item in enumerate(response_list):
            team_name = item.get("team", {}).get("name", f"Equipo {idx+1}")
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

            if idx == 0:
                home_lineup = t_lineup
            else:
                away_lineup = t_lineup

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
