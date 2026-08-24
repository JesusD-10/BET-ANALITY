/* eslint-disable @next/next/no-img-element */
"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState, useTransition } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowUpRight, Search } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import LeagueSelector from "../components/LeagueSelector";
import {
  ApiError,
  ApiTimeoutError,
  getMatches,
  isAbortError,
  type Match,
} from "../lib/api";
import { findLeagueByName, getLeagueById } from "../lib/leagues";

function PartidosContent() {
  const searchParams = useSearchParams();
  const selectedLeagueId = searchParams.get("liga");
  const selectedLeague = selectedLeagueId ? getLeagueById(selectedLeagueId) : undefined;
  const [, startTransition] = useTransition();

  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    if (!selectedLeagueId) {
      setMatches([]);
      setError("");
      setNotice("");
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError("");
    setNotice("");

    const timer = window.setTimeout(() => {
      getMatches(query, controller.signal)
        .then((data) => {
          if (controller.signal.aborted) return;
          setMatches(data.matches);
          setNotice(data.notice ?? "");
        })
        .catch((requestError: unknown) => {
          if (controller.signal.aborted || isAbortError(requestError)) return;

          setMatches([]);
          setNotice("");
          if (requestError instanceof ApiTimeoutError) {
            setError("La agenda tardo demasiado en responder. Intenta nuevamente.");
          } else if (requestError instanceof ApiError && requestError.status >= 500) {
            setError("El servicio de partidos no esta disponible temporalmente.");
          } else if (requestError instanceof ApiError) {
            setError(requestError.detail);
          } else {
            setError("No se pudo conectar con el catalogo de partidos.");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [selectedLeagueId, query, retryVersion]);

  const leagueMatches = useMemo(() => {
    if (!selectedLeagueId) return [];
    return matches.filter((match) => {
      const leagueInfo = findLeagueByName(match.competition);
      return leagueInfo?.id === selectedLeagueId;
    });
  }, [matches, selectedLeagueId]);

  return (
    <AppShell>
      <PageHeader
        eyebrow="AGENDA · BUSQUEDA Y CALENDARIO"
        title="Partidos"
        action={
          <Link className="outline-link" href="/">
            Volver al panorama <ArrowUpRight size={15} />
          </Link>
        }
      />

      {!selectedLeagueId ? (
        <>
          <div className="page-toolbar">
            <span className="date-label">
              Ligas principales primero. Al hacer clic, se abre una pestaña nueva con sus
              partidos.
            </span>
          </div>
          <section className="league-browser">
            <LeagueSelector selectedLeagueId={selectedLeagueId} openInNewTab />
          </section>
        </>
      ) : (
        <>
          <div className="page-toolbar">
            <div className="search-wrap">
              <Search size={18} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Buscar equipo en esta liga..."
              />
            </div>
            <Link
              className="text-button"
              href="/partidos"
              onClick={(event) => {
                event.preventDefault();
                startTransition(() => {
                  window.history.pushState(null, "", "/partidos");
                });
              }}
            >
              <ArrowLeft size={15} />
              Volver a ligas
            </Link>
          </div>

          {selectedLeague && (
            <p className="data-notice">
              {selectedLeague.name} · {selectedLeague.country}
            </p>
          )}

          {notice && <p className="data-notice">{notice}</p>}
          {error && (
            <div className="api-alert">
              <span>{error}</span>
              <button
                className="text-button"
                type="button"
                onClick={() => setRetryVersion((value) => value + 1)}
              >
                Reintentar
              </button>
            </div>
          )}

          <section className="league-detail-view">
            {loading ? (
              <div className="empty-state">Consultando partidos...</div>
            ) : error ? null : leagueMatches.length === 0 ? (
              <div className="empty-state">
                {query.trim()
                  ? `No encontramos partidos para "${query.trim()}" en esta liga.`
                  : "No hay partidos disponibles en esta liga para hoy."}
              </div>
            ) : (
              <div className="match-list">
                {leagueMatches.map((match) => (
                  <Link className="match-list-row" href={`/partidos/${match.id}`} key={match.id}>
                    <div>
                      <small>
                        {new Date(match.kickoff_at).toLocaleDateString("es-PE", {
                          weekday: "short",
                          day: "2-digit",
                          month: "short",
                        })}{" "}
                        ·{" "}
                        {new Date(match.kickoff_at).toLocaleTimeString("es-PE", {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </small>
                      <strong>
                        {match.home_logo && (
                          <img src={match.home_logo} alt="" className="row-team-logo" />
                        )}
                        {match.home_team} <span>vs</span>{" "}
                        {match.away_logo && (
                          <img src={match.away_logo} alt="" className="row-team-logo" />
                        )}
                        {match.away_team}
                      </strong>
                    </div>
                    <div className="row-meta">
                      <span>{match.odds_available ? "Cuotas" : "Sin cuotas"}</span>
                      <b>{Math.round(match.data_quality * 100)}%</b>
                      <ArrowUpRight size={16} />
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <ResponsibleNote />
    </AppShell>
  );
}

function PartidosLoading() {
  return (
    <AppShell>
      <PageHeader
        eyebrow="AGENDA · BUSQUEDA Y CALENDARIO"
        title="Partidos"
        action={
          <Link className="outline-link" href="/">
            Volver al panorama <ArrowUpRight size={15} />
          </Link>
        }
      />
      <div className="empty-state">Preparando agenda...</div>
      <ResponsibleNote />
    </AppShell>
  );
}

export default function PartidosPage() {
  return (
    <Suspense fallback={<PartidosLoading />}>
      <PartidosContent />
    </Suspense>
  );
}
