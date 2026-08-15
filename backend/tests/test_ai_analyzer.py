from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app.schemas.matches import (
    EvidenceCoverageItem,
    EvidenceProvenance,
    H2HMatchItem,
    InjuryItem,
    MatchEvidenceContext,
    MatchStatisticsSummary,
    MatchSummary,
    PlayerContext,
    PlayerStatisticsSnapshot,
    RefereeInfo,
    TeamStatisticsSnapshot,
    VerifiedOddsEvidence,
)
from app.services.ai_analyzer import (
    _available_market_families,
    _consensus_market_payloads,
    _format_recent_history,
    _generate_local_fallback_analysis,
    _goal_profile,
    _structured_evidence_payload,
    analyze_match_with_ai,
)
from app.services.ai_gateway import ai_gateway


def test_local_fallback_analysis_returns_rich_context():
    match = MatchSummary(
        id="demo-test-1",
        competition="La Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Real Madrid",
        away_team="Barcelona",
        venue="Santiago Bernabéu",
        referee="Mateu Lahoz",
        data_quality=0.9,
        odds_available=True,
        status="PROGRAMADO",
    )

    referee = RefereeInfo(name="Mateu Lahoz", yellow_cards_avg=5.1, red_cards_avg=0.3, tendency="Amonesta temprano")
    injuries = [InjuryItem(player="Courtois", team="Real Madrid", reason="Lesión de rodilla", status="Baja confirmada")]
    h2h = [H2HMatchItem(date="2025-10-26", competition="La Liga", home_team="Real Madrid", away_team="Barcelona", score="3 - 1", winner="Real Madrid")]

    analysis = _generate_local_fallback_analysis(match, referee, injuries, None, h2h)

    assert analysis.match.home_team == "Real Madrid"
    assert len(analysis.markets) >= 3
    assert analysis.referee_info.name == "Mateu Lahoz"
    assert len(analysis.injuries) == 1
    assert analysis.injuries[0].player == "Courtois"
    assert len(analysis.h2h_matches) == 1
    assert analysis.markets[0].fair_odds > 1.0


def test_multi_ai_advanced_market_is_filtered_without_source_statistics(monkeypatch):
    match = MatchSummary(
        id="provider-1",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        data_quality=0.8,
        odds_available=False,
        status="PROGRAMADO",
    )
    content = json.dumps(
        {
            "markets": [
                {
                    "market_key": "TOTAL_CORNERS_OVER_9_5",
                    "label": "Córners",
                    "selection": "Más de 9.5",
                    "probability": 0.55,
                    "fair_odds": 1.82,
                    "best_odds": 2.0,
                    "expected_value": 0.1,
                    "confidence": "Media",
                    "data_quality": 0.8,
                    "factors_for": ["Supuesto no respaldado"],
                    "risks": ["Sin datos"],
                },
                {
                    "market_key": "TOTAL_GOALS_OVER_2_5",
                    "label": "Goles",
                    "selection": "Más de 2.5",
                    "probability": 0.52,
                    "fair_odds": 1.92,
                    "best_odds": 2.05,
                    "expected_value": 0.066,
                    "confidence": "Media",
                    "data_quality": 0.8,
                    "factors_for": ["Marcadores recientes"],
                    "risks": ["Varianza"],
                },
            ]
        }
    )
    completion = SimpleNamespace(
        json_data=json.loads(content),
        provider="cerebras",
        model="gpt-oss-120b",
    )
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(ai_gateway, "complete_json_consensus", lambda **_: [completion])

    analysis = analyze_match_with_ai(match)

    assert [market.market_key for market in analysis.markets] == ["TOTAL_GOALS_OVER_2_5"]
    assert analysis.markets[0].best_odds is None
    assert analysis.markets[0].expected_value is None
    assert analysis.model_version == "multi-ai-cerebras-gpt-oss-120b"


