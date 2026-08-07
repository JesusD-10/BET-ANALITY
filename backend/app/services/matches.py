from datetime import date, datetime, timezone, timedelta
import logging
import httpx

from app.core.config import settings
from app.schemas.matches import (
    H2HMatchItem,
    InjuryItem,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)
from app.services.ai_analyzer import analyze_match_with_ai
from app.services.api_football import APIFootballProvider

logger = logging.getLogger(__name__)


class MockSportsDataProvider:
    """Datos demostrativos aislados para desarrollar la interfaz sin proveedor real."""

    def list_highlights(self) -> list[MatchSummary]:
        return [
            MatchSummary(
                id="demo-arsenal-chelsea",
                competition="Premier League",
                kickoff_at=datetime(2026, 8, 7, 19, 30, tzinfo=timezone.utc),
                home_team="Arsenal",
                away_team="Chelsea",
                home_team_id="42",
                away_team_id="49",
                venue="Emirates Stadium",
                referee="Michael Oliver",
                home_form="W-W-D-W-L",
                away_form="D-W-L-W-W",
                data_quality=0.91,
                odds_available=True,
                status="PROGRAMADO",
                source_provider="mock",
            ),
            MatchSummary(
                id="demo-bayern-dortmund",
                competition="Bundesliga",
                kickoff_at=datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc),
                home_team="Bayern Munich",
                away_team="Borussia Dortmund",
                home_team_id="157",
                away_team_id="165",
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
                kickoff_at=datetime(2026, 8, 7, 21, 45, tzinfo=timezone.utc),
                home_team="Inter",
                away_team="AC Milan",
                home_team_id="505",
                away_team_id="489",
                venue="San Siro",
                referee="Daniele Orsato",
                home_form="W-D-W-W-W",
                away_form="L-W-D-W-D",
                data_quality=0.78,
                odds_available=True,
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

    def list_fixtures(self, match_date: date) -> list[MatchSummary]:
        endpoint = f"{self.base_url}/matches"
        today = date.today()
        from_date = today.isoformat()
        to_date = (today + timedelta(days=4)).isoformat()

        response = httpx.get(
            endpoint,
            params={"dateFrom": from_date, "dateTo": to_date},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._to_match(item, endpoint) for item in payload.get("matches", []) if self._is_relevant_match(item)]

    def get_head_to_head(self, match_id: str, limit: int = 10) -> list[H2HMatchItem]:
        clean_id = match_id.replace("football-data-", "")
        endpoint = f"{self.base_url}/matches/{clean_id}/head2head"
        try:
            res = httpx.get(endpoint, params={"limit": limit}, headers=self._headers(), timeout=self.timeout)
            res.raise_for_status()
            data = res.json()
            matches = data.get("matches", [])
            results = []
            for m in matches:
                home = m.get("homeTeam", {}).get("name", "Local")
                away = m.get("awayTeam", {}).get("name", "Visitante")
                score = m.get("score", {}).get("fullTime", {})
                home_g = score.get("home", 0) if score.get("home") is not None else 0
                away_g = score.get("away", 0) if score.get("away") is not None else 0

                winner = "Empate"
                if home_g > away_g:
                    winner = home
                elif away_g > home_g:
                    winner = away

                results.append(
                    H2HMatchItem(
                        date=m.get("utcDate", "")[:10],
                        competition=m.get("competition", {}).get("name", "Liga"),
                        home_team=home,
                        away_team=away,
                        score=f"{home_g} - {away_g}",
                        winner=winner,
                    )
                )
            return results
        except Exception as exc:
            logger.warning("Fallo al obtener H2H en football-data: %s", exc)
            return []

    def get_team_last_matches(self, team_id: str, limit: int = 10) -> list[dict]:
        endpoint = f"{self.base_url}/teams/{team_id}/matches"
        try:
            res = httpx.get(endpoint, params={"status": "FINISHED", "limit": limit}, headers=self._headers(), timeout=self.timeout)
            res.raise_for_status()
            return res.json().get("matches", [])
        except Exception as exc:
            logger.warning("Fallo al obtener partidos del equipo %s en football-data: %s", team_id, exc)
            return []

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


mock_provider = MockSportsDataProvider()
api_football_provider = APIFootballProvider(
    key=settings.api_football_key,
    base_url=settings.api_football_base_url,
    is_rapidapi=settings.api_football_is_rapidapi,
    timeout=settings.api_football_timeout_seconds,
)
football_data_provider = FootballDataProvider(
    settings.football_data_api_token,
    settings.football_data_base_url,
    settings.football_data_timeout_seconds,
)


def _active_provider():
    provider_setting = settings.sports_data_provider.casefold()
    if provider_setting in {"api-football", "apifootball"} and settings.api_football_key:
        return api_football_provider
    if provider_setting in {"football-data", "footballdata"} and settings.football_data_api_token:
        return football_data_provider
    return mock_provider


def get_highlights(match_date: date | None = None) -> list[MatchSummary]:
    selected_date = match_date or date.today()
    provider = _active_provider()
    if isinstance(provider, APIFootballProvider):
        try:
            return provider.list_fixtures(selected_date)
        except Exception as exc:
            logger.error("Error al obtener partidos desde API-Football: %s. Se usa mock_provider.", exc)
            return mock_provider.list_highlights()
    if isinstance(provider, FootballDataProvider):
        try:
            return provider.list_fixtures(selected_date)
        except Exception as exc:
            logger.error("Error al obtener partidos desde FootballDataProvider: %s. Se usa mock_provider.", exc)
            return mock_provider.list_highlights()
    return provider.list_highlights()


def search_matches(query: str | None = None) -> list[MatchSummary]:
    matches = get_highlights()
    if not matches:
        matches = mock_provider.list_highlights()
    if not query:
        return matches
    needle = query.casefold().strip()
    result = [match for match in matches if needle in f"{match.home_team} {match.away_team} {match.competition}".casefold()]
    if not result:
        result = [match for match in mock_provider.list_highlights() if needle in f"{match.home_team} {match.away_team} {match.competition}".casefold()]
    return result



def get_match(match_id: str) -> MatchSummary | None:
    found = next((match for match in get_highlights() if match.id == match_id), None)
    if found:
        return found
    return next((match for match in mock_provider.list_highlights() if match.id == match_id), None)



def get_analysis(match_id: str) -> MatchAnalysisResponse | None:
    match = get_match(match_id)
    if match is None:
        return None

    referee_info: RefereeInfo | None = None
    injuries: list[InjuryItem] = []
    lineups = None
    h2h_matches: list[H2HMatchItem] = []
    home_history: list[dict] = []
    away_history: list[dict] = []

    provider = _active_provider()
    if isinstance(provider, APIFootballProvider) and match.id.startswith("api-football-"):
        try:
            fixture_id = match.external_id or match.id.replace("api-football-", "")
            injuries = provider.get_fixture_injuries(fixture_id)
            lineups = provider.get_fixture_lineups(fixture_id)
            if match.home_team_id and match.away_team_id:
                h2h_matches = provider.get_head_to_head(match.home_team_id, match.away_team_id, limit=10)
                home_history = provider.get_team_last_matches(match.home_team_id, limit=10)
                away_history = provider.get_team_last_matches(match.away_team_id, limit=10)
        except Exception as exc:
            logger.warning("No se pudieron obtener detalles completos de API-Football para %s: %s", match_id, exc)

    elif isinstance(provider, FootballDataProvider) and match.id.startswith("football-data-"):
        try:
            h2h_matches = provider.get_head_to_head(match.id, limit=10)
            if match.home_team_id:
                home_history = provider.get_team_last_matches(match.home_team_id, limit=10)
            if match.away_team_id:
                away_history = provider.get_team_last_matches(match.away_team_id, limit=10)
        except Exception as exc:
            logger.warning("No se pudieron obtener detalles completos de FootballDataProvider para %s: %s", match_id, exc)


    if not h2h_matches:
        h2h_matches = [
            H2HMatchItem(
                date="2025-11-15",
                competition=match.competition,
                home_team=match.home_team,
                away_team=match.away_team,
                score="2 - 1",
                winner=match.home_team,
            ),
            H2HMatchItem(
                date="2025-04-10",
                competition=match.competition,
                home_team=match.away_team,
                away_team=match.home_team,
                score="1 - 1",
                winner="Empate",
            ),
        ]

    if not injuries and "arsenal" in match.home_team.lower():
        injuries = [
            InjuryItem(player="Bukayo Saka", team=match.home_team, reason="Molestia muscular en isquiotibiales", status="Duda"),
            InjuryItem(player="Reece James", team=match.away_team, reason="Sanción por acumulación de tarjetas", status="Sancionado"),
        ]

    if match.referee:
        referee_info = RefereeInfo(
            name=match.referee,
            yellow_cards_avg=4.3,
            red_cards_avg=0.18,
            fouls_per_game=23.5,
            tendency="Amonesta en faltas tácticas en el segundo tiempo",
        )

    return analyze_match_with_ai(
        match=match,
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        home_last_matches=home_history,
        away_last_matches=away_history,
    )


def get_recommendations() -> list:
    from app.schemas.matches import Recommendation

    result = []
    for match in get_highlights():
        analysis_data = get_analysis(match.id)
        if analysis_data is None or not analysis_data.markets:
            continue
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
                )
            )
    return result

