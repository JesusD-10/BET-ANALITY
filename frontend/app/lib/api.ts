export const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "https://bet-anality.onrender.com/api/v1";
export const requestTimeoutMs = 10_000;

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const controller = new AbortController();
  const abortFromParent = () => controller.abort();
  if (init.signal?.aborted) controller.abort();
  init.signal?.addEventListener("abort", abortFromParent, { once: true });
  const timeoutId = globalThis.setTimeout(() => controller.abort(), requestTimeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
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
};

export type LineupsSummary = {
  confirmed: boolean;
  home?: TeamLineup | null;
  away?: TeamLineup | null;
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

export async function getMatches(query = "") {
  const response = await apiFetch(`${apiUrl}/matches/search?q=${encodeURIComponent(query)}`, { cache: "no-store" });
  if (!response.ok) throw new Error("No se pudo cargar la agenda");
  return response.json() as Promise<{ matches: Match[]; source: string; notice: string | null }>;
}

export async function getAnalysis(id: string) {
  const response = await apiFetch(`${apiUrl}/matches/${id}/analysis`, { cache: "no-store" });
  if (!response.ok) throw new Error("El análisis no está disponible");
  return response.json() as Promise<Analysis>;
}

export async function getRecommendations(kind: "daily" | "dreams" = "daily", limit?: number) {
  const query = limit ? `?limit=${limit}` : "";
  const response = await apiFetch(`${apiUrl}/recommendations/${kind}${query}`, { cache: "no-store" });
  if (!response.ok) throw new Error("No se pudieron cargar las recomendaciones");
  return response.json() as Promise<{ recommendations: Recommendation[] }>;
}