def test_consensus_averages_probabilities_and_backend_recalculates_fair_odds(monkeypatch):
    match = MatchSummary(
        id="consensus-1",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        data_quality=0.8,
        odds_available=False,
        status="PROGRAMADO",
    )
    first = SimpleNamespace(
        json_data={
            "markets": [
                {
                    "market_key": "TOTAL_GOALS_OVER_2_5",
                    "label": "Goles",
                    "selection": "Más de 2.5",
                    "probability": 0.60,
                    "fair_odds": 99.0,
                    "confidence": "Media",
                    "data_quality": 0.8,
                    "factors_for": ["Forma"],
                    "risks": ["Varianza"],
                }
            ],
            "notes": [],
        },
        provider="cerebras",
        model="gpt-oss-120b",
    )
    second = SimpleNamespace(
        json_data={
            "markets": [
                {
                    "market_key": "TOTAL_GOALS_OVER_2_5",
                    "label": "Total goles",
                    "selection": "más de 2.5",
                    "probability": 0.70,
                    "fair_odds": 1.01,
                    "confidence": "Alta",
                    "data_quality": 0.8,
                    "factors_for": ["Historial"],
                    "risks": ["Ritmo"],
                }
            ]
        },
        provider="openrouter",
        model="openrouter/free",
    )
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(
        ai_gateway,
        "complete_json_consensus",
        lambda **_: [first, second],
    )

    analysis = analyze_match_with_ai(match)

    assert analysis.markets[0].probability == pytest.approx(0.65)
    assert analysis.markets[0].fair_odds == 1.54
    assert analysis.markets[0].best_odds is None
    assert analysis.model_version == "multi-ai-consensus-cerebras+openrouter"
    assert analysis.markets[0].factors_for == ["Forma", "Historial"]
    assert analysis.markets[0].risks == ["Varianza", "Ritmo"]
    assert any("participaron 2" in note for note in analysis.notes)


def test_consensus_does_not_average_opposing_selections() -> None:
    completions = [
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "BOTH_TEAMS_TO_SCORE",
                        "selection": "Sí",
                        "probability": 0.62,
                    }
                ]
            }
        ),
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "BOTH_TEAMS_TO_SCORE",
                        "selection": "No",
                        "probability": 0.38,
                    }
                ]
            }
        ),
    ]

    markets = _consensus_market_payloads(completions, {"result", "goals"})

    assert markets == []


def test_consensus_uses_median_for_three_and_merges_deduplicated_evidence() -> None:
    completions = [
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "TOTAL_GOALS_OVER_2_5",
                        "selection": "Más de 2.5",
                        "probability": 0.20,
                        "data_quality": 0.95,
                        "factors_for": ["Forma reciente", "Ataque local"],
                        "risks": ["Rotaciones"],
                    }
                ]
            }
        ),
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "TOTAL_GOALS_OVER_2_5",
                        "selection": "más de 2.5",
                        "probability": 0.60,
                        "data_quality": 0.80,
                        "factors_for": ["forma reciente", "Lesiones defensivas"],
                        "risks": ["Clima"],
                    }
                ]
            }
        ),
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "TOTAL_GOALS_OVER_2_5",
                        "selection": "MÁS DE 2.5",
                        "probability": 0.90,
                        "data_quality": 0.70,
                        "factors_for": ["Ritmo alto"],
                        "risks": ["rotaciones", "Varianza"],
                    }
                ]
            }
        ),
    ]

    markets = _consensus_market_payloads(completions, {"result", "goals"})

    assert markets[0]["probability"] == 0.60
    assert markets[0]["data_quality"] == 0.80
    assert markets[0]["confidence"] == "Media"
    assert markets[0]["factors_for"] == [
        "Forma reciente",
        "Ataque local",
        "Lesiones defensivas",
        "Ritmo alto",
    ]
    assert markets[0]["risks"] == ["Rotaciones", "Clima", "Varianza"]


def test_consensus_four_uses_middle_estimates_and_discards_two_two_tie() -> None:
    probabilities = [0.10, 0.60, 0.70, 0.95]
    completions = []
    for index, probability in enumerate(probabilities):
        completions.append(
            SimpleNamespace(
                json_data={
                    "markets": [
                        {
                            "market_key": "TOTAL_GOALS_OVER_2_5",
                            "selection": "Más de 2.5",
                            "probability": probability,
                        },
                        {
                            "market_key": "BOTH_TEAMS_TO_SCORE",
                            "selection": "Sí" if index < 2 else "No",
                            "probability": 0.55 + index * 0.02,
                        },
                    ]
                }
            )
        )

    markets = _consensus_market_payloads(completions, {"result", "goals"})

    assert [market["market_key"] for market in markets] == ["TOTAL_GOALS_OVER_2_5"]
    assert markets[0]["probability"] == pytest.approx(0.65)


