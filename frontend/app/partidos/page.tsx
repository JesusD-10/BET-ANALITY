"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import { getMatches, Match } from "../lib/api";

export default function PartidosPage() {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      getMatches(query).then((data) => { setMatches(data.matches); setNotice(data.notice ?? ""); setError(""); }).catch(() => setError("No se pudo conectar con el catálogo de partidos."));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  return <AppShell><PageHeader eyebrow="AGENDA · BÚSQUEDA Y CALENDARIO" title="Partidos" action={<Link className="outline-link" href="/">Volver al panorama <ArrowUpRight size={15} /></Link>} />
  <div className="page-toolbar"><div className="search-wrap"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Equipo, liga o competición" /></div><span className="date-label">Próximos y destacados</span></div>
  {notice && <p className="data-notice">{notice}</p>}{error && <p className="api-alert">{error}</p>}
  <section className="match-list">{matches.map((match) => <Link className="match-list-row" href={`/partidos/${match.id}`} key={match.id}><div><small>{match.competition} · {new Date(match.kickoff_at).toLocaleString("es-PE", { dateStyle: "medium", timeStyle: "short" })}</small><strong>{match.home_team} <span>vs</span> {match.away_team}</strong></div><div className="row-meta"><span>{match.odds_available ? "Cuotas" : "Sin cuotas"}</span><b>{Math.round(match.data_quality * 100)}%</b><ArrowUpRight size={16} /></div></Link>)}</section>
  <ResponsibleNote /></AppShell>;
}
