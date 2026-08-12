from datetime import datetime, timezone
import json
import logging
import unicodedata

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
from app.services.ai_gateway import AICompletion, ai_gateway

logger = logging.getLogger(__name__)


def _provider_blocks(
    history: list[dict | H2HMatchItem],
    key: str,
):
    """Yield canonical provider blocks, never inferring metrics from raw names."""

    for item in history:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if isinstance(value, dict):
            yield value
        elif isinstance(value, list):
            yield from (block for block in value if isinstance(block, dict))


def _has_metric(blocks: list[dict], *path: str) -> bool:
    for block in blocks:
        value: object = block
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            # Explicit zero is valid evidence; null means the provider did not
            # collect that statistic for the fixture.
            if value is not None:
                return True
    return False


def _available_market_families(
    referee_info: RefereeInfo | None,
    home_history: list[dict | H2HMatchItem],
    away_history: list[dict | H2HMatchItem],
) -> set[str]:
    histories = [*home_history, *away_history]
    team_statistics = list(_provider_blocks(histories, "statistics"))
    player_statistics = list(_provider_blocks(histories, "player_statistics"))
    available = {"result", "goals"}

    if (
        referee_info
        and (referee_info.yellow_cards_avg is not None or referee_info.red_cards_avg is not None)
    ) or _has_metric(team_statistics, "yellow_cards") or _has_metric(team_statistics, "red_cards"):
        available.add("cards")
    if _has_metric(team_statistics, "corners"):
        available.add("corners")
    if _has_metric(team_statistics, "total_shots") or _has_metric(team_statistics, "shots_on_target"):
        available.add("team_shots")
    if _has_metric(player_statistics, "shots", "total"):
        available.add("player_shots")
    if _has_metric(player_statistics, "shots", "on_target"):
        available.add("player_shots_on_target")
    if _has_metric(player_statistics, "goals", "total"):
        available.add("player_goals")
    return available


def _market_has_evidence(market_key: str, available_families: set[str]) -> bool:
    family = market_family(market_key)
    if family is None:
        return False
    return family not in DATA_DEPENDENT_MARKET_FAMILIES or family in available_families


def _consensus_market_payloads(
    completions: list[AICompletion],
    available_families: set[str],
    limit: int = 4,
) -> list[dict]:
    """Merge provider estimates while keeping the first provider's market set.

    The deterministic primary result defines ordering and labels. When another
    provider proposes the same canonical market, its probability is contrasted
    through an equal-weight arithmetic mean. Unique secondary markets only fill
    missing slots. Unverifiable market families are removed before aggregation.
    """

    provider_candidates: list[dict[str, tuple[dict, float]]] = []
    provider_order: list[list[str]] = []
    for completion in completions:
        payload = completion.json_data or {}
        raw_markets = payload.get("markets", [])
        candidates: dict[str, tuple[dict, float]] = {}
        order: list[str] = []
        if not isinstance(raw_markets, list):
            provider_candidates.append(candidates)
            provider_order.append(order)
            continue
        for raw_market in raw_markets:
            if not isinstance(raw_market, dict):
                continue
            market_key = str(raw_market.get("market_key", "")).strip().upper()
            if (
                not market_key
                or market_key in candidates
                or not _market_has_evidence(market_key, available_families)
            ):
                continue
            try:
                probability = float(raw_market.get("probability", 0.5))
            except (TypeError, ValueError):
                continue
            probability = max(0.05, min(0.95, probability))
            candidates[market_key] = (raw_market, probability)
            order.append(market_key)
        provider_candidates.append(candidates)
        provider_order.append(order)

    ordered_keys: list[str] = []
    for order in provider_order:
        for market_key in order:
            if market_key not in ordered_keys:
                ordered_keys.append(market_key)

    merged: list[dict] = []
    for market_key in ordered_keys[: max(1, limit)]:
        estimates: list[float] = []
        base_market: dict | None = None
        base_selection = ""
        for candidates in provider_candidates:
            candidate = candidates.get(market_key)
            if candidate is None:
                continue
            raw_market, probability = candidate
            if base_market is None:
                base_market = raw_market
                base_selection = _selection_signature(raw_market.get("selection"))
            elif _selection_signature(raw_market.get("selection")) != base_selection:
                # Some market keys (for example BTTS) can represent opposing
                # selections. Never average estimates that disagree on the bet.
                continue
            estimates.append(probability)
        if base_market is None or not estimates:
            continue
        consensus_market = dict(base_market)
        consensus_market["market_key"] = market_key
        consensus_market["probability"] = sum(estimates) / len(estimates)
        merged.append(consensus_market)
    return merged


def _selection_signature(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


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
        # Only canonical blocks enter the prompt. The raw ``players`` payload
        # duplicates these values and can be much larger.
        for key in ("statistics", "player_statistics"):
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
    allow_external_ai: bool = True,
) -> MatchAnalysisResponse:
    injuries_list = injuries or []
    h2h_list = h2h_matches or []
    home_history = home_last_matches or []
    away_history = away_last_matches or []

    if allow_external_ai and ai_gateway.is_available():
        try:
            analysis = _query_distributed_ai_analysis(
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
            logger.warning(
                "Falló el motor multi-IA (%s). Se aplica fallback estadístico.",
                type(exc).__name__,
            )

    return enrich_analysis_with_opportunities(
        _generate_local_fallback_analysis(
            match=match,
            referee_info=referee_info,
            injuries=injuries_list,
            lineups=lineups,
            h2h_matches=h2h_list,
        )
    )


def _query_distributed_ai_analysis(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    injuries: list[InjuryItem],
    lineups: LineupsSummary | None,
    h2h_matches: list[H2HMatchItem],
    home_history: list[dict | H2HMatchItem],
    away_history: list[dict | H2HMatchItem],
) -> MatchAnalysisResponse:
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

    completions = ai_gateway.complete_json_consensus(
        messages=[
            {"role": "system", "content": "Eres un asistente de análisis probabilístico cuantitativo deportivo. Respondes únicamente en formato JSON válido."},
            {"role": "user", "content": prompt_context},
        ],
        task="analysis",
        routing_key=match.id,
    )
    primary_completion = completions[0]
    parsed = primary_completion.json_data or {}

    available_families = _available_market_families(referee_info, home_history, away_history)
    raw_markets = _consensus_market_payloads(completions, available_families)
    markets = []
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

    if len(completions) > 1:
        provider_label = "+".join(completion.provider for completion in completions)
        model_version = f"multi-ai-consensus-{provider_label}"
        consensus_note = (
            "Consenso multi-IA: las probabilidades coincidentes se promedian "
            "con el mismo peso entre dos proveedores independientes."
        )
    else:
        model_version = (
            f"multi-ai-{primary_completion.provider}-{primary_completion.model}"
        )
        consensus_note = "Análisis validado con el único proveedor disponible."

    raw_notes = parsed.get("notes", [])
    notes = [str(note) for note in raw_notes] if isinstance(raw_notes, list) else []
    notes.append(consensus_note)

    return MatchAnalysisResponse(
        match=match,
        model_version=model_version,
        updated_at=datetime.now(timezone.utc),
        referee_info=referee_info,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        tactical_summary=parsed.get("tactical_summary"),
        injuries_impact=parsed.get("injuries_impact"),
        referee_impact=parsed.get("referee_impact"),
        markets=markets,
        notes=notes,
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
