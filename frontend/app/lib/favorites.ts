export type FavoriteTeam = {
  id: number;
  name: string;
  slug?: string | null;
  kind?: string | null;
  logo_url?: string | null;
  country_name?: string | null;
  country_flag_url?: string | null;
};

const FAVORITES_STORAGE_KEY = "bet-anality:favorites:v1";

function safeReadFavorites(): FavoriteTeam[] {
  if (typeof window === "undefined") return [];

  try {
    const rawValue = window.localStorage.getItem(FAVORITES_STORAGE_KEY);
    if (!rawValue) return [];
    const parsed: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter((item): item is FavoriteTeam => {
      if (typeof item !== "object" || item === null) return false;
      const record = item as Record<string, unknown>;
      return typeof record.id === "number" && typeof record.name === "string";
    });
  } catch {
    return [];
  }
}

export function readFavoriteTeams(): FavoriteTeam[] {
  return safeReadFavorites();
}

export function isFavoriteTeam(teamId: number): boolean {
  return readFavoriteTeams().some((team) => team.id === teamId);
}

export function toggleFavoriteTeam(team: FavoriteTeam): FavoriteTeam[] {
  if (typeof window === "undefined") return [];

  const current = safeReadFavorites();
  const exists = current.some((item) => item.id === team.id);
  const next = exists ? current.filter((item) => item.id !== team.id) : [
    { ...team },
    ...current.filter((item) => item.id !== team.id),
  ].slice(0, 30);

  try {
    window.localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // La persistencia local puede fallar si el navegador bloquea almacenamiento.
  }

  return next;
}

export function setFavoriteTeams(teams: FavoriteTeam[]) {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(teams.slice(0, 30)));
  } catch {
    // Ignore storage errors.
  }
}
