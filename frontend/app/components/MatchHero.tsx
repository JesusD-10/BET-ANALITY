/* eslint-disable @next/next/no-img-element */

import type { Match } from "../lib/api";

type HeroPhase = "future" | "live" | "finished" | "other";

const LIVE_STATUS_CODES = new Set(["1H", "HT", "2H", "BT", "ET", "P", "LIVE"]);
const FINISHED_STATUS_CODES = new Set(["FT", "AET", "PEN", "AWD", "WO"]);
const FUTURE_STATUS_CODES = new Set(["NS", "TBD", "TBA", "SCHEDULED", "TIMED"]);

function normalizedStatus(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toUpperCase();
}

function heroPhase(match: Match): HeroPhase {
  const shortStatus = normalizedStatus(match.status_short ?? "");
  const status = normalizedStatus(match.status ?? "");
  if (
    LIVE_STATUS_CODES.has(shortStatus) ||
    status.startsWith("EN JUEGO") ||
    ["ENTRETIEMPO", "EN PAUSA", "DESCANSO", "TIEMPO EXTRA", "PENALES"].some((item) =>
      status.includes(item),
    )
  ) return "live";
  if (FINISHED_STATUS_CODES.has(shortStatus) || status.startsWith("FINALIZADO")) return "finished";
  if (FUTURE_STATUS_CODES.has(shortStatus) || ["PROGRAMADO", "POR DEFINIR"].includes(status)) return "future";
  const kickoff = new Date(match.kickoff_at).getTime();
  return Number.isFinite(kickoff) && kickoff > Date.now() ? "future" : "other";
}

function matchStatusLabel(match: Match, phase: HeroPhase): string {
  const shortStatus = normalizedStatus(match.status_short ?? "");
  const status = normalizedStatus(match.status ?? "");
  if (phase === "live") {
    if (shortStatus === "HT" || status.includes("ENTRETIEMPO")) return "ENTRETIEMPO";
    if (shortStatus === "P" || status.includes("PENALES")) return "PENALES";
    if (shortStatus === "ET" || status.includes("TIEMPO EXTRA")) {
      return typeof match.elapsed === "number" && match.elapsed > 0
        ? `TIEMPO EXTRA · ${match.elapsed}′`
        : "TIEMPO EXTRA";
    }
    if (typeof match.elapsed === "number" && match.elapsed > 0) return `EN VIVO · ${match.elapsed}′`;
    return "EN VIVO";
  }
  if (phase === "finished") return "FINALIZADO";
  return status || (phase === "future" ? "PROGRAMADO" : "ESTADO PENDIENTE");
}

export default function MatchHero({
  match,
  modelVersion,
  updatedAt,
}: {
  match: Match;
  modelVersion: string;
  updatedAt?: string;
}) {
  const provider = match.source_provider
    ? match.source_provider.replaceAll("-", " ")
    : null;
  const phase = heroPhase(match);
  const statusLabel = matchStatusLabel(match, phase);
  const hasScore = typeof match.home_score === "number" && typeof match.away_score === "number";
  const score = hasScore ? `${match.home_score} - ${match.away_score}` : null;
  let centerValue = "VS";
  let centerDetail = "";
  if (phase === "live") {
    if (score) {
      centerValue = score;
      centerDetail = statusLabel;
    } else if (typeof match.elapsed === "number" && match.elapsed > 0) {
      centerValue = `${match.elapsed}′`;
      centerDetail = statusLabel.split(" · ")[0];
    } else {
      centerValue = statusLabel;
      centerDetail = statusLabel === "EN VIVO" ? "" : "EN VIVO";
    }
  } else if (phase === "finished") {
    centerValue = score ?? "FINAL";
    centerDetail = score ? statusLabel : "";
  } else if (phase === "other") {
    centerValue = score ?? statusLabel;
    centerDetail = score ? statusLabel : "";
  }
  const accessibleTitle = hasScore && phase !== "future"
    ? `${match.home_team} ${match.home_score}, ${match.away_team} ${match.away_score}. ${statusLabel}`
    : `${match.home_team} contra ${match.away_team}. ${statusLabel}`;

  return (
    <header className="match-detail-header match-hero">
      <p className="eyebrow match-hero-eyebrow">
        {match.competition} · {new Date(match.kickoff_at).toLocaleString("es-PE", { dateStyle: "full", timeStyle: "short" })}
      </p>
      <h1 className="sr-only">{accessibleTitle}</h1>
      <div className="match-hero-board" aria-hidden="true">
        <div className="match-hero-team">
          <div className="match-hero-logo-shell">
            {match.home_logo ? <img src={match.home_logo} alt="" className="match-hero-logo" /> : <span>{match.home_team.slice(0, 2)}</span>}
          </div>
          <small>LOCAL</small>
          <strong>{match.home_team}</strong>
        </div>
        <div className={`match-hero-versus is-${phase} ${hasScore && phase !== "future" ? "has-score" : ""}`}>
          <span>{centerValue}</span>
          {centerDetail && <small>{centerDetail}</small>}
          <i />
        </div>
        <div className="match-hero-team">
          <div className="match-hero-logo-shell">
            {match.away_logo ? <img src={match.away_logo} alt="" className="match-hero-logo" /> : <span>{match.away_team.slice(0, 2)}</span>}
          </div>
          <small>VISITANTE</small>
          <strong>{match.away_team}</strong>
        </div>
      </div>
      <div className="detail-meta match-hero-meta">
        <span>{statusLabel}</span>
        <span>Calidad {Math.round(match.data_quality * 100)}%</span>
        <span>Modelo: {modelVersion}</span>
        {provider && <span>Datos: {provider}</span>}
        {updatedAt && (
          <span>
            Actualizado: {new Date(updatedAt).toLocaleString("es-PE", {
              dateStyle: "short",
              timeStyle: "short",
            })}
          </span>
        )}
        {match.venue && <span>Estadio: {match.venue}</span>}
      </div>
    </header>
  );
}
