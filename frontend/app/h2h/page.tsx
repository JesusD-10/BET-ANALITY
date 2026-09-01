/* eslint-disable @next/next/no-img-element */
"use client";

import { useEffect, useState } from "react";
import { Pencil, Search, Swords } from "lucide-react";

import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import {
  ApiError,
  getTeamHeadToHead,
  getTeams,
  isAbortError,
  refreshTeamHeadToHead,
  type TeamMatch,
  type TeamSummary,
} from "../lib/api";

function TeamPicker({
  label,
  value,
  selected,
  onSelect,
  onQueryChange,
  onClear,
}: {
  label: string;
  value: string;
  selected: TeamSummary | null;
  onSelect: (team: TeamSummary) => void;
  onQueryChange: (value: string) => void;
  onClear: () => void;
}) {
  const [results, setResults] = useState<TeamSummary[]>([]);

  useEffect(() => {
    if (selected || value.trim().length < 2) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getTeams({ query: value, pageSize: 8 }, controller.signal)
        .then((response) => setResults(response.items))
        .catch((error: unknown) => {
          if (!isAbortError(error)) setResults([]);
        });
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selected, value]);

  return (
    <div className="h2h-picker">
      <span className="h2h-picker-label">{label}</span>
      {selected ? (
        <div className="h2h-selected">
          {selected.logo_url && <img src={selected.logo_url} alt="" />}
          <span><strong>{selected.name}</strong><small>{selected.country.name}</small></span>
          <button type="button" aria-label={`Cambiar ${label.toLowerCase()}`} onClick={onClear}>
            <Pencil size={15} aria-hidden="true" />
          </button>
        </div>
      ) : (
        <label className="search-wrap">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">{label}</span>
          <input value={value} onChange={(event) => onQueryChange(event.target.value)} placeholder="Escribe un equipo..." />
        </label>
      )}
      {!selected && results.length > 0 && value.trim().length >= 2 && (
        <div className="h2h-options">
          {results.map((team) => (
            <button type="button" key={team.id} onClick={() => onSelect(team)}>
              {team.logo_url && <img src={team.logo_url} alt="" />}
              <span><strong>{team.name}</strong><small>{team.country.name}</small></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function HeadToHeadPage() {
  const [home, setHome] = useState<TeamSummary | null>(null);
  const [away, setAway] = useState<TeamSummary | null>(null);
  const [homeQuery, setHomeQuery] = useState("");
  const [awayQuery, setAwayQuery] = useState("");
  const [matches, setMatches] = useState<TeamMatch[]>([]);
  const [upcoming, setUpcoming] = useState<TeamMatch[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!home?.id || !away?.id || home.id === away.id) {
      setMatches([]);
      setTotal(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    getTeamHeadToHead(home.id, away.id, 1, 100, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) {
          setMatches(response.items);
          setUpcoming(response.upcoming);
          setTotal(response.total);
          refreshTeamHeadToHead(home.id, away.id, controller.signal)
            .then(() => getTeamHeadToHead(home.id, away.id, 1, 100, controller.signal))
            .then((updated) => {
              if (!controller.signal.aborted) setUpcoming(updated.upcoming);
            })
            .catch(() => undefined);
        }
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(requestError instanceof ApiError ? requestError.detail : "No fue posible consultar los cruces históricos.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [home?.id, away?.id]);

  return (
    <AppShell contentId="h2h">
      <PageHeader eyebrow="ARCHIVO HISTÓRICO" title="Cara a cara" />
      <section className="h2h-panel" aria-label="Seleccionar equipos para enfrentamientos directos">
        <TeamPicker label="Equipo local" value={homeQuery} selected={home} onQueryChange={(value) => { setHome(null); setHomeQuery(value); }} onClear={() => { setHome(null); setHomeQuery(""); }} onSelect={(team) => { setHome(team); setHomeQuery(team.name); }} />
        <Swords className="h2h-versus" size={24} aria-hidden="true" />
        <TeamPicker label="Equipo visitante" value={awayQuery} selected={away} onQueryChange={(value) => { setAway(null); setAwayQuery(value); }} onClear={() => { setAway(null); setAwayQuery(""); }} onSelect={(team) => { setAway(team); setAwayQuery(team.name); }} />
      </section>
      {home?.id === away?.id && <div className="empty-state">Selecciona dos equipos distintos.</div>}
      {loading && <div className="empty-state">Buscando enfrentamientos en el historial...</div>}
      {error && <div className="empty-state" role="alert">{error}</div>}
      {total !== null && !loading && !error && (
        <section className="team-history-section" aria-labelledby="h2h-results-title">
          <div className="section-heading team-history-heading"><div><p className="section-kicker">CRUCES REGISTRADOS</p><h2 id="h2h-results-title">{home?.name} vs {away?.name}</h2></div><span>{total} partidos</span></div>
          {upcoming.length > 0 && <div className="team-record-context">{upcoming.map((match) => <span key={match.id}>Próximo cruce <b>{new Date(`${match.match_date}T00:00:00Z`).toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })}</b> · {match.competition.name}</span>)}</div>}
          {matches.length ? <div className="team-history-list">{matches.map((match) => <article className="team-history-row" key={match.id}><div className="team-history-date"><strong>{new Date(`${match.match_date}T00:00:00Z`).toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })}</strong><span>{match.season?.label ?? "Temporada N/D"}</span></div><div className="team-history-match"><small>{match.competition.name}</small><div className="team-history-score"><span>{match.home_team.name}</span><b>{match.home_score ?? "–"} – {match.away_score ?? "–"}</b><span>{match.away_team.name}</span></div></div></article>)}</div> : <div className="empty-state">No hay cruces históricos finalizados entre estos equipos.</div>}
        </section>
      )}
      <ResponsibleNote />
    </AppShell>
  );
}