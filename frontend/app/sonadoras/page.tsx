"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, CircleAlert, Sparkles } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import DreamRecommendationCard from "../components/DreamRecommendationCard";
import { getRecommendations, type Recommendation } from "../lib/api";

export default function DreamsPage() {
  const [items, setItems] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getRecommendations("dreams", 12)
      .then((data) => { setItems(data.recommendations); setError(""); })
      .catch(() => { setItems([]); setError("No se pudieron cargar las Soñadoras del día."); })
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppShell>
      <PageHeader eyebrow="OPORTUNIDADES DEL DÍA · PROBABILIDAD MÍNIMA 30%" title="Soñadoras" action={<Link className="outline-link" href="/recomendaciones">Ver simples <ArrowUpRight size={15} /></Link>} />
      <div className="dream-banner"><Sparkles size={22} /><div><strong>Cuota alta, evidencia visible</strong><p>Combinadas con cuota justa de referencia desde 3.00. Son selecciones de alta varianza y no representan una garantía.</p></div></div>
      {loading ? (
        <div className="empty-state">Calculando las Soñadoras de la agenda...</div>
      ) : error ? (
        <div className="empty-state"><CircleAlert size={17} /> {error}</div>
      ) : items.length ? (
        <section className="recommendation-grid">{items.map((item) => <DreamRecommendationCard item={item} key={item.id} />)}</section>
      ) : (
        <div className="empty-state"><CircleAlert size={17} /> No hay Soñadoras disponibles con la agenda actual.</div>
      )}
      <ResponsibleNote />
    </AppShell>
  );
}
