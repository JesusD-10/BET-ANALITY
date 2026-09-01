/* eslint-disable @next/next/no-img-element */
"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Flag,
  Heart,
  MapPin,
  Shield,
  Trophy,
} from "lucide-react";

import AppShell, { ResponsibleNote } from "../../components/AppShell";
import {
  ApiError,
  ApiTimeoutError,
  getTeam,
  getTeamMatches,
  isAbortError,
  type TeamDetailResponse,
  type TeamMatch,
  type TeamMatchesResponse,
  type TeamMatchScope,
} from "../../lib/api";
import { readFavoriteTeams, toggleFavoriteTeam } from "../../lib/favorites";

const HISTORY_PAGE_SIZE = 15;
const dateFormatter = new Intl.DateTimeFormat("es-PE", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const timeFormatter = new Intl.DateTimeFormat("es-PE", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "America/Lima",
});

const scopes: Array<{ value: TeamMatchScope; label: string }> = [
  { value: "past", label: "Partidos pasados" },
  { value: "upcoming", label: "Próximos" },
  { value: "all", label: "Todos" },
];

function formatDate(value?: string | null) {
  if (!value) return "Sin datos";
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
}

function errorMessage(error: unknown, target: "team" | "matches") {
  if (error instanceof ApiError && error.status === 404) {
    return target === "team"
      ? "Este equipo no existe en el catálogo histórico."
      : "No encontramos el historial solicitado.";
  }
  if (error instanceof ApiTimeoutError) {
    return "La consulta tardó más de lo esperado. Intenta nuevamente.";
  }
  if (error instanceof ApiError && error.status >= 500) {
    return "El archivo histórico no está disponible temporalmente.";
  }
  if (error instanceof ApiError) return error.detail;
  return "No pudimos conectar con el archivo histórico.";
}

function resultLabel(result?: string | null) {
  if (result === "win") return "Victoria";
  if (result === "draw") return "Empate";
  if (result === "loss") return "Derrota";
  return null;
}

function resultClass(result?: string | null) {
  if (result === "win") return "is-win";
  if (result === "draw") return "is-draw";
  if (result === "loss") return "is-loss";
  return "is-pending";
}

function MatchRow({ match, teamId }: { match: TeamMatch; teamId: number }) {
  const outcome = resultLabel(match.result);
  const hasScore = match.home_score !== null && match.home_score !== undefined
    && match.away_score !== null && match.away_score !== undefined;
  const preciseKickoff = match.kickoff_precision === "datetime" && match.kickoff_at;

  return (
    <article className="team-history-row">
      <div className="team-history-date">
        <strong>{formatDate(match.match_date)}</strong>
        {preciseKickoff && <span>{timeFormatter.format(new Date(match.kickoff_at))} h</span>}
      </div>
      <div className="team-history-match">
        <small>
          {match.competition.name}
          {match.season?.label ? ` · ${match.season.label}` : ""}
          {match.round ? ` · ${match.round}` : ""}
        </small>
        <div className="team-history-score">
          <span className={match.home_team.id === teamId ? "selected-team" : ""}>
            {match.home_team.logo_url && <img src={match.home_team.logo_url} alt="" />}
            {match.home_team.name}
          </span>
          <b>{hasScore ? `${match.home_score} – ${match.away_score}` : "–"}</b>
          <span className={match.away_team.id === teamId ? "selected-team" : ""}>
            {match.away_team.logo_url && <img src={match.away_team.logo_url} alt="" />}
            {match.away_team.name}
          </span>
        </div>
        {(match.venue || match.opponent?.name) && (
          <span className="team-history-subline">
            {match.venue ? <><MapPin size={11} /> {match.venue}</> : <>Rival: {match.opponent.name}</>}
          </span>
        )}
      </div>
      <span className={`team-result-badge ${resultClass(match.result)}`}>
        {outcome ?? match.status_short ?? match.status}
      </span>
    </article>
  );
}

