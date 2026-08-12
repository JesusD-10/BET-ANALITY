import { Layers3, Sparkles } from "lucide-react";
import type { Combination } from "../lib/api";

export default function CombinationCard({ item, dream = false }: { item: Combination; dream?: boolean }) {
  const displayedOdds = item.best_odds ?? item.fair_odds;

  return (
    <article className={`combination-card ${dream ? "combination-card-dream" : ""}`}>
      <div className="combination-card-top">
        <span>{dream ? <Sparkles size={15} /> : <Layers3 size={15} />}{item.label}</span>
        <strong>{Math.round(item.probability * 100)}%</strong>
      </div>
      <h3>{item.selection}</h3>
      <ol className="combination-legs">
        {item.legs.map((leg, index) => (
          <li key={`${item.id}-${leg.market_key}`}>
            <b>{index + 1}</b>
            <span><small>{leg.label}</small>{leg.selection}</span>
          </li>
        ))}
      </ol>
      <div className="combination-stats">
        <span><b>{displayedOdds.toFixed(2)}</b>{item.best_odds ? "mejor cuota" : "cuota justa ref."}</span>
        <span><b>{item.confidence}</b>confianza</span>
        <span><b>{Math.round(item.data_quality * 100)}%</b>calidad</span>
      </div>
      <p className="combination-note">{item.correlation_note}</p>
      <div className="combination-evidence">
        <span>+ {item.factors_for[0]}</span>
        <span>! {item.risks[0]}</span>
      </div>
    </article>
  );
}
