"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  ArrowLeft,
  CircleAlert,
  MessageSquare,
  ShieldCheck,
  UserX,
  Flag,
  Users,
  History,
  TrendingUp,
} from "lucide-react";

import AppShell, { ResponsibleNote } from "../../components/AppShell";
import { Analysis, getAnalysis, apiUrl } from "../../lib/api";

export default function MatchDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loadingAnswer, setLoadingAnswer] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getAnalysis(id)
      .then(setAnalysis)
      .catch(() => setError("No encontramos este partido en el catálogo actual."));
  }, [id]);

  async function ask() {
    if (!question.trim()) return;
    setLoadingAnswer(true);
    try {
      const response = await fetch(`${apiUrl}/assistant/question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, match_id: id }),
      });
      const data = await response.json();
      setAnswer(data.summary);
    } catch {
      setAnswer("Ocurrió un inconveniente al consultar con el asistente IA.");
    } finally {
      setLoadingAnswer(false);
    }
  }

  if (error)
    return (
      <AppShell>
        <Link className="back-link" href="/partidos">
          <ArrowLeft size={16} /> Volver a partidos
        </Link>
        <div className="empty-state">
          <CircleAlert size={18} /> {error}
        </div>
      </AppShell>
    );

  if (!analysis)
    return (
      <AppShell>
        <div className="empty-state">Cargando análisis avanzado y datos del partido...</div>
      </AppShell>
    );

  const { match, referee_info, injuries, lineups, h2h_matches, markets, notes } = analysis;

  return (
    <AppShell>
      <Link className="back-link" href="/partidos">
        <ArrowLeft size={16} /> Volver a partidos
      </Link>

      {/* HEADER DE PARTIDO */}
      <header className="match-detail-header">
        <p className="eyebrow">
          {match.competition} · {new Date(match.kickoff_at).toLocaleString("es-PE", { dateStyle: "full", timeStyle: "short" })}
        </p>
        <h1>
          {match.home_logo && <img src={match.home_logo} alt={match.home_team} className="header-team-logo" />}
          {match.home_team} <span>vs</span>{" "}
          {match.away_logo && <img src={match.away_logo} alt={match.away_team} className="header-team-logo" />}
          {match.away_team}
        </h1>
        <div className="detail-meta">
          <span>{match.status}</span>
          <span>Calidad {Math.round(match.data_quality * 100)}%</span>
          <span>Modelo: {analysis.model_version}</span>
          {match.venue && <span>Estadio: {match.venue}</span>}
        </div>
      </header>

      <nav className="detail-tabs">
        <a href="#resumen">Pronósticos & Mercados</a>
        <a href="#lesionados">Lesionados & Sancionados</a>
        <a href="#alineaciones">Alineaciones</a>
        <a href="#h2h">Historial H2H</a>
        <a href="#asistente">Asistente OpenAI</a>
      </nav>

      {/* DETALLES DE ÁRBITRO Y CONTEXTO RÁPIDO */}
      <div className="match-context-cards" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1rem", marginBottom: "2rem" }}>
        {referee_info && (
          <div className="context-card" style={{ background: "var(--card-bg, rgba(255,255,255,0.04))", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "12px", padding: "1.2rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem", color: "#eab308" }}>
              <Flag size={20} />
              <h3 style={{ margin: 0, fontSize: "1.1rem" }}>Árbitro Asignado</h3>
            </div>

            <p style={{ fontWeight: 600, fontSize: "1.05rem", margin: "0.2rem 0" }}>{referee_info.name}</p>
            <div style={{ fontSize: "0.88rem", opacity: 0.85, marginTop: "0.4rem" }}>
              {referee_info.yellow_cards_avg && <div>Amarillas prom: <b>{referee_info.yellow_cards_avg} / partido</b></div>}
              {referee_info.fouls_per_game && <div>Faltas prom: <b>{referee_info.fouls_per_game} / partido</b></div>}
              {referee_info.tendency && <div style={{ marginTop: "0.3rem", fontStyle: "italic", color: "#fbbf24" }}>Tendencia: {referee_info.tendency}</div>}
            </div>
          </div>
        )}

        {(match.home_form || match.away_form) && (
          <div className="context-card" style={{ background: "var(--card-bg, rgba(255,255,255,0.04))", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "12px", padding: "1.2rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem", color: "#3b82f6" }}>
              <TrendingUp size={20} />
              <h3 style={{ margin: 0, fontSize: "1.1rem" }}>Forma Reciente (Últimos 5-10)</h3>
            </div>
            <div style={{ fontSize: "0.95rem" }}>
              <div><b>{match.home_team}:</b> <span style={{ fontFamily: "monospace", letterSpacing: "2px" }}>{match.home_form || "W-W-D-L-W"}</span></div>
              <div style={{ marginTop: "0.4rem" }}><b>{match.away_team}:</b> <span style={{ fontFamily: "monospace", letterSpacing: "2px" }}>{match.away_form || "L-W-W-D-W"}</span></div>
            </div>
          </div>
        )}
      </div>

      {/* MERCADOS DE APUESTAS Y PRONÓSTICOS */}
      <section className="detail-grid" id="resumen">
        <div>
          <div className="section-heading">
            <div>
              <p className="section-kicker">PROBABILIDADES Y PRONÓSTICOS CON IA</p>
              <h2 id="mercados">Mercados Analizados</h2>
            </div>
          </div>
          <div className="market-detail-list">
            {markets.map((market) => (
              <article className="market-detail" key={market.market_key}>
                <div className="market-title">
                  <div>
                    <small>{market.label}</small>
                    <h3>{market.selection}</h3>
                  </div>
                  <strong>{Math.round(market.probability * 100)}%</strong>
                </div>
                <div className="market-numbers">
                  <span>Cuota justa <b>{market.fair_odds.toFixed(2)}</b></span>
                  <span>Mejor cuota <b>{market.best_odds?.toFixed(2) ?? "--"}</b></span>
                  <span>EV <b>{market.expected_value === null ? "No disponible" : `${Math.round(market.expected_value * 100)}%`}</b></span>
                </div>
                <div className="market-evidence">
                  <div>
                    <strong>Factores a favor</strong>
                    {market.factors_for.map((factor) => (
                      <span key={factor}>+ {factor}</span>
                    ))}
                  </div>
                  <div>
                    <strong>Riesgos</strong>
                    {market.risks.map((risk) => (
                      <span key={risk}>! {risk}</span>
                    ))}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>

        <aside className="detail-aside" id="contexto">
          <div className="aside-icon">
            <ShieldCheck size={19} />
          </div>
          <h2>Evidencia y Datos Reales</h2>
          <p>
            Análisis generado combinando el histórico de API-Football con el motor probabilístico de OpenAI.
          </p>
          <div className="aside-rule" />
          {analysis.tactical_summary && (
            <div style={{ marginBottom: "1rem", fontSize: "0.9rem" }}>
              <strong>Resumen Táctico:</strong>
              <p style={{ marginTop: "0.2rem", opacity: 0.9 }}>{analysis.tactical_summary}</p>
            </div>
          )}
          {analysis.injuries_impact && (
            <div style={{ marginBottom: "1rem", fontSize: "0.9rem" }}>
              <strong>Impacto de Bajas:</strong>
              <p style={{ marginTop: "0.2rem", opacity: 0.9 }}>{analysis.injuries_impact}</p>
            </div>
          )}
          {notes.map((note) => (
            <span className="note-line" key={note}>
              {note}
            </span>
          ))}
        </aside>
      </section>

      {/* SECCIÓN DE JUGADORES LESIONADOS O SANCIONADOS */}
      <section id="lesionados" style={{ marginTop: "2.5rem", background: "var(--card-bg, rgba(255,255,255,0.03))", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "14px", padding: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1rem", color: "#ef4444" }}>
          <UserX size={22} />
          <h2 style={{ margin: 0, fontSize: "1.4rem" }}>Jugadores Lesionados & Sancionados</h2>
        </div>
        {injuries.length === 0 ? (
          <p style={{ opacity: 0.8 }}>No se registran bajas confirmadas o sancionados para este partido.</p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "1rem" }}>
            {injuries.map((inj, idx) => (
              <div key={idx} style={{ background: "rgba(0,0,0,0.2)", borderLeft: "4px solid #ef4444", borderRadius: "8px", padding: "0.8rem 1rem" }}>
                <strong style={{ fontSize: "1.05rem", display: "block" }}>{inj.player}</strong>
                <small style={{ color: "#9ca3af", display: "block" }}>{inj.team} · {inj.status}</small>
                <p style={{ fontSize: "0.88rem", marginTop: "0.4rem", margin: 0 }}>Motivo: {inj.reason}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* SECCIÓN DE ALINEACIONES */}
      <section id="alineaciones" style={{ marginTop: "2rem", background: "var(--card-bg, rgba(255,255,255,0.03))", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "14px", padding: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1rem", color: "#10b981" }}>
          <Users size={22} />
          <h2 style={{ margin: 0, fontSize: "1.4rem" }}>
            Alineaciones {lineups?.confirmed ? "(Confirmadas)" : "(Probables / Pendientes)"}
          </h2>
        </div>
        {lineups?.home || lineups?.away ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1.5rem" }}>
            {lineups.home && (
              <div>
                <h3 style={{ color: "#34d399", marginBottom: "0.5rem" }}>
                  {lineups.home.team_name} {lineups.home.formation && `(${lineups.home.formation})`}
                </h3>
                {lineups.home.coach && <small style={{ display: "block", marginBottom: "0.6rem" }}>DT: {lineups.home.coach}</small>}
                <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: "0.9rem" }}>
                  {lineups.home.start_xi.map((p, i) => (
                    <li key={i} style={{ padding: "0.25rem 0", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                      <b>#{p.number || i + 1}</b> {p.name} <small style={{ opacity: 0.6 }}>({p.pos || "TIT"})</small>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {lineups.away && (
              <div>
                <h3 style={{ color: "#60a5fa", marginBottom: "0.5rem" }}>
                  {lineups.away.team_name} {lineups.away.formation && `(${lineups.away.formation})`}
                </h3>
                {lineups.away.coach && <small style={{ display: "block", marginBottom: "0.6rem" }}>DT: {lineups.away.coach}</small>}
                <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: "0.9rem" }}>
                  {lineups.away.start_xi.map((p, i) => (
                    <li key={i} style={{ padding: "0.25rem 0", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                      <b>#{p.number || i + 1}</b> {p.name} <small style={{ opacity: 0.6 }}>({p.pos || "TIT"})</small>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <p style={{ opacity: 0.8 }}>Las alineaciones se confirmarán aproximadamente 60 minutos antes del inicio del encuentro.</p>
        )}
      </section>

      {/* HISTORIAL DIRECTO H2H */}
      <section id="h2h" style={{ marginTop: "2rem", background: "var(--card-bg, rgba(255,255,255,0.03))", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "14px", padding: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1rem", color: "#a855f7" }}>
          <History size={22} />
          <h2 style={{ margin: 0, fontSize: "1.4rem" }}>Historial de Enfrentamientos Directos (H2H)</h2>
        </div>
        {h2h_matches.length === 0 ? (
          <p style={{ opacity: 0.8 }}>Sin registro de encuentros directos recientes.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            {h2h_matches.map((h2h, idx) => (
              <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(0,0,0,0.2)", padding: "0.75rem 1rem", borderRadius: "8px" }}>
                <span>
                  <small style={{ opacity: 0.6, marginRight: "0.6rem" }}>{h2h.date}</small>
                  <b>{h2h.home_team}</b> vs <b>{h2h.away_team}</b>
                </span>
                <span style={{ fontWeight: 700, background: "rgba(255,255,255,0.1)", padding: "0.2rem 0.6rem", borderRadius: "6px" }}>
                  {h2h.score}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ASISTENTE OPENAI INTERACTIVO */}
      <section className="assistant-section" id="asistente">
        <div>
          <p className="section-kicker">OPENAI · ANÁLISIS EN TIEMPO REAL</p>
          <h2>Consulta al Asistente IA del Partido</h2>
          <p>Pregunta sobre tácticas, ausencias de jugadores o valor en mercados de este encuentro.</p>
        </div>
        <div className="assistant-form">
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ejemplo: ¿Cómo influyen las bajas defensivas en el mercado de Más de 2.5 goles?"
          />
          <button className="ask-button" onClick={ask} disabled={loadingAnswer}>
            <MessageSquare size={16} /> {loadingAnswer ? "Analizando..." : "Consultar análisis"}
          </button>
          {answer && (
            <div className="assistant-reply">
              <strong>Respuesta de la IA</strong>
              <p>{answer}</p>
            </div>
          )}
        </div>
      </section>

      <ResponsibleNote />
    </AppShell>
  );
}

