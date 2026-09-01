const defaultApiUrl =
  process.env.NODE_ENV === "development"
    ? "/api/v1"
    : "https://bet-anality.onrender.com/api/v1";

export const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? defaultApiUrl;
// Agenda may traverse three sports providers sequentially. Match analysis then
// queries complementary data and up to four AI providers concurrently.
export const requestTimeoutMs = 65_000;
export const analysisRequestTimeoutMs = 90_000;

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export class ApiTimeoutError extends Error {
  constructor() {
    super("La solicitud superó el tiempo máximo de espera.");
    this.name = "ApiTimeoutError";
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function throwApiError(response: Response, fallbackDetail: string): Promise<never> {
  let detail = fallbackDetail;

  try {
    const payload: unknown = await response.json();
    if (
      typeof payload === "object" &&
      payload !== null &&
      "detail" in payload &&
      typeof payload.detail === "string"
    ) {
      detail = payload.detail;
    }
  } catch {
    // Some upstream errors do not include a JSON response body.
  }

  throw new ApiError(response.status, detail);
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = requestTimeoutMs,
) {
  const controller = new AbortController();
  const abortFromParent = () => controller.abort();
  if (init.signal?.aborted) controller.abort();
  init.signal?.addEventListener("abort", abortFromParent, { once: true });
  let timedOut = false;
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut && !init.signal?.aborted) {
      throw new ApiTimeoutError();
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutId);
    init.signal?.removeEventListener("abort", abortFromParent);
  }
}

export type RefereeInfo = {
  name: string;
  yellow_cards_avg?: number | null;
  red_cards_avg?: number | null;
  fouls_per_game?: number | null;
  tendency?: string | null;
};

export type Injury = {
  player: string;
  team: string;
  reason: string;
  status: string;
  type?: string | null;
};

export type PlayerLineup = {
  id?: number | null;
  name: string;
  number?: number | null;
  pos?: string | null;
  grid?: string | null;
};

export type TeamLineup = {
  team_name: string;
  formation?: string | null;
  coach?: string | null;
  start_xi: PlayerLineup[];
  substitutes: PlayerLineup[];
  confirmed?: boolean;
  source?: "api_football" | "recent_form" | string;
  sample_size?: number | null;
};

export type TeamDisciplineAverage = {
  team_name: string;
  sample_size: number;
  fouls_avg?: number | null;
  yellow_cards_avg?: number | null;
  red_cards_avg?: number | null;
};

export type DisciplineSummary = {
  home?: TeamDisciplineAverage | null;
  away?: TeamDisciplineAverage | null;
  note: string;
};

export type DataAvailability = {
  status: "available" | "partial" | "unavailable" | "not_requested" | "not_applicable" | string;
  reason?: string | null;
  sample_size?: number | null;
};

export type DataProvenance = {
  provider: string;
  endpoint?: string | null;
  fetched_at?: string | null;
  verified: boolean;
};

export type EvidenceCoverageItem = DataAvailability & {
  section:
    | "team_statistics"
    | "standings"
    | "h2h"
    | "recent_fixtures"
    | "players"
    | "injuries"
    | "lineups"
    | "provider_prediction"
    | "verified_odds"
    | string;
  provenance: DataProvenance[];
};

export type TeamStatisticsSnapshot = {
  team_id?: string | null;
  team_name: string;
  form?: string | null;
  fixtures_played?: number | null;
  wins?: number | null;
  draws?: number | null;
  losses?: number | null;
  goals_for?: number | null;
  goals_against?: number | null;
  goals_for_avg?: number | null;
  goals_against_avg?: number | null;
  clean_sheets?: number | null;
  failed_to_score?: number | null;
  averages: Record<string, number | null>;
  rates: Record<string, number | null>;
};

export type FixtureStatisticsSnapshot = {
  fixture_id: string;
  date?: string | null;
  competition?: string | null;
  home_team: string;
  away_team: string;
  home_goals?: number | null;
  away_goals?: number | null;
  home_statistics: Record<string, number | null>;
  away_statistics: Record<string, number | null>;
};

export type MatchStatisticsSummary = {
  home?: TeamStatisticsSnapshot | null;
  away?: TeamStatisticsSnapshot | null;
  home_recent_fixtures: FixtureStatisticsSnapshot[];
  away_recent_fixtures: FixtureStatisticsSnapshot[];
};

