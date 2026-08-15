from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class InjuryItem(BaseModel):
    player: str
    team: str
    reason: str  # ej: "Lesión de rodilla", "Sanción por amarillas"
    status: str = "Baja confirmada"  # "Baja confirmada", "Duda", "Sancionado"
    type: str | None = None


class RefereeInfo(BaseModel):
    name: str
    yellow_cards_avg: float | None = None
    red_cards_avg: float | None = None
    fouls_per_game: float | None = None
    tendency: str | None = None  # ej: "Propenso a amonestar temprano"


class TeamDisciplineAverage(BaseModel):
    team_name: str
    sample_size: int = 0
    fouls_avg: float | None = None
    yellow_cards_avg: float | None = None
    red_cards_avg: float | None = None


class DisciplineSummary(BaseModel):
    home: TeamDisciplineAverage | None = None
    away: TeamDisciplineAverage | None = None
    note: str


class PlayerLineup(BaseModel):
    id: int | None = None
    name: str
    number: int | None = None
    pos: str | None = None
    grid: str | None = None


class TeamLineup(BaseModel):
    team_name: str
    formation: str | None = None
    coach: str | None = None
    start_xi: list[PlayerLineup] = Field(default_factory=list)
    substitutes: list[PlayerLineup] = Field(default_factory=list)
    confirmed: bool = False
    source: str = "recent_form"
    sample_size: int | None = None


class LineupsSummary(BaseModel):
    confirmed: bool = False
    home: TeamLineup | None = None
    away: TeamLineup | None = None
    status: str = "pending"
    note: str | None = None


class H2HMatchItem(BaseModel):
    date: str
    competition: str
    home_team: str
    away_team: str
    score: str
    winner: str | None = None


class MatchSummary(BaseModel):
    id: str
    competition: str
    kickoff_at: datetime
    home_team: str
    away_team: str
    home_team_id: str | None = None
    away_team_id: str | None = None
    league_id: str | None = None
    season: int | None = None
    round: str | None = None
    venue_id: str | None = None
    venue: str | None = None
    referee: str | None = None
    home_form: str | None = None  # ej: "W-W-D-L-W"
    away_form: str | None = None  # ej: "L-W-W-D-W"
    home_logo: str | None = None
    away_logo: str | None = None
    data_quality: float = Field(default=0.9, ge=0, le=1)
    odds_available: bool = False
    status: str
    source_provider: str = "mock"
    source_url: str | None = None
    external_id: str | None = None


EvidenceStatus = Literal["available", "partial", "unavailable", "not_requested"]
EvidenceSection = Literal[
    "team_statistics",
    "standings",
    "h2h",
    "recent_fixtures",
    "players",
    "injuries",
    "lineups",
    "provider_prediction",
    "verified_odds",
]


class EvidenceProvenance(BaseModel):
    provider: str
    endpoint: str
    fetched_at: datetime | None = None
    verified: bool = True


class EvidenceCoverageItem(BaseModel):
    section: EvidenceSection
    status: EvidenceStatus
    reason: str | None = None
    sample_size: int | None = Field(default=None, ge=0)
    provenance: list[EvidenceProvenance] = Field(default_factory=list)


class TeamStatisticsSnapshot(BaseModel):
    team_id: str | None = None
    team_name: str
    form: str | None = None
    fixtures_played: int | None = Field(default=None, ge=0)
    wins: int | None = Field(default=None, ge=0)
    draws: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    goals_for: int | None = Field(default=None, ge=0)
    goals_against: int | None = Field(default=None, ge=0)
    goals_for_avg: float | None = Field(default=None, ge=0)
    goals_against_avg: float | None = Field(default=None, ge=0)
    clean_sheets: int | None = Field(default=None, ge=0)
    failed_to_score: int | None = Field(default=None, ge=0)
    averages: dict[str, float | None] = Field(default_factory=dict)
    rates: dict[str, float | None] = Field(default_factory=dict)


