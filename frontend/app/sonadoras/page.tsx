"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, CircleAlert, Sparkles } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import { getRecommendations, Recommendation } from "../lib/api";

export default function DreamsPage() {
  const [items, setItems] = useState<Recommendation[]>([]);
  useEffect(() => { getRecommendations("dreams").then((data) => setItems(data.recommendations)).catch(() => setItems([])); }, []);
  return <AppShell><PageHeader eyebrow="OPORTUNIDADES DE MAYOR VARIANZA · MÁXIMO 2" title="Soñadoras" action={<Link className="outline-link" href="/recomendaciones">Ver simples <ArrowUpRight size={15} /></Link>} />
    <div className="dream-banner"><Sparkles size={22} /><div><strong>Cuota alta, evidencia visible</strong><p>Estas selecciones requieren más tolerancia al riesgo. No representan una garantía ni una recomendación automática.</p></div></div>
    {items.length ? <section className="recommendation-grid">{items.map((item) => <Link className="recommendation-card dream-card" href={`/partidos/${item.match_id}`} key={item.id}><small>{item.match_label}</small><h2>{item.selection}</h2><p>{item.market}</p><div className="recommendation-stats"><span><b>{Math.round(item.probability * 100)}%</b> prob.</span><span><b>{item.best_odds?.toFixed(2) ?? "--"}</b> cuota</span><span><b>+{Math.round((item.expected_value ?? 0) * 100)}%</b> EV</span></div><footer>{item.rationale}<ArrowUpRight size={15} /></footer></Link>)}</section> : <div className="empty-state"><CircleAlert size={17} /> No hay Soñadoras elegibles con los datos y cuotas disponibles.</div>}
    <ResponsibleNote /></AppShell>;
}
