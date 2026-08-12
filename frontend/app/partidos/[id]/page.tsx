"use client";

import Link from "next/link";
import { use, useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowLeft,
  CircleAlert,
  MessageSquare,
  ShieldCheck,
  UserX,
  Flag,
  Users,
  History,
  Sparkles,
  TrendingUp,
} from "lucide-react";

import AppShell, { ResponsibleNote } from "../../components/AppShell";
import CombinationCard from "../../components/CombinationCard";
import MatchHero from "../../components/MatchHero";
import {
  ApiError,
  ApiTimeoutError,
  apiFetch,
  apiUrl,
  getAnalysis,
  isAbortError,
  type Analysis,
} from "../../lib/api";

type DetailTab = "summary" | "combinations" | "dream" | "injuries" | "lineups" | "h2h" | "assistant";
type H2HTab = "meetings" | "home" | "away";

const detailTabs: Array<{ id: DetailTab; label: string }> = [
  { id: "summary", label: "Resumen / Apuestas" },
  { id: "combinations", label: "Combinadas" },
  { id: "dream", label: "Soñadora" },
  { id: "injuries", label: "Bajas" },
  { id: "lineups", label: "Alineaciones" },
  { id: "h2h", label: "H2H" },
  { id: "assistant", label: "Asistente" },
];