export type StandingSnapshot = {
  team_id?: string | null;
  team_name: string;
  rank?: number | null;
  points?: number | null;
  played?: number | null;
  wins?: number | null;
  draws?: number | null;
  losses?: number | null;
  goals_for?: number | null;
  goals_against?: number | null;
  goal_difference?: number | null;
  form?: string | null;
  description?: string | null;
};

export type StandingsContext = {
  league_id?: string | null;
  season?: number | null;
  round?: string | null;
  venue_id?: string | null;
  home?: StandingSnapshot | null;
  away?: StandingSnapshot | null;
};

export type PlayerStatisticsSnapshot = {
  player_id?: string | null;
  player_name: string;
  team_id?: string | null;
  team_name?: string | null;
  position?: string | null;
  appearances?: number | null;
  starts?: number | null;
  minutes?: number | null;
  rating?: number | null;
  goals?: number | null;
  assists?: number | null;
  shots?: number | null;
  shots_on_target?: number | null;
  key_passes?: number | null;
  tackles?: number | null;
  interceptions?: number | null;
  saves?: number | null;
  yellow_cards?: number | null;
  red_cards?: number | null;
};

export type PlayerContext = {
  home: PlayerStatisticsSnapshot[];
  away: PlayerStatisticsSnapshot[];
  top_scorers: PlayerStatisticsSnapshot[];
  top_assists: PlayerStatisticsSnapshot[];
  top_yellow_cards: PlayerStatisticsSnapshot[];
  top_red_cards: PlayerStatisticsSnapshot[];
};

export type ProviderPredictionEvidence = {
  winner_id?: string | null;
  winner_name?: string | null;
  winner_comment?: string | null;
  advice?: string | null;
  win_or_draw?: boolean | null;
  under_over?: string | null;
  goals_home?: string | null;
  goals_away?: string | null;
  percent_home?: number | null;
  percent_draw?: number | null;
  percent_away?: number | null;
};

export type VerifiedOddsEvidence = {
  market_key: string;
  selection: string;
  odds: number;
  bookmaker: string;
  captured_at?: string | null;
  live: boolean;
  provenance: DataProvenance;
};

export type AIConsensusSummary = {
  requested: number;
  completed: number;
  providers: string[];
  required_support: number;
  status: "consensus" | "partial" | "single" | "unavailable" | "fallback" | string;
  reason?: string | null;
};

export type LineupsSummary = {
  confirmed: boolean;
  home?: TeamLineup | null;
  away?: TeamLineup | null;
  status?: "confirmed" | "partial" | "probable" | "pending" | string;
  note?: string | null;
};

export type H2HMatch = {
  date: string;
  competition: string;
  home_team: string;
  away_team: string;
  score: string;
  winner?: string | null;
};

export type Match = {
  id: string;
  competition: string;
  country?: string | null;
  country_code?: string | null;
  competition_logo?: string | null;
  kickoff_at: string;
  home_team: string;
  away_team: string;
  home_team_id?: string | null;
  away_team_id?: string | null;
  league_id?: string | null;
  season?: number | null;
  home_logo?: string | null;
  away_logo?: string | null;
  venue?: string | null;
  referee?: string | null;
  home_form?: string | null;
  away_form?: string | null;
  data_quality: number;
  odds_available: boolean;
  status: string;
  status_short?: string | null;
  elapsed?: number | null;
  home_score?: number | null;
  away_score?: number | null;
  halftime_home_score?: number | null;
  halftime_away_score?: number | null;
  source_provider?: string;
  source_url?: string | null;
  external_id?: string | null;
};

export type MatchListResponse = {
  date?: string;
  matches: Match[];
  source: string;
  notice: string | null;
};

export type Market = {
  market_key: string;
  label: string;
  selection: string;
  probability: number;
  fair_odds: number;
  best_odds: number | null;
  bookmaker: string | null;
  expected_value: number | null;
  confidence: string;
  data_quality: number;
  factors_for: string[];
  risks: string[];
  evidence_refs?: string[];
};

export type CombinationLeg = {
  market_key: string;
  label: string;
  selection: string;
};