class FixtureStatisticsSnapshot(BaseModel):
    fixture_id: str
    date: datetime | None = None
    competition: str | None = None
    home_team: str
    away_team: str
    home_goals: int | None = Field(default=None, ge=0)
    away_goals: int | None = Field(default=None, ge=0)
    home_statistics: dict[str, float | int | None] = Field(default_factory=dict)
    away_statistics: dict[str, float | int | None] = Field(default_factory=dict)


class MatchStatisticsSummary(BaseModel):
    home: TeamStatisticsSnapshot | None = None
    away: TeamStatisticsSnapshot | None = None
    home_recent_fixtures: list[FixtureStatisticsSnapshot] = Field(default_factory=list)
    away_recent_fixtures: list[FixtureStatisticsSnapshot] = Field(default_factory=list)


class StandingSnapshot(BaseModel):
    team_id: str | None = None
    team_name: str
    rank: int | None = Field(default=None, ge=1)
    points: int | None = Field(default=None, ge=0)
    played: int | None = Field(default=None, ge=0)
    wins: int | None = Field(default=None, ge=0)
    draws: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    goals_for: int | None = Field(default=None, ge=0)
    goals_against: int | None = Field(default=None, ge=0)
    goal_difference: int | None = None
    form: str | None = None
    description: str | None = None


class StandingsContext(BaseModel):
    league_id: str | None = None
    season: int | None = None
    home: StandingSnapshot | None = None
    away: StandingSnapshot | None = None


class PlayerStatisticsSnapshot(BaseModel):
    player_id: str | None = None
    player_name: str
    team_id: str | None = None
    team_name: str | None = None
    position: str | None = None
    appearances: int | None = Field(default=None, ge=0)
    starts: int | None = Field(default=None, ge=0)
    minutes: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0)
    goals: int | None = Field(default=None, ge=0)
    assists: int | None = Field(default=None, ge=0)
    shots: int | None = Field(default=None, ge=0)
    shots_on_target: int | None = Field(default=None, ge=0)
    key_passes: int | None = Field(default=None, ge=0)
    tackles: int | None = Field(default=None, ge=0)
    interceptions: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    yellow_cards: int | None = Field(default=None, ge=0)
    red_cards: int | None = Field(default=None, ge=0)


class PlayerContext(BaseModel):
    home: list[PlayerStatisticsSnapshot] = Field(default_factory=list)
    away: list[PlayerStatisticsSnapshot] = Field(default_factory=list)
    top_scorers: list[PlayerStatisticsSnapshot] = Field(default_factory=list)
    top_assists: list[PlayerStatisticsSnapshot] = Field(default_factory=list)
    top_yellow_cards: list[PlayerStatisticsSnapshot] = Field(default_factory=list)
    top_red_cards: list[PlayerStatisticsSnapshot] = Field(default_factory=list)


class ProviderPredictionEvidence(BaseModel):
    winner_id: str | None = None
    winner_name: str | None = None
    winner_comment: str | None = None
    advice: str | None = None
    win_or_draw: bool | None = None
    under_over: str | None = None
    goals_home: str | None = None
    goals_away: str | None = None
    percent_home: float | None = Field(default=None, ge=0, le=1)
    percent_draw: float | None = Field(default=None, ge=0, le=1)
    percent_away: float | None = Field(default=None, ge=0, le=1)


class VerifiedOddsEvidence(BaseModel):
    market_key: str
    selection: str
    odds: float = Field(gt=1)
    bookmaker: str
    captured_at: datetime | None = None
    live: bool = False
    provenance: EvidenceProvenance


class MatchEvidenceContext(BaseModel):
    data_coverage: list[EvidenceCoverageItem] = Field(default_factory=list)
    statistics_summary: MatchStatisticsSummary | None = None
    standings: StandingsContext | None = None
    h2h: list[H2HMatchItem] = Field(default_factory=list)
    recent_fixtures: list[FixtureStatisticsSnapshot] = Field(default_factory=list)
    player_context: PlayerContext | None = None
    injuries: list[InjuryItem] = Field(default_factory=list)
    lineups: LineupsSummary | None = None
    provider_prediction: ProviderPredictionEvidence | None = None
    verified_odds: list[VerifiedOddsEvidence] = Field(default_factory=list)


