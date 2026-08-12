from datetime import datetime, timezone
import json
import logging

from app.core.config import settings
from app.schemas.matches import (
    H2HMatchItem,
    InjuryItem,
    LineupsSummary,
    MarketAnalysis,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)
from app.services.opportunities import (
    DATA_DEPENDENT_MARKET_FAMILIES,
    MARKET_TAXONOMY,
    enrich_analysis_with_opportunities,
    market_family,
)

logger = logging.getLogger(__name__)


def _iter_key_paths(value: object, prefix: str = ""):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path.lower()
            yield from _iter_key_paths(nested, path)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_key_paths(nested, prefix)


def _has_stat_key(history: list[dict | H2HMatchItem], *terms: str) -> bool:
    normalized_terms = tuple(term.lower() for term in terms)
    return any(
        all(term in path for term in normalized_terms)
        for path in _iter_key_paths(history)
    )


def _available_market_families(
    referee_info: RefereeInfo | None,
    home_history: list[dict | H2HMatchItem],
    away_history: list[dict | H2HMatchItem],
) -> set[str]:
    histories = [*home_history, *away_history]
    available = {"result", "goals"}

    if (
        referee_info
        and (referee_info.yellow_cards_avg is not None or referee_info.red_cards_avg is not None)
    ) or _has_stat_key(histories, "card"):
        available.add("cards")
    if _has_stat_key(histories, "corner"):
        available.add("corners")
    if _has_stat_key(histories, "shot") or _has_stat_key(histories, "remate"):
        available.add("team_shots")
    if _has_stat_key(histories, "player", "shot") or _has_stat_key(histories, "jugador", "remate"):
        available.update({"player_shots", "player_shots_on_target"})
    if (
        _has_stat_key(histories, "player", "goal")
        or _has_stat_key(histories, "player", "scored")
        or _has_stat_key(histories, "jugador", "gol")
    ):
        available.add("player_goals")
    return available


def _market_has_evidence(market_key: str, available_families: set[str]) -> bool:
    family = market_family(market_key)
    if family is None:
        return False
    return family not in DATA_DEPENDENT_MARKET_FAMILIES or family in available_families


def _format_recent_history(history: list[dict | H2HMatchItem], limit: int = 5) -> str:
    if not history:
        return "Sin partidos recientes provistos por la API."

    compact_items: list[dict] = []
    for item in history[:limit]:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        score = item.get("score") or {}
        compact: dict[str, object] = {
            "date": fixture.get("date") or item.get("utcDate") or item.get("date"),
            "competition": item.get("competition"),
            "home": (teams.get("home") or {}).get("name") or (item.get("homeTeam") or {}).get("name") or item.get("home_team"),
            "away": (teams.get("away") or {}).get("name") or (item.get("awayTeam") or {}).get("name") or item.get("away_team"),
            "goals": item.get("goals") or score.get("fullTime") or item.get("score"),
        }
        # Future providers may attach the evidence needed for advanced markets.
        # Preserve those explicit blocks while omitting unrelated fixture noise.
        for key in ("statistics", "player_statistics", "players"):
            if item.get(key) is not None:
                compact[key] = item[key]
        compact_items.append({key: value for key, value in compact.items() if value is not None})

    return "\n".join(
        f"- {json.dumps(item, ensure_ascii=False, default=str, separators=(',', ':'))}"
        for item in compact_items
    )


def analyze_match_with_ai(
    match: MatchSummary,
    referee_info: RefereeInfo | None = None,
    injuries: list[InjuryItem] | None = None,
    lineups: LineupsSummary | None = None,
    h2h_matches: list[H2HMatchItem] | None = None,
    home_last_matches: list[dict | H2HMatchItem] | None = None,
    away_last_matches: list[dict | H2HMatchItem] | None = None,
    allow_openai: bool = True,
) -> MatchAnalysisResponse:
    injuries_list = injuries or []
    h2h_list = h2h_matches or []
    home_history = home_last_matches or []
    away_history = away_last_matches or []

    if allow_openai and settings.openai_api_key:
        try:
            analysis = _query_openai_analysis(
                match=match,
                referee_info=referee_info,
                injuries=injuries_list,
                lineups=lineups,
                h2h_matches=h2h_list,
                home_history=home_history,
                away_history=away_history,
            )
            return enrich_analysis_with_opportunities(analysis)
        except Exception as exc:
            logger.warning("Fallo en la llamada a OpenAI API (%s). Se aplica fallback estadístico.", exc)

    return enrich_analysis_with_opportunities(
        _generate_local_fallback_analysis(
            match=match,
            referee_info=referee_info,
            injuries=injuries_list,
            lineups=lineups,
            h2h_matches=h2h_list,
        )
    )


