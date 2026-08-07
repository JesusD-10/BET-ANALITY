from datetime import datetime, timezone
from app.schemas.matches import MatchSummary, RefereeInfo, InjuryItem, H2HMatchItem
from app.services.ai_analyzer import analyze_match_with_ai, _generate_local_fallback_analysis


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
