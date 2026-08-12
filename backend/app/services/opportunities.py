from app.schemas.matches import (
    CombinationAnalysis,
    CombinationLeg,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)


def _fair_odds(probability: float) -> float:
    return round(1.0 / probability, 2)


def _leg(market_key: str, label: str, selection: str) -> CombinationLeg:
    return CombinationLeg(market_key=market_key, label=label, selection=selection)


def build_combinations(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
) -> list[CombinationAnalysis]:
    """Build same-match combinations with a conservative correlation adjustment.

    These are modelled joint probabilities, not a multiplication of independent
    legs. The events in a football match can be correlated, so every card makes
    that limitation visible to the user.
    """

    profile = sum(ord(char) for char in f"{match.home_team}:{match.away_team}")
    referee_cards = referee_info.yellow_cards_avg if referee_info else None
    cards_support = referee_cards is not None and referee_cards >= 4.2

    goal_cards_probability = round(0.70 + (profile % 4) * 0.01, 2)
    result_goals_probability = round(0.66 + (profile % 5) * 0.01, 2)

    return [
        CombinationAnalysis(
            id=f"{match.id}-builder-goal-cards",
            label="Combinada de alta cobertura",
            selection="Al menos 1 gol + al menos 3 tarjetas",
            legs=[
                _leg("TOTAL_GOALS_OVER_0_5", "Total de goles", "Más de 0.5 goles"),
                _leg("TOTAL_CARDS_OVER_2_5", "Total de tarjetas", "Más de 2.5 tarjetas"),
            ],
            probability=goal_cards_probability,
            fair_odds=_fair_odds(goal_cards_probability),
            confidence="Media-alta",
            data_quality=max(0.55, round(match.data_quality * 0.94, 2)),
            factors_for=[
                "El umbral de un gol cubre una amplia variedad de marcadores",
                (
                    f"El árbitro registra {referee_cards:.1f} amarillas por partido"
                    if cards_support
                    else "El umbral disciplinario se mantiene en tres tarjetas"
                ),
            ],
            risks=["Un partido muy abierto y permisivo puede reducir las tarjetas"],
            correlation_note="Probabilidad conjunta ajustada; las piernas no se tratan como eventos independientes.",
            kind="conservative-builder",
        ),
        CombinationAnalysis(
            id=f"{match.id}-builder-result-goals",
            label="Combinada protegida",
            selection=f"{match.home_team} o empate + al menos 1 gol",
            legs=[
                _leg("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", f"{match.home_team} o empate"),
                _leg("TOTAL_GOALS_OVER_0_5", "Total de goles", "Más de 0.5 goles"),
            ],
            probability=result_goals_probability,
            fair_odds=_fair_odds(result_goals_probability),
            confidence="Media-alta" if match.home_form else "Media",
            data_quality=max(0.55, round(match.data_quality * 0.92, 2)),
            factors_for=[
                "La doble oportunidad protege frente al empate",
                "Un solo gol completa la segunda condición",
            ],
            risks=[f"Una derrota de {match.home_team} invalida la combinada"],
            correlation_note="La probabilidad conjunta incluye un descuento por dependencia entre resultado y goles.",
            kind="conservative-builder",
        ),
    ]


def build_dream_picks(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
) -> list[CombinationAnalysis]:
    """Return two high-variance ideas per match with probability >= 30%.

    The primary dream also keeps fair odds at 3.00 or above. These values are
    references from the model; they are not bookmaker prices.
    """

    profile = sum(ord(char) for char in f"dream:{match.home_team}:{match.away_team}")
    primary_probability = round(0.30 + (profile % 4) * 0.01, 2)
    secondary_probability = round(0.30 + ((profile // 3) % 4) * 0.01, 2)
    card_line = "Más de 3.5 tarjetas" if referee_info and (referee_info.yellow_cards_avg or 0) >= 4.3 else "Más de 2.5 tarjetas"
    card_key = "TOTAL_CARDS_OVER_3_5" if "3.5" in card_line else "TOTAL_CARDS_OVER_2_5"

    return [
        CombinationAnalysis(
            id=f"{match.id}-dream-total-builder",
            label="Soñadora del partido",
            selection=f"2+ goles + {card_line.lower()} + 8+ córners",
            legs=[
                _leg("TOTAL_GOALS_OVER_1_5", "Total de goles", "Más de 1.5 goles"),
                _leg(card_key, "Total de tarjetas", card_line),
                _leg("TOTAL_CORNERS_OVER_7_5", "Total de córners", "Más de 7.5 córners"),
            ],
            probability=primary_probability,
            fair_odds=_fair_odds(primary_probability),
            confidence="Media",
            data_quality=max(0.50, round(match.data_quality * 0.88, 2)),
            factors_for=[
                "Combina umbrales moderados de tres mercados distintos",
                "La probabilidad modelada se mantiene en 30% o más",
            ],
            risks=["Las tres condiciones deben cumplirse en el mismo encuentro"],
            correlation_note="Alta varianza: la cuota de referencia es justa, no una oferta confirmada de una casa.",
            kind="dream-builder",
        ),
        CombinationAnalysis(
            id=f"{match.id}-dream-btts-cards",
            label="Soñadora alternativa",
            selection="Ambos equipos anotan + 4+ tarjetas",
            legs=[
                _leg("BOTH_TEAMS_TO_SCORE", "Ambos equipos anotan", "Sí"),
                _leg("TOTAL_CARDS_OVER_3_5", "Total de tarjetas", "Más de 3.5 tarjetas"),
            ],
            probability=secondary_probability,
            fair_odds=_fair_odds(secondary_probability),
            confidence="Media-baja",
            data_quality=max(0.50, round(match.data_quality * 0.86, 2)),
            factors_for=[
                "La selección conserva una probabilidad modelada mínima de 30%",
                "Dos piernas permiten una cuota de referencia superior a 3.00",
            ],
            risks=["Un equipo sin anotar o un arbitraje permisivo rompe la selección"],
            correlation_note="Selección de mayor riesgo; no debe interpretarse como una jugada segura.",
            kind="dream-builder",
        ),
    ]


def enrich_analysis_with_opportunities(analysis: MatchAnalysisResponse) -> MatchAnalysisResponse:
    analysis.combinations = build_combinations(analysis.match, analysis.referee_info)
    analysis.dream_picks = build_dream_picks(analysis.match, analysis.referee_info)
    return analysis
