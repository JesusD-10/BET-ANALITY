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
    """Merge independently validated markets using adaptive provider quorum.

    A single successful completion may stand on its own. Once two or more
    providers return valid JSON, a selection needs at least two independent
    supporters. Opposing selections for one market are resolved by support;
    tied leaders are discarded instead of allowing routing order to decide.
    """

    provider_candidates: list[dict[str, tuple[str, dict, float]]] = []
    ordered_keys: list[str] = []
    for completion in completions:
        payload = completion.json_data or {}
        raw_markets = payload.get("markets", [])
        candidates: dict[str, tuple[str, dict, float]] = {}
        if not isinstance(raw_markets, list):
            provider_candidates.append(candidates)
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
            candidates[market_key] = (
                _selection_signature(raw_market.get("selection")),
                raw_market,
                probability,
            )
            if market_key not in ordered_keys:
                ordered_keys.append(market_key)
        provider_candidates.append(candidates)

    merged: list[dict] = []
    provider_count = len(completions)
    required_support = 1 if provider_count == 1 else 2
    for market_key in ordered_keys:
        selection_groups: dict[str, list[tuple[dict, float]]] = {}
        for candidates in provider_candidates:
            candidate = candidates.get(market_key)
            if candidate is None:
                continue
            selection, raw_market, probability = candidate
            selection_groups.setdefault(selection, []).append((raw_market, probability))

        eligible_groups = [
            supporters
            for supporters in selection_groups.values()
            if len(supporters) >= required_support
        ]
        if not eligible_groups:
            continue
        strongest_support = max(len(supporters) for supporters in eligible_groups)
        strongest_groups = [
            supporters
            for supporters in eligible_groups
            if len(supporters) == strongest_support
        ]
        if len(strongest_groups) != 1:
            # A 1-1 or 2-2 split is evidence of disagreement, not consensus.
            continue

        supporters = strongest_groups[0]
        base_market = supporters[0][0]
        estimates = [probability for _, probability in supporters]
        consensus_market = dict(base_market)
        consensus_market["market_key"] = market_key
        consensus_market["probability"] = _robust_probability(estimates)
        consensus_market["confidence"] = _consensus_confidence(
            support=len(supporters),
            provider_count=provider_count,
            estimates=estimates,
        )

        factors_for = _merge_text_items(supporters, "factors_for")
        risks = _merge_text_items(supporters, "risks")
        if factors_for:
            consensus_market["factors_for"] = factors_for
        else:
            consensus_market.pop("factors_for", None)
        if risks:
            consensus_market["risks"] = risks
        else:
            consensus_market.pop("risks", None)

        quality_estimates: list[float] = []
        for raw_market, _ in supporters:
            try:
                quality = float(raw_market.get("data_quality"))
            except (TypeError, ValueError):
                continue
            quality_estimates.append(max(0.0, min(1.0, quality)))
        if quality_estimates:
            consensus_market["data_quality"] = _robust_probability(quality_estimates)
        else:
            consensus_market.pop("data_quality", None)

        merged.append(consensus_market)
        if len(merged) >= max(1, limit):
            break
    return merged


