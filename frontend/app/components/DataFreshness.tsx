import { BadgeCheck, CircleAlert, Clock3 } from "lucide-react";

import type { DataAvailability, DataProvenance } from "../lib/api";

const statusLabel = (status: string) => {
  switch (status) {
    case "available":
      return "Disponible";
    case "partial":
      return "Cobertura parcial";
    case "not_applicable":
      return "No aplica";
    case "not_requested":
      return "No solicitado";
    case "unavailable":
      return "No disponible";
    default:
      return status.replaceAll("_", " ");
  }
};

const providerLabel = (provider: string) => provider.replaceAll("-", " ");

function formattedDate(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" });
}

export default function DataFreshness({
  availability,
  provenance,
}: {
  availability: DataAvailability;
  provenance?: DataProvenance | DataProvenance[] | null;
}) {
  const available = availability.status === "available";
  const partial = availability.status === "partial";
  const sources = provenance ? (Array.isArray(provenance) ? provenance : [provenance]) : [];
  const StatusIcon = available ? BadgeCheck : partial ? Clock3 : CircleAlert;

  return (
    <div className={`data-freshness data-freshness-${availability.status}`}>
      <span className="data-freshness-status">
        <StatusIcon size={14} aria-hidden="true" />
        {statusLabel(availability.status)}
      </span>
      {availability.sample_size != null && (
        <span>Muestra: {availability.sample_size} registros</span>
      )}
      {sources.map((source, index) => {
        const fetchedAt = formattedDate(source.fetched_at);
        return (
          <span key={`${source.provider}-${source.endpoint ?? "source"}-${index}`}>
            Fuente: {providerLabel(source.provider)}
            {source.endpoint ? ` · ${source.endpoint}` : ""}
            {source.verified ? " · verificada" : " · sin verificar"}
            {fetchedAt ? ` · ${fetchedAt}` : ""}
          </span>
        );
      })}
      {availability.reason && <small>{availability.reason}</small>}
    </div>
  );
}
