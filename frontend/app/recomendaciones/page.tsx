"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Sparkles } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import { getRecommendations, Recommendation } from "../lib/api";

export default function RecommendationsPage() {
  const [items, setItems] = useState<Recommendation[]>([]);
  useEffect(() => { getRecommendations().then((data) => setItems(data.recommendations)).catch(() => setItems([])); }, []);
  return <AppShell><PageHeader eyebrow="MOTOR DE RECOMENDACIONES · SIMPLES" title="Señales con contexto" action={<Link className="outline-link" href="/partidos">Explorar partidos <ArrowUpRight size={15} /></Link>} />
  <div className="page-intro"><Sparkles size={20} /><p>Ordenamos las señales por probabilidad, precio, calidad de datos y evidencia. Una cuota baja no equivale automáticamente a valor.</p></div>
  <section className="recommendation-grid">{items.map((item) => <Link className="recommendation-card" href={`/partidos/${item.match_id}`} key={item.id}><small>{item.match_label}</small><h2>{item.selection}</h2><p>{item.market}</p><div className="recommendation-stats"><span><b>{Math.round(item.probability * 100)}%</b> prob.</span><span><b>{item.fair_odds.toFixed(2)}</b> justa</span><span><b>{item.best_odds?.toFixed(2) ?? "--"}</b> mejor</span></div><footer>{item.rationale}<ArrowUpRight size={15} /></footer></Link>)}</section>
  <ResponsibleNote /></AppShell>;
}
