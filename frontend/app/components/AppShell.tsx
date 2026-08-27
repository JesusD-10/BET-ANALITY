"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  CalendarDays,
  CircleAlert,
  Gauge,
  Heart,
  PanelLeftClose,
  PanelLeftOpen,
  Sparkles,
  Users,
} from "lucide-react";

const navigationItems = [
  { href: "/", label: "Panorama", icon: BarChart3 },
  { href: "/partidos", label: "Partidos", icon: CalendarDays },
  { href: "/equipos", label: "Equipos", icon: Users },
  { href: "/favoritos", label: "Favoritos", icon: Heart },
  { href: "/recomendaciones", label: "Recomendaciones", icon: Sparkles },
  { href: "/sonadoras", label: "Soñadoras", icon: Sparkles },
  { href: "/rendimiento", label: "Rendimiento", icon: Gauge },
];

function isCurrentSection(pathname: string, href: string) {
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

export default function AppShell({ children, contentId }: { children: React.ReactNode; contentId?: string }) {
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    if (window.matchMedia("(max-width: 900px)").matches) setSidebarOpen(false);
  }, []);

  function closeOnSmallScreen() {
    if (window.matchMedia("(max-width: 900px)").matches) setSidebarOpen(false);
  }

  return (
    <main className={`app-shell ${sidebarOpen ? "sidebar-open" : "sidebar-collapsed"}`}>
      <aside className="sidebar" id="main-sidebar" aria-label="Menú de secciones">
        <div className="sidebar-heading">
          <Link className="brand-mark" href="/" onClick={closeOnSmallScreen}><span>BA</span><div><strong>BET</strong><small>ANALIZADOR</small></div></Link>
          <button
            className="sidebar-toggle"
            type="button"
            aria-expanded={sidebarOpen}
            aria-controls="sidebar-navigation"
            aria-label="Ocultar menú de secciones"
            onClick={() => setSidebarOpen(false)}
          >
            <PanelLeftClose size={19} />
          </button>
        </div>
        <nav className="nav-list" id="sidebar-navigation" aria-label="Navegación principal">
          {navigationItems.map(({ href, label, icon: Icon }) => (
            <Link
              className={`nav-item ${isCurrentSection(pathname, href) ? "active" : ""}`}
              href={href}
              key={href}
              aria-current={isCurrentSection(pathname, href) ? "page" : undefined}
              onClick={closeOnSmallScreen}
            >
              <Icon size={18} /> {label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-footer"><CircleAlert size={16} /><span>Analisis informativo.<br />Sin garantias.</span></div>
      </aside>
      {sidebarOpen && <button className="sidebar-backdrop" type="button" aria-label="Cerrar menú" onClick={() => setSidebarOpen(false)} />}
      {!sidebarOpen && (
        <button
          className="sidebar-toggle sidebar-reopen"
          type="button"
          aria-expanded="false"
          aria-controls="sidebar-navigation"
          aria-label="Mostrar menú de secciones"
          onClick={() => setSidebarOpen(true)}
        >
          <PanelLeftOpen size={20} />
        </button>
      )}
      <section className="content" id={contentId}>{children}</section>
    </main>
  );
}

export function PageHeader({ eyebrow, title, action }: { eyebrow: string; title: string; action?: React.ReactNode }) {
  return <header className="page-header"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>{action}</header>;
}

export function ResponsibleNote() {
  return <footer className="responsible-banner"><Activity size={20} /><div><strong>Uso responsable</strong><span>Las estimaciones se basan en datos y no garantizan resultados.</span></div></footer>;
}