class AIConsensusSummary(BaseModel):
    requested: int = Field(default=4, ge=1)
    completed: int = Field(default=0, ge=0)
    providers: list[str] = Field(default_factory=list)
    required_support: int = Field(default=0, ge=0)
    status: Literal["consensus", "partial", "single", "unavailable", "fallback"]
    reason: str | None = None


class MatchListResponse(BaseModel):
    date: date
    matches: list[MatchSummary]
    source: str = "mock"
    notice: str | None = None


class MarketAnalysis(BaseModel):
    market_key: str
    label: str
    selection: str
    probability: float = Field(ge=0, le=1)
    fair_odds: float = Field(gt=1)
    best_odds: float | None = Field(default=None, gt=1)
    bookmaker: str | None = None
    expected_value: float | None = None
    confidence: str
    data_quality: float = Field(ge=0, le=1)
    factors_for: list[str]
    risks: list[str]
    evidence_refs: list[EvidenceSection] = Field(default_factory=list)


class CombinationLeg(BaseModel):
    market_key: str
    label: str
    selection: str


class CombinationAnalysis(BaseModel):
    id: str
    label: str
    selection: str
    legs: list[CombinationLeg]
    probability: float = Field(ge=0, le=1)
    fair_odds: float = Field(gt=1)
    best_odds: float | None = Field(default=None, gt=1)
    expected_value: float | None = None
    confidence: str
    data_quality: float = Field(ge=0, le=1)
    factors_for: list[str]
    risks: list[str]
    evidence_refs: list[EvidenceSection] = Field(default_factory=list)
    correlation_note: str
    kind: str = "combination"


class MatchAnalysisResponse(BaseModel):
    match: MatchSummary
    model_version: str
    updated_at: datetime
    referee_info: RefereeInfo | None = None
    discipline: DisciplineSummary | None = None
    injuries: list[InjuryItem] = []
    lineups: LineupsSummary | None = None
    h2h_matches: list[H2HMatchItem] = []
    home_recent_matches: list[H2HMatchItem] = Field(default_factory=list)
    away_recent_matches: list[H2HMatchItem] = Field(default_factory=list)
    data_coverage: list[EvidenceCoverageItem] = Field(default_factory=list)
    statistics_summary: MatchStatisticsSummary | None = None
    standings: StandingsContext | None = None
    provider_prediction: ProviderPredictionEvidence | None = None
    player_context: PlayerContext | None = None
    verified_odds: list[VerifiedOddsEvidence] = Field(default_factory=list)
    ai_consensus: AIConsensusSummary | None = None
    tactical_summary: str | None = None
    injuries_impact: str | None = None
    referee_impact: str | None = None
    markets: list[MarketAnalysis]
    combinations: list[CombinationAnalysis] = Field(default_factory=list)
    dream_picks: list[CombinationAnalysis] = Field(default_factory=list)
    notes: list[str]


class Recommendation(BaseModel):
    id: str
    match_id: str
    match_label: str
    market: str
    selection: str
    probability: float = Field(ge=0, le=1)
    fair_odds: float = Field(gt=1)
    best_odds: float | None = Field(default=None, gt=1)
    expected_value: float | None = None
    kind: str
    rationale: str
    legs: list[CombinationLeg] = Field(default_factory=list)
    confidence: str = "Media"
    data_quality: float = Field(default=0.7, ge=0, le=1)
    risk_note: str | None = None
    home_logo: str | None = None
    away_logo: str | None = None


class RecommendationResponse(BaseModel):
    recommendations: list[Recommendation]


class AssistantQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    match_id: str | None = None


class AssistantResponse(BaseModel):
    summary: str
    factors_for: list[str]
    factors_against: list[str]
    data_limitations: list[str]
    responsible_note: str
    source: str