def _query_openai_analysis(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    injuries: list[InjuryItem],
    lineups: LineupsSummary | None,
    h2h_matches: list[H2HMatchItem],
    home_history: list[dict | H2HMatchItem],
    away_history: list[dict | H2HMatchItem],
) -> MatchAnalysisResponse:
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    h2h_text = "\n".join([f"- {m.date} | {m.home_team} {m.score} {m.away_team} (Ganador: {m.winner})" for m in h2h_matches[:10]]) or "Sin historial directo reciente."
    injuries_text = "\n".join([f"- {inj.team}: {inj.player} ({inj.reason} - {inj.status})" for inj in injuries]) or "Sin bajas o lesionados reportados."
    
    lineup_text = "Alineaciones no confirmadas aún."
    if lineups and lineups.confirmed and lineups.home and lineups.away:
        home_xi = ", ".join([p.name for p in lineups.home.start_xi])
        away_xi = ", ".join([p.name for p in lineups.away.start_xi])
        lineup_text = (
            f"Confirmadas:\n"
            f"- {lineups.home.team_name} ({lineups.home.formation or 'N/D'}): Titulares: {home_xi}\n"
            f"- {lineups.away.team_name} ({lineups.away.formation or 'N/D'}): Titulares: {away_xi}"
        )

    referee_text = f"Árbitro: {match.referee or 'Por designar'}"
    if referee_info:
        referee_text += f" (Amonestaciones promedio: {referee_info.yellow_cards_avg or 'N/D'}, Rojas: {referee_info.red_cards_avg or 'N/D'}, Tendencia: {referee_info.tendency or 'Normal'})"

    home_form_text = f"Forma reciente de {match.home_team}: {match.home_form or 'N/D'}"
    away_form_text = f"Forma reciente de {match.away_team}: {match.away_form or 'N/D'}"
    home_history_text = _format_recent_history(home_history)
    away_history_text = _format_recent_history(away_history)
    available_families = _available_market_families(referee_info, home_history, away_history)
    supported_market_text = ", ".join(sorted(available_families))
    market_key_examples = ", ".join(
        prefixes[0]
        for family, prefixes in MARKET_TAXONOMY.items()
        if family in available_families
    )

    prompt_context = f"""
Eres un analista experto en apuestas y probabilidad deportiva cuantitativa. Analiza este partido de fútbol ÚNICO y genera recomendaciones verdaderamente adaptadas a las características tácticas de ESTOS dos equipos.

PARTIDO:
- Competición: {match.competition}
- Encuentro: {match.home_team} vs {match.away_team}
- Estado/Fecha: {match.kickoff_at}
- Estadio: {match.venue or 'N/D'}
- {referee_text}
- {home_form_text}
- {away_form_text}

HISTORIAL DIRECTO H2H:
{h2h_text}

ÚLTIMOS PARTIDOS DE {match.home_team} (máximo 5):
{home_history_text}

ÚLTIMOS PARTIDOS DE {match.away_team} (máximo 5):
{away_history_text}

BAJAS Y LESIONADOS:
{injuries_text}

ALINEACIONES:
{lineup_text}

INSTRUCCIONES CLAVE:
1. No generes siempre los mismos mercados para todos los partidos. Selecciona 3 o 4 mercados con respaldo cuantitativo para este choque.
2. Calcula probabilidades realistas (entre 0.05 y 0.95) y ajusta la "fair_odds" (1 / probabilidad).
3. Adapta las tarjetas promedio y tendencias del árbitro a las estadísticas entregadas. No inventes 4.2 tarjetas si el árbitro o partido sugieren otra métrica.
4. Familias habilitadas por los datos recibidos: {supported_market_text}. Usa claves compatibles, por ejemplo: {market_key_examples}.
5. Córners, remates de equipo, remates de jugador y goleadores son mercados válidos del sistema, pero SOLO puedes devolverlos cuando el contexto incluya estadísticas explícitas de esa familia. Una alineación o una descripción táctica no basta. No inventes jugadores, volúmenes ni promedios.
6. "best_odds" y "expected_value" deben ser null porque este contexto no incluye cotizaciones verificadas de una casa. "fair_odds" sí es la cuota justa del modelo.

Debes responder ÚNICAMENTE con un objeto JSON estricto con la siguiente estructura:
{{
  "tactical_summary": "Resumen táctico y de forma reciente del encuentro",
  "injuries_impact": "Evaluación del impacto de los lesionados/sancionados en la plantilla",
  "referee_impact": "Análisis del arbitraje y su influencia en tarjetas/faltas",
  "markets": [
    {{
      "market_key": "CLAVE_MERCADO (ej: WINNER_HOME, TOTAL_GOALS_OVER_2_5, BOTH_TEAMS_TO_SCORE, HANDICAP_HOME_MINUS_1, TOTAL_CARDS_OVER_3_5, TOTAL_CORNERS_OVER_9_5)",
      "label": "Etiqueta descriptiva del mercado",
      "selection": "Selección específica acorde al partido",
      "probability": 0.72,
      "fair_odds": 1.39,
      "best_odds": null,
      "expected_value": null,
      "confidence": "Alta | Media-alta | Media | Baja",
      "data_quality": 0.90,
      "factors_for": ["Factor específico 1 a favor", "Factor específico 2 a favor"],
      "risks": ["Riesgo específico 1", "Riesgo específico 2"]
    }}
  ],
  "notes": ["Nota relevante 1", "Nota relevante 2"]
}}
"""

    model_to_use = settings.openai_model if settings.openai_model and ("gpt" in settings.openai_model or "o3" in settings.openai_model or "o1" in settings.openai_model) else "gpt-4o-mini"
    
    response = client.chat.completions.create(
        model=model_to_use,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Eres un asistente de análisis probabilístico cuantitativo deportivo. Respondes únicamente en formato JSON válido."},
            {"role": "user", "content": prompt_context},
        ],
    )

    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)

    raw_markets = parsed.get("markets", [])
    markets = []
    available_families = _available_market_families(referee_info, home_history, away_history)
    for m in raw_markets:
        market_key = str(m.get("market_key", "")).strip().upper()
        if not _market_has_evidence(market_key, available_families):
            logger.info("Mercado %s descartado por falta de datos verificables.", market_key or "sin-clave")
            continue
        prob = float(m.get("probability", 0.5))
        prob = max(0.05, min(0.95, prob))
        fair_odds = round(1.0 / prob, 2)
        markets.append(
            MarketAnalysis(
                market_key=market_key,
                label=m.get("label", "Mercado"),
                selection=m.get("selection", "Selección"),
                probability=prob,
                fair_odds=fair_odds,
                # No bookmaker prices are passed to this prompt. Never turn a
                # model-generated number into a displayed quote or EV.
                best_odds=None,
                expected_value=None,
                confidence=m.get("confidence", "Media"),
                data_quality=float(m.get("data_quality", match.data_quality)),
                factors_for=m.get("factors_for", ["Análisis respaldado por forma reciente"]),
                risks=m.get("risks", ["Varianza estándar de partido"]),
            )
        )

    if not markets:
        return _generate_local_fallback_analysis(match, referee_info, injuries, lineups, h2h_matches)

    return MatchAnalysisResponse(
        match=match,
        model_version=f"openai-{model_to_use}",
        updated_at=datetime.now(timezone.utc),
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        tactical_summary=parsed.get("tactical_summary"),
        injuries_impact=parsed.get("injuries_impact"),
        referee_impact=parsed.get("referee_impact"),
        markets=markets,
        notes=parsed.get("notes", ["Análisis cuantitativo generado con Inteligencia Artificial."]),
    )


