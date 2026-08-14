/* eslint-disable @next/next/no-img-element */
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Activity, ArrowUpRight, CalendarDays, Check, ChevronRight, CircleAlert, Database, Search, ShieldCheck, Sparkles, X } from "lucide-react";
import AppShell from "./components/AppShell";
import DreamRecommendationCard from "./components/DreamRecommendationCard";
import {
  ApiError,
  ApiTimeoutError,
  apiFetch,
  apiUrl,
  getAnalysis,
  getMatches,
  getRecommendations,
  isAbortError,
  type Analysis,
  type Match,
  type Recommendation,
} from "./lib/api";
import { groupMatchesByLeague, selectHomeMatches } from "./lib/popularTeams";
const percent = (value: number) => `${Math.round(value * 100)}%`;
const formatTime = (value: string) => new Intl.DateTimeFormat("es-PE", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const formatDate = (value: Date) => new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "2-digit", month: "short", year: "numeric" }).format(value);
const formatMatchDate = (value: string) => new Intl.DateTimeFormat("es-PE", { weekday: "short", day: "2-digit", month: "short" }).format(new Date(value));

export default function Home() {
  const router = useRouter();
  const [matches, setMatches] = useState<Match[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [catalogNotice, setCatalogNotice] = useState("");
  const [catalogMatchCount, setCatalogMatchCount] = useState(0);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantText, setAssistantText] = useState("");
  const [assistantReply, setAssistantReply] = useState("");
  const [dreams, setDreams] = useState<Recommendation[]>([]);
  const [dreamsLoading, setDreamsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setCatalogNotice("");
    setAnalysis(null);
    const timer = window.setTimeout(async () => {
      try {
        const data = await getMatches(query, controller.signal);
        if (controller.signal.aborted) return;
        const visibleMatches = selectHomeMatches(data.matches, Boolean(query.trim()));
        setMatches(visibleMatches);
        setCatalogMatchCount(data.matches.length);
        setCatalogNotice(data.notice ?? "");
        setError("");
        setLoading(false);

        if (visibleMatches[0]) {
          try {
            const nextAnalysis = await getAnalysis(visibleMatches[0].id, controller.signal);
            if (!controller.signal.aborted) setAnalysis(nextAnalysis);
          } catch (analysisError: unknown) {
            if (!controller.signal.aborted && !isAbortError(analysisError)) setAnalysis(null);
          }
        }
      } catch (requestError: unknown) {
        if (controller.signal.aborted || isAbortError(requestError)) return;
        setMatches([]);
        setCatalogMatchCount(0);
        setAnalysis(null);
        if (requestError instanceof ApiTimeoutError) {
          setError("La agenda tardó demasiado en responder. Intenta nuevamente.");
        } else if (requestError instanceof ApiError && requestError.status >= 500) {
          setError("El servicio de partidos no está disponible temporalmente.");
        } else if (requestError instanceof ApiError) {
          setError(requestError.detail);
        } else {
          setError("No pudimos conectar con el catálogo de partidos.");
        }
      }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query]);

  useEffect(() => {
    getRecommendations("dreams", 20)
      .then((data) => setDreams(data.recommendations))
      .catch(() => setDreams([]))
      .finally(() => setDreamsLoading(false));
  }, []);

  async function askAssistant() {
    if (!assistantText.trim()) return;
    const response = await apiFetch(`${apiUrl}/assistant/question`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: assistantText, match_id: analysis?.match.id }) });
    const data = await response.json();
    setAssistantReply(data.summary);
  }

  const groupedMatches = groupMatchesByLeague(matches);
  const visibleMatchIds = new Set(matches.map((match) => match.id));
  const visibleDreams = dreams.filter((item) => visibleMatchIds.has(item.match_id)).slice(0, 4);
  const isSearching = Boolean(query.trim());

  return (
    <AppShell contentId="inicio">
      <header className="topbar"><div><p className="eyebrow">{formatDate(new Date())} <span className="live-dot" /> MVP EN VIVO</p><h1>Una lectura más clara<br /><em>del fútbol de hoy.</em></h1></div><div className="model-chip"><Database size={15} /> baseline-poisson <span>v0.1</span></div></header>
        <section className="hero-tools" id="partidos"><div className="search-wrap"><Search size={19} /><input aria-label="Buscar partido" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Busca un equipo, liga o partido..." /><kbd>⌘ K</kbd></div><button className="date-button" onClick={() => router.push("/partidos")}><CalendarDays size={17} /> Ver agenda <ChevronRight size={15} /></button></section>
        {catalogNotice && <p className="data-notice">{catalogNotice}</p>}
        {error && <div className="api-alert"><CircleAlert size={17} /> {error}</div>}
        <section className="section-block">
          <div className="section-heading">
            <div>
              <p className="section-kicker">{isSearching ? "RESULTADOS DE BÚSQUEDA" : "CLUBES DESTACADOS"} · {matches.length} PARTIDOS</p>
              <h2>Partidos separados por liga</h2>
            </div>
            <button className="text-button" onClick={() => router.push("/partidos")}>Ver agenda completa <ArrowUpRight size={15} /></button>
          </div>
          {loading ? (
            <div className="empty-state">Consultando calendario...</div>
          ) : matches.length === 0 ? (
            <div className="empty-state home-empty-state">
              <span>{query.trim() ? `No encontramos partidos para “${query.trim()}”.` : catalogMatchCount > 0 ? "Hoy no hay partidos de equipos populares en la agenda." : "No hay partidos reales disponibles para hoy."}</span>
              {!query.trim() && <Link href="/partidos">Revisar agenda completa <ArrowUpRight size={14} /></Link>}
            </div>
          ) : (
            <div className="league-groups">
              {groupedMatches.map((league) => (
              <section key={league.key} className="league-group">
                <div className="league-group-header">
                  <span><span className="league-mark" aria-hidden="true">{league.competition.slice(0, 2).toUpperCase()}</span>{league.competition}</span>
                  <small>{league.matches.length} {league.matches.length === 1 ? "partido" : "partidos"}</small>
                </div>
                <div className="match-grid">
                  {league.matches.map((match) => (
                    <button className={`match-card ${analysis?.match.id === match.id ? "selected" : ""}`} key={match.id} onClick={() => router.push(`/partidos/${match.id}`)}>
                      <div className="match-meta"><span>{formatMatchDate(match.kickoff_at)}</span><b>{formatTime(match.kickoff_at)}</b></div>
                      <div className="teams">
                        <div>
                          <div className="team-badge home-badge">
                            {match.home_logo ? (
                              <img src={match.home_logo} alt={match.home_team} className="team-logo-img" />
                            ) : (
                              match.home_team.slice(0, 1)
                            )}
                          </div>
                          <strong>{match.home_team}</strong>
                        </div>
                        <span className="versus">VS</span>
                        <div>
                          <div className="team-badge away-badge">
                            {match.away_logo ? (
                              <img src={match.away_logo} alt={match.away_team} className="team-logo-img" />
                            ) : (
                              match.away_team.slice(0, 1)
                            )}
                          </div>
                          <strong>{match.away_team}</strong>
                        </div>
                      </div>
                      <div className="match-bottom">
                        <span className={`quality quality-${match.data_quality > .85 ? "high" : "mid"}`}><i /> Calidad {percent(match.data_quality)}</span>
                        <span className="odds-status">{match.odds_available ? "Cuotas disponibles" : "Modo estadistico"}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </section>
              ))}
            </div>
          )}
        </section>
        <section className="home-dreams" id="sonadoras">
          <div className="section-heading">
            <div><p className="section-kicker">SOÑADORAS DE LOS PARTIDOS DEL DÍA</p><h2>Cuotas altas con el riesgo a la vista</h2></div>
            <Link className="text-button" href="/sonadoras">Ver todas <ArrowUpRight size={15} /></Link>
          </div>
          <p className="opportunity-intro">Selecciones o combinadas de alta varianza cuya cuota total de referencia alcanza 3.00, con cada condición visible.</p>
          {dreamsLoading || loading ? <div className="empty-state">Buscando Soñadoras del día...</div> : visibleDreams.length ? <div className="recommendation-grid">{visibleDreams.map((item) => <DreamRecommendationCard item={item} key={item.id} />)}</div> : <div className="empty-state">No hay Soñadoras disponibles para los partidos destacados de la portada.</div>}
        </section>
        {analysis && <section className="analysis-layout" id="recomendaciones"><div className="section-block signals-block"><div className="section-heading"><div><p className="section-kicker">ANÁLISIS · {analysis.model_version}</p><h2>Mercados observados</h2></div><button className="text-button" onClick={() => setAssistantOpen(true)}><Sparkles size={15} /> Preguntar al asistente</button></div><div className="signal-list">{analysis.markets.map((market, index) => <Link className="signal-row" href={`/partidos/${analysis.match.id}?tab=summary&market=${encodeURIComponent(market.market_key)}#mercado-${market.market_key.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`} key={market.market_key} aria-label={`Ver ${market.selection} en ${analysis.match.home_team} contra ${analysis.match.away_team}`}><span className="signal-index">0{index + 1}</span><div className="signal-main"><strong>{market.selection}</strong><small>{market.label} · {market.confidence}</small></div><div className="signal-stat"><b>{percent(market.probability)}</b><small>probabilidad</small></div><div className="signal-stat"><b>{market.fair_odds.toFixed(2)}</b><small>cuota justa</small></div><div className="signal-status">{market.best_odds ? <><strong>+{percent(market.expected_value ?? 0)}</strong><small>EV · {market.bookmaker}</small></> : <><strong className="muted-value">--</strong><small>sin cuotas</small></>}</div><ChevronRight size={17} /></Link>)}</div></div><aside className="insight-panel"><div className="panel-top"><span className="spark-icon"><Sparkles size={17} /></span><span>LECTURA DEL MODELO</span></div><h3>La probabilidad no<br />es rentabilidad.</h3><p>Comparamos precio, estabilidad y calidad de datos. Cuando no hay cuota, mostramos cuota justa sin inventar valor esperado.</p><div className="panel-rule" /><div className="panel-foot"><span>CALIDAD DEL PARTIDO</span><strong>{percent(analysis.match.data_quality)}</strong></div></aside></section>}
        <section className="status-strip" id="estado"><div><span className="status-icon"><Check size={15} /></span><div><strong>Motor de análisis operativo</strong><small>Datos mock versionados · modo degradado disponible</small></div></div><div><span className="status-icon"><ShieldCheck size={15} /></span><div><strong>Motor multi-IA protegido</strong><small>Proveedores externos · fallback local activo</small></div></div><div><span className="status-icon"><Activity size={15} /></span><div><strong>Calidad media</strong><small>Se calcula por partido y mercado</small></div></div></section><footer className="responsible-banner"><ShieldCheck size={20} /><div><strong>Uso responsable</strong><span>Las predicciones son estimaciones basadas en datos. No garantizan resultados ni sustituyen tu criterio.</span></div><a href="#metodologia">Metodología <ArrowUpRight size={15} /></a></footer>
      {assistantOpen && <div className="modal-backdrop" onClick={() => setAssistantOpen(false)}><section className="assistant-modal" onClick={(event) => event.stopPropagation()}><button className="close-button" onClick={() => setAssistantOpen(false)} aria-label="Cerrar"><X size={18} /></button><p className="section-kicker">ASISTENTE DE ANÁLISIS</p><h2>Pregunta sobre este partido</h2><p className="modal-copy">La respuesta se limita a los datos recuperados y distingue evidencia de riesgo.</p><textarea value={assistantText} onChange={(event) => setAssistantText(event.target.value)} placeholder="¿Qué respalda esta señal?" /><button className="ask-button" onClick={askAssistant}><Sparkles size={16} /> Consultar</button>{assistantReply && <div className="assistant-reply"><strong>Lectura del asistente</strong><p>{assistantReply}</p></div>}</section></div>}
    </AppShell>
  );
}
