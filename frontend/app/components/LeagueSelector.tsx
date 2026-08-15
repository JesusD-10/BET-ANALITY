/* eslint-disable @next/next/no-img-element */
"use client";

import Link from "next/link";
import { LEAGUES, sortLeaguesByPriority, type League } from "../lib/leagues";

interface LeagueSelectorProps {
  /** ID de la liga actualmente seleccionada, si existe */
  selectedLeagueId?: string | null;
  /** Abre el detalle de liga en una pestaña nueva */
  openInNewTab?: boolean;
  /** Función callback cuando se selecciona una liga (no es Link directo) */
  onSelectLeague?: (league: League) => void;
}

/**
 * Selector de ligas en fila horizontal (tipo FlashScore).
 * Muestra logos de ligas y banderas de países.
 * Al hacer clic, navega a /partidos?liga=<id>
 */
export default function LeagueSelector({
  selectedLeagueId,
  openInNewTab = false,
  onSelectLeague,
}: LeagueSelectorProps) {
  const orderedLeagues = sortLeaguesByPriority(LEAGUES);

  return (
    <div className="league-selector-wrapper">
      <div className="league-selector" role="region" aria-label="Selecciona una liga">
        {orderedLeagues.map((league) => {
          const isSelected = selectedLeagueId === league.id;
          const href = `/partidos?liga=${encodeURIComponent(league.id)}`;

          return (
            <Link
              key={league.id}
              href={href}
              className={`league-selector-card ${isSelected ? "active" : ""}`}
              onClick={() => onSelectLeague?.(league)}
              target={openInNewTab ? "_blank" : undefined}
              rel={openInNewTab ? "noopener noreferrer" : undefined}
              title={`${league.name} - ${league.country}`}
            >
              <div className="league-logo-container">
                {league.logoUrl ? (
                  <img
                    src={league.logoUrl}
                    alt={league.name}
                    className="league-logo"
                    onError={(e) => {
                      const target = e.target as HTMLImageElement;
                      target.style.display = "none";
                    }}
                  />
                ) : (
                  <span className="league-logo-fallback">
                    {league.name.slice(0, 2).toUpperCase()}
                  </span>
                )}
              </div>

              <div className="league-info">
                <strong className="league-name">{league.name}</strong>
                <div className="league-country-info">
                  {league.countryFlagUrl && (
                    <img
                      src={league.countryFlagUrl}
                      alt={league.country}
                      className="country-flag"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = "none";
                      }}
                    />
                  )}
                  <small className="league-country">{league.country}</small>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
