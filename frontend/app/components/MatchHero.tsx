/* eslint-disable @next/next/no-img-element */

import type { Match } from "../lib/api";

export default function MatchHero({
  match,
  modelVersion,
}: {
  match: Match;
  modelVersion: string;
}) {
  return (
    <header className="match-detail-header match-hero">
      <p className="eyebrow match-hero-eyebrow">
        {match.competition} · {new Date(match.kickoff_at).toLocaleString("es-PE", { dateStyle: "full", timeStyle: "short" })}
      </p>
      <h1 className="sr-only">{match.home_team} vs {match.away_team}</h1>
      <div className="match-hero-board" aria-hidden="true">
        <div className="match-hero-team">
          <div className="match-hero-logo-shell">
            {match.home_logo ? <img src={match.home_logo} alt="" className="match-hero-logo" /> : <span>{match.home_team.slice(0, 2)}</span>}
          </div>
          <small>LOCAL</small>
          <strong>{match.home_team}</strong>
        </div>
        <div className="match-hero-versus"><span>VS</span><i /></div>
        <div className="match-hero-team">
          <div className="match-hero-logo-shell">
            {match.away_logo ? <img src={match.away_logo} alt="" className="match-hero-logo" /> : <span>{match.away_team.slice(0, 2)}</span>}
          </div>
          <small>VISITANTE</small>
          <strong>{match.away_team}</strong>
        </div>
      </div>
      <div className="detail-meta match-hero-meta">
        <span>{match.status}</span>
        <span>Calidad {Math.round(match.data_quality * 100)}%</span>
        <span>Modelo: {modelVersion}</span>
        {match.venue && <span>Estadio: {match.venue}</span>}
      </div>
    </header>
  );
}
