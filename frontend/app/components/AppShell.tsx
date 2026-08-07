"use client";

import Link from "next/link";
import { Activity, BarChart3, CalendarDays, CircleAlert, Gauge, Sparkles } from "lucide-react";

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <Link className="brand-mark" href="/"><span>BA</span><div><strong>BET</strong><small>ANALIZADOR</small></div></Link>
        <nav className="nav-list" aria-label="Navegacion principal">
          <Link className="nav-item" href="/"><BarChart3 size={18} /> Panorama</Link>
          <Link className="nav-item" href="/partidos"><CalendarDays size={18} /> Partidos</Link>
          <Link className="nav-item" href="/recomendaciones"><Sparkles size={18} /> Recomendaciones</Link>
          <Link className="nav-item" href="/sonadoras"><Sparkles size={18} /> Sonadoras</Link>
          <Link className="nav-item" href="/rendimiento"><Gauge size={18} /> Rendimiento</Link>
        </nav>
        <div className="sidebar-footer"><CircleAlert size={16} /><span>Analisis informativo.<br />Sin garantias.</span></div>
      </aside>
      <section className="content">{children}</section>
    </main>
  );
}

export function PageHeader({ eyebrow, title, action }: { eyebrow: string; title: string; action?: React.ReactNode }) {
  return <header className="page-header"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>{action}</header>;
}

export function ResponsibleNote() {
  return <footer className="responsible-banner"><Activity size={20} /><div><strong>Uso responsable</strong><span>Las estimaciones se basan en datos y no garantizan resultados.</span></div></footer>;
}
