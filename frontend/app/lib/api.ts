export const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "https://bet-anality.onrender.com/api/v1";
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
  kickoff_at: string;
  home_team: string;
  away_team: string;
  home_team_id?: string | null;
  away_team_id?: string | null;
  home_logo?: string | null;
  away_logo?: string | null;
  venue?: string | null;
  referee?: string | null;
  home_form?: string | null;
  away_form?: string | null;
  data_quality: number;
  odds_available: boolean;
  status: string;
  source_provider?: string;
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
  injuries: Injury[];
  lineups?: LineupsSummary | null;
  h2h_matches: H2HMatch[];
  home_recent_matches: H2HMatch[];
  away_recent_matches: H2HMatch[];
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

export async function getMatches(query = "", signal?: AbortSignal) {
  const response = await apiFetch(`${apiUrl}/matches/search?q=${encodeURIComponent(query)}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) await throwApiError(response, "No se pudo cargar la agenda");
  return response.json() as Promise<{ matches: Match[]; source: string; notice: string | null }>;
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

