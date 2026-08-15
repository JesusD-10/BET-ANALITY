"""Normalize provider payloads into compact, auditable match evidence.

The API-Football responses are intentionally not passed verbatim to language
models.  This module keeps only predictive, documented fields, preserves
missing values as ``None`` and attaches availability/provenance metadata so an
empty response can never silently become a statistical zero.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping, Sequence

from app.schemas.matches import (
    EvidenceCoverageItem,
    EvidenceProvenance,
    FixtureStatisticsSnapshot,
    H2HMatchItem,
    InjuryItem,
    LineupsSummary,
    MatchEvidenceContext,
    MatchStatisticsSummary,
    MatchSummary,
    PlayerContext,
    PlayerStatisticsSnapshot,
    ProviderPredictionEvidence,
    StandingSnapshot,
    StandingsContext,
    TeamStatisticsSnapshot,
    VerifiedOddsEvidence,
)


_TEAM_METRICS = (
    "fouls",
    "yellow_cards",
    "red_cards",
    "corners",
    "total_shots",
    "shots_on_target",
)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
        if not value:
            return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: object) -> int | None:
    parsed = _number(value)
    if parsed is None or not parsed.is_integer() or parsed < 0:
        return None
    return int(parsed)


def _split_value(block: object, key: str = "total") -> object:
    return block.get(key) if isinstance(block, Mapping) else None


def _entity_id(entity: object) -> str | None:
    if not isinstance(entity, Mapping) or entity.get("id") is None:
        return None
    return str(entity["id"])


def _entity_name(entity: object) -> str:
    return str(entity.get("name") or "").strip() if isinstance(entity, Mapping) else ""


def _same_team(
    entity: object,
    *,
    team_id: str | None,
    team_name: str,
) -> bool:
    provider_id = _entity_id(entity)
    if provider_id is not None and team_id is not None:
        return provider_id == str(team_id)
    provider_name = _entity_name(entity).casefold()
    return bool(provider_name and provider_name == team_name.strip().casefold())


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _metric_block(
    item: Mapping[str, Any],
    *,
    team_id: str | None,
    team_name: str,
) -> dict[str, float | int | None]:
    blocks = item.get("statistics")
    if not isinstance(blocks, list):
        return {}
    for block in blocks:
        if not isinstance(block, Mapping) or not _same_team(
            block.get("team"), team_id=team_id, team_name=team_name
        ):
            continue
        result: dict[str, float | int | None] = {}
        for metric in _TEAM_METRICS:
            value = _number(block.get(metric))
            if value is not None:
                result[metric] = int(value) if value.is_integer() else value
        return result
    return {}


def _fixture_snapshot(item: object) -> FixtureStatisticsSnapshot | None:
    if not isinstance(item, Mapping):
        return None
    fixture = item.get("fixture") if isinstance(item.get("fixture"), Mapping) else {}
    teams = item.get("teams") if isinstance(item.get("teams"), Mapping) else {}
    home = teams.get("home") if isinstance(teams.get("home"), Mapping) else item.get("homeTeam")
    away = teams.get("away") if isinstance(teams.get("away"), Mapping) else item.get("awayTeam")
    home_name = _entity_name(home) or str(item.get("home_team") or "").strip()
    away_name = _entity_name(away) or str(item.get("away_team") or "").strip()
    if not home_name or not away_name:
        return None

    fixture_id = fixture.get("id") or item.get("id") or item.get("fixture_id")
    if fixture_id is None:
        return None
    goals = item.get("goals") if isinstance(item.get("goals"), Mapping) else {}
    score = item.get("score") if isinstance(item.get("score"), Mapping) else {}
    full_time = score.get("fullTime") if isinstance(score.get("fullTime"), Mapping) else {}
    league = item.get("league") if isinstance(item.get("league"), Mapping) else item.get("competition")
    competition = _entity_name(league) if isinstance(league, Mapping) else str(league or "").strip()

    return FixtureStatisticsSnapshot(
        fixture_id=str(fixture_id),
        date=_parse_datetime(fixture.get("date") or item.get("utcDate") or item.get("date")),
        competition=competition or None,
        home_team=home_name,
        away_team=away_name,
        home_goals=_integer(goals.get("home") if goals else full_time.get("home")),
        away_goals=_integer(goals.get("away") if goals else full_time.get("away")),
        home_statistics=_metric_block(
            item,
            team_id=_entity_id(home),
            team_name=home_name,
        ),
        away_statistics=_metric_block(
            item,
            team_id=_entity_id(away),
            team_name=away_name,
        ),
    )


def _recent_team_snapshot(
    history: Sequence[dict],
    *,
    team_id: str | None,
    team_name: str,
) -> TeamStatisticsSnapshot | None:
    results: list[str] = []
    goals_for: list[int] = []
    goals_against: list[int] = []
    metric_samples: dict[str, list[float]] = defaultdict(list)

    for raw in history:
        snap = _fixture_snapshot(raw)
        if snap is None:
            continue
        teams = raw.get("teams") if isinstance(raw.get("teams"), Mapping) else {}
        home_entity = teams.get("home") if isinstance(teams, Mapping) else raw.get("homeTeam")
        away_entity = teams.get("away") if isinstance(teams, Mapping) else raw.get("awayTeam")
        is_home = _same_team(home_entity, team_id=team_id, team_name=team_name)
        is_away = _same_team(away_entity, team_id=team_id, team_name=team_name)
        if not is_home and not is_away:
            continue

        own_goals = snap.home_goals if is_home else snap.away_goals
        opponent_goals = snap.away_goals if is_home else snap.home_goals
        if own_goals is not None and opponent_goals is not None:
            goals_for.append(own_goals)
            goals_against.append(opponent_goals)
            results.append("W" if own_goals > opponent_goals else "D" if own_goals == opponent_goals else "L")
        metrics = snap.home_statistics if is_home else snap.away_statistics
        for key, value in metrics.items():
            parsed = _number(value)
            if parsed is not None:
                metric_samples[key].append(parsed)

    fixtures_played = len(results)
    if not fixtures_played and not metric_samples:
        return None

    averages: dict[str, float | None] = {
        key: round(sum(values) / len(values), 2) if values else None
        for key, values in metric_samples.items()
    }
    return TeamStatisticsSnapshot(
        team_id=team_id,
        team_name=team_name,
        form="-".join(results) or None,
        fixtures_played=fixtures_played or None,
        wins=results.count("W") if results else None,
        draws=results.count("D") if results else None,
        losses=results.count("L") if results else None,
        goals_for=sum(goals_for) if goals_for else None,
        goals_against=sum(goals_against) if goals_against else None,
        goals_for_avg=round(sum(goals_for) / len(goals_for), 2) if goals_for else None,
        goals_against_avg=round(sum(goals_against) / len(goals_against), 2) if goals_against else None,
        averages=averages,
    )


def _season_team_snapshot(
    raw: object,
    *,
    fallback: TeamStatisticsSnapshot | None,
    team_id: str | None,
    team_name: str,
) -> TeamStatisticsSnapshot | None:
    if not isinstance(raw, Mapping):
        return fallback
    fixtures = raw.get("fixtures") if isinstance(raw.get("fixtures"), Mapping) else {}
    goals_for = raw.get("goals_for") if isinstance(raw.get("goals_for"), Mapping) else {}
    goals_against = raw.get("goals_against") if isinstance(raw.get("goals_against"), Mapping) else {}
    clean_sheets = raw.get("clean_sheets")
    failed_to_score = raw.get("failed_to_score")

    played = _integer(_split_value(fixtures.get("played")))
    wins = _integer(_split_value(fixtures.get("wins")))
    draws = _integer(_split_value(fixtures.get("draws")))
    losses = _integer(_split_value(fixtures.get("losses")))
    total_for = _integer(_split_value(goals_for.get("total")))
    total_against = _integer(_split_value(goals_against.get("total")))
    avg_for = _number(_split_value(goals_for.get("average")))
    avg_against = _number(_split_value(goals_against.get("average")))

    averages = dict(fallback.averages) if fallback else {}
    rates = dict(fallback.rates) if fallback else {}
    if avg_for is not None:
        averages["goals_for"] = round(avg_for, 2)
    if avg_against is not None:
        averages["goals_against"] = round(avg_against, 2)
    if played:
        if wins is not None:
            rates["win_rate"] = round(wins / played, 3)
        if draws is not None:
            rates["draw_rate"] = round(draws / played, 3)
        if losses is not None:
            rates["loss_rate"] = round(losses / played, 3)
        clean_total = _integer(_split_value(clean_sheets))
        failed_total = _integer(_split_value(failed_to_score))
        if clean_total is not None:
            rates["clean_sheet_rate"] = round(clean_total / played, 3)
        if failed_total is not None:
            rates["failed_to_score_rate"] = round(failed_total / played, 3)

        cards = raw.get("cards") if isinstance(raw.get("cards"), Mapping) else {}
        for provider_key, metric_key in (("yellow", "yellow_cards"), ("red", "red_cards")):
            buckets = cards.get(provider_key)
            if not isinstance(buckets, Mapping):
                continue
            totals = [
                _number(bucket.get("total"))
                for bucket in buckets.values()
                if isinstance(bucket, Mapping)
            ]
            available_totals = [value for value in totals if value is not None]
            if available_totals:
                averages[metric_key] = round(sum(available_totals) / played, 2)

        for side, goal_block in (("for", goals_for), ("against", goals_against)):
            under_over = goal_block.get("over_under")
            if not isinstance(under_over, Mapping):
                continue
            for line, counts in under_over.items():
                if not isinstance(counts, Mapping):
                    continue
                over = _number(counts.get("over"))
                if over is not None:
                    rates[f"goals_{side}_over_{str(line).replace('.', '_')}"] = round(over / played, 3)

    return TeamStatisticsSnapshot(
        team_id=team_id or _entity_id(raw.get("team")),
        team_name=_entity_name(raw.get("team")) or team_name,
        form=str(raw.get("form") or "").strip() or (fallback.form if fallback else None),
        fixtures_played=played if played is not None else (fallback.fixtures_played if fallback else None),
        wins=wins if wins is not None else (fallback.wins if fallback else None),
        draws=draws if draws is not None else (fallback.draws if fallback else None),
        losses=losses if losses is not None else (fallback.losses if fallback else None),
        goals_for=total_for if total_for is not None else (fallback.goals_for if fallback else None),
        goals_against=total_against if total_against is not None else (fallback.goals_against if fallback else None),
        goals_for_avg=avg_for if avg_for is not None else (fallback.goals_for_avg if fallback else None),
        goals_against_avg=avg_against if avg_against is not None else (fallback.goals_against_avg if fallback else None),
        clean_sheets=_integer(_split_value(clean_sheets)),
        failed_to_score=_integer(_split_value(failed_to_score)),
        averages=averages,
        rates=rates,
    )


def _standing_row(raw: object, *, team_id: str | None, team_name: str) -> StandingSnapshot | None:
    if not isinstance(raw, Mapping) or not _same_team(
        raw.get("team"), team_id=team_id, team_name=team_name
    ):
        return None
    overall = raw.get("overall") if isinstance(raw.get("overall"), Mapping) else {}
    return StandingSnapshot(
        team_id=_entity_id(raw.get("team")) or team_id,
        team_name=_entity_name(raw.get("team")) or team_name,
        rank=_integer(raw.get("rank")),
        points=_integer(raw.get("points")),
        played=_integer(overall.get("played")),
        wins=_integer(overall.get("wins")),
        draws=_integer(overall.get("draws")),
        losses=_integer(overall.get("losses")),
        goals_for=_integer(overall.get("goals_for")),
        goals_against=_integer(overall.get("goals_against")),
        goal_difference=int(value) if (value := _number(raw.get("goal_difference"))) is not None else None,
        form=str(raw.get("form") or "").strip() or None,
        description=str(raw.get("description") or "").strip() or None,
    )


def _standings_context(raw: object, match: MatchSummary) -> StandingsContext | None:
    if not isinstance(raw, Mapping):
        return None
    home: StandingSnapshot | None = None
    away: StandingSnapshot | None = None
    for group in raw.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        for row in group.get("table") or []:
            home = home or _standing_row(row, team_id=match.home_team_id, team_name=match.home_team)
            away = away or _standing_row(row, team_id=match.away_team_id, team_name=match.away_team)
    if home is None and away is None:
        return None
    return StandingsContext(
        league_id=match.league_id,
        season=match.season,
        home=home,
        away=away,
    )


def _prediction(raw: object) -> ProviderPredictionEvidence | None:
    if not isinstance(raw, Mapping):
        return None
    winner = raw.get("winner") if isinstance(raw.get("winner"), Mapping) else {}
    expected_goals = raw.get("expected_goals") if isinstance(raw.get("expected_goals"), Mapping) else {}
    percentages = raw.get("percentages") if isinstance(raw.get("percentages"), Mapping) else {}

    def probability(value: object) -> float | None:
        parsed = _number(value)
        if parsed is None:
            return None
        parsed = parsed / 100 if parsed > 1 else parsed
        return max(0.0, min(1.0, parsed))

    return ProviderPredictionEvidence(
        winner_id=_entity_id(winner),
        winner_name=_entity_name(winner) or None,
        winner_comment=str(winner.get("comment") or "").strip() or None,
        advice=str(raw.get("advice") or "").strip() or None,
        win_or_draw=raw.get("win_or_draw") if isinstance(raw.get("win_or_draw"), bool) else None,
        under_over=str(raw.get("under_over") or "").strip() or None,
        goals_home=str(expected_goals.get("home")) if expected_goals.get("home") is not None else None,
        goals_away=str(expected_goals.get("away")) if expected_goals.get("away") is not None else None,
        percent_home=probability(percentages.get("home")),
        percent_draw=probability(percentages.get("draw")),
        percent_away=probability(percentages.get("away")),
    )


def _aggregate_players(
    histories: Iterable[dict],
    *,
    team_id: str | None,
    team_name: str,
    limit: int = 12,
) -> list[PlayerStatisticsSnapshot]:
    aggregate: dict[str, dict[str, Any]] = {}
    additive = (
        "goals",
        "assists",
        "shots",
        "shots_on_target",
        "key_passes",
        "tackles",
        "interceptions",
        "saves",
        "yellow_cards",
        "red_cards",
        "minutes",
    )
    for fixture in histories:
        players = fixture.get("player_statistics") if isinstance(fixture, Mapping) else None
        if not isinstance(players, list):
            continue
        seen_in_fixture: set[str] = set()
        for block in players:
            if not isinstance(block, Mapping) or not _same_team(
                block.get("team"), team_id=team_id, team_name=team_name
            ):
                continue
            player = block.get("player") if isinstance(block.get("player"), Mapping) else {}
            name = _entity_name(player)
            if not name:
                continue
            key = _entity_id(player) or name.casefold()
            row = aggregate.setdefault(
                key,
                {
                    "player_id": _entity_id(player),
                    "player_name": name,
                    "team_id": team_id or _entity_id(block.get("team")),
                    "team_name": _entity_name(block.get("team")) or team_name,
                    "appearances": 0,
                    "rating_values": [],
                },
            )
            if key not in seen_in_fixture:
                row["appearances"] += 1
                seen_in_fixture.add(key)
            row["position"] = block.get("position") or row.get("position")
            rating = _number(block.get("rating"))
            if rating is not None:
                row["rating_values"].append(rating)
            shots = block.get("shots") if isinstance(block.get("shots"), Mapping) else {}
            goals = block.get("goals") if isinstance(block.get("goals"), Mapping) else {}
            cards = block.get("cards") if isinstance(block.get("cards"), Mapping) else {}
            mapped = {
                "goals": goals.get("total") if goals else block.get("goals"),
                "assists": block.get("assists"),
                "shots": shots.get("total") if shots else block.get("shots"),
                "shots_on_target": shots.get("on_target") if shots else block.get("shots_on_target"),
                "key_passes": block.get("key_passes"),
                "tackles": block.get("tackles"),
                "interceptions": block.get("interceptions"),
                "saves": block.get("saves"),
                "yellow_cards": cards.get("yellow") if cards else block.get("yellow_cards"),
                "red_cards": cards.get("red") if cards else block.get("red_cards"),
                "minutes": block.get("minutes"),
            }
            for field in additive:
                value = _integer(mapped.get(field))
                if value is not None:
                    row[field] = row.get(field, 0) + value

    results: list[PlayerStatisticsSnapshot] = []
    for row in aggregate.values():
        ratings = row.pop("rating_values")
        row["rating"] = round(sum(ratings) / len(ratings), 2) if ratings else None
        results.append(PlayerStatisticsSnapshot(**row))
    results.sort(
        key=lambda player: (
            player.goals or 0,
            player.assists or 0,
            player.shots_on_target or 0,
            player.rating or 0,
            player.appearances or 0,
        ),
        reverse=True,
    )
    return results[:limit]


def _player_snapshot(raw: object) -> PlayerStatisticsSnapshot | None:
    """Convert the canonical `/players` or top-player row into model evidence."""

    if not isinstance(raw, Mapping):
        return None
    player = raw.get("player") if isinstance(raw.get("player"), Mapping) else {}
    team = raw.get("team") if isinstance(raw.get("team"), Mapping) else {}
    games = raw.get("games") if isinstance(raw.get("games"), Mapping) else {}
    shots = raw.get("shots") if isinstance(raw.get("shots"), Mapping) else {}
    goals = raw.get("goals") if isinstance(raw.get("goals"), Mapping) else {}
    passes = raw.get("passes") if isinstance(raw.get("passes"), Mapping) else {}
    tackles = raw.get("tackles") if isinstance(raw.get("tackles"), Mapping) else {}
    cards = raw.get("cards") if isinstance(raw.get("cards"), Mapping) else {}
    name = _entity_name(player)
    if not name:
        return None
    appearances_value = (
        games.get("appearences") if "appearences" in games else games.get("appearances")
    )
    shots_on_value = shots.get("on") if "on" in shots else shots.get("on_target")
    return PlayerStatisticsSnapshot(
        player_id=_entity_id(player),
        player_name=name,
        team_id=_entity_id(team),
        team_name=_entity_name(team) or None,
        position=str(games.get("position") or "").strip() or None,
        appearances=_integer(appearances_value),
        starts=_integer(games.get("lineups")),
        minutes=_integer(games.get("minutes")),
        rating=_number(games.get("rating")),
        goals=_integer(goals.get("total")),
        assists=_integer(goals.get("assists")),
        shots=_integer(shots.get("total")),
        shots_on_target=_integer(shots_on_value),
        key_passes=_integer(passes.get("key")),
        tackles=_integer(tackles.get("total")),
        interceptions=_integer(tackles.get("interceptions")),
        saves=_integer(goals.get("saves")),
        yellow_cards=_integer(cards.get("yellow")),
        red_cards=_integer(cards.get("red")),
    )


def _player_rows(raw: object, limit: int) -> list[PlayerStatisticsSnapshot]:
    if isinstance(raw, Mapping):
        raw = raw.get("players") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    rows = [snapshot for item in raw if (snapshot := _player_snapshot(item)) is not None]
    rows.sort(
        key=lambda player: (
            player.goals or 0,
            player.assists or 0,
            player.shots_on_target or 0,
            player.rating or 0,
            player.appearances or 0,
        ),
        reverse=True,
    )
    return rows[:limit]


def _coverage_flag(raw: object, *path: str) -> bool | None:
    current: object = raw
    if isinstance(current, Mapping) and "coverage" in current:
        current = current.get("coverage")
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, bool) else None


def build_match_evidence(
    *,
    match: MatchSummary,
    provider_name: str,
    home_history: list[dict],
    away_history: list[dict],
    h2h: list[H2HMatchItem],
    injuries: list[InjuryItem],
    lineups: LineupsSummary | None,
    odds_quotes: Mapping[str, object],
    league_coverage: object = None,
    standings: object = None,
    home_team_statistics: object = None,
    away_team_statistics: object = None,
    provider_prediction: object = None,
    home_player_statistics: object = None,
    away_player_statistics: object = None,
    top_scorers: object = None,
    top_assists: object = None,
    top_yellow_cards: object = None,
    top_red_cards: object = None,
    h2h_failed: bool = False,
    home_history_failed: bool = False,
    away_history_failed: bool = False,
    injuries_failed: bool = False,
    lineups_requested: bool = False,
    lineups_failed: bool = False,
    odds_failed: bool = False,
    prediction_failed: bool = False,
    standings_requested: bool = True,
    prediction_requested: bool = True,
    provider_unavailable_reason: str | None = None,
) -> MatchEvidenceContext:
    now = datetime.now(timezone.utc)

    def provenance(endpoint: str, *, verified: bool = True) -> list[EvidenceProvenance]:
        return [
            EvidenceProvenance(
                provider=provider_name,
                endpoint=endpoint,
                fetched_at=now,
                verified=verified,
            )
        ]

    coverage: list[EvidenceCoverageItem] = []

    def add_coverage(
        section: str,
        status: str,
        endpoint: str,
        *,
        reason: str | None = None,
        sample_size: int | None = None,
        verified: bool = True,
    ) -> None:
        coverage.append(
            EvidenceCoverageItem(
                section=section,
                status=status,
                reason=reason,
                sample_size=sample_size,
                provenance=provenance(endpoint, verified=verified),
            )
        )

    home_fixtures = [snap for item in home_history if (snap := _fixture_snapshot(item)) is not None]
    away_fixtures = [snap for item in away_history if (snap := _fixture_snapshot(item)) is not None]
    home_recent = _recent_team_snapshot(
        home_history,
        team_id=match.home_team_id,
        team_name=match.home_team,
    )
    away_recent = _recent_team_snapshot(
        away_history,
        team_id=match.away_team_id,
        team_name=match.away_team,
    )
    home_stats = _season_team_snapshot(
        home_team_statistics,
        fallback=home_recent,
        team_id=match.home_team_id,
        team_name=match.home_team,
    )
    away_stats = _season_team_snapshot(
        away_team_statistics,
        fallback=away_recent,
        team_id=match.away_team_id,
        team_name=match.away_team,
    )
    statistics_summary = (
        MatchStatisticsSummary(
            home=home_stats,
            away=away_stats,
            home_recent_fixtures=home_fixtures[:10],
            away_recent_fixtures=away_fixtures[:10],
        )
        if home_stats or away_stats or home_fixtures or away_fixtures
        else None
    )
    team_status = "available" if home_stats and away_stats else "partial" if statistics_summary else "unavailable"
    add_coverage(
        "team_statistics",
        team_status,
        "/teams/statistics + /fixtures?ids",
        reason=None if team_status == "available" else provider_unavailable_reason or "Faltan estadísticas verificadas de uno o ambos equipos.",
        sample_size=len(home_fixtures) + len(away_fixtures),
    )

    standings_context = _standings_context(standings, match)
    standings_supported = _coverage_flag(league_coverage, "standings")
    standings_status = (
        "available"
        if standings_context and standings_context.home and standings_context.away
        else "partial"
        if standings_context
        else "unavailable"
        if standings_requested
        else "not_requested"
    )
    add_coverage(
        "standings",
        standings_status,
        "/standings",
        reason=(
            None
            if standings_context and standings_context.home and standings_context.away
            else provider_unavailable_reason
            if provider_unavailable_reason
            else "Omitido por el perfil de cuota configurado; los datos recientes siguen activos."
            if not standings_requested
            else "La competición no publicó una clasificación completa para ambos equipos."
            if standings_supported is not False
            else "API-Football marca standings=false para esta liga/temporada."
        ),
        sample_size=sum(context is not None for context in (standings_context.home, standings_context.away)) if standings_context else 0,
    )

    add_coverage(
        "h2h",
        "available" if h2h else "unavailable",
        "/fixtures/headtohead",
        reason=(
            None
            if h2h
            else provider_unavailable_reason
            or ("La consulta H2H falló temporalmente." if h2h_failed else "No se publicaron enfrentamientos directos previos.")
        ),
        sample_size=len(h2h),
    )
    recent_count = len(home_fixtures) + len(away_fixtures)
    recent_status = "available" if home_fixtures and away_fixtures else "partial" if recent_count else "unavailable"
    if recent_status == "available":
        recent_reason = None
    elif provider_unavailable_reason:
        recent_reason = provider_unavailable_reason
    elif home_history_failed or away_history_failed:
        recent_reason = "Falló al menos una consulta de forma reciente."
    else:
        recent_reason = "Faltan partidos recientes verificables de uno o ambos equipos."
    add_coverage(
        "recent_fixtures",
        recent_status,
        "/fixtures?team + /fixtures?ids",
        reason=recent_reason,
        sample_size=recent_count,
    )

    home_recent_players = _aggregate_players(
        home_history,
        team_id=match.home_team_id,
        team_name=match.home_team,
    )
    away_recent_players = _aggregate_players(
        away_history,
        team_id=match.away_team_id,
        team_name=match.away_team,
    )
    home_players = _player_rows(home_player_statistics, 12) or home_recent_players
    away_players = _player_rows(away_player_statistics, 12) or away_recent_players
    normalized_top_scorers = _player_rows(top_scorers, 5)
    normalized_top_assists = _player_rows(top_assists, 5)
    normalized_top_yellow = _player_rows(top_yellow_cards, 5)
    normalized_top_red = _player_rows(top_red_cards, 5)
    player_context = (
        PlayerContext(
            home=home_players,
            away=away_players,
            top_scorers=normalized_top_scorers,
            top_assists=normalized_top_assists,
            top_yellow_cards=normalized_top_yellow,
            top_red_cards=normalized_top_red,
        )
        if home_players
        or away_players
        or normalized_top_scorers
        or normalized_top_assists
        or normalized_top_yellow
        or normalized_top_red
        else None
    )
    player_status = "available" if home_players and away_players else "partial" if player_context else "unavailable"
    player_supported = _coverage_flag(league_coverage, "fixtures", "statistics_players")
    add_coverage(
        "players",
        player_status,
        "/fixtures?ids (players)",
        reason=(
            None
            if player_status == "available"
            else provider_unavailable_reason
            if provider_unavailable_reason
            else "API-Football marca statistics_players=false para esta liga/temporada."
            if player_supported is False
            else "No hubo una muestra verificable de rendimiento individual reciente."
        ),
        sample_size=(
            len(home_players)
            + len(away_players)
            + len(normalized_top_scorers)
            + len(normalized_top_assists)
            + len(normalized_top_yellow)
            + len(normalized_top_red)
        ),
    )

    injuries_supported = _coverage_flag(league_coverage, "injuries")
    injuries_status = "unavailable" if provider_unavailable_reason or injuries_failed or injuries_supported is False else "available"
    if provider_unavailable_reason:
        injuries_reason = provider_unavailable_reason
    elif injuries_failed:
        injuries_reason = "La consulta de lesiones falló."
    elif injuries_supported is False:
        injuries_reason = "API-Football marca injuries=false para esta liga/temporada."
    elif not injuries:
        injuries_reason = "Sin bajas reportadas por el proveedor."
    else:
        injuries_reason = None
    add_coverage(
        "injuries",
        injuries_status,
        "/injuries",
        reason=injuries_reason,
        sample_size=len(injuries),
    )

    lineups_supported = _coverage_flag(league_coverage, "fixtures", "lineups")
    if provider_unavailable_reason:
        lineup_status = "not_requested"
        lineup_reason = provider_unavailable_reason
    elif not lineups_requested:
        lineup_status = "not_requested"
        lineup_reason = "Las alineaciones sólo se consultan dentro de la ventana previa al inicio."
    elif lineups_failed or lineups_supported is False:
        lineup_status = "unavailable"
        lineup_reason = "La consulta de alineaciones falló." if lineups_failed else "API-Football marca lineups=false para esta liga/temporada."
    elif lineups and (lineups.home or lineups.away):
        lineup_status = "available" if lineups.confirmed else "partial"
        lineup_reason = None if lineups.confirmed else "Alineación probable o confirmada sólo parcialmente."
    else:
        lineup_status = "partial"
        lineup_reason = "El proveedor aún no publicó las alineaciones."
    add_coverage(
        "lineups",
        lineup_status,
        "/fixtures/lineups",
        reason=lineup_reason,
        sample_size=(len(lineups.home.start_xi) if lineups and lineups.home else 0) + (len(lineups.away.start_xi) if lineups and lineups.away else 0),
    )

    normalized_prediction = _prediction(provider_prediction)
    predictions_supported = _coverage_flag(league_coverage, "predictions")
    if provider_unavailable_reason:
        prediction_reason = provider_unavailable_reason
    elif prediction_failed:
        prediction_reason = "La consulta de predictions falló."
    elif not prediction_requested:
        prediction_reason = "Omitido por el perfil de cuota; API-Football predictions es una señal secundaria."
    elif predictions_supported is False:
        prediction_reason = "API-Football marca predictions=false para esta liga/temporada."
    elif normalized_prediction is None:
        prediction_reason = "El proveedor no publicó una predicción para este partido."
    else:
        prediction_reason = None
    add_coverage(
        "provider_prediction",
        "available" if normalized_prediction else "unavailable" if prediction_requested else "not_requested",
        "/predictions",
        reason=prediction_reason,
        sample_size=1 if normalized_prediction else 0,
    )

    verified_odds: list[VerifiedOddsEvidence] = []
    for market_key, quote in odds_quotes.items():
        odd = _number(getattr(quote, "odds", None))
        bookmaker = str(getattr(quote, "bookmaker", "") or "").strip()
        if odd is None or odd <= 1 or not bookmaker:
            continue
        captured = _parse_datetime(getattr(quote, "updated_at", None))
        verified_odds.append(
            VerifiedOddsEvidence(
                market_key=str(market_key),
                selection=str(market_key),
                odds=odd,
                bookmaker=bookmaker,
                captured_at=captured or now,
                provenance=provenance("/odds")[0],
            )
        )
    odds_supported = _coverage_flag(league_coverage, "odds")
    if provider_unavailable_reason:
        odds_reason = provider_unavailable_reason
    elif odds_failed:
        odds_reason = "La consulta de cuotas falló."
    elif odds_supported is False:
        odds_reason = "API-Football marca odds=false para esta liga/temporada."
    elif not verified_odds:
        odds_reason = "No hay cotizaciones prepartido exactas dentro de la ventana disponible."
    else:
        odds_reason = None
    add_coverage(
        "verified_odds",
        "available" if verified_odds else "unavailable",
        "/odds",
        reason=odds_reason,
        sample_size=len(verified_odds),
    )

    return MatchEvidenceContext(
        data_coverage=coverage,
        statistics_summary=statistics_summary,
        standings=standings_context,
        h2h=h2h,
        recent_fixtures=[*home_fixtures[:10], *away_fixtures[:10]],
        player_context=player_context,
        injuries=injuries,
        lineups=lineups,
        provider_prediction=normalized_prediction,
        verified_odds=verified_odds,
    )
