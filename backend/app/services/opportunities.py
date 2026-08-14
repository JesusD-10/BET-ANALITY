import math
import re
from itertools import combinations as iter_combinations

from app.schemas.matches import (
    CombinationAnalysis,
    CombinationLeg,
    DisciplineSummary,
    MarketAnalysis,
    MatchAnalysisResponse,
    MatchSummary,
    RefereeInfo,
)


MIN_DREAM_PROBABILITY = 0.30
MAX_DREAM_PROBABILITY = 1 / 3
MIN_DREAM_REFERENCE_ODDS = 3.0
MIN_COMBINATION_LEG_PROBABILITY = 0.55
MIN_COMBINATION_PROBABILITY = 0.40
MAX_COMBINATIONS_PER_MATCH = 8
MAX_DREAMS_PER_MATCH = 8

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


def _leg(market: MarketAnalysis) -> CombinationLeg:
    return CombinationLeg(
        market_key=market.market_key,
        label=market.label,
        selection=market.selection,
    )


def _selection_signature(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _poisson_at_most(rate: float, maximum: int) -> float:
    """Return P(X <= maximum) for a Poisson rate without extra dependencies."""

    return math.exp(-rate) * sum(rate**count / math.factorial(count) for count in range(maximum + 1))


def _card_market_from_average(
    match: MatchSummary,
    average: float,
    *,
    factors_for: list[str],
    quality_cap: float,
) -> MarketAnalysis | None:
    if not math.isfinite(average) or average <= 0:
        return None

    if average >= 4.6:
        market_key = "TOTAL_CARDS_OVER_3_5"
        selection = "Más de 3.5 tarjetas"
        probability = 1.0 - _poisson_at_most(average, 3)
    elif average >= 3.2:
        market_key = "TOTAL_CARDS_OVER_2_5"
        selection = "Más de 2.5 tarjetas"
        probability = 1.0 - _poisson_at_most(average, 2)
    else:
        market_key = "TOTAL_CARDS_UNDER_4_5"
        selection = "Menos de 4.5 tarjetas"
        probability = _poisson_at_most(average, 4)

    probability = round(max(0.05, min(0.95, probability)), 3)
    data_quality = round(min(match.data_quality, quality_cap), 2)
    return MarketAnalysis(
        market_key=market_key,
        label="Total de tarjetas",
        selection=selection,
        probability=probability,
        fair_odds=_fair_odds(probability),
        best_odds=None,
        expected_value=None,
        confidence="Alta" if probability >= 0.75 and data_quality >= 0.7 else "Media-alta",
        data_quality=data_quality,
        factors_for=factors_for,
        risks=[
            "El promedio arbitral no garantiza el mismo volumen de tarjetas en este encuentro"
        ],
    )


def _discipline_card_market(
    match: MatchSummary,
    discipline: DisciplineSummary | None,
) -> MarketAnalysis | None:
    if discipline is None or discipline.home is None or discipline.away is None:
        return None
    home = discipline.home
    away = discipline.away
    if (
        home.yellow_cards_avg is None
        or away.yellow_cards_avg is None
        or home.sample_size <= 0
        or away.sample_size <= 0
    ):
        return None
    home_average = float(home.yellow_cards_avg)
    away_average = float(away.yellow_cards_avg)
    if not all(math.isfinite(value) and value >= 0 for value in (home_average, away_average)):
        return None
    total_average = home_average + away_average
    minimum_sample = min(home.sample_size, away.sample_size)
    quality_cap = min(0.84, 0.66 + minimum_sample * 0.03)
    return _card_market_from_average(
        match,
        total_average,
        factors_for=[
            (
                f"{home.team_name} promedia {home_average:.1f} amarillas en "
                f"{home.sample_size} partidos con datos"
            ),
            (
                f"{away.team_name} promedia {away_average:.1f} amarillas en "
                f"{away.sample_size} partidos con datos"
            ),
        ],
        quality_cap=quality_cap,
    )


def _referee_card_market(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
) -> MarketAnalysis | None:
    """Turn an explicit referee average into one conservative card signal."""

    if referee_info is None or referee_info.yellow_cards_avg is None:
        return None
    average = float(referee_info.yellow_cards_avg)
    return _card_market_from_average(
        match,
        average,
        factors_for=[
            f"{referee_info.name} registra {average:.1f} amarillas por partido"
        ],
        quality_cap=0.78,
    )


def _evidence_markets(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    markets: list[MarketAnalysis],
    discipline: DisciplineSummary | None = None,
) -> list[MarketAnalysis]:
    """Keep unambiguous analyzed selections and add verified referee evidence.

    If providers disagree on the selection represented by one key, that key is
    discarded instead of arbitrarily choosing a side. An explicit card market
    always takes precedence over the market derived from the referee average.
    """

    by_key: dict[str, dict[str, list[MarketAnalysis]]] = {}
    order: list[str] = []
    for market in markets:
        market_key = market.market_key.strip().upper()
        if market_family(market_key) is None:
            continue
        if market_key not in by_key:
            by_key[market_key] = {}
            order.append(market_key)
        selection = _selection_signature(market.selection)
        by_key[market_key].setdefault(selection, []).append(market)

    result: list[MarketAnalysis] = []
    for market_key in order:
        selection_groups = by_key[market_key]
        if len(selection_groups) != 1:
            continue
        candidates = next(iter(selection_groups.values()))
        result.append(
            max(
                candidates,
                key=lambda item: (item.data_quality, item.probability),
            )
        )

    if not any(market_family(market.market_key) == "cards" for market in result):
        derived_market = _discipline_card_market(match, discipline)
        if derived_market is None:
            derived_market = _referee_card_market(match, referee_info)
        if derived_market is not None:
            result.append(derived_market)
    return result


def _legs_signature(item: CombinationAnalysis) -> tuple[str, ...]:
    return tuple(sorted(leg.market_key for leg in item.legs))


def _joint_probability(
    markets: tuple[MarketAnalysis, ...],
    dependency_factor: float,
) -> float:
    probability = math.prod(market.probability for market in markets)
    probability *= dependency_factor ** (len(markets) - 1)
    return round(probability, 4)


def _text_evidence(markets: tuple[MarketAnalysis, ...], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for market in markets:
        for raw_value in getattr(market, field):
            value = " ".join(raw_value.split())
            signature = _selection_signature(value)
            if value and signature not in seen:
                seen.add(signature)
                values.append(value)
    return values


def _combination_selection(markets: tuple[MarketAnalysis, ...]) -> str:
    # Labels make short selections such as "Sí" or a team name understandable
    # when the recommendation is rendered outside the match-analysis screen.
    return " + ".join(f"{market.label}: {market.selection}" for market in markets)


def _combination_id(
    match: MatchSummary,
    prefix: str,
    markets: tuple[MarketAnalysis, ...],
) -> str:
    keys = "-".join(market.market_key.lower() for market in markets)
    return f"{match.id}-{prefix}-{keys}"


def _total_line(market_key: str) -> tuple[str, float] | None:
    matched = re.fullmatch(
        r"TOTAL_GOALS_(OVER|UNDER)_(\d+)_(\d+)",
        market_key.strip().upper(),
    )
    if matched is None:
        return None
    return matched.group(1), float(f"{matched.group(2)}.{matched.group(3)}")


def _goal_markets_are_compatible(
    first: MarketAnalysis,
    second: MarketAnalysis,
) -> bool:
    first_line = _total_line(first.market_key)
    second_line = _total_line(second.market_key)
    if first_line is not None and second_line is not None:
        first_side, first_value = first_line
        second_side, second_value = second_line
        if first_side == second_side:
            # Two overs or two unders are nested rather than additional legs.
            return False
        over_value = first_value if first_side == "OVER" else second_value
        under_value = first_value if first_side == "UNDER" else second_value
        return over_value < under_value

    keys = {first.market_key.strip().upper(), second.market_key.strip().upper()}
    if "BOTH_TEAMS_TO_SCORE" in keys:
        total_market = second if first.market_key.strip().upper() == "BOTH_TEAMS_TO_SCORE" else first
        total_line = _total_line(total_market.market_key)
        btts_market = first if first.market_key.strip().upper() == "BOTH_TEAMS_TO_SCORE" else second
        btts_yes = _selection_signature(btts_market.selection) in {"si", "sí", "yes"}
        if total_line is not None and total_line[0] == "UNDER" and total_line[1] <= 1.5:
            return not btts_yes
        return True
    return False


def _group_is_compatible(markets: tuple[MarketAnalysis, ...]) -> bool:
    if len({market.market_key.strip().upper() for market in markets}) != len(markets):
        return False
    for first, second in iter_combinations(markets, 2):
        first_family = market_family(first.market_key)
        second_family = market_family(second.market_key)
        if first_family != second_family:
            continue
        if first_family != "goals" or not _goal_markets_are_compatible(first, second):
            return False
    return True


def _compatible_groups(
    markets: list[MarketAnalysis],
    *,
    minimum_leg_probability: float,
) -> list[tuple[MarketAnalysis, ...]]:
    eligible = [
        market
        for market in markets
        if market.probability >= minimum_leg_probability
        and market_family(market.market_key) is not None
    ]
    groups: list[tuple[MarketAnalysis, ...]] = []
    for size in (2, 3):
        for group in iter_combinations(eligible, size):
            if _group_is_compatible(group):
                groups.append(group)
    return groups


def _build_combination(
    match: MatchSummary,
    markets: tuple[MarketAnalysis, ...],
    probability: float,
) -> CombinationAnalysis:
    advanced = any(
        market_family(market.market_key) in DATA_DEPENDENT_MARKET_FAMILIES
        for market in markets
    )
    data_quality = round(min(market.data_quality for market in markets), 2)
    factors_for = _text_evidence(markets, "factors_for")
    risks = _text_evidence(markets, "risks")
    factor = 0.90
    return CombinationAnalysis(
        id=_combination_id(match, "builder", markets),
        label=(
            "Combinada avanzada respaldada"
            if advanced
            else "Combinada respaldada del partido"
        ),
        selection=_combination_selection(markets),
        legs=[_leg(market) for market in markets],
        probability=probability,
        fair_odds=_fair_odds(probability),
        best_odds=None,
        expected_value=None,
        confidence=(
            "Media-alta"
            if probability >= 0.50 and data_quality >= 0.70
            else "Media"
        ),
        data_quality=data_quality,
        factors_for=factors_for
        or ["Todas las piernas proceden de mercados analizados para este partido"],
        risks=risks or ["Todas las condiciones deben cumplirse en el mismo partido"],
        correlation_note=(
            f"Probabilidad conjunta calculada desde {len(markets)} señales del partido "
            f"con ajuste conservador de dependencia {factor:.2f} por unión adicional; "
            "no presupone independencia plena ni representa una cuota de casa."
        ),
        kind="advanced-builder" if advanced else "conservative-builder",
    )


def build_combinations(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    markets: list[MarketAnalysis] | None = None,
    discipline: DisciplineSummary | None = None,
) -> list[CombinationAnalysis]:
    """Build several high-coverage combinations from match evidence only."""

    evidence = _evidence_markets(match, referee_info, markets or [], discipline)
    candidates: list[CombinationAnalysis] = []
    for group in _compatible_groups(
        evidence,
        minimum_leg_probability=MIN_COMBINATION_LEG_PROBABILITY,
    ):
        probability = _joint_probability(group, dependency_factor=0.90)
        if probability < MIN_COMBINATION_PROBABILITY:
            continue
        candidates.append(_build_combination(match, group, probability))

    candidates.sort(
        key=lambda item: (
            item.probability * item.data_quality,
            item.probability,
            len(item.legs),
        ),
        reverse=True,
    )
    unique: list[CombinationAnalysis] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        signature = _legs_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(candidate)
        if len(unique) >= MAX_COMBINATIONS_PER_MATCH:
            break
    return unique


def _qualifying_singles(markets: list[MarketAnalysis]) -> list[MarketAnalysis]:
    candidates: list[MarketAnalysis] = []
    for market in markets:
        reference_odds = market.best_odds if market.best_odds is not None else market.fair_odds
        if market.probability < MIN_DREAM_PROBABILITY:
            continue
        if market.best_odds is None and market.probability > MAX_DREAM_PROBABILITY:
            continue
        if reference_odds < MIN_DREAM_REFERENCE_ODDS:
            continue
        if market.best_odds is not None and (
            market.expected_value is None or market.expected_value < 0
        ):
            continue
        candidates.append(market)
    candidates.sort(
        key=lambda item: (
            item.best_odds is not None,
            item.probability * item.data_quality,
        ),
        reverse=True,
    )
    return candidates


def _single_dream(match: MatchSummary, market: MarketAnalysis) -> CombinationAnalysis:
    return CombinationAnalysis(
        id=f"{match.id}-dream-single-{market.market_key.lower()}",
        label="Soñadora individual",
        selection=f"{market.label}: {market.selection}",
        legs=[_leg(market)],
        probability=market.probability,
        fair_odds=market.fair_odds,
        best_odds=market.best_odds,
        expected_value=market.expected_value if market.best_odds is not None else None,
        confidence=market.confidence,
        data_quality=market.data_quality,
        factors_for=market.factors_for,
        risks=market.risks,
        correlation_note="Selección simple; la cuota objetivo corresponde al mercado completo.",
        kind="dream-single",
    )


def _dream_builder(
    match: MatchSummary,
    markets: tuple[MarketAnalysis, ...],
    probability: float,
    dependency_factor: float,
) -> CombinationAnalysis:
    data_quality = round(min(market.data_quality for market in markets), 2)
    factors_for = _text_evidence(markets, "factors_for")
    risks = _text_evidence(markets, "risks")
    return CombinationAnalysis(
        id=_combination_id(match, "dream-markets", markets),
        label="Soñadora interpretada del partido",
        selection=_combination_selection(markets),
        legs=[_leg(market) for market in markets],
        probability=probability,
        fair_odds=_fair_odds(probability),
        best_odds=None,
        expected_value=None,
        confidence="Media",
        data_quality=data_quality,
        factors_for=factors_for
        or ["Todas las piernas proceden de mercados analizados para este partido"],
        risks=risks or ["Todas las condiciones deben cumplirse en el mismo partido"],
        correlation_note=(
            f"Probabilidad conjunta calculada desde {len(markets)} señales del partido "
            f"con ajuste conservador de dependencia {dependency_factor:.2f} por unión "
            "adicional; no presupone independencia plena ni representa una cuota de casa."
        ),
        kind="dream-builder",
    )


def build_dream_picks(
    match: MatchSummary,
    referee_info: RefereeInfo | None,
    markets: list[MarketAnalysis] | None = None,
    discipline: DisciplineSummary | None = None,
) -> list[CombinationAnalysis]:
    """Return all distinct 3+ dream picks supported by match evidence.

    Generated builders never multiply bookmaker prices. Their ``fair_odds`` is
    the inverse of the modeled joint probability and ``best_odds`` remains
    empty until a provider supplies an exact same-game-combination quote.
    """

    evidence = _evidence_markets(match, referee_info, markets or [], discipline)
    dreams = [_single_dream(match, market) for market in _qualifying_singles(evidence)]

    for group in _compatible_groups(
        evidence,
        minimum_leg_probability=0.40,
    ):
        families = {market_family(market.market_key) for market in group}
        dependency_factor = 0.82 if families == {"result", "goals"} else 0.85
        probability = _joint_probability(group, dependency_factor=dependency_factor)
        if not (
            MIN_DREAM_PROBABILITY <= probability <= MAX_DREAM_PROBABILITY
            and _fair_odds(probability) >= MIN_DREAM_REFERENCE_ODDS
        ):
            continue
        dreams.append(
            _dream_builder(
                match,
                group,
                probability,
                dependency_factor,
            )
        )

    # Exact leg signatures are unique within a match. Do not truncate at two:
    # the daily endpoint applies its own cross-match diversity and limit.
    unique: list[CombinationAnalysis] = []
    seen: set[tuple[str, ...]] = set()
    for dream in sorted(
        dreams,
        key=lambda item: (
            item.best_odds is not None,
            item.data_quality,
            -abs(item.probability - 0.315),
            len(item.legs),
        ),
        reverse=True,
    ):
        signature = _legs_signature(dream)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(dream)
        if len(unique) >= MAX_DREAMS_PER_MATCH:
            break
    return unique


def enrich_analysis_with_opportunities(analysis: MatchAnalysisResponse) -> MatchAnalysisResponse:
    analysis.combinations = build_combinations(
        analysis.match,
        analysis.referee_info,
        analysis.markets,
        analysis.discipline,
    )
    analysis.dream_picks = build_dream_picks(
        analysis.match,
        analysis.referee_info,
        analysis.markets,
        analysis.discipline,
    )
    return analysis