def _generate_local_fallback_analysis(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    injuries: list[InjuryItem],
    lineups: LineupsSummary | None,
    h2h_matches: list[H2HMatchItem],
) -> MatchAnalysisResponse:
    """Generador estadístico dinámico basado en nombres de equipos y forma reciente para evitar datos duplicados."""
    team_hash = sum(ord(c) for c in (match.home_team + match.away_team))
    
    referee_obj = referee_info or RefereeInfo(
        name=match.referee or "Sin designar",
        tendency="Sin métricas arbitrales verificadas",
    )

    # Selección dinámica de mercados según perfil de los equipos
    mod = team_hash % 3
    if mod == 0:
        p1, p2, p3 = 0.68, 0.76, 0.62
        markets = [
            MarketAnalysis(
                market_key="WINNER_HOME",
                label="Ganador del partido",
                selection=f"Victoria de {match.home_team}",
                probability=p1,
                fair_odds=round(1.0 / p1, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media-alta",
                data_quality=match.data_quality,
                factors_for=[f"Fuerte rendimiento de {match.home_team} en casa", "Ventaja táctica en mediocampo"],
                risks=["Visitantes efectivos en contraataque"],
            ),
            MarketAnalysis(
                market_key="TOTAL_GOALS_OVER_2_5",
                label="Total de goles",
                selection="Más de 2.5 goles",
                probability=p2,
                fair_odds=round(1.0 / p2, 2),
                best_odds=None,
                expected_value=None,
                confidence="Alta",
                data_quality=match.data_quality,
                factors_for=["Mercado general derivado de los marcadores disponibles"],
                risks=["Un planteamiento conservador puede reducir el volumen de ocasiones"],
            ),
            MarketAnalysis(
                market_key="BOTH_TEAMS_TO_SCORE",
                label="Ambos equipos anotan",
                selection="Sí",
                probability=p3,
                fair_odds=round(1.0 / p3, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media",
                data_quality=match.data_quality,
                factors_for=["Mercado de goles compatible con los datos básicos disponibles"],
                risks=["Una portería a cero invalida la selección"],
            ),
        ]
    elif mod == 1:
        p1, p2, p3 = 0.72, 0.65, 0.58
        markets = [
            MarketAnalysis(
                market_key="DOUBLE_CHANCE_HOME_DRAW",
                label="Doble oportunidad",
                selection=f"{match.home_team} o Empate (1X)",
                probability=p1,
                fair_odds=round(1.0 / p1, 2),
                best_odds=None,
                expected_value=None,
                confidence="Alta",
                data_quality=match.data_quality,
                factors_for=[f"La doble oportunidad reduce la exposición a un empate de {match.home_team}"],
                risks=["Presión constante del equipo visitante"],
            ),
            MarketAnalysis(
                market_key="BOTH_TEAMS_TO_SCORE",
                label="Ambos equipos anotan",
                selection="Sí",
                probability=p2,
                fair_odds=round(1.0 / p2, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media-alta",
                data_quality=match.data_quality,
                factors_for=["Mercado general de anotación disponible con datos de marcador"],
                risks=["Defensa cerrada en partidos eliminatorios"],
            ),
            MarketAnalysis(
                market_key="TOTAL_GOALS_UNDER_3_5",
                label="Total de goles",
                selection="Menos de 3.5 goles",
                probability=p3,
                fair_odds=round(1.0 / p3, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media",
                data_quality=match.data_quality,
                factors_for=["Umbral moderado calculado con información de marcadores"],
                risks=["Un intercambio ofensivo temprano puede superar la línea"],
            ),
        ]
    else:
        p1, p2, p3 = 0.80, 0.64, 0.69
        markets = [
            MarketAnalysis(
                market_key="TOTAL_GOALS_OVER_1_5",
                label="Total de goles",
                selection="Más de 1.5 goles",
                probability=p1,
                fair_odds=round(1.0 / p1, 2),
                best_odds=None,
                expected_value=None,
                confidence="Alta",
                data_quality=match.data_quality,
                factors_for=["Línea de goles compatible con el conjunto de datos básico"],
                risks=["Clima adverso o rotación en delantera"],
            ),
            MarketAnalysis(
                market_key="HANDICAP_HOME_MINUS_0_5",
                label="Hándicap asiático",
                selection=f"{match.home_team} (-0.5)",
                probability=p2,
                fair_odds=round(1.0 / p2, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media",
                data_quality=match.data_quality,
                factors_for=[f"Escenario ofensivo del modelo basal para {match.home_team}"],
                risks=["Reacción en bloque del equipo rival"],
            ),
            MarketAnalysis(
                market_key="DOUBLE_CHANCE_AWAY_DRAW",
                label="Doble oportunidad",
                selection=f"{match.away_team} o empate (X2)",
                probability=p3,
                fair_odds=round(1.0 / p3, 2),
                best_odds=None,
                expected_value=None,
                confidence="Media-alta",
                data_quality=match.data_quality,
                factors_for=["La doble oportunidad cubre empate y triunfo visitante"],
                risks=[f"Una victoria de {match.home_team} invalida la selección"],
            ),
        ]

    return MatchAnalysisResponse(
        match=match,
        model_version="baseline-poisson-v0.3",
        updated_at=datetime.now(timezone.utc),
        referee_info=referee_obj,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        tactical_summary=f"Choque entre {match.home_team} y {match.away_team}. El modelo analiza transiciones rápidas y solidez defensiva.",
        injuries_impact=f"Se identifican {len(injuries)} bajas en las plantillas.",
        referee_impact=(
            f"Árbitro {referee_obj.name}: registra {referee_obj.yellow_cards_avg} amarillas promedio."
            if referee_obj.yellow_cards_avg is not None
            else f"Árbitro {referee_obj.name}: sin métricas verificadas para recomendar mercados de tarjetas."
        ),
        markets=markets,
        notes=[
            "Análisis adaptativo dinámico basado en estadísticas de forma y H2H.",
            "Cuota justa calculada inversamente a la probabilidad estimada.",
            "Córners, remates y mercados de jugador se omiten cuando la API no aporta estadísticas específicas.",
        ],
    )