def test_consensus_requires_two_supporters_after_multiple_successes() -> None:
    completions = [
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "WINNER_HOME",
                        "selection": "Local",
                        "probability": 0.60,
                    }
                ]
            }
        ),
        SimpleNamespace(
            json_data={
                "markets": [
                    {
                        "market_key": "TOTAL_GOALS_OVER_2_5",
                        "selection": "Más de 2.5",
                        "probability": 0.58,
                    }
                ]
            }
        ),
    ]

    assert _consensus_market_payloads(completions, {"result", "goals"}) == []


def test_consensus_uses_the_only_selection_with_adaptive_quorum() -> None:
    completions = []
    for selection, probability in (
        ("Sí", 0.62),
        ("si", 0.66),
        ("No", 0.40),
    ):
        completions.append(
            SimpleNamespace(
                json_data={
                    "markets": [
                        {
                            "market_key": "BOTH_TEAMS_TO_SCORE",
                            "selection": selection,
                            "probability": probability,
                        }
                    ]
                }
            )
        )

    markets = _consensus_market_payloads(completions, {"result", "goals"})

    assert markets[0]["selection"] == "Sí"
    assert markets[0]["probability"] == pytest.approx(0.64)
    assert markets[0]["confidence"] == "Media-alta"


def test_analysis_caps_consensus_quality_and_reports_four_participants(monkeypatch):
    match = MatchSummary(
        id="consensus-four",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        data_quality=0.76,
        odds_available=False,
        status="PROGRAMADO",
    )
    completions = []
    for index, provider in enumerate(("deepseek", "cerebras", "xai", "openrouter")):
        completions.append(
            SimpleNamespace(
                json_data={
                    "markets": [
                        {
                            "market_key": "TOTAL_GOALS_OVER_2_5",
                            "label": "Goles",
                            "selection": "Más de 2.5",
                            "probability": 0.60 + index * 0.01,
                            "data_quality": 0.99,
                            "factors_for": [f"Factor {index}"],
                            "risks": [f"Riesgo {index}"],
                        }
                    ],
                    "notes": [],
                },
                provider=provider,
                model=f"model-{index}",
            )
        )
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(
        ai_gateway,
        "complete_json_consensus",
        lambda **_: completions,
    )

    analysis = analyze_match_with_ai(match)

    assert analysis.markets[0].probability == pytest.approx(0.615)
    assert analysis.markets[0].data_quality == match.data_quality
    assert analysis.markets[0].confidence == "Alta"
    assert len(analysis.markets[0].factors_for) == 4
    assert any("participaron 4" in note for note in analysis.notes)
    assert analysis.ai_consensus.requested == 4
    assert analysis.ai_consensus.completed == 4
    assert analysis.ai_consensus.providers == [
        "deepseek",
        "cerebras",
        "xai",
        "openrouter",
    ]
    assert analysis.ai_consensus.required_support == 2
    assert analysis.ai_consensus.status == "consensus"


def test_advanced_families_activate_only_from_explicit_statistics():
    families = _available_market_families(
        None,
        [
            {
                "statistics": [{"corners": 8, "total_shots": 14}],
                "player_statistics": [
                    {
                        "player": {"name": "Delantero"},
                        "shots": {"total": 3},
                        "goals": {"total": 1},
                    }
                ],
            }
        ],
        [],
    )

    assert {"corners", "team_shots", "player_shots", "player_goals"}.issubset(families)
    assert "player_shots_on_target" not in families


def test_player_shots_do_not_enable_team_shots_and_null_does_not_enable_market():
    families = _available_market_families(
        None,
        [
            {
                "statistics": [{"corners": None}],
                "player_statistics": [{"shots": {"total": 2, "on_target": None}}],
            }
        ],
        [],
    )

    assert "player_shots" in families
    assert "player_shots_on_target" not in families
    assert "team_shots" not in families
    assert "corners" not in families


