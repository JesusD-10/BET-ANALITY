import type { Match } from "./api";

const MAX_HOME_MATCHES = 12;
const MAX_HOME_MATCHES_PER_LEAGUE = 4;

/**
 * Aliases are deliberately exact after normalization. This avoids false
 * positives such as Barcelona SC being treated as FC Barcelona while still
 * accepting accents, punctuation and the common provider name variants.
 */
const POPULAR_TEAM_ALIASES: readonly (readonly string[])[] = [
  // Peru
  ["Alianza Lima", "Club Alianza Lima"],
  ["Universitario", "Universitario de Deportes", "Club Universitario de Deportes"],
  ["Sporting Cristal", "Club Sporting Cristal"],
  ["FBC Melgar", "Melgar"],
  ["Cienciano", "Club Cienciano"],

  // Spain, England, Italy, Germany and France
  ["Real Madrid", "Real Madrid CF"],
  ["Barcelona", "FC Barcelona"],
  ["Atletico Madrid", "Atletico de Madrid", "Club Atletico de Madrid"],
  ["Arsenal", "Arsenal FC"],
  ["Chelsea", "Chelsea FC"],
  ["Liverpool", "Liverpool FC"],
  ["Manchester City", "Manchester City FC"],
  ["Manchester United", "Manchester United FC"],
  ["Tottenham", "Tottenham Hotspur", "Tottenham Hotspur FC"],
  ["Newcastle", "Newcastle United", "Newcastle United FC"],
  ["Juventus", "Juventus FC"],
  ["Inter", "Inter Milan", "Internazionale", "FC Internazionale Milano"],
  ["AC Milan", "Milan"],
  ["Napoli", "SSC Napoli"],
  ["AS Roma", "Roma"],
  ["Bayern Munich", "Bayern Munchen", "FC Bayern München"],
  ["Borussia Dortmund", "BVB"],
  ["Bayer Leverkusen", "Bayer 04 Leverkusen"],
  ["Paris Saint-Germain", "Paris Saint Germain", "PSG"],
  ["Olympique Marseille", "Olympique de Marseille", "Marseille"],
  ["Olympique Lyon", "Olympique Lyonnais", "Lyon"],

  // Portugal and Netherlands
  ["Benfica", "SL Benfica"],
  ["Porto", "FC Porto"],
  ["Sporting CP", "Sporting Lisbon", "Sporting Clube de Portugal"],
  ["Ajax", "AFC Ajax"],
  ["PSV", "PSV Eindhoven"],
  ["Feyenoord", "Feyenoord Rotterdam"],

  // South America
  ["Boca Juniors", "CA Boca Juniors"],
  ["River Plate", "CA River Plate"],
  ["Racing Club", "Racing Club de Avellaneda"],
  ["Independiente", "CA Independiente"],
  ["San Lorenzo", "San Lorenzo de Almagro"],
  ["Flamengo", "CR Flamengo"],
  ["Palmeiras", "SE Palmeiras"],
  ["Corinthians", "SC Corinthians", "Sport Club Corinthians Paulista"],
  ["Sao Paulo", "São Paulo FC"],
  ["Santos", "Santos FC"],
  ["Fluminense", "Fluminense FC"],
  ["Botafogo", "Botafogo FR"],
  ["Gremio", "Grêmio FBPA"],
  ["Internacional", "SC Internacional"],
  ["Vasco da Gama", "CR Vasco da Gama"],
  ["Atletico Mineiro", "Atlético Mineiro", "Clube Atletico Mineiro"],
  ["Atletico Nacional", "Atlético Nacional"],
  ["Millonarios", "Millonarios FC"],
  ["America de Cali", "América de Cali"],
  ["Independiente Santa Fe", "Santa Fe"],
  ["Colo-Colo", "Colo Colo"],
  ["Universidad de Chile", "U de Chile"],
  ["LDU Quito", "Liga de Quito", "Liga Deportiva Universitaria"],
  ["Barcelona SC", "Barcelona Sporting Club"],
  ["Emelec", "CS Emelec"],
  ["Penarol", "Peñarol", "CA Penarol"],
  ["Nacional", "Club Nacional de Football"],

  // Mexico, MLS and Saudi Pro League
  ["Club America", "Club América"],
  ["Guadalajara", "Chivas", "Chivas Guadalajara", "CD Guadalajara"],
  ["Cruz Azul", "CF Cruz Azul"],
  ["Pumas UNAM", "UNAM Pumas"],
  ["Tigres UANL", "Tigres"],
  ["Monterrey", "CF Monterrey", "Rayados de Monterrey"],
  ["Inter Miami", "Inter Miami CF"],
  ["LA Galaxy", "Los Angeles Galaxy"],
  ["Al Nassr", "Al-Nassr"],
  ["Al Hilal", "Al-Hilal"],
  ["Al Ittihad", "Al-Ittihad"],

  // Popular national teams (when the daily agenda is international)
  ["Peru", "Perú"],
  ["Argentina"],
  ["Brazil", "Brasil"],
  ["Colombia"],
  ["Chile"],
  ["Ecuador"],
  ["Uruguay"],
  ["Mexico", "México"],
  ["Spain", "España"],
  ["England", "Inglaterra"],
  ["France", "Francia"],
  ["Germany", "Alemania"],
  ["Italy", "Italia"],
  ["Portugal"],
  ["Netherlands", "Países Bajos", "Holland", "Holanda"],
];

