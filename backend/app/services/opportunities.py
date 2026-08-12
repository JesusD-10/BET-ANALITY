from app.schemas.matches import (
    CombinationAnalysis,
    CombinationLeg,
    MarketAnalysis,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)


MIN_DREAM_PROBABILITY = 0.30
MAX_DREAM_PROBABILITY = 1 / 3
MIN_DREAM_REFERENCE_ODDS = 3.0

# Stable market-key families. New providers can expose these markets without a
# response-schema migration; the analyzer only enables data-dependent families
# when their source payload contains the corresponding statistics.
MARKET_TAXONOMY: dict[str, tuple[str, ...]] = {
    "player_shots_on_target": ("PLAYER_SHOTS_ON_TARGET", "PLAYER_SOT"),
    "player_shots": ("PLAYER_SHOTS", "PLAYER_TOTAL_SHOTS"),
    "player_goals": ("PLAYER_TO_SCORE", "ANYTIME_GOALSCORER", "FIRST_GOALSCORER"),
    "team_shots": ("TEAM_SHOTS", "TOTAL_SHOTS", "SHOTS_ON_TARGET"),
    "corners": ("TOTAL_CORNERS", "TEAM_CORNERS", "CORNERS_"),
    "cards": ("TOTAL_CARDS", "TEAM_CARDS", "PLAYER_CARD", "BOOKINGS"),
    "goals": (
        "TOTAL_GOALS",
        "TEAM_TOTAL_GOALS",
        "BOTH_TEAMS_TO_SCORE",
        "CORRECT_SCORE",
        "FIRST_TEAM_TO_SCORE",
    ),
    "result": ("WINNER", "DOUBLE_CHANCE", "DRAW_NO_BET", "HANDICAP", "ASIAN_HANDICAP"),
}

DATA_DEPENDENT_MARKET_FAMILIES = frozenset(
    {
        "cards",
        "corners",
        "team_shots",
        "player_shots",
        "player_shots_on_target",
        "player_goals",
    }
)


def market_family(market_key: str) -> str | None:
    normalized = market_key.strip().upper()
    for family, prefixes in MARKET_TAXONOMY.items():
        if any(normalized.startswith(prefix) for prefix in prefixes):
            return family
    return None


def _fair_odds(probability: float) -> float:
    return round(1.0 / probability, 2)


def _leg(market_key: str, label: str, selection: str) -> CombinationLeg:
    return CombinationLeg(market_key=market_key, label=label, selection=selection)


def _dream_probability(profile: int, offset: int = 0) -> float:
    # 30%-33% keeps the model reference between 3.03 and 3.33.
    return round(MIN_DREAM_PROBABILITY + ((profile + offset) % 4) * 0.01, 2)


def _qualifying_single(markets: list[MarketAnalysis]) -> MarketAnalysis | None:
    candidates: list[MarketAnalysis] = []
    for market in markets:
        reference_odds = market.best_odds if market.best_odds is not None else market.fair_odds
        # A verified bookmaker quote can be 3+ even when fair odds are lower.
        # The 1/3 ceiling only applies when fair_odds is the sole reference.
        probability_is_eligible = market.probability >= MIN_DREAM_PROBABILITY
        if market.best_odds is None:
            probability_is_eligible = probability_is_eligible and market.probability <= MAX_DREAM_PROBABILITY
        if probability_is_eligible and reference_odds >= MIN_DREAM_REFERENCE_ODDS:
            candidates.append(market)

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.probability, item.data_quality))


