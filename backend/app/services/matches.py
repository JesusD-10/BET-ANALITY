from datetime import date, datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import time
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
from app.services.api_football import APIFootballProvider

logger = logging.getLogger(__name__)


class MockSportsDataProvider:
    """Datos demostrativos aislados para desarrollar la interfaz sin proveedor real."""

    def list_highlights(self, match_date: date | None = None) -> list[MatchSummary]:
        selected_date = match_date or date.today()
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
                odds_available=True,
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
        from_date = match_date.isoformat()
        to_date = match_date.isoformat()

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

_FIXTURE_CACHE_TTL_SECONDS = 30
_ANALYSIS_CACHE_TTL_SECONDS = 120
_fixture_cache: dict[str, tuple[float, list[MatchSummary]]] = {}
_analysis_cache: dict[tuple[str, bool], tuple[float, MatchAnalysisResponse]] = {}


def _cache_get(cache: dict, key: object, ttl: int):
    cached = cache.get(key)
    if cached is None:
        return None
    stored_at, value = cached
    if time.monotonic() - stored_at > ttl:
        cache.pop(key, None)
        return None
    return value


def _cache_set(cache: dict, key: object, value: object) -> None:
    cache[key] = (time.monotonic(), value)


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
    cache_key = f"{getattr(provider, 'provider_name', 'mock')}:{selected_date.isoformat()}"
    cached = _cache_get(_fixture_cache, cache_key, _FIXTURE_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    matches: list[MatchSummary]
    if isinstance(provider, APIFootballProvider):
        try:
            matches = provider.list_fixtures(selected_date)
        except Exception as exc:
            logger.error("Error al obtener partidos desde API-Football: %s. Se usa mock_provider.", exc)
            matches = mock_provider.list_highlights(selected_date)
    elif isinstance(provider, FootballDataProvider):
        try:
            matches = provider.list_fixtures(selected_date)
        except Exception as exc:
            logger.error("Error al obtener partidos desde FootballDataProvider: %s. Se usa mock_provider.", exc)
            matches = mock_provider.list_highlights(selected_date)
    else:
        matches = provider.list_highlights(selected_date)

    _cache_set(_fixture_cache, cache_key, matches)
    return matches


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



def _future_value(future: Future | None, default):
    if future is None:
        return default
    try:
        return future.result()
    except Exception as exc:
        logger.warning("Dato complementario no disponible: %s", exc)
        return default


def get_analysis(match_id: str, use_openai: bool = True) -> MatchAnalysisResponse | None:
    cache_key = (match_id, use_openai)
    cached = _cache_get(_analysis_cache, cache_key, _ANALYSIS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    if not use_openai:
        enriched_cached = _cache_get(_analysis_cache, (match_id, True), _ANALYSIS_CACHE_TTL_SECONDS)
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

    provider = _active_provider()
    if isinstance(provider, APIFootballProvider) and match.id.startswith("api-football-"):
        fixture_id = match.external_id or match.id.replace("api-football-", "")
        with ThreadPoolExecutor(max_workers=5) as executor:
            injuries_future = executor.submit(provider.get_fixture_injuries, fixture_id)
            lineups_future = executor.submit(provider.get_fixture_lineups, fixture_id)
            h2h_future = None
            home_future = None
            away_future = None
            if match.home_team_id and match.away_team_id:
                h2h_future = executor.submit(provider.get_head_to_head, match.home_team_id, match.away_team_id, 10)
                home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 10)
                away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 10)

            injuries = _future_value(injuries_future, [])
            lineups = _future_value(lineups_future, None)
            h2h_matches = _future_value(h2h_future, [])
            home_history = _future_value(home_future, [])
            away_history = _future_value(away_future, [])

    elif isinstance(provider, FootballDataProvider) and match.id.startswith("football-data-"):
        with ThreadPoolExecutor(max_workers=3) as executor:
            h2h_future = executor.submit(provider.get_head_to_head, match.id, 10)
            home_future = executor.submit(provider.get_team_last_matches, match.home_team_id, 10) if match.home_team_id else None
            away_future = executor.submit(provider.get_team_last_matches, match.away_team_id, 10) if match.away_team_id else None
            h2h_matches = _future_value(h2h_future, [])
            home_history = _future_value(home_future, [])
            away_history = _future_value(away_future, [])


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

    analysis = analyze_match_with_ai(
        match=match,
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        home_last_matches=home_history,
        away_last_matches=away_history,
        allow_openai=use_openai,
    )
    _cache_set(_analysis_cache, cache_key, analysis)
    return analysis


def _quick_analysis(match: MatchSummary) -> MatchAnalysisResponse:
    return analyze_match_with_ai(match=match, allow_openai=False)


def get_recommendations(limit: int | None = None) -> list[Recommendation]:
    """Build the daily simple list without N OpenAI/provider detail calls."""
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