export default function TeamDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const teamId = Number(id);
  const validTeamId = Number.isSafeInteger(teamId) && teamId > 0;
  const [detail, setDetail] = useState<TeamDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(validTeamId);
  const [detailError, setDetailError] = useState(validTeamId ? "" : "El identificador del equipo no es válido.");
  const [detailRetry, setDetailRetry] = useState(0);
  const [scope, setScope] = useState<TeamMatchScope>("past");
  const [competitionId, setCompetitionId] = useState("");
  const [seasonId, setSeasonId] = useState("");
  const [page, setPage] = useState(1);
  const [matches, setMatches] = useState<TeamMatchesResponse | null>(null);
  const [matchesLoading, setMatchesLoading] = useState(validTeamId);
  const [matchesError, setMatchesError] = useState("");
  const [matchesRetry, setMatchesRetry] = useState(0);
  const [favoriteIds, setFavoriteIds] = useState<Set<number>>(() => new Set(readFavoriteTeams().map((team) => team.id)));

  useEffect(() => {
    const syncFavorites = () => setFavoriteIds(new Set(readFavoriteTeams().map((team) => team.id)));
    syncFavorites();
    window.addEventListener("storage", syncFavorites);
    return () => window.removeEventListener("storage", syncFavorites);
  }, []);

  useEffect(() => {
    if (!validTeamId) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");

    getTeam(teamId, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setDetail(response);
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted || isAbortError(requestError)) return;
        setDetail(null);
        setDetailError(errorMessage(requestError, "team"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });

    return () => controller.abort();
  }, [teamId, validTeamId, detailRetry]);

  useEffect(() => {
    if (!validTeamId) return;
    const controller = new AbortController();
    setMatchesLoading(true);
    setMatchesError("");

    getTeamMatches(
      teamId,
      {
        scope,
        page,
        pageSize: HISTORY_PAGE_SIZE,
        competitionId: competitionId ? Number(competitionId) : undefined,
        seasonId: seasonId ? Number(seasonId) : undefined,
      },
      controller.signal,
    )
      .then((response) => {
        if (!controller.signal.aborted) setMatches(response);
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted || isAbortError(requestError)) return;
        setMatches(null);
        setMatchesError(errorMessage(requestError, "matches"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setMatchesLoading(false);
      });

    return () => controller.abort();
  }, [teamId, validTeamId, scope, competitionId, seasonId, page, matchesRetry]);

  const visibleCompetitions = useMemo(
    () => [...(detail?.competitions ?? [])].sort((left, right) => right.matches - left.matches),
    [detail?.competitions],
  );

  function selectScope(value: TeamMatchScope) {
    setScope(value);
    setPage(1);
  }

  function selectCompetition(value: string) {
    setCompetitionId(value);
    setPage(1);
  }

  function selectSeason(value: string) {
    setSeasonId(value);
    setPage(1);
  }

  if (detailLoading) {
    return (
      <AppShell>
        <Link className="back-link" href="/equipos"><ArrowLeft size={16} /> Volver a equipos</Link>
        <div className="empty-state team-state">Preparando la ficha histórica del equipo...</div>
      </AppShell>
    );
  }

  if (detailError || !detail) {
    return (
      <AppShell>
        <Link className="back-link" href="/equipos"><ArrowLeft size={16} /> Volver a equipos</Link>
        <div className="empty-state team-state" role="alert">
          <CircleAlert size={18} />
          <span>{detailError || "No fue posible cargar este equipo."}</span>
          {validTeamId && (
            <button className="ask-button" type="button" onClick={() => setDetailRetry((value) => value + 1)}>
              Reintentar
            </button>
          )}
        </div>
      </AppShell>
    );
  }

  const { team, statistics } = detail;
  const totalPages = Math.max(1, matches?.total_pages ?? 1);
  const isFavorite = favoriteIds.has(team.id);

  return (
    <AppShell contentId="equipo-historial">
      <Link className="back-link" href="/equipos"><ArrowLeft size={16} /> Volver a equipos</Link>

      <header className="team-profile-header">
        <div className="team-profile-logo">
          {team.logo_url ? <img src={team.logo_url} alt="" /> : <Shield size={45} aria-hidden="true" />}
        </div>
        <div className="team-profile-meta">
          <div>
            <p className="eyebrow">{team.kind === "national" ? "SELECCIÓN NACIONAL" : "CLUB"} · ARCHIVO HISTÓRICO</p>
            <h1>{team.name}</h1>
            <span className="team-profile-country">
              {team.country.flag_url && <img src={team.country.flag_url} alt="" />}
              {team.country.name}
              {team.short_code ? ` · ${team.short_code}` : ""}
            </span>
          </div>
          <button
            className={`team-favorite-toggle ${isFavorite ? "active" : ""}`}
            type="button"
            aria-label={isFavorite ? `Quitar ${team.name} de favoritos` : `Agregar ${team.name} a favoritos`}
            onClick={() => {
              setFavoriteIds(() => {
                const next = toggleFavoriteTeam({
                  id: team.id,
                  name: team.name,
                  slug: team.slug,
                  kind: team.kind,
                  logo_url: team.logo_url,
                  country_name: team.country.name,
                  country_flag_url: team.country.flag_url,
                });
                return new Set(next.map((item) => item.id));
              });
            }}
          >
            <Heart size={16} fill={isFavorite ? "currentColor" : "none"} aria-hidden="true" />
            {isFavorite ? "En favoritos" : "Agregar a favoritos"}
          </button>
        </div>
      </header>

      <section className="team-stat-grid" aria-label="Resumen histórico">
        <article><small>Partidos registrados</small><strong>{statistics.total_matches.toLocaleString("es-PE")}</strong></article>
        <article><small>Victorias</small><strong>{statistics.wins.toLocaleString("es-PE")}</strong></article>
        <article><small>Empates</small><strong>{statistics.draws.toLocaleString("es-PE")}</strong></article>
        <article><small>Derrotas</small><strong>{statistics.losses.toLocaleString("es-PE")}</strong></article>
        <article><small>Goles a favor</small><strong>{statistics.goals_for.toLocaleString("es-PE")}</strong></article>
        <article><small>Goles en contra</small><strong>{statistics.goals_against.toLocaleString("es-PE")}</strong></article>
      </section>

      <div className="team-record-context">
        <span><CalendarDays size={15} /> Primer registro <b>{formatDate(statistics.first_match_date)}</b></span>
        <span><CalendarDays size={15} /> Último resultado <b>{formatDate(statistics.last_match_date)}</b></span>
        {statistics.next_match_date && <span><CalendarDays size={15} /> Próximo partido <b>{formatDate(statistics.next_match_date)}</b></span>}
      </div>

      <section className="team-competitions" aria-labelledby="team-competitions-title">
        <div className="section-heading">
          <div>
            <p className="section-kicker">COBERTURA</p>
            <h2 id="team-competitions-title">Competiciones registradas</h2>
          </div>
        </div>
        {visibleCompetitions.length > 0 ? (
          <div className="team-competition-list">
            {visibleCompetitions.slice(0, 10).map((competition) => (
              <button
                className={competitionId === String(competition.id) ? "active" : ""}
                type="button"
                key={competition.id}
                onClick={() => selectCompetition(
                  competitionId === String(competition.id) ? "" : String(competition.id),
                )}
              >
                <Trophy size={14} />
                <span>{competition.name}</span>
                <b>{competition.matches}</b>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-state">No hay competiciones vinculadas a este equipo.</div>
        )}
      </section>

      <section className="team-history-section" aria-labelledby="team-history-title">
        <div className="section-heading team-history-heading">
          <div>
            <p className="section-kicker">RESULTADOS Y CALENDARIO</p>
            <h2 id="team-history-title">Historial de partidos</h2>
          </div>
          <span>{matches?.total ?? 0} registros</span>
        </div>

        <div className="team-history-filters">
          <div className="team-scope-tabs" role="tablist" aria-label="Tipo de partidos">
            {scopes.map((item) => (
              <button
                className={scope === item.value ? "active" : ""}
                type="button"
                role="tab"
                aria-selected={scope === item.value}
                key={item.value}
                onClick={() => selectScope(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <label>
            <span className="sr-only">Filtrar por competición</span>
            <select value={competitionId} onChange={(event) => selectCompetition(event.target.value)}>
              <option value="">Todas las competiciones</option>
              {visibleCompetitions.map((competition) => (
                <option value={competition.id} key={competition.id}>{competition.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Filtrar por temporada</span>
            <select value={seasonId} onChange={(event) => selectSeason(event.target.value)}>
              <option value="">Todas las temporadas</option>
              {detail.seasons.map((season) => (
                <option value={season.id} key={season.id}>{season.label} ({season.matches})</option>
              ))}
            </select>
          </label>
        </div>

        {matchesLoading ? (
          <div className="empty-state team-state">Consultando el historial...</div>
        ) : matchesError ? (
          <div className="empty-state team-state" role="alert">
            <CircleAlert size={18} />
            <span>{matchesError}</span>
            <button className="ask-button" type="button" onClick={() => setMatchesRetry((value) => value + 1)}>
              Reintentar
            </button>
          </div>
        ) : !matches?.items.length ? (
          <div className="empty-state team-state">
            {scope === "past"
              ? "No hay partidos finalizados con estos filtros."
              : scope === "upcoming"
                ? "No hay próximos partidos registrados con estos filtros."
                : "No hay partidos registrados con estos filtros."}
          </div>
        ) : (
          <>
            <div className="team-history-list">
              {matches.items.map((match) => <MatchRow match={match} teamId={teamId} key={match.id} />)}
            </div>
            {totalPages > 1 && (
              <nav className="history-pagination" aria-label="Páginas del historial">
                <button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                  <ChevronLeft size={16} /> Anterior
                </button>
                <span>Página <b>{matches.page}</b> de <b>{totalPages}</b></span>
                <button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                  Siguiente <ChevronRight size={16} />
                </button>
              </nav>
            )}
          </>
        )}
      </section>

      <div className="team-history-note">
        <Flag size={16} /> Los resultados reflejan únicamente los partidos disponibles en las fuentes importadas.
      </div>
      <ResponsibleNote />
    </AppShell>
  );
}