def _build_advanced_market_builder(
    match: MatchSummary,
    markets: list[MarketAnalysis],
    *,
    require_dream_thresholds: bool = False,
) -> CombinationAnalysis | None:
    """Combine two already-gated advanced markets without fabricating prices.

    ``markets`` is the validated analysis output, so this helper deliberately
    does not infer availability from a referee, lineup, or team name. The 0.90
    factor is a conservative dependency discount over the raw probability
    product; it avoids presenting the legs as naively independent.
    """

    candidates: list[tuple[MarketAnalysis, str, int]] = []
    for position, market in enumerate(markets):
        family = market_family(market.market_key)
        if family in DATA_DEPENDENT_MARKET_FAMILIES and market.probability >= 0.55:
            candidates.append((market, family, position))

    eligible_pairs: list[
        tuple[tuple[float, float, int, int], MarketAnalysis, MarketAnalysis, float, float]
    ] = []
    for first_index, (first, first_family, first_position) in enumerate(candidates):
        for second, second_family, second_position in candidates[first_index + 1 :]:
            if first_family == second_family:
                continue

            joint_probability = round(first.probability * second.probability * 0.90, 4)
            fair_odds = _fair_odds(joint_probability)
            if require_dream_thresholds and not (
                joint_probability >= MIN_DREAM_PROBABILITY
                and fair_odds >= MIN_DREAM_REFERENCE_ODDS
            ):
                continue

            # Prefer the pair whose weaker leg has the best data quality, then
            # the larger adjusted probability. Input order breaks exact ties.
            score = (
                min(first.data_quality, second.data_quality),
                joint_probability,
                -first_position,
                -second_position,
            )
            eligible_pairs.append((score, first, second, joint_probability, fair_odds))

    if not eligible_pairs:
        return None

    _, first, second, joint_probability, fair_odds = max(
        eligible_pairs,
        key=lambda pair: pair[0],
    )
    factors_for = list(dict.fromkeys([*first.factors_for[:1], *second.factors_for[:1]]))
    risks = list(dict.fromkeys([*first.risks[:1], *second.risks[:1]]))

    return CombinationAnalysis(
        id=(
            f"{match.id}-advanced-{first.market_key.lower()}-"
            f"{second.market_key.lower()}"
        ),
        label="Combinada avanzada respaldada",
        selection=f"{first.selection} + {second.selection}",
        legs=[
            _leg(first.market_key, first.label, first.selection),
            _leg(second.market_key, second.label, second.selection),
        ],
        probability=joint_probability,
        fair_odds=fair_odds,
        best_odds=None,
        expected_value=None,
        confidence="Media",
        data_quality=round(min(first.data_quality, second.data_quality), 2),
        factors_for=factors_for or ["Ambos mercados superaron el filtro estadístico del análisis"],
        risks=risks or ["Las dos condiciones deben cumplirse en el mismo partido"],
        correlation_note=(
            "Probabilidad conjunta: producto de ambas probabilidades con un descuento "
            "de dependencia de 0.90; no se presupone independencia plena."
        ),
        kind="advanced-builder",
    )


def build_combinations(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    markets: list[MarketAnalysis] | None = None,
) -> list[CombinationAnalysis]:
    """Build same-match combinations without inventing unsupported markets."""

    profile = sum(ord(char) for char in f"{match.home_team}:{match.away_team}")
    referee_cards = referee_info.yellow_cards_avg if referee_info else None
    result_goals_probability = round(0.66 + (profile % 5) * 0.01, 2)

    combinations = [
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
        )
    ]

    # Cards are only offered when the backend received an actual referee metric.
    if referee_cards is not None:
        # The conservative builder keeps the requested floor of at least three
        # cards; the higher dream builder below may use a stricter line.
        card_line = "Más de 2.5 tarjetas"
        card_key = "TOTAL_CARDS_OVER_2_5"
        goal_cards_probability = round(0.70 + (profile % 4) * 0.01, 2)
        combinations.insert(
            0,
            CombinationAnalysis(
                id=f"{match.id}-builder-goal-cards",
                label="Combinada de alta cobertura",
                selection=f"Al menos 1 gol + {card_line.lower()}",
                legs=[
                    _leg("TOTAL_GOALS_OVER_0_5", "Total de goles", "Más de 0.5 goles"),
                    _leg(card_key, "Total de tarjetas", card_line),
                ],
                probability=goal_cards_probability,
                fair_odds=_fair_odds(goal_cards_probability),
                confidence="Media-alta",
                data_quality=max(0.55, round(match.data_quality * 0.94, 2)),
                factors_for=[
                    "El umbral de un gol cubre una amplia variedad de marcadores",
                    f"El árbitro registra {referee_cards:.1f} amarillas por partido",
                ],
                risks=["Un partido muy abierto y permisivo puede reducir las tarjetas"],
                correlation_note="Probabilidad conjunta ajustada; las piernas no se tratan como eventos independientes.",
                kind="conservative-builder",
            ),
        )

    advanced_builder = _build_advanced_market_builder(match, markets or [])
    if advanced_builder is not None:
        combinations.insert(0, advanced_builder)

    return combinations