export type Combination = {
  id: string;
  label: string;
  selection: string;
  legs: CombinationLeg[];
  probability: number;
  fair_odds: number;
  best_odds: number | null;
  expected_value: number | null;
  confidence: string;
  data_quality: number;
  factors_for: string[];
  risks: string[];
  correlation_note: string;
  kind: string;
};

export type Analysis = {
  match: Match;
  model_version: string;
  updated_at: string;
  referee_info?: RefereeInfo | null;
  discipline?: DisciplineSummary | null;
  injuries: Injury[];
  lineups?: LineupsSummary | null;
  h2h_matches: H2HMatch[];
  home_recent_matches: H2HMatch[];
  away_recent_matches: H2HMatch[];
  data_coverage?: EvidenceCoverageItem[];
  statistics_summary?: MatchStatisticsSummary | null;
  standings?: StandingsContext | null;
  provider_prediction?: ProviderPredictionEvidence | null;
  player_context?: PlayerContext | null;
  verified_odds?: VerifiedOddsEvidence[];
  ai_consensus?: AIConsensusSummary | null;
  tactical_summary?: string | null;
  injuries_impact?: string | null;
  referee_impact?: string | null;
  markets: Market[];
  combinations: Combination[];
  dream_picks: Combination[];
  notes: string[];
};

export type Recommendation = {
  id: string;
  match_id: string;
  match_label: string;
  market: string;
  selection: string;
  probability: number;
  fair_odds: number;
  best_odds: number | null;
  expected_value: number | null;
  kind: string;
  rationale: string;
  legs?: CombinationLeg[];
  confidence?: string;
  data_quality?: number;
  risk_note?: string | null;
  home_logo?: string | null;
  away_logo?: string | null;
};

export function getMatches(query?: string, signal?: AbortSignal): Promise<MatchListResponse>;
export function getMatches(
  query: string,
  matchDate: string | undefined,
  signal?: AbortSignal,
): Promise<MatchListResponse>;
export async function getMatches(
  query = "",
  matchDateOrSignal?: string | AbortSignal,
  signal?: AbortSignal,
): Promise<MatchListResponse> {
  const matchDate = typeof matchDateOrSignal === "string" ? matchDateOrSignal : undefined;
  const requestSignal =
    typeof matchDateOrSignal === "string" || matchDateOrSignal === undefined
      ? signal
      : matchDateOrSignal;
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  if (matchDate) params.set("match_date", matchDate);
  const requestUrl = `${apiUrl}/matches/search${params.size ? `?${params.toString()}` : ""}`;

  const response = await apiFetch(requestUrl, {
    cache: "no-store",
    signal: requestSignal,
  });
  if (!response.ok) await throwApiError(response, "No se pudo cargar la agenda");
  return response.json() as Promise<MatchListResponse>;
}

export async function getAnalysis(id: string, signal?: AbortSignal) {
  const response = await apiFetch(`${apiUrl}/matches/${encodeURIComponent(id)}/analysis`, {
    cache: "no-store",
    signal,
  }, analysisRequestTimeoutMs);
  if (!response.ok) await throwApiError(response, "El análisis no está disponible");
  return response.json() as Promise<Analysis>;
}

export async function getRecommendations(kind: "daily" | "dreams" = "daily", limit?: number) {
  const query = limit ? `?limit=${limit}` : "";
  const response = await apiFetch(`${apiUrl}/recommendations/${kind}${query}`, { cache: "no-store" });
  if (!response.ok) await throwApiError(response, "No se pudieron cargar las recomendaciones");
  return response.json() as Promise<{ recommendations: Recommendation[] }>;
}

export type TeamCountry = {
  code: string;
  name: string;
  flag_url?: string | null;
};

export type TeamSummary = {
  id: number;
  name: string;
  slug: string;
  short_code?: string | null;
  kind: string;
  logo_url?: string | null;
  country: TeamCountry;
};