def _selection_signature(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def _robust_probability(estimates: list[float]) -> float:
    """Aggregate up to four estimates while limiting outlier influence."""

    ordered = sorted(estimates)
    if not ordered:
        raise ValueError("Se requiere al menos una estimación")
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        return sum(ordered) / 2
    if len(ordered) == 3:
        return ordered[1]
    # The gateway caps analysis at four providers. Keeping this defensive slice
    # also behaves sensibly if a caller supplies more completions in a test.
    trimmed = ordered[1:-1]
    return sum(trimmed) / len(trimmed)


def _merge_text_items(
    supporters: list[tuple[dict, float]],
    field_name: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_market, _ in supporters:
        values = raw_market.get(field_name, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            clean_value = " ".join(value.split())
            signature = _selection_signature(clean_value)
            if not clean_value or signature in seen:
                continue
            seen.add(signature)
            merged.append(clean_value)
    return merged


def _consensus_confidence(
    *,
    support: int,
    provider_count: int,
    estimates: list[float],
) -> str:
    if support <= 1:
        return "Baja"
    dispersion = max(estimates) - min(estimates)
    coverage = support / max(1, provider_count)
    if support >= 3 and coverage >= 0.75 and dispersion <= 0.08:
        return "Alta"
    if coverage >= 0.50 and dispersion <= 0.12:
        return "Media-alta"
    return "Media"


def _bounded_data_quality(value: object, ceiling: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return ceiling
    return max(0.0, min(ceiling, parsed))


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
            home_history=home_history,
            away_history=away_history,
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
    injuries_text = "\n".join([f"- {inj.team}: {inj.player} ({inj.reason} - {inj.status})" for inj in injuries]) or "Sin datos verificados de bajas o lesionados para este análisis."
    
    lineup_text = "Sin alineaciones verificadas disponibles para este análisis."
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
                data_quality=_bounded_data_quality(
                    m.get("data_quality", match.data_quality),
                    match.data_quality,
                ),
                factors_for=m.get("factors_for", ["Análisis respaldado por forma reciente"]),
                risks=m.get("risks", ["Varianza estándar de partido"]),
            )
        )

    if not markets:
        return _generate_local_fallback_analysis(
            match,
            referee_info,
            injuries,
            lineups,
            h2h_matches,
            home_history,
            away_history,
        )

    if len(completions) > 1:
        provider_label = "+".join(completion.provider for completion in completions)
        model_version = f"multi-ai-consensus-{provider_label}"
        consensus_note = (
            f"Consenso multi-IA: participaron {len(completions)} proveedores válidos; "
            "cada selección publicada recibió al menos dos apoyos y su probabilidad "
            "se agregó de forma robusta."
        )
    else:
        model_version = (
            f"multi-ai-{primary_completion.provider}-{primary_completion.model}"
        )
        consensus_note = (
            "Análisis individual: participó 1 proveedor válido; no hubo consenso "
            "independiente disponible."
        )

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


def _numeric_goal(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else None
    return None


def _history_result(item: dict | H2HMatchItem) -> tuple[str, str, str | None, str | None, int, int] | None:
    """Normalize just the fields used by the local probability model."""

    if hasattr(item, "model_dump"):
        item = item.model_dump()
    if not isinstance(item, dict):
        return None

    teams = item.get("teams") or {}
    home = teams.get("home") or item.get("homeTeam") or {}
    away = teams.get("away") or item.get("awayTeam") or {}
    home_name = str(home.get("name") or item.get("home_team") or "").strip()
    away_name = str(away.get("name") or item.get("away_team") or "").strip()
    home_id = str(home["id"]) if isinstance(home, dict) and home.get("id") is not None else None
    away_id = str(away["id"]) if isinstance(away, dict) and away.get("id") is not None else None

    goals = item.get("goals") or ((item.get("score") or {}).get("fullTime") if isinstance(item.get("score"), dict) else None) or {}
    home_goals = _numeric_goal(goals.get("home")) if isinstance(goals, dict) else None
    away_goals = _numeric_goal(goals.get("away")) if isinstance(goals, dict) else None
    if home_goals is None or away_goals is None:
        raw_score = item.get("score")
        if isinstance(raw_score, str):
            score_parts = raw_score.replace("–", "-").split("-")
            if len(score_parts) == 2:
                home_goals = _numeric_goal(score_parts[0])
                away_goals = _numeric_goal(score_parts[1])

    if not home_name or not away_name or home_goals is None or away_goals is None:
        return None
    return home_name, away_name, home_id, away_id, home_goals, away_goals


def _team_form_profile(
    team_name: str,
    team_id: str | None,
    history: list[dict | H2HMatchItem],
    compact_form: str | None,
) -> tuple[float, int, float, float]:
    """Return points rate, sample size, goals for and goals against."""

    points = 0
    games = 0
    goals_for = 0
    goals_against = 0
    target_name = _selection_signature(team_name)
    target_id = str(team_id) if team_id is not None else None
    for raw_item in history[:10]:
        result = _history_result(raw_item)
        if result is None:
            continue
        home_name, away_name, home_id, away_id, home_goals, away_goals = result
        is_home = bool(
            (target_id is not None and home_id == target_id)
            or _selection_signature(home_name) == target_name
        )
        is_away = bool(
            (target_id is not None and away_id == target_id)
            or _selection_signature(away_name) == target_name
        )
        if is_home == is_away:
            continue
        scored, conceded = (home_goals, away_goals) if is_home else (away_goals, home_goals)
        games += 1
        goals_for += scored
        goals_against += conceded
        points += 3 if scored > conceded else 1 if scored == conceded else 0

    if games:
        return points / (games * 3), games, goals_for / games, goals_against / games

    form_tokens = [token.strip().upper() for token in (compact_form or "").replace(",", "-").split("-")]
    form_tokens = [token for token in form_tokens if token in {"W", "D", "L", "G", "E", "P"}]
    if form_tokens:
        form_points = sum(3 if token in {"W", "G"} else 1 if token in {"D", "E"} else 0 for token in form_tokens)
        return form_points / (len(form_tokens) * 3), len(form_tokens), 0.0, 0.0
    return 0.5, 0, 0.0, 0.0


def _goal_profile(
    *histories: list[dict | H2HMatchItem],
) -> tuple[int, float, float, float, float, float]:
    seen: set[tuple[str, str, str, int, int]] = set()
    totals: list[int] = []
    btts = 0
    for history in histories:
        for raw_item in history[:10]:
            result = _history_result(raw_item)
            if result is None:
                continue
            home_name, away_name, _, _, home_goals, away_goals = result
            serialized = raw_item.model_dump() if hasattr(raw_item, "model_dump") else raw_item
            fixture = serialized.get("fixture") or {} if isinstance(serialized, dict) else {}
            played_at = (
                fixture.get("date")
                or serialized.get("utcDate")
                or serialized.get("starting_at")
                or serialized.get("date")
                or ""
            )
            # API-Football histories use a full timestamp while normalized H2H
            # rows expose YYYY-MM-DD. Compare the calendar date so the same
            # fixture is not counted twice merely because it came through two
            # evidence paths.
            signature = (str(played_at)[:10], home_name, away_name, home_goals, away_goals)
            if signature in seen:
                continue
            seen.add(signature)
            totals.append(home_goals + away_goals)
            btts += int(home_goals > 0 and away_goals > 0)
    sample_size = len(totals)
    if not sample_size:
        return 0, 2.45, 0.74, 0.51, 0.70, 0.54
    # Four virtual league-average matches prevent tiny samples from producing
    # extreme probabilities while letting real recent scores move the model.
    return (
        sample_size,
        sum(totals) / sample_size,
        (sum(total >= 2 for total in totals) + 3.0) / (sample_size + 4),
        (sum(total >= 3 for total in totals) + 2.04) / (sample_size + 4),
        (sum(total <= 3 for total in totals) + 2.8) / (sample_size + 4),
        (btts + 2.16) / (sample_size + 4),
    )


def _clamp_probability(value: float, lower: float = 0.35, upper: float = 0.88) -> float:
    return round(max(lower, min(upper, value)), 3)


def _local_market(
    *,
    market_key: str,
    label: str,
    selection: str,
    probability: float,
    data_quality: float,
    factors_for: list[str],
    risks: list[str],
) -> MarketAnalysis:
    probability = _clamp_probability(probability)
    return MarketAnalysis(
        market_key=market_key,
        label=label,
        selection=selection,
        probability=probability,
        fair_odds=round(1.0 / probability, 2),
        best_odds=None,
        expected_value=None,
        confidence="Alta" if probability >= 0.75 and data_quality >= 0.7 else "Media-alta" if probability >= 0.64 else "Media",
        data_quality=data_quality,
        factors_for=factors_for,
        risks=risks,
    )


def _generate_local_fallback_analysis(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    injuries: list[InjuryItem],
    lineups: LineupsSummary | None,
    h2h_matches: list[H2HMatchItem],
    home_history: list[dict | H2HMatchItem] | None = None,
    away_history: list[dict | H2HMatchItem] | None = None,
) -> MatchAnalysisResponse:
    """Build match-specific markets from form and scores, with an honest prior."""

    home_history = home_history or []
    away_history = away_history or []
    referee_obj = referee_info or RefereeInfo(
        name=match.referee or "Sin designar",
        tendency="Sin métricas arbitrales verificadas",
    )
    home_rate, home_games, home_gf, home_ga = _team_form_profile(
        match.home_team,
        match.home_team_id,
        home_history,
        match.home_form,
    )
    away_rate, away_games, away_gf, away_ga = _team_form_profile(
        match.away_team,
        match.away_team_id,
        away_history,
        match.away_form,
    )
    goal_samples, avg_total, over_1_5, over_2_5, under_3_5, btts_yes = _goal_profile(
        home_history,
        away_history,
        h2h_matches,
    )

    evidence_samples = min(10, home_games + away_games + min(goal_samples, 4))
    data_quality = round(
        min(match.data_quality, 0.52 + evidence_samples * 0.035),
        2,
    )
    strength_gap = home_rate - away_rate
    scoring_gap = ((home_gf + away_ga) - (away_gf + home_ga)) / 4
    prefer_away = strength_gap < -0.08
    protected_probability = (
        0.65 + (-strength_gap * 0.20) - scoring_gap * 0.025
        if prefer_away
        else 0.68 + (strength_gap * 0.20) + scoring_gap * 0.025
    )
    protected_key = "DOUBLE_CHANCE_AWAY_DRAW" if prefer_away else "DOUBLE_CHANCE_HOME_DRAW"
    protected_team = match.away_team if prefer_away else match.home_team
    form_factor = (
        f"Forma reciente: {match.home_team} {home_rate * 100:.0f}% y "
        f"{match.away_team} {away_rate * 100:.0f}% de los puntos posibles"
        if home_games or away_games
        else "Sin forma reciente verificada; se aplica un prior conservador específico del cruce"
    )
    goal_factor = (
        f"{avg_total:.2f} goles totales por partido en {goal_samples} marcadores recientes únicos"
        if goal_samples
        else "Sin marcadores recientes verificados; la estimación se contrae al promedio basal"
    )

    btts_selection = "Sí" if btts_yes >= 0.5 else "No"
    btts_probability = btts_yes if btts_yes >= 0.5 else 1.0 - btts_yes
    goals_2_5_selection = "Más de 2.5 goles" if over_2_5 >= 0.5 else "Menos de 2.5 goles"
    goals_2_5_key = "TOTAL_GOALS_OVER_2_5" if over_2_5 >= 0.5 else "TOTAL_GOALS_UNDER_2_5"
    goals_2_5_probability = over_2_5 if over_2_5 >= 0.5 else 1.0 - over_2_5

    candidates = [
        _local_market(
            market_key=protected_key,
            label="Doble oportunidad",
            selection=f"{protected_team} o empate",
            probability=protected_probability,
            data_quality=data_quality,
            factors_for=[form_factor, "La doble oportunidad cubre también el empate"],
            risks=[f"Una derrota de {protected_team} invalida la selección"],
        ),
        _local_market(
            market_key="TOTAL_GOALS_OVER_1_5",
            label="Total de goles",
            selection="Más de 1.5 goles",
            probability=over_1_5,
            data_quality=data_quality,
            factors_for=[goal_factor],
            risks=["Un partido cerrado o con baja eficacia puede quedar por debajo de dos goles"],
        ),
        _local_market(
            market_key="TOTAL_GOALS_UNDER_3_5",
            label="Total de goles",
            selection="Menos de 3.5 goles",
            probability=under_3_5,
            data_quality=data_quality,
            factors_for=[goal_factor],
            risks=["Un gol temprano puede abrir el partido y elevar el marcador"],
        ),
        _local_market(
            market_key="BOTH_TEAMS_TO_SCORE",
            label="Ambos equipos anotan",
            selection=btts_selection,
            probability=btts_probability,
            data_quality=data_quality,
            factors_for=[goal_factor],
            risks=["La selección depende de la eficacia de ambos ataques y defensas"],
        ),
        _local_market(
            market_key=goals_2_5_key,
            label="Total de goles",
            selection=goals_2_5_selection,
            probability=goals_2_5_probability,
            data_quality=data_quality,
            factors_for=[goal_factor],
            risks=["La línea de 2.5 tiene mayor varianza que los umbrales protegidos"],
        ),
    ]
    markets = sorted(
        candidates,
        key=lambda market: (market.probability * market.data_quality, market.probability),
        reverse=True,
    )

    return MatchAnalysisResponse(
        match=match,
        model_version="baseline-poisson-v0.3",
        updated_at=datetime.now(timezone.utc),
        referee_info=referee_obj,
        injuries=injuries,
        lineups=lineups,
        h2h_matches=h2h_matches,
        tactical_summary=(
            f"{match.home_team} vs {match.away_team}: la lectura local compara forma, "
            f"producción de goles y {goal_samples} marcadores recientes únicos."
        ),
        injuries_impact=f"Se identifican {len(injuries)} bajas en las plantillas.",
        referee_impact=(
            f"Árbitro {referee_obj.name}: registra {referee_obj.yellow_cards_avg} amarillas promedio."
            if referee_obj.yellow_cards_avg is not None
            else f"Árbitro {referee_obj.name}: sin métricas verificadas para recomendar mercados de tarjetas."
        ),
        markets=markets,
        notes=[
            f"Análisis adaptativo por partido con {evidence_samples} muestras de forma/goles disponibles.",
            "Cuota justa calculada inversamente a la probabilidad estimada.",
            "best_odds y EV permanecen vacíos cuando no existe una cotización verificada.",
            "Córners, remates y mercados de jugador se omiten cuando la API no aporta estadísticas específicas.",
        ],
    )
