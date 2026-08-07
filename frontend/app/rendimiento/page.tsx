import Link from "next/link";
import { ArrowUpRight, BarChart3, Gauge, ShieldCheck } from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";

export default function PerformancePage() {
  return <AppShell><PageHeader eyebrow="MODELOS · CALIBRACIÓN Y COBERTURA" title="Rendimiento" action={<Link className="outline-link" href="/">Volver al panorama <ArrowUpRight size={15} /></Link>} />
    <section className="metric-grid"><article><Gauge size={18} /><small>Modelo activo</small><strong>baseline-poisson</strong><span>v0.1 · demostrativo</span></article><article><BarChart3 size={18} /><small>Calidad media</small><strong>85.0%</strong><span>sobre la agenda actual</span></article><article><ShieldCheck size={18} /><small>Estado</small><strong>Monitoreado</strong><span>Sin resultados liquidados aún</span></article></section>
  <section className="performance-panel"><p className="section-kicker">LECTURA DEL SISTEMA</p><h2>La precisión necesita resultados observados</h2><p>El panel está listo para registrar predicciones, liquidar partidos y calcular Brier Score, Log Loss, calibración y ROI hipotético cuando exista un historial real. No se presenta una métrica inventada como rendimiento.</p><div className="progress-line"><span>Datos históricos conectados</span><b>Mock</b></div><div className="progress-line"><span>Backtesting walk-forward</span><b>Pendiente</b></div><div className="progress-line"><span>Modelos versionados</span><b>v0.1</b></div></section><ResponsibleNote /></AppShell>;
}
