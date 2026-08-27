"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, CalendarDays, Heart, Search, Shield, Trophy } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import { getTeamMatches, type TeamMatch } from "../lib/api";
import { readFavoriteTeams, type FavoriteTeam } from "../lib/favorites";

function formatFixtureDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Sin fecha";
  return new Intl.DateTimeFormat("es-PE", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  }).format(date);
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  return new Intl.DateTimeFormat("es-PE", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/Lima",
  }).format(date);
}

function normalizeMatchDay(matchDate: string): string {
  return new Date(`${matchDate.slice(0, 10)}T12:00:00Z`).toISOString().slice(0, 10);
}

function getFavoriteMatch(team: FavoriteTeam, allMatches: Record<number, TeamMatch[]>) {
  const teamMatches = allMatches[team.id] ?? [];
  const today = new Date();
  const todayKey = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

  const sameDay = teamMatches.filter((match) => normalizeMatchDay(match.match_date) === todayKey);
  if (sameDay.length > 0) {
    return sameDay
      .sort((left, right) => Number(new Date(right.kickoff_at).getTime()) - Number(new Date(left.kickoff_at).getTime()))[0];
  }

  const futureMatches = teamMatches
    .filter((match) => new Date(match.kickoff_at).getTime() >= Date.now())
    .sort((left, right) => Number(new Date(left.kickoff_at).getTime()) - Number(new Date(right.kickoff_at).getTime()));

  return futureMatches[0] ?? null;
}

export default function FavoriteTeamsPage() {
  const [teams, setTeams] = useState<FavoriteTeam[]>([]);
  const [matchMap, setMatchMap] = useState<Record<number, TeamMatch[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const syncTeams = () => {
      const nextTeams = readFavoriteTeams();
      setTeams(nextTeams);
      setLoading(false);
    };

    syncTeams();
    window.addEventListener("storage", syncTeams);
    return () => window.removeEventListener("storage", syncTeams);
  }, []);

  useEffect(() => {
    if (!teams.length) {
      setMatchMap({});
      return;
    }

    let cancelled = false;

    async function loadMatches() {
      const results: Record<number, TeamMatch[]> = {};
      for (const team of teams) {
        try {
          const response = await getTeamMatches(team.id, {
            scope: "all",
            page: 1,
            pageSize: 20,
          });
          if (!cancelled) {
            results[team.id] = response.items ?? [];
          }
        } catch {
          if (!cancelled) {
            results[team.id] = [];
          }
        }
      }

      if (!cancelled) setMatchMap(results);
    }

    void loadMatches();
    return () => {
      cancelled = true;
    };
  }, [teams]);

  const favoriteCards = useMemo(
    () => teams.map((team) => ({ team, match: getFavoriteMatch(team, matchMap) })),
    [matchMap, teams],
  );

  return (
    <AppShell contentId="favoritos">
      <PageHeader
        eyebrow="SECCIÓN PERSONALIZADA"
        title="Favoritos"
        action={<Link className="outline-link" href="/equipos">Ver equipos <ArrowLeft size={15} /></Link>}
      />

      <div className="teams-intro">
        <Heart size={19} />
        <p>
          Tus equipos favoritos aparecen aquí con su partido más relevante del día. Si hubo partido hoy,
          se muestra ese encuentro; si no, se muestra el próximo disponible.
        </p>
      </div>

      {!teams.length ? (
        <div className="empty-state team-state">
          <Search size={18} />
          <span>No tienes equipos favoritos todavía. Guarda algunos desde el catálogo de equipos.</span>
          <Link className="ask-button" href="/equipos">Ir a equipos</Link>
        </div>
      ) : loading ? (
        <div className="empty-state team-state">Cargando tus equipos favoritos...</div>
      ) : (
        <div className="favorites-grid">
          {favoriteCards.map(({ team, match }) => {
            const logo = team.logo_url || "";
            const opponent = match ? (match.home_team.id === team.id ? match.away_team : match.home_team) : null;
            const isToday = match ? new Date(`${match.match_date.slice(0, 10)}T12:00:00Z`).toISOString().slice(0, 10) === new Date(new Date().getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10) : false;

            return (
              <article className="favorite-card" key={team.id}>
                <div className="favorite-header">
                  <div className="favorite-team-meta">
                    <span className="favorite-team-logo-shell">
                      {logo ? <img src={logo} alt="" className="favorite-team-logo" /> : <Shield size={18} />}
                    </span>
                    <div>
                      <small>Equipo favorito</small>
                      <strong>{team.name}</strong>
                    </div>
                  </div>
                  <Link href={`/equipos/${team.id}`} className="outline-link">Ficha <ArrowLeft size={14} /></Link>
                </div>

                {match ? (
                  <div className="favorite-match-card">
                    <div className="favorite-match-topline">
                      <span className="favorite-match-tag">{isToday ? "Hoy" : "Próximo"}</span>
                      <span>{formatFixtureDate(match.match_date)}</span>
                    </div>
                    <div className="favorite-match-logos">
                      <span>
                        {team.logo_url ? <img src={team.logo_url} alt="" /> : <Shield size={16} />}
                        <strong>{team.name}</strong>
                      </span>
                      <small>vs</small>
                      <span>
                        {opponent?.logo_url ? <img src={opponent.logo_url} alt="" /> : <Shield size={16} />}
                        <strong>{opponent?.name ?? "Rival"}</strong>
                      </span>
                    </div>
                    <div className="favorite-match-meta">
                      <span><CalendarDays size={13} /> {formatTime(match.kickoff_at)}</span>
                      <span><Trophy size={13} /> {match.competition.name}</span>
                    </div>
                    <Link className="favorite-match-link" href={`/partidos/${match.id}`}>
                      Ver partido
                    </Link>
                  </div>
                ) : (
                  <div className="favorite-empty-match">
                    No hay partidos relevantes para este equipo en la agenda actual.
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      <ResponsibleNote />
    </AppShell>
  );
}
