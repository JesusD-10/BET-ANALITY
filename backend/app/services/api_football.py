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
            odds_available=True,
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

    def get_team_last_matches(self, team_id: str, limit: int = 10) -> list[dict]:
        data = self._request("fixtures", params={"team": team_id, "last": str(limit)})
        return data.get("response", [])

    def get_head_to_head(self, team1_id: str, team2_id: str, limit: int = 10) -> list[H2HMatchItem]:
        h2h_param = f"{team1_id}-{team2_id}"
        data = self._request("fixtures/headtohead", params={"h2h": h2h_param, "last": str(limit)})
        results = []
        for item in data.get("response", []):
            fixture = item.get("fixture", {})
            league = item.get("league", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})

            date_raw = fixture.get("date", "")[:10]
            home = teams.get("home", {}).get("name", "Local")
            away = teams.get("away", {}).get("name", "Visitante")
            home_goals = goals.get("home") if goals.get("home") is not None else 0
            away_goals = goals.get("away") if goals.get("away") is not None else 0

            score_str = f"{home_goals} - {away_goals}"
            winner = None
            if home_goals > away_goals:
                winner = home
            elif away_goals > home_goals:
                winner = away
            else:
                winner = "Empate"

            results.append(
                H2HMatchItem(
                    date=date_raw,
                    competition=league.get("name", "Liga"),
                    home_team=home,
                    away_team=away,
                    score=score_str,
                    winner=winner,
                )
            )
        return results

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
