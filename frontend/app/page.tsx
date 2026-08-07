"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Activity, ArrowUpRight, BarChart3, CalendarDays, Check, ChevronRight, CircleAlert, Database, Search, ShieldCheck, Sparkles, X } from "lucide-react";

type Match = { id: string; competition: string; kickoff_at: string; home_team: string; away_team: string; data_quality: number; odds_available: boolean; status: string };
type Market = { market_key: string; label: string; selection: string; probability: number; fair_odds: number; best_odds: number | null; bookmaker: string | null; expected_value: number | null; confidence: string; data_quality: number; factors_for: string[]; risks: string[] };
type Analysis = { match: Match; model_version: string; markets: Market[]; notes: string[] };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "https://bet-anality.onrender.com/api/v1";
const percent = (value: number) => `${Math.round(value * 100)}%`;
const formatTime = (value: string) => new Intl.DateTimeFormat("es-PE", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const formatDate = (value: Date) => new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "2-digit", month: "short", year: "numeric" }).format(value);

export default function Home() {
  const router = useRouter();
  const [matches, setMatches] = useState<Match[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantText, setAssistantText] = useState("");
  const [assistantReply, setAssistantReply] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${apiUrl}/matches/search?q=${encodeURIComponent(query)}`);
        if (!response.ok) throw new Error("No se pudo cargar la agenda");
        const data = await response.json();
        setMatches(data.matches);
        if (!analysis && data.matches[0]) await loadAnalysis(data.matches[0].id);
        setError("");
      } catch (error) {
        console.error("ERROR API:", error);
        setError(`Error API: ${error}`);
      }
      finally { setLoading(false); }
    }, 250);
    return () => window.clearTimeout(timer);
    // The selected analysis intentionally stays stable while a search is typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function loadAnalysis(id: string) {
    const response = await fetch(`${apiUrl}/matches/${id}/analysis`);
    if (!response.ok) throw new Error("No se pudo cargar el análisis");
    setAnalysis(await response.json());
  }

  async function askAssistant() {
    if (!assistantText.trim()) return;
    const response = await fetch(`${apiUrl}/assistant/question`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: assistantText, match_id: analysis?.match.id }) });
    const data = await response.json();
    setAssistantReply(data.summary);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar"><Link className="brand-mark" href="/"><span>BA</span><div><strong>BET</strong><small>ANALIZADOR</small></div></Link><nav className="nav-list" aria-label="Navegacion principal"><Link className="nav-item active" href="/"><BarChart3 size={18} /> Panorama <span>01</span></Link><Link className="nav-item" href="/partidos"><CalendarDays size={18} /> Partidos</Link><Link className="nav-item" href="/recomendaciones"><Sparkles size={18} /> Recomendaciones</Link><Link className="nav-item" href="/sonadoras"><ArrowUpRight size={18} /> Sonadoras</Link><Link className="nav-item" href="/rendimiento"><Activity size={18} /> Rendimiento</Link></nav><div className="sidebar-footer"><CircleAlert size={16} /><span>Analisis informativo.<br />Sin garantias.</span></div></aside>
      <section className="content" id="inicio"><header className="topbar"><div><p className="eyebrow">{formatDate(new Date())} <span className="live-dot" /> MVP EN VIVO</p><h1>Una lectura más clara<br /><em>del fútbol de hoy.</em></h1></div><div className="model-chip"><Database size={15} /> baseline-poisson <span>v0.1</span></div></header>
        <section className="hero-tools" id="partidos"><div className="search-wrap"><Search size={19} /><input aria-label="Buscar partido" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Busca un equipo, liga o partido..." /><kbd>⌘ K</kbd></div><button className="date-button" onClick={() => router.push("/partidos")}><CalendarDays size={17} /> Ver agenda <ChevronRight size={15} /></button></section>
        {error && <div className="api-alert"><CircleAlert size={17} /> {error}</div>}
        <section className="section-block"><div className="section-heading"><div><p className="section-kicker">SELECCIÓN DEL DÍA · {matches.length} PARTIDOS</p><h2>Partidos destacados</h2></div><button className="text-button" onClick={() => router.push("/partidos")}>Ver agenda <ArrowUpRight size={15} /></button></div><div className="match-grid">{loading ? <div className="empty-state">Consultando calendario...</div> : matches.length === 0 ? <div className="empty-state">No hay partidos disponibles hoy. Verifica tu token y API.</div> : matches.map((match) => <button className={`match-card ${analysis?.match.id === match.id ? "selected" : ""}`} key={match.id} onClick={() => router.push(`/partidos/${match.id}`)}><div className="match-meta"><span>{match.competition}</span><b>{formatTime(match.kickoff_at)}</b></div><div className="teams"><div><div className="team-badge home-badge">{match.home_team.slice(0, 1)}</div><strong>{match.home_team}</strong></div><span className="versus">VS</span><div><div className="team-badge away-badge">{match.away_team.slice(0, 1)}</div><strong>{match.away_team}</strong></div></div><div className="match-bottom"><span className={`quality quality-${match.data_quality > .85 ? "high" : "mid"}`}><i /> Calidad {percent(match.data_quality)}</span><span className="odds-status">{match.odds_available ? "Cuotas disponibles" : "Modo estadistico"}</span></div></button>)}</div></section>
        {analysis && <section className="analysis-layout" id="recomendaciones"><div className="section-block signals-block"><div className="section-heading"><div><p className="section-kicker">ANÁLISIS · {analysis.model_version}</p><h2>Mercados observados</h2></div><button className="text-button" onClick={() => setAssistantOpen(true)}><Sparkles size={15} /> Preguntar al asistente</button></div><div className="signal-list">{analysis.markets.map((market, index) => <article className="signal-row" key={market.market_key}><span className="signal-index">0{index + 1}</span><div className="signal-main"><strong>{market.selection}</strong><small>{market.label} · {market.confidence}</small></div><div className="signal-stat"><b>{percent(market.probability)}</b><small>probabilidad</small></div><div className="signal-stat"><b>{market.fair_odds.toFixed(2)}</b><small>cuota justa</small></div><div className="signal-status">{market.best_odds ? <><strong>+{percent(market.expected_value ?? 0)}</strong><small>EV · {market.bookmaker}</small></> : <><strong className="muted-value">--</strong><small>sin cuotas</small></>}</div><ChevronRight size={17} /></article>)}</div></div><aside className="insight-panel" id="sonadoras"><div className="panel-top"><span className="spark-icon"><Sparkles size={17} /></span><span>LECTURA DEL MODELO</span></div><h3>La probabilidad no<br />es rentabilidad.</h3><p>Comparamos precio, estabilidad y calidad de datos. Cuando no hay cuota, mostramos cuota justa sin inventar valor esperado.</p><div className="panel-rule" /><div className="panel-foot"><span>CALIDAD DEL PARTIDO</span><strong>{percent(analysis.match.data_quality)}</strong></div></aside></section>}
        <section className="status-strip" id="estado"><div><span className="status-icon"><Check size={15} /></span><div><strong>Motor de análisis operativo</strong><small>Datos mock versionados · modo degradado disponible</small></div></div><div><span className="status-icon"><ShieldCheck size={15} /></span><div><strong>OpenAI protegido</strong><small>Asistente backend · fallback local activo</small></div></div><div><span className="status-icon"><Activity size={15} /></span><div><strong>Calidad media</strong><small>Se calcula por partido y mercado</small></div></div></section><footer className="responsible-banner"><ShieldCheck size={20} /><div><strong>Uso responsable</strong><span>Las predicciones son estimaciones basadas en datos. No garantizan resultados ni sustituyen tu criterio.</span></div><a href="#metodologia">Metodología <ArrowUpRight size={15} /></a></footer>
      </section>
      {assistantOpen && <div className="modal-backdrop" onClick={() => setAssistantOpen(false)}><section className="assistant-modal" onClick={(event) => event.stopPropagation()}><button className="close-button" onClick={() => setAssistantOpen(false)} aria-label="Cerrar"><X size={18} /></button><p className="section-kicker">ASISTENTE DE ANÁLISIS</p><h2>Pregunta sobre este partido</h2><p className="modal-copy">La respuesta se limita a los datos recuperados y distingue evidencia de riesgo.</p><textarea value={assistantText} onChange={(event) => setAssistantText(event.target.value)} placeholder="¿Qué respalda esta señal?" /><button className="ask-button" onClick={askAssistant}><Sparkles size={16} /> Consultar</button>{assistantReply && <div className="assistant-reply"><strong>Lectura del asistente</strong><p>{assistantReply}</p></div>}</section></div>}
    </main>
  );
}