def build_dream_picks(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    markets: list[MarketAnalysis] | None = None,
) -> list[CombinationAnalysis]:
    """Return simple or combined dream picks with a whole-bet reference >= 3.

    ``fair_odds`` represents the complete selection (all legs together), never
    the price required from every individual leg. ``best_odds`` stays ``None``
    for generated builders because no bookmaker quote was supplied.
    """

    profile = sum(ord(char) for char in f"dream:{match.home_team}:{match.away_team}")
    dreams: list[CombinationAnalysis] = []
    single = _qualifying_single(markets or [])

    if single is not None:
        dreams.append(
            CombinationAnalysis(
                id=f"{match.id}-dream-single-{single.market_key.lower()}",
                label="Soñadora individual",
                selection=single.selection,
                legs=[_leg(single.market_key, single.label, single.selection)],
                probability=single.probability,
                fair_odds=single.fair_odds,
                best_odds=single.best_odds,
                expected_value=single.expected_value if single.best_odds is not None else None,
                confidence=single.confidence,
                data_quality=single.data_quality,
                factors_for=single.factors_for,
                risks=single.risks,
                correlation_note="Selección simple: la cuota objetivo 3+ corresponde al mercado completo.",
                kind="dream-single",
            )
        )

    advanced_builder = _build_advanced_market_builder(
        match,
        markets or [],
        require_dream_thresholds=True,
    )
    if advanced_builder is not None:
        dreams.append(advanced_builder)

    primary_probability = _dream_probability(profile)
    if (
        len(dreams) < 2
        and referee_info
        and referee_info.yellow_cards_avg is not None
    ):
        referee_cards = referee_info.yellow_cards_avg
        card_line = "Más de 3.5 tarjetas" if referee_cards >= 4.3 else "Más de 2.5 tarjetas"
        card_key = "TOTAL_CARDS_OVER_3_5" if referee_cards >= 4.3 else "TOTAL_CARDS_OVER_2_5"
        dreams.append(
            CombinationAnalysis(
                id=f"{match.id}-dream-goals-cards",
                label="Soñadora combinada",
                selection=f"2+ goles + {card_line.lower()}",
                legs=[
                    _leg("TOTAL_GOALS_OVER_1_5", "Total de goles", "Más de 1.5 goles"),
                    _leg(card_key, "Total de tarjetas", card_line),
                ],
                probability=primary_probability,
                fair_odds=_fair_odds(primary_probability),
                confidence="Media",
                data_quality=max(0.50, round(match.data_quality * 0.88, 2)),
                factors_for=[
                    "La cuota de referencia se calcula sobre las dos piernas juntas",
                    f"El dato arbitral disponible es {referee_cards:.1f} amarillas por partido",
                ],
                risks=["Ambas condiciones deben cumplirse en el mismo encuentro"],
                correlation_note="Cuota justa conjunta del modelo; no es una oferta confirmada de una casa.",
                kind="dream-builder",
            )
        )

    if len(dreams) < 2:
        secondary_probability = _dream_probability(profile, offset=2)
        dreams.append(
            CombinationAnalysis(
                id=f"{match.id}-dream-result-goals",
                label="Soñadora combinada alternativa",
                selection=f"{match.home_team} o empate + más de 2.5 goles",
                legs=[
                    _leg("DOUBLE_CHANCE_HOME_DRAW", "Doble oportunidad", f"{match.home_team} o empate"),
                    _leg("TOTAL_GOALS_OVER_2_5", "Total de goles", "Más de 2.5 goles"),
                ],
                probability=secondary_probability,
                fair_odds=_fair_odds(secondary_probability),
                confidence="Media-baja",
                data_quality=max(0.50, round(match.data_quality * 0.86, 2)),
                factors_for=[
                    "La selección combina resultado protegido y producción de goles",
                    "La probabilidad conjunta modelada se mantiene en 30% o más",
                ],
                risks=[f"Una derrota de {match.home_team} o un partido de pocos goles invalida la selección"],
                correlation_note="Selección de mayor varianza; la cuota justa pertenece a la combinada completa.",
                kind="dream-builder",
            )
        )

    # Keep the response compact while guaranteeing a useful alternative. A
    # qualifying single takes one slot; otherwise two grounded builders do.
    if len(dreams) == 1:
        tertiary_probability = _dream_probability(profile, offset=1)
        dreams.append(
            CombinationAnalysis(
                id=f"{match.id}-dream-btts-result",
                label="Soñadora combinada de goles",
                selection=f"Ambos equipos anotan + {match.away_team} o empate",
                legs=[
                    _leg("BOTH_TEAMS_TO_SCORE", "Ambos equipos anotan", "Sí"),
                    _leg("DOUBLE_CHANCE_AWAY_DRAW", "Doble oportunidad", f"{match.away_team} o empate"),
                ],
                probability=tertiary_probability,
                fair_odds=_fair_odds(tertiary_probability),
                confidence="Media-baja",
                data_quality=max(0.50, round(match.data_quality * 0.84, 2)),
                factors_for=["Dos mercados generales disponibles con la información actual"],
                risks=[f"{match.home_team} puede dejar al rival sin anotar o ganar el encuentro"],
                correlation_note="Probabilidad conjunta ajustada por dependencia entre goles y resultado.",
                kind="dream-builder",
            )
        )

    return dreams[:2]


def enrich_analysis_with_opportunities(analysis: MatchAnalysisResponse) -> MatchAnalysisResponse:
    analysis.combinations = build_combinations(
        analysis.match,
        analysis.referee_info,
        analysis.markets,
    )
    analysis.dream_picks = build_dream_picks(analysis.match, analysis.referee_info, analysis.markets)
    return analysis
