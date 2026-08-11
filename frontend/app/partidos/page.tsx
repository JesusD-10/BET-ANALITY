"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import { getMatches, Match } from "../lib/api";

const groupMatchesByDate = (matchesList: Match[]) => {
  const groups: { [key: string]: Match[] } = {};
  for (const m of matchesList) {
    const dateObj = new Date(m.kickoff_at);
    const dateStr = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" }).format(dateObj);
    const formattedDate = dateStr.charAt(0).toUpperCase() + dateStr.slice(1);
    if (!groups[formattedDate]) groups[formattedDate] = [];
    groups[formattedDate].push(m);
  }
  return groups;
};

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

  const grouped = groupMatchesByDate(matches);

  return (
    <AppShell>
      <PageHeader eyebrow="AGENDA · BÚSQUEDA Y CALENDARIO" title="Partidos" action={<Link className="outline-link" href="/">Volver al panorama <ArrowUpRight size={15} /></Link>} />
      <div className="page-toolbar"><div className="search-wrap"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Equipo, liga o competición" /></div><span className="date-label">Organizados por fecha</span></div>
      {notice && <p className="data-notice">{notice}</p>}{error && <p className="api-alert">{error}</p>}
      <section className="match-list">
        {Object.entries(grouped).map(([dateLabel, dayMatches]) => (
          <div key={dateLabel}>
            <div className="date-group-header">{dateLabel}</div>
            {dayMatches.map((match) => (
              <Link className="match-list-row" href={`/partidos/${match.id}`} key={match.id}>
                <div>
                  <small>{match.competition} · {new Date(match.kickoff_at).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" })}</small>
                  <strong>
                    {match.home_logo && <img src={match.home_logo} alt="" className="row-team-logo" />}
                    {match.home_team} <span>vs</span>{" "}
                    {match.away_logo && <img src={match.away_logo} alt="" className="row-team-logo" />}
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
        ))}
      </section>
      <ResponsibleNote />
    </AppShell>
  );
}