def test_prompt_history_omits_duplicate_raw_player_payload():
    text = _format_recent_history(
        [
            {
                "fixture": {"date": "2026-08-10"},
                "players": [{"large": "raw-payload"}],
                "player_statistics": [{"player": {"name": "Nueve"}, "shots": {"total": 3}}],
            }
        ]
    )

    assert "player_statistics" in text
    assert "raw-payload" not in text


def test_normalized_recent_matches_are_accepted_without_enabling_advanced_markets():
    recent = H2HMatchItem(
        date="2026-08-10",
        competition="Liga",
        home_team="Local",
        away_team="Rival",
        score="2 - 1",
        winner="Local",
    )

    families = _available_market_families(None, [recent], [])

    assert families == {"result", "goals"}


def test_local_fallback_uses_match_scores_instead_of_team_name_hashes():
    match = MatchSummary(
        id="same-fixture",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        home_team_id="1",
        away_team_id="2",
        data_quality=0.9,
        status="PROGRAMADO",
    )
    high_scoring = [
        H2HMatchItem(
            date=f"2026-08-0{index}",
            competition="Liga",
            home_team="Local",
            away_team=f"Rival {index}",
            score="3 - 2",
            winner="Local",
        )
        for index in range(1, 6)
    ]
    low_scoring = [
        H2HMatchItem(
            date=f"2026-07-0{index}",
            competition="Liga",
            home_team="Local",
            away_team=f"Rival {index}",
            score="1 - 0",
            winner="Local",
        )
        for index in range(1, 6)
    ]

    high = _generate_local_fallback_analysis(
        match,
        None,
        [],
        None,
        [],
        high_scoring,
        [],
    )
    low = _generate_local_fallback_analysis(
        match,
        None,
        [],
        None,
        [],
        low_scoring,
        [],
    )
    high_markets = {market.market_key: market for market in high.markets}
    low_markets = {market.market_key: market for market in low.markets}

    assert high_markets["TOTAL_GOALS_OVER_1_5"].probability > low_markets["TOTAL_GOALS_OVER_1_5"].probability
    assert high_markets["TOTAL_GOALS_UNDER_3_5"].probability < low_markets["TOTAL_GOALS_UNDER_3_5"].probability


def test_goal_profile_deduplicates_same_fixture_with_timestamp_and_date():
    raw = {
        "fixture": {"date": "2026-08-10T20:00:00+00:00"},
        "teams": {
            "home": {"id": 1, "name": "Local"},
            "away": {"id": 2, "name": "Visitante"},
        },
        "goals": {"home": 2, "away": 1},
    }
    normalized = H2HMatchItem(
        date="2026-08-10",
        competition="Liga",
        home_team="Local",
        away_team="Visitante",
        score="2 - 1",
        winner="Local",
    )

    samples, *_ = _goal_profile([raw], [normalized])

    assert samples == 1


def _structured_evidence() -> MatchEvidenceContext:
    fetched_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    source = EvidenceProvenance(
        provider="api-football",
        endpoint="/teams/statistics",
        fetched_at=fetched_at,
        verified=True,
    )
    odds_source = EvidenceProvenance(
        provider="api-football",
        endpoint="/odds",
        fetched_at=fetched_at,
        verified=True,
    )
    return MatchEvidenceContext(
        data_coverage=[
            EvidenceCoverageItem(
                section="team_statistics",
                status="available",
                sample_size=12,
                provenance=[source],
            ),
            EvidenceCoverageItem(
                section="players",
                status="unavailable",
                reason="La competición no ofrece estadísticas de jugadores.",
                provenance=[],
            ),
            EvidenceCoverageItem(
                section="verified_odds",
                status="available",
                sample_size=1,
                provenance=[odds_source],
            ),
        ],
        statistics_summary=MatchStatisticsSummary(
            home=TeamStatisticsSnapshot(
                team_id="1",
                team_name="Local",
                fixtures_played=12,
                averages={"corners": 6.1, "total_shots": 13.2},
            ),
            away=TeamStatisticsSnapshot(
                team_id="2",
                team_name="Visitante",
                fixtures_played=12,
                averages={"corners": 4.8, "total_shots": 9.4},
            ),
        ),
        # Populated deliberately, but unavailable/unverified coverage must keep
        # this block out of every AI prompt and response.
        player_context=PlayerContext(
            home=[PlayerStatisticsSnapshot(player_name="Jugador fantasma", goals=99)]
        ),
        verified_odds=[
            VerifiedOddsEvidence(
                market_key="TOTAL_CORNERS_OVER_9_5",
                selection="Más de 9.5 córners",
                odds=9.99,
                bookmaker="HiddenBook",
                captured_at=fetched_at,
                provenance=odds_source,
            )
        ],
    )