const LEAGUE_PRIORITY = [
  "uefa champions league",
  "copa libertadores",
  "uefa europa league",
  "premier league",
  "la liga",
  "primera division",
  "serie a",
  "bundesliga",
  "ligue 1",
  "liga 1",
  "liga profesional",
  "brasileirao",
  "liga mx",
];

export type HomeLeagueGroup = {
  key: string;
  competition: string;
  matches: Match[];
};

export function normalizeFootballName(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es")
    .replace(/&/g, " y ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

const POPULAR_TEAM_NAMES = new Set(
  POPULAR_TEAM_ALIASES.flatMap((aliases) => aliases.map(normalizeFootballName)),
);

export function isPopularTeam(teamName: string): boolean {
  return POPULAR_TEAM_NAMES.has(normalizeFootballName(teamName));
}

export function isPopularMatch(match: Match): boolean {
  return isPopularTeam(match.home_team) || isPopularTeam(match.away_team);
}

function leaguePriority(competition: string): number {
  const normalized = normalizeFootballName(competition);
  const priority = LEAGUE_PRIORITY.findIndex((name) => normalized.includes(name));
  return priority === -1 ? LEAGUE_PRIORITY.length : priority;
}

export function groupMatchesByLeague(matches: Match[]): HomeLeagueGroup[] {
  const groups = new Map<string, HomeLeagueGroup>();

  for (const match of matches) {
    const key = normalizeFootballName(match.competition) || "otras-competiciones";
    const group = groups.get(key);
    if (group) {
      group.matches.push(match);
    } else {
      groups.set(key, { key, competition: match.competition || "Otras competiciones", matches: [match] });
    }
  }

  return [...groups.values()]
    .map((group) => ({
      ...group,
      matches: [...group.matches].sort(
        (left, right) => new Date(left.kickoff_at).getTime() - new Date(right.kickoff_at).getTime(),
      ),
    }))
    .sort((left, right) => {
      const priorityDifference = leaguePriority(left.competition) - leaguePriority(right.competition);
      if (priorityDifference !== 0) return priorityDifference;

      const kickoffDifference =
        new Date(left.matches[0]?.kickoff_at ?? 0).getTime() -
        new Date(right.matches[0]?.kickoff_at ?? 0).getTime();
      return kickoffDifference || left.competition.localeCompare(right.competition, "es");
    });
}

/**
 * The default home feed only contains popular teams. Explicit searches keep
 * working across the complete catalog, but remain capped and grouped.
 */
export function selectHomeMatches(matches: Match[], includeSearchResults = false): Match[] {
  const candidates = includeSearchResults ? matches : matches.filter(isPopularMatch);
  const groups = groupMatchesByLeague(candidates);
  const selected: Match[] = [];

  for (let index = 0; index < MAX_HOME_MATCHES_PER_LEAGUE; index += 1) {
    for (const group of groups) {
      const match = group.matches[index];
      if (match) selected.push(match);
      if (selected.length === MAX_HOME_MATCHES) return selected;
    }
  }

  return selected;
}
