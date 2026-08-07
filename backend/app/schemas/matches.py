from datetime import date, datetime

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
    start_xi: list[PlayerLineup] = []
    substitutes: list[PlayerLineup] = []


class LineupsSummary(BaseModel):
    confirmed: bool = False
    home: TeamLineup | None = None
    away: TeamLineup | None = None


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
    venue: str | None = None
    referee: str | None = None
    home_form: str | None = None  # ej: "W-W-D-L-W"
    away_form: str | None = None  # ej: "L-W-W-D-W"
    data_quality: float = Field(default=0.9, ge=0, le=1)
    odds_available: bool = False
    status: str
    source_provider: str = "mock"
    source_url: str | None = None
    external_id: str | None = None


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


class MatchAnalysisResponse(BaseModel):
    match: MatchSummary
    model_version: str
    updated_at: datetime
    referee_info: RefereeInfo | None = None
    injuries: list[InjuryItem] = []
    lineups: LineupsSummary | None = None
    h2h_matches: list[H2HMatchItem] = []
    tactical_summary: str | None = None
    injuries_impact: str | None = None
    referee_impact: str | None = None
    markets: list[MarketAnalysis]
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