def test_structured_evidence_is_provenance_gated_and_redacts_bookmaker_prices():
    payload = _structured_evidence_payload(_structured_evidence())
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["statistics_summary"]["home"]["averages"]["corners"] == 6.1
    assert "player_context" not in payload
    assert payload["verified_market_availability"] == [
        {
            "market_key": "TOTAL_CORNERS_OVER_9_5",
            "selection": "Más de 9.5 córners",
            "live": False,
        }
    ]
    assert "9.99" not in serialized
    assert "HiddenBook" not in serialized


def test_ai_receives_structured_sporting_evidence_without_odds_anchor(monkeypatch):
    match = MatchSummary(
        id="structured-1",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        league_id="10",
        season=2026,
        data_quality=0.84,
        odds_available=True,
        status="PROGRAMADO",
    )
    completion = SimpleNamespace(
        json_data={
            "markets": [
                {
                    "market_key": "TOTAL_CORNERS_OVER_9_5",
                    "label": "Córners",
                    "selection": "Más de 9.5 córners",
                    "probability": 0.61,
                    "best_odds": 9.99,
                    "expected_value": 5.0,
                    "data_quality": 0.8,
                    "factors_for": ["Promedios de córners de ambos equipos"],
                    "risks": ["Varianza del ritmo"],
                    "evidence_refs": ["team_statistics", "verified_odds"],
                }
            ],
            "notes": [],
        },
        provider="cerebras",
        model="gpt-oss-120b",
    )
    captured: dict = {}

    def complete(**kwargs):
        captured.update(kwargs)
        return [completion]

    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(ai_gateway, "complete_json_consensus", complete)

    analysis = analyze_match_with_ai(match, evidence=_structured_evidence())
    prompt = captured["messages"][1]["content"]

    assert analysis.markets[0].market_key == "TOTAL_CORNERS_OVER_9_5"
    assert analysis.markets[0].evidence_refs == ["team_statistics"]
    assert analysis.markets[0].best_odds is None
    assert analysis.markets[0].expected_value is None
    assert analysis.statistics_summary is not None
    assert analysis.player_context is None
    assert analysis.verified_odds[0].odds == 9.99
    assert analysis.ai_consensus.requested == 4
    assert analysis.ai_consensus.completed == 1
    assert analysis.ai_consensus.status == "single"
    assert "9.99" not in prompt
    assert "HiddenBook" not in prompt
    assert "no ancles tu estimación" in prompt


def test_structured_ai_market_without_verified_evidence_reference_is_rejected(monkeypatch):
    match = MatchSummary(
        id="structured-no-ref",
        competition="Liga",
        kickoff_at=datetime.now(timezone.utc),
        home_team="Local",
        away_team="Visitante",
        data_quality=0.8,
        status="PROGRAMADO",
    )
    completion = SimpleNamespace(
        json_data={
            "markets": [
                {
                    "market_key": "TOTAL_GOALS_OVER_2_5",
                    "selection": "Más de 2.5",
                    "probability": 0.8,
                    "evidence_refs": ["players"],
                }
            ]
        },
        provider="openrouter",
        model="free",
    )
    monkeypatch.setattr(ai_gateway, "is_available", lambda: True)
    monkeypatch.setattr(ai_gateway, "complete_json_consensus", lambda **_: [completion])

    analysis = analyze_match_with_ai(match, evidence=_structured_evidence())

    assert analysis.model_version == "baseline-poisson-v0.3"
    assert analysis.ai_consensus.completed == 1
    assert analysis.ai_consensus.status == "fallback"
    assert "ninguna selección" in analysis.ai_consensus.reason
