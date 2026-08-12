/* eslint-disable @next/next/no-img-element */

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { Recommendation } from "../lib/api";

export default function DreamRecommendationCard({ item }: { item: Recommendation }) {
  const displayedOdds = item.best_odds ?? item.fair_odds;
  const legs = item.legs ?? [];
  const dataQuality = item.data_quality ?? 0.7;

  return (
    <Link className="recommendation-card dream-card daily-dream-card" href={`/partidos/${item.match_id}`}>
      <div className="daily-dream-match">
        <div className="daily-dream-logos">
          {item.home_logo && <img src={item.home_logo} alt="" />}
          {item.away_logo && <img src={item.away_logo} alt="" />}
        </div>
        <small>{item.match_label}</small>
      </div>
      <span className="dream-pill">ALTA VARIANZA</span>
      <h2>{item.selection}</h2>
      <p>{item.market}{legs.length ? ` · ${legs.length} condiciones` : ""}</p>
      <div className="recommendation-stats">
        <span><b>{Math.round(item.probability * 100)}%</b>prob. modelada</span>
        <span><b>{displayedOdds.toFixed(2)}</b>cuota ref.</span>
        <span><b>{Math.round(dataQuality * 100)}%</b>calidad</span>
      </div>
      <div className="daily-dream-legs">
        {legs.map((leg) => <span key={`${item.id}-${leg.market_key}`}>+ {leg.selection}</span>)}
      </div>
      <footer>{item.rationale}<ArrowUpRight size={15} /></footer>
      {item.risk_note && <small className="dream-risk">{item.risk_note}</small>}
    </Link>
  );
}
