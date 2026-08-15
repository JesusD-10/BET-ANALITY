/**
 * Liga de fútbol con información de país, logo y prioridad de visualización.
 * Los logos se obtienen de estatapi.com o flaticon CDN si están disponibles.
 */
export type League = {
  id: string; // identificador único normalizado
  name: string; // nombre mostrado
  country: string; // país
  countryCode: string; // código ISO 2 (ej: ES, EN, IT)
  logoUrl?: string; // URL del logo de la liga
  countryFlagUrl?: string; // URL de la bandera del país
  priority: number; // menor = más importante (mostrado primero)
};

/**
 * Ligas ordenadas por importancia (de más conocidas a menos).
 * Se muestran en fila horizontal en la página de partidos.
 */
export const LEAGUES: League[] = [
  {
    id: "uefa-champions-league",
    name: "UEFA Champions League",
    country: "Europa",
    countryCode: "EU",
    logoUrl: "https://api.api-sports.io/leagues/1.png",
    countryFlagUrl: "https://flagcdn.com/eu.svg",
    priority: 1,
  },
  {
    id: "copa-libertadores",
    name: "Copa Libertadores",
    country: "América del Sur",
    countryCode: "SA",
    logoUrl: "https://api.api-sports.io/leagues/544.png",
    countryFlagUrl: "https://flagcdn.com/sa.svg",
    priority: 2,
  },
  {
    id: "uefa-europa-league",
    name: "UEFA Europa League",
    country: "Europa",
    countryCode: "EU",
    logoUrl: "https://api.api-sports.io/leagues/2.png",
    countryFlagUrl: "https://flagcdn.com/eu.svg",
    priority: 3,
  },
  {
    id: "premier-league",
    name: "Premier League",
    country: "Inglaterra",
    countryCode: "GB",
    logoUrl: "https://api.api-sports.io/leagues/39.png",
    countryFlagUrl: "https://flagcdn.com/gb.svg",
    priority: 4,
  },
  {
    id: "la-liga",
    name: "La Liga",
    country: "España",
    countryCode: "ES",
    logoUrl: "https://api.api-sports.io/leagues/140.png",
    countryFlagUrl: "https://flagcdn.com/es.svg",
    priority: 5,
  },
  {
    id: "serie-a",
    name: "Serie A",
    country: "Italia",
    countryCode: "IT",
    logoUrl: "https://api.api-sports.io/leagues/135.png",
    countryFlagUrl: "https://flagcdn.com/it.svg",
    priority: 6,
  },
  {
    id: "bundesliga",
    name: "Bundesliga",
    country: "Alemania",
    countryCode: "DE",
    logoUrl: "https://api.api-sports.io/leagues/78.png",
    countryFlagUrl: "https://flagcdn.com/de.svg",
    priority: 7,
  },
  {
    id: "ligue-1",
    name: "Ligue 1",
    country: "Francia",
    countryCode: "FR",
    logoUrl: "https://api.api-sports.io/leagues/61.png",
    countryFlagUrl: "https://flagcdn.com/fr.svg",
    priority: 8,
  },
  {
    id: "primeira-liga",
    name: "Primeira Liga",
    country: "Portugal",
    countryCode: "PT",
    logoUrl: "https://api.api-sports.io/leagues/94.png",
    countryFlagUrl: "https://flagcdn.com/pt.svg",
    priority: 9,
  },
  {
    id: "eredivisie",
    name: "Eredivisie",
    country: "Países Bajos",
    countryCode: "NL",
    logoUrl: "https://api.api-sports.io/leagues/88.png",
    countryFlagUrl: "https://flagcdn.com/nl.svg",
    priority: 10,
  },
  {
    id: "liga-profesional",
    name: "Liga Profesional",
    country: "Argentina",
    countryCode: "AR",
    logoUrl: "https://api.api-sports.io/leagues/128.png",
    countryFlagUrl: "https://flagcdn.com/ar.svg",
    priority: 11,
  },
  {
    id: "serie-a-br",
    name: "Série A",
    country: "Brasil",
    countryCode: "BR",
    logoUrl: "https://api.api-sports.io/leagues/71.png",
    countryFlagUrl: "https://flagcdn.com/br.svg",
    priority: 12,
  },
  {
    id: "liga-mx",
    name: "Liga MX",
    country: "México",
    countryCode: "MX",
    logoUrl: "https://api.api-sports.io/leagues/262.png",
    countryFlagUrl: "https://flagcdn.com/mx.svg",
    priority: 13,
  },
  {
    id: "mls",
    name: "MLS",
    country: "Estados Unidos",
    countryCode: "US",
    logoUrl: "https://api.api-sports.io/leagues/115.png",
    countryFlagUrl: "https://flagcdn.com/us.svg",
    priority: 14,
  },
  {
    id: "super-lig",
    name: "Super Lig",
    country: "Turquía",
    countryCode: "TR",
    logoUrl: "https://api.api-sports.io/leagues/203.png",
    countryFlagUrl: "https://flagcdn.com/tr.svg",
    priority: 15,
  },
  {
    id: "saudi-pro-league",
    name: "Saudi Pro League",
    country: "Arabia Saudita",
    countryCode: "SA",
    logoUrl: "https://api.api-sports.io/leagues/541.png",
    countryFlagUrl: "https://flagcdn.com/sa.svg",
    priority: 16,
  },
];

/**
 * Mapeo de nombres de competición (normalizados) a liga conocida.
 * Permite asociar ligas del backend con información de logo/país.
 */
export function findLeagueByName(competitionName: string): League | undefined {
  const normalized = competitionName
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

  return LEAGUES.find((league) => {
    const leagueName = league.name
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    return normalized.includes(leagueName) || leagueName.includes(normalized);
  });
}

/**
 * Obtiene una liga por su ID normalizado.
 */
export function getLeagueById(id: string): League | undefined {
  return LEAGUES.find((league) => league.id === id);
}

/**
 * Ordena un conjunto de ligas por prioridad (de más a menos importante).
 */
export function sortLeaguesByPriority(leagues: League[]): League[] {
  return [...leagues].sort((a, b) => a.priority - b.priority);
}
