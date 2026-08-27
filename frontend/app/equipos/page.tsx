/* eslint-disable @next/next/no-img-element */
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Heart,
  Search,
  Shield,
  Users,
  X,
} from "lucide-react";

import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import {
  ApiError,
  ApiTimeoutError,
  getTeams,
  isAbortError,
  type TeamSearchResponse,
  type TeamSummary,
} from "../lib/api";
import { readFavoriteTeams, toggleFavoriteTeam } from "../lib/favorites";

const PAGE_SIZE = 20;

function teamKindLabel(kind: string) {
  return kind === "national" ? "Selección" : kind === "club" ? "Club" : kind;
}

function requestErrorMessage(error: unknown) {
  if (error instanceof ApiTimeoutError) {
    return "La búsqueda tardó más de lo esperado. Intenta nuevamente.";
  }
  if (error instanceof ApiError && error.status >= 500) {
    return "El catálogo de equipos no está disponible temporalmente.";
  }
  if (error instanceof ApiError) return error.detail;
  return "No pudimos conectar con el catálogo de equipos.";
}

function TeamLogo({ team }: { team: TeamSummary }) {
  if (team.logo_url) {
    return <img className="team-directory-logo" src={team.logo_url} alt="" />;
  }
  return (
    <span className="team-directory-logo team-directory-logo-fallback" aria-hidden="true">
      <Shield size={25} />
    </span>
  );
}

export default function TeamsPage() {
  const [query, setQuery] = useState("");
  const [deferredQuery, setDeferredQuery] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<TeamSearchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const [favoriteIds, setFavoriteIds] = useState<Set<number>>(() => new Set(readFavoriteTeams().map((team) => team.id)));

  useEffect(() => {
    const syncFavorites = () => setFavoriteIds(new Set(readFavoriteTeams().map((team) => team.id)));
    syncFavorites();
    window.addEventListener("storage", syncFavorites);
    return () => window.removeEventListener("storage", syncFavorites);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDeferredQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");

    getTeams(
      {
        query: deferredQuery,
        kind: kind || undefined,
        page,
        pageSize: PAGE_SIZE,
      },
      controller.signal,
    )
      .then((response) => {
        if (!controller.signal.aborted) setResult(response);
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted || isAbortError(requestError)) return;
        setResult(null);
        setError(requestErrorMessage(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [deferredQuery, kind, page, retryVersion]);

  function updateQuery(value: string) {
    setQuery(value);
    setPage(1);
  }

  function updateKind(value: string) {
    setKind(value);
    setPage(1);
  }

  const items = result?.items ?? [];
  const totalPages = Math.max(1, result?.total_pages ?? 1);

  return (
    <AppShell contentId="equipos">
      <PageHeader eyebrow="ARCHIVO HISTÓRICO · CLUBES Y SELECCIONES" title="Equipos" />

      <div className="teams-intro">
        <Users size={19} />
        <p>
          Busca un equipo para consultar sus resultados, balance histórico y competiciones
          registradas en la base de datos.
        </p>
      </div>

      <section className="team-search-panel" aria-label="Buscar equipos">
        <label className="search-wrap team-search-input">
          <Search size={18} aria-hidden="true" />
          <span className="sr-only">Nombre del equipo</span>
          <input
            autoComplete="off"
            value={query}
            onChange={(event) => updateQuery(event.target.value)}
            placeholder="Busca un club o selección..."
          />
          {query && (
            <button
              className="clear-search-button"
              type="button"
              aria-label="Limpiar búsqueda"
              onClick={() => updateQuery("")}
            >
              <X size={16} />
            </button>
          )}
        </label>
        <label className="team-kind-filter">
          <span>Tipo</span>
          <select value={kind} onChange={(event) => updateKind(event.target.value)}>
            <option value="">Todos</option>
            <option value="club">Clubes</option>
            <option value="national">Selecciones</option>
          </select>
        </label>
      </section>

      <div className="team-results-heading" aria-live="polite">
        <span>
          {loading
            ? "Consultando catálogo..."
            : `${result?.total ?? 0} equipo${result?.total === 1 ? "" : "s"}`}
        </span>
        {deferredQuery && !loading && <small>Resultados para “{deferredQuery}”</small>}
      </div>

      {loading ? (
        <div className="empty-state team-state">Buscando equipos en el archivo histórico...</div>
      ) : error ? (
        <div className="empty-state team-state" role="alert">
          <CircleAlert size={18} />
          <span>{error}</span>
          <button className="ask-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>
            Reintentar
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state team-state">
          No encontramos equipos con esos filtros. Prueba con otro nombre o muestra todos los tipos.
        </div>
      ) : (
        <>
          <div className="team-directory-grid">
            {items.map((team) => {
              const isFavorite = favoriteIds.has(team.id);
              return (
                <div className="team-directory-card-shell" key={team.id}>
                  <Link className="team-directory-card" href={`/equipos/${team.id}`}>
                    <TeamLogo team={team} />
                    <div>
                      <small>{teamKindLabel(team.kind)}</small>
                      <strong>{team.name}</strong>
                      <span>
                        {team.country.flag_url && <img src={team.country.flag_url} alt="" />}
                        {team.country.name}
                      </span>
                    </div>
                    <ChevronRight size={18} aria-hidden="true" />
                  </Link>
                  <button
                    className={`team-directory-favorite ${isFavorite ? "active" : ""}`}
                    type="button"
                    aria-label={isFavorite ? `Quitar ${team.name} de favoritos` : `Agregar ${team.name} a favoritos`}
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
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
                  </button>
                </div>
              );
            })}
          </div>

          {totalPages > 1 && (
            <nav className="history-pagination" aria-label="Páginas de equipos">
              <button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                <ChevronLeft size={16} /> Anterior
              </button>
              <span>
                Página <b>{result?.page ?? page}</b> de <b>{totalPages}</b>
              </span>
              <button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                Siguiente <ChevronRight size={16} />
              </button>
            </nav>
          )}
        </>
      )}

      <ResponsibleNote />
    </AppShell>
  );
}