export default function MatchDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loadingAnswer, setLoadingAnswer] = useState(false);
  const [error, setError] = useState("");
  const [loadingAnalysis, setLoadingAnalysis] = useState(true);
  const [retryVersion, setRetryVersion] = useState(0);
  const [activeTab, setActiveTab] = useState<DetailTab>("summary");
  const [activeH2HTab, setActiveH2HTab] = useState<H2HTab>("meetings");
  const assistantControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    assistantControllerRef.current?.abort();
    assistantControllerRef.current = null;
    setAnalysis(null);
    setError("");
    setQuestion("");
    setAnswer("");
    setLoadingAnswer(false);
    setLoadingAnalysis(true);
    setActiveTab("summary");
    setActiveH2HTab("meetings");

    getAnalysis(id, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setAnalysis(result);
        setError("");
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted || isAbortError(requestError)) return;

        if (requestError instanceof ApiError && requestError.status === 404) {
          setError("Este partido ya no está disponible en el catálogo actual.");
        } else if (requestError instanceof ApiTimeoutError) {
          setError("El análisis tardó más de lo esperado. Puedes intentarlo nuevamente.");
        } else if (requestError instanceof ApiError && requestError.status >= 500) {
          setError("El servicio de análisis no está disponible temporalmente.");
        } else if (requestError instanceof ApiError) {
          setError(requestError.detail);
        } else {
          setError("No pudimos conectar con el servicio de análisis.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingAnalysis(false);
      });

    return () => {
      controller.abort();
      assistantControllerRef.current?.abort();
    };
  }, [id, retryVersion]);

  async function ask() {
    if (!question.trim()) return;
    assistantControllerRef.current?.abort();
    const controller = new AbortController();
    assistantControllerRef.current = controller;
    setLoadingAnswer(true);
    setAnswer("");
    try {
      const response = await apiFetch(`${apiUrl}/assistant/question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, match_id: id }),
        signal: controller.signal,
      });
      if (!response.ok) throw new ApiError(response.status, "No se pudo consultar al asistente.");
      const data = await response.json();
      setAnswer(data.summary);
    } catch (requestError: unknown) {
      if (controller.signal.aborted || isAbortError(requestError)) return;
      if (requestError instanceof ApiTimeoutError) {
        setAnswer("El asistente tardó demasiado en responder. Intenta nuevamente.");
      } else if (requestError instanceof ApiError && requestError.status >= 500) {
        setAnswer("El asistente no está disponible temporalmente.");
      } else {
        setAnswer("Ocurrió un inconveniente al consultar con el asistente IA.");
      }
    } finally {
      if (assistantControllerRef.current === controller) {
        assistantControllerRef.current = null;
        setLoadingAnswer(false);
      }
    }
  }

  function selectAdjacentTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % detailTabs.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + detailTabs.length) % detailTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = detailTabs.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    setActiveTab(detailTabs[nextIndex].id);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>("[role='tab']")
      [nextIndex]?.focus();
  }

  if (error)
    return (
      <AppShell>
        <Link className="back-link" href="/partidos">
          <ArrowLeft size={16} /> Volver a partidos
        </Link>
        <div className="empty-state">
          <CircleAlert size={18} /> <span>{error}</span>
          <button className="ask-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Reintentar</button>
        </div>
      </AppShell>
    );

  if (loadingAnalysis || !analysis)
    return (
      <AppShell>
        <div className="empty-state">Cargando análisis avanzado y datos del partido...</div>
      </AppShell>
    );

  const {
    match,
    referee_info,
    injuries,
    lineups,
    h2h_matches,
    home_recent_matches = [],
    away_recent_matches = [],
    markets,
    combinations = [],
    dream_picks = [],
    notes,
  } = analysis;
  const selectedHistory = activeH2HTab === "meetings"
    ? h2h_matches
    : activeH2HTab === "home"
      ? home_recent_matches
      : away_recent_matches;
  const historyEmptyMessage = activeH2HTab === "meetings"
    ? "Sin registro de enfrentamientos directos recientes."
    : activeH2HTab === "home"
      ? `Todavía no hay últimos partidos disponibles de ${match.home_team}.`
      : `Todavía no hay últimos partidos disponibles de ${match.away_team}.`;
  const h2hTabs: Array<{ id: H2HTab; label: string }> = [
    { id: "meetings", label: "Enfrentamientos" },
    { id: "home", label: `Últimos 5 ${match.home_team}` },
    { id: "away", label: `Últimos 5 ${match.away_team}` },
  ];

  return (
    <AppShell>
      <Link className="back-link" href="/partidos">
        <ArrowLeft size={16} /> Volver a partidos
      </Link>

      <MatchHero match={match} modelVersion={analysis.model_version} />

      <div className="detail-tabs" role="tablist" aria-label="Secciones del análisis del partido">
        {detailTabs.map((tab, index) => (
          <button
            className={activeTab === tab.id ? "detail-tab active" : "detail-tab"}
            id={`detail-tab-${tab.id}`}
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`detail-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            onClick={() => setActiveTab(tab.id)}
            onKeyDown={(event) => selectAdjacentTab(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "summary" && (
      <section
        className="detail-tab-panel"
        id="detail-panel-summary"
        role="tabpanel"
        aria-labelledby="detail-tab-summary"
        tabIndex={0}
      >
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
      <div className="detail-grid">
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
          <h2>Evidencia y datos disponibles</h2>
          <p>
            El análisis combina la información disponible del proveedor con el motor probabilístico y muestra sus limitaciones.
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
      </div>
      </section>
      )}

      {activeTab === "combinations" && (
      <section
        className="detail-tab-panel"
        id="detail-panel-combinations"
        role="tabpanel"
        aria-labelledby="detail-tab-combinations"
        tabIndex={0}
      >
        <div className="section-heading compact-heading">
          <div>
            <p className="section-kicker">BET BUILDER · PROBABILIDAD CONJUNTA AJUSTADA</p>
            <h2>Combinadas de alta cobertura</h2>
          </div>
        </div>
        <p className="opportunity-intro">Ideas como un gol mínimo más tres tarjetas, calibradas como un único evento y con el riesgo de correlación visible.</p>
        {combinations.length ? <div className="combination-grid">
          {combinations.map((combination) => <CombinationCard item={combination} key={combination.id} />)}
        </div> : <div className="empty-state">Todavía no hay combinadas calculadas para este partido.</div>}
      </section>
      )}

      {activeTab === "dream" && (
      <section
        className="detail-tab-panel match-dream-section"
        id="detail-panel-dream"
        role="tabpanel"
        aria-labelledby="detail-tab-dream"
        tabIndex={0}
      >
        <div className="section-heading">
          <div>
            <p className="section-kicker">SOÑADORAS · SELECCIÓN O COMBINADA</p>
            <h2>Jugadas de mayor cuota para este partido</h2>
          </div>
          <Sparkles size={22} />
        </div>
        <p className="opportunity-intro">La cuota 3.00 corresponde a la jugada completa: puede ser una selección individual o una combinada de 2–3 condiciones. Son ideas de alta varianza, no apuestas seguras.</p>
        {dream_picks.length ? <div className="combination-grid dream-combination-grid">
          {dream_picks.map((dream) => <CombinationCard item={dream} dream key={dream.id} />)}
        </div> : <div className="empty-state">Todavía no hay Soñadoras calculadas para este partido.</div>}
      </section>
      )}

      {/* SECCIÓN DE JUGADORES LESIONADOS O SANCIONADOS */}
      {activeTab === "injuries" && (
      <section className="detail-tab-panel detail-data-panel" id="detail-panel-injuries" role="tabpanel" aria-labelledby="detail-tab-injuries" tabIndex={0}>
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
      )}

      {/* SECCIÓN DE ALINEACIONES */}
      {activeTab === "lineups" && (
      <section className="detail-tab-panel detail-data-panel" id="detail-panel-lineups" role="tabpanel" aria-labelledby="detail-tab-lineups" tabIndex={0}>
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
      )}

      {/* HISTORIAL DIRECTO H2H */}
      {activeTab === "h2h" && (
      <section className="detail-tab-panel detail-data-panel" id="detail-panel-h2h" role="tabpanel" aria-labelledby="detail-tab-h2h" tabIndex={0}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1rem", color: "#a855f7" }}>
          <History size={22} />
          <h2 style={{ margin: 0, fontSize: "1.4rem" }}>Historial y forma reciente</h2>
        </div>

        <div className="h2h-subtabs" role="tablist" aria-label="Vistas del historial del partido">
          {h2hTabs.map((tab, index) => (
            <button
              className={activeH2HTab === tab.id ? "h2h-subtab active" : "h2h-subtab"}
              id={`h2h-tab-${tab.id}`}
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeH2HTab === tab.id}
              aria-controls={`h2h-panel-${tab.id}`}
              tabIndex={activeH2HTab === tab.id ? 0 : -1}
              onClick={() => setActiveH2HTab(tab.id)}
              onKeyDown={(event) => {
                let nextIndex: number | null = null;
                if (event.key === "ArrowRight") nextIndex = (index + 1) % h2hTabs.length;
                if (event.key === "ArrowLeft") nextIndex = (index - 1 + h2hTabs.length) % h2hTabs.length;
                if (event.key === "Home") nextIndex = 0;
                if (event.key === "End") nextIndex = h2hTabs.length - 1;
                if (nextIndex === null) return;
                event.preventDefault();
                setActiveH2HTab(h2hTabs[nextIndex].id);
                event.currentTarget.parentElement
                  ?.querySelectorAll<HTMLButtonElement>("[role='tab']")
                  [nextIndex]?.focus();
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div
          className="history-tab-panel"
          id={`h2h-panel-${activeH2HTab}`}
          role="tabpanel"
          aria-labelledby={`h2h-tab-${activeH2HTab}`}
          tabIndex={0}
        >
        {selectedHistory.length === 0 ? (
          <div className="empty-state">{historyEmptyMessage}</div>
        ) : (
          <div className="history-match-list">
            {selectedHistory.slice(0, activeH2HTab === "meetings" ? 10 : 5).map((historyMatch, idx) => (
              <div className="history-match-row" key={`${historyMatch.date}-${historyMatch.home_team}-${historyMatch.away_team}-${idx}`}>
                <span>
                  <small>{historyMatch.date}</small>
                  <b>{historyMatch.home_team}</b> vs <b>{historyMatch.away_team}</b>
                </span>
                <strong>{historyMatch.score}</strong>
              </div>
            ))}
          </div>
        )}
        </div>
      </section>
      )}

      {/* ASISTENTE OPENAI INTERACTIVO */}
      {activeTab === "assistant" && (
      <section className="detail-tab-panel assistant-section" id="detail-panel-assistant" role="tabpanel" aria-labelledby="detail-tab-assistant" tabIndex={0}>
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
      )}

      <ResponsibleNote />
    </AppShell>
  );
}