export type TeamSearchResponse = {
  items: TeamSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type TeamStatistics = {
  total_matches: number;
  completed_matches: number;
  upcoming_matches: number;
  wins: number;
  draws: number;
  losses: number;
  goals_for: number;
  goals_against: number;
  first_match_date?: string | null;
  last_match_date?: string | null;
  next_match_date?: string | null;
};

export type TeamCompetition = {
  id: number;
  name: string;
  slug: string;
  kind: string;
  country_code?: string | null;
  logo_url?: string | null;
  matches: number;
};

export type TeamDetailResponse = {
  team: TeamSummary;
  statistics: TeamStatistics;
  competitions: TeamCompetition[];
  seasons: Array<{ id: number; label: string; start_year: number; end_year: number; matches: number }>;
};

export type HistoricalMatchTeam = {
  id: number;
  name: string;
  slug: string;
  logo_url?: string | null;
};

export type HistoricalMatchCompetition = {
  id: number;
  name: string;
  slug: string;
  kind: string;
  country_code?: string | null;
  logo_url?: string | null;
};

export type HistoricalMatchSeason = {
  id: number;
  label: string;
  start_year: number;
  end_year: number;
};

export type TeamMatch = {
  id: string;
  match_date: string;
  kickoff_at: string;
  kickoff_precision: string;
  status: string;
  status_short?: string | null;
  competition: HistoricalMatchCompetition;
  season?: HistoricalMatchSeason | null;
  round?: string | null;
  venue?: string | null;
  home_team: HistoricalMatchTeam;
  away_team: HistoricalMatchTeam;
  home_score?: number | null;
  away_score?: number | null;
  half_time_home_score?: number | null;
  half_time_away_score?: number | null;
  team_side: "home" | "away" | string;
  result?: "win" | "draw" | "loss" | null;
  opponent: HistoricalMatchTeam;
};

export type TeamMatchScope = "past" | "upcoming" | "all";

export type TeamMatchesResponse = {
  team_id: number;
  scope: TeamMatchScope | "h2h";
  opponent_id?: number;
  items: TeamMatch[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  upcoming: TeamMatch[];
};

export type TeamSearchOptions = {
  query?: string;
  countryCode?: string;
  kind?: string;
  page?: number;
  pageSize?: number;
};

export async function getTeams(
  options: TeamSearchOptions = {},
  signal?: AbortSignal,
): Promise<TeamSearchResponse> {
  const params = new URLSearchParams();
  if (options.query?.trim()) params.set("q", options.query.trim());
  if (options.countryCode) params.set("country_code", options.countryCode);
  if (options.kind) params.set("kind", options.kind);
  params.set("page", String(options.page ?? 1));
  params.set("page_size", String(options.pageSize ?? 20));

  const response = await apiFetch(`${apiUrl}/teams?${params.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) await throwApiError(response, "No se pudieron cargar los equipos");
  return response.json() as Promise<TeamSearchResponse>;
}

export async function getTeam(teamId: number, signal?: AbortSignal): Promise<TeamDetailResponse> {
  const response = await apiFetch(`${apiUrl}/teams/${encodeURIComponent(String(teamId))}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) await throwApiError(response, "No se pudo cargar el equipo");
  return response.json() as Promise<TeamDetailResponse>;
}

export type TeamMatchesOptions = {
  scope?: TeamMatchScope;
  page?: number;
  pageSize?: number;
  competitionId?: number;
  seasonId?: number;
};

export async function getTeamMatches(
  teamId: number,
  options: TeamMatchesOptions = {},
  signal?: AbortSignal,
): Promise<TeamMatchesResponse> {
  const params = new URLSearchParams({
    scope: options.scope ?? "past",
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  });
  if (options.competitionId !== undefined) {
    params.set("competition_id", String(options.competitionId));
  }
  if (options.seasonId !== undefined) {
    params.set("season_id", String(options.seasonId));
  }

  const response = await apiFetch(
    `${apiUrl}/teams/${encodeURIComponent(String(teamId))}/matches?${params.toString()}`,
    { cache: "no-store", signal },
  );
  if (!response.ok) await throwApiError(response, "No se pudo cargar el historial del equipo");
  return response.json() as Promise<TeamMatchesResponse>;
}

export async function getTeamHeadToHead(
  teamId: number,
  opponentId: number,
  page = 1,
  pageSize = 20,
  signal?: AbortSignal,
): Promise<TeamMatchesResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const response = await apiFetch(
    `${apiUrl}/teams/${encodeURIComponent(String(teamId))}/h2h/${encodeURIComponent(String(opponentId))}?${params.toString()}`,
    { cache: "no-store", signal },
  );
  if (!response.ok) await throwApiError(response, "No se pudo cargar el historial entre los equipos");
  return response.json() as Promise<TeamMatchesResponse>;
}

