import {
  BarChart3,
  BrainCircuit,
  CircleDollarSign,
  Database,
  Target,
  Trophy,
  Users,
} from "lucide-react";

import type {
  AIConsensusSummary,
  EvidenceCoverageItem,
  FixtureStatisticsSnapshot,
  Match,
  MatchStatisticsSummary,
  PlayerContext,
  PlayerStatisticsSnapshot,
  ProviderPredictionEvidence,
  StandingSnapshot,
  StandingsContext,
  TeamStatisticsSnapshot,
  VerifiedOddsEvidence,
} from "../lib/api";
import DataFreshness from "./DataFreshness";

type EvidenceSection = EvidenceCoverageItem["section"];

const coverageLabels: Record<string, string> = {
  team_statistics: "Estadísticas de equipos",
  standings: "Clasificación",
  h2h: "Enfrentamientos directos",
  recent_fixtures: "Partidos recientes",
  players: "Estadísticas de jugadores",
  injuries: "Lesiones y sanciones",
  lineups: "Alineaciones",
  provider_prediction: "Predicción del proveedor",
  verified_odds: "Cuotas verificadas",
};

const averageKeys: Array<{ label: string; keys: string[]; suffix?: string }> = [
  { label: "Goles a favor", keys: ["goals_for"] },
  { label: "Goles en contra", keys: ["goals_against"] },
  { label: "Corners", keys: ["corners", "corner_kicks"] },
  { label: "Tiros", keys: ["total_shots", "shots"] },
  { label: "Tiros a puerta", keys: ["shots_on_target"] },
  { label: "Faltas", keys: ["fouls"] },
  { label: "Amarillas", keys: ["yellow_cards"] },
  { label: "Rojas", keys: ["red_cards"] },
];

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function displayNumber(value?: number | null, digits = 2) {
  if (!finiteNumber(value)) return "N/D";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

function teamAverage(team: TeamStatisticsSnapshot, keys: string[]) {
  const explicit = keys[0] === "goals_for"
    ? team.goals_for_avg
    : keys[0] === "goals_against"
      ? team.goals_against_avg
      : undefined;
  if (finiteNumber(explicit)) return explicit;

  for (const key of keys) {
    const value = team.averages?.[key];
    if (finiteNumber(value)) return value;
  }
  return null;
}

function coverageFor(coverage: EvidenceCoverageItem[], section: EvidenceSection) {
  return coverage.find((item) => item.section === section);
}

function EvidenceStatus({
  coverage,
  section,
  hasData,
}: {
  coverage: EvidenceCoverageItem[];
  section: EvidenceSection;
  hasData: boolean;
}) {
  const item = coverageFor(coverage, section);
  if (item) return <DataFreshness availability={item} provenance={item.provenance} />;

  return (
    <DataFreshness
      availability={{
        status: hasData ? "partial" : "unavailable",
        reason: hasData
          ? "La respuesta contiene datos, pero no incluye metadatos de cobertura o procedencia."
          : "Este bloque no llegó en la respuesta actual.",
      }}
    />
  );
}

function TeamAverageCard({ team }: { team: TeamStatisticsSnapshot }) {
  return (
    <article className="team-average-card">
      <header>
        <div>
          <small>PROMEDIOS DEL EQUIPO</small>
          <h3>{team.team_name}</h3>
        </div>
        {team.form && <span className="form-chip">{team.form}</span>}
      </header>
      <div className="team-average-grid">
        {averageKeys.map((metric) => (
          <span key={metric.label}>
            <b>{displayNumber(teamAverage(team, metric.keys))}</b>
            <small>{metric.label}</small>
          </span>
        ))}
      </div>
      <footer>
        <span>{team.fixtures_played ?? "N/D"} partidos de temporada</span>
        <span>{team.clean_sheets ?? "N/D"} porterías a cero</span>
        <span>{team.failed_to_score ?? "N/D"} sin marcar</span>
      </footer>
    </article>
  );
}

function fixtureMetric(statistics: Record<string, number | null>, keys: string[]) {
  for (const key of keys) {
    const value = statistics[key];
    if (finiteNumber(value)) return value;
  }
  return null;
}

function RecentFixtureList({
  fixtures,
  teamName,
}: {
  fixtures: FixtureStatisticsSnapshot[];
  teamName: string;
}) {
  const visible = fixtures.slice(0, 5);
  return (
    <div className="recent-fixture-sample">
      <h3>{teamName}</h3>
      {visible.length ? visible.map((fixture) => {
        const date = fixture.date ? new Date(fixture.date) : null;
        const dateLabel = date && !Number.isNaN(date.getTime())
          ? date.toLocaleDateString("es-PE", { day: "2-digit", month: "short" })
          : "Fecha N/D";
        const metrics = [
          { label: "Corners", keys: ["corners", "corner_kicks"] },
          { label: "Tiros", keys: ["total_shots", "shots"] },
          { label: "A puerta", keys: ["shots_on_target"] },
          { label: "Amarillas", keys: ["yellow_cards"] },
        ];
        return (
          <article key={fixture.fixture_id}>
            <header>
              <span><small>{dateLabel} · {fixture.competition ?? "Competición N/D"}</small><strong>{fixture.home_team} vs {fixture.away_team}</strong></span>
              <b>{fixture.home_goals ?? "–"} - {fixture.away_goals ?? "–"}</b>
            </header>
            <div>
              {metrics.map((metric) => (
                <span key={metric.label}>
                  <b>{displayNumber(fixtureMetric(fixture.home_statistics, metric.keys))}–{displayNumber(fixtureMetric(fixture.away_statistics, metric.keys))}</b>
                  <small>{metric.label} L–V</small>
                </span>
              ))}
            </div>
          </article>
        );
      }) : <div className="empty-state compact-empty-state">Sin muestra de partidos recientes con estadísticas.</div>}
    </div>
  );
}

function StandingCard({ standing, side }: { standing: StandingSnapshot; side: string }) {
  return (
    <article className="standing-card">
      <header>
        <div>
          <small>{side}</small>
          <h3>{standing.team_name}</h3>
        </div>
        <strong>{standing.rank ? `#${standing.rank}` : "N/D"}</strong>
      </header>
      <div>
        <span><b>{displayNumber(standing.points, 0)}</b><small>Puntos</small></span>
        <span><b>{displayNumber(standing.played, 0)}</b><small>Jugados</small></span>
        <span><b>{displayNumber(standing.wins, 0)}</b><small>Ganados</small></span>
        <span><b>{displayNumber(standing.draws, 0)}</b><small>Empates</small></span>
        <span><b>{displayNumber(standing.losses, 0)}</b><small>Perdidos</small></span>
        <span><b>{displayNumber(standing.goals_for, 0)}</b><small>Goles a favor</small></span>
        <span><b>{displayNumber(standing.goals_against, 0)}</b><small>Goles en contra</small></span>
        <span><b>{displayNumber(standing.goal_difference, 0)}</b><small>Dif. goles</small></span>
      </div>
      {(standing.form || standing.description) && (
        <footer>{standing.form && <b>{standing.form}</b>}{standing.description && <span>{standing.description}</span>}</footer>
      )}
    </article>
  );
}

function playerScore(player: PlayerStatisticsSnapshot) {
  return (player.rating ?? 0) * 100
    + (player.goals ?? 0) * 15
    + (player.assists ?? 0) * 10
    + (player.minutes ?? 0) / 1000;
}

function PlayerTable({ players, title }: { players: PlayerStatisticsSnapshot[]; title: string }) {
  const visible = [...players].sort((left, right) => playerScore(right) - playerScore(left)).slice(0, 6);
  return (
    <div className="player-context-table">
      <h3>{title}</h3>
      {visible.length ? (
        <div>
          {visible.map((player, index) => (
            <article key={`${player.player_id ?? player.player_name}-${index}`}>
              <span>
                <strong>{player.player_name}</strong>
                <small>{player.position ?? "Posición N/D"} · {player.appearances ?? "N/D"} apariciones</small>
              </span>
              <span><b>{player.goals ?? "N/D"}</b><small>Goles</small></span>
              <span><b>{player.assists ?? "N/D"}</b><small>Asist.</small></span>
              <span><b>{player.shots_on_target ?? "N/D"}</b><small>Tiros a puerta</small></span>
              <span><b>{player.yellow_cards ?? "N/D"}/{player.red_cards ?? "N/D"}</b><small>A/R</small></span>
              <span><b>{player.rating?.toFixed(2) ?? "N/D"}</b><small>Rating</small></span>
            </article>
          ))}
        </div>
      ) : <div className="empty-state compact-empty-state">Sin estadísticas de jugadores para este equipo.</div>}
    </div>
  );
}

function PlayerLeaderboards({ context }: { context: PlayerContext }) {
  const boards = [
    { label: "Goleadores", players: context.top_scorers, value: (player: PlayerStatisticsSnapshot) => player.goals },
    { label: "Asistencias", players: context.top_assists, value: (player: PlayerStatisticsSnapshot) => player.assists },
    { label: "Amarillas", players: context.top_yellow_cards, value: (player: PlayerStatisticsSnapshot) => player.yellow_cards },
    { label: "Rojas", players: context.top_red_cards, value: (player: PlayerStatisticsSnapshot) => player.red_cards },
  ].filter((board) => board.players.length > 0);

  if (!boards.length) return null;
  return (
    <div className="player-leaderboards">
      {boards.map((board) => (
        <article key={board.label}>
          <h3>Top {board.label}</h3>
          <ol>
            {board.players.slice(0, 5).map((player, index) => (
              <li key={`${board.label}-${player.player_id ?? player.player_name}-${index}`}>
                <b>{index + 1}</b>
                <span><strong>{player.player_name}</strong><small>{player.team_name ?? "Equipo N/D"}</small></span>
                <em>{board.value(player) ?? "N/D"}</em>
              </li>
            ))}
          </ol>
        </article>
      ))}
    </div>
  );
}

function PredictionCard({ prediction, match }: { prediction: ProviderPredictionEvidence; match: Match }) {
  const percentages = [
    { label: match.home_team, value: prediction.percent_home },
    { label: "Empate", value: prediction.percent_draw },
    { label: match.away_team, value: prediction.percent_away },
  ];
  return (
    <article className="provider-prediction-card">
      <header><Target size={18} /><div><small>SEÑAL EXTERNA</small><h3>Predicción de API-Football</h3></div></header>
      <p>Es evidencia adicional del proveedor, no una recomendación ni sustituye el consenso de las IAs.</p>
      <div className="prediction-bars">
        {percentages.map(({ label, value }) => (
          <span key={label}>
            <small>{label}</small>
            <i><em style={{ width: `${finiteNumber(value) ? Math.round(value * 100) : 0}%` }} /></i>
            <b>{finiteNumber(value) ? `${Math.round(value * 100)}%` : "N/D"}</b>
          </span>
        ))}
      </div>
      <footer>
        {prediction.winner_name && <span>Favorito: <b>{prediction.winner_name}</b></span>}
        {prediction.under_over && <span>Goles: <b>{prediction.under_over}</b></span>}
        {prediction.advice && <span>{prediction.advice}</span>}
      </footer>
    </article>
  );
}

function OddsEvidence({ odds }: { odds: VerifiedOddsEvidence[] }) {
  return (
    <div className="verified-odds-list">
      {odds.map((quote, index) => (
        <article key={`${quote.market_key}-${quote.selection}-${quote.bookmaker}-${index}`}>
          <span><small>{quote.market_key}</small><strong>{quote.selection}</strong></span>
          <span><b>{quote.odds.toFixed(2)}</b><small>{quote.bookmaker}{quote.live ? " · EN VIVO" : " · PREPARTIDO"}</small></span>
          <DataFreshness availability={{ status: "available" }} provenance={quote.provenance} />
        </article>
      ))}
    </div>
  );
}

function ConsensusCard({ consensus }: { consensus: AIConsensusSummary }) {
  return (
    <article className={`consensus-evidence-card consensus-${consensus.status}`}>
      <BrainCircuit size={20} />
      <div>
        <small>CONSENSO MULTI-IA</small>
        <h3>{consensus.completed} de {consensus.requested} IAs respondieron</h3>
        <p>
          Estado: {consensus.status.replaceAll("_", " ")} · soporte mínimo requerido: {consensus.required_support}.
          {consensus.providers.length ? ` Proveedores: ${consensus.providers.join(", ")}.` : ""}
        </p>
        {consensus.reason && <span>{consensus.reason}</span>}
      </div>
    </article>
  );
}

export default function StatisticalContext({
  match,
  coverage = [],
  statistics,
  standings,
  players,
  prediction,
  verifiedOdds = [],
  consensus,
}: {
  match: Match;
  coverage?: EvidenceCoverageItem[];
  statistics?: MatchStatisticsSummary | null;
  standings?: StandingsContext | null;
  players?: PlayerContext | null;
  prediction?: ProviderPredictionEvidence | null;
  verifiedOdds?: VerifiedOddsEvidence[];
  consensus?: AIConsensusSummary | null;
}) {
  const hasTeamStatistics = Boolean(statistics?.home || statistics?.away);
  const hasStandings = Boolean(standings?.home || standings?.away);
  const hasPlayers = Boolean(players && (
    players.home.length
    || players.away.length
    || players.top_scorers.length
    || players.top_assists.length
    || players.top_yellow_cards.length
    || players.top_red_cards.length
  ));

  return (
    <div className="statistical-context">
      <section className="statistical-section">
        <div className="statistical-section-heading">
          <div><BarChart3 size={19} /><span><small>BASE ESTADÍSTICA</small><h2>Promedios comparados</h2></span></div>
          <EvidenceStatus coverage={coverage} section="team_statistics" hasData={hasTeamStatistics} />
        </div>
        {hasTeamStatistics ? (
          <div className="team-average-cards">
            {statistics?.home && <TeamAverageCard team={statistics.home} />}
            {statistics?.away && <TeamAverageCard team={statistics.away} />}
          </div>
        ) : <div className="empty-state">No llegaron promedios ampliados para este partido. La ausencia no se interpreta como cero.</div>}
      </section>

      <section className="statistical-section">
        <div className="statistical-section-heading">
          <div><Database size={19} /><span><small>MUESTRA OBSERVADA</small><h2>Partidos recientes procesados</h2></span></div>
          <EvidenceStatus
            coverage={coverage}
            section="recent_fixtures"
            hasData={Boolean(statistics?.home_recent_fixtures?.length || statistics?.away_recent_fixtures?.length)}
          />
        </div>
        <div className="recent-fixture-grid">
          <RecentFixtureList fixtures={statistics?.home_recent_fixtures ?? []} teamName={match.home_team} />
          <RecentFixtureList fixtures={statistics?.away_recent_fixtures ?? []} teamName={match.away_team} />
        </div>
      </section>

      <section className="statistical-section">
        <div className="statistical-section-heading">
          <div><Trophy size={19} /><span><small>CONTEXTO DE LIGA</small><h2>Clasificación</h2></span></div>
          <EvidenceStatus coverage={coverage} section="standings" hasData={hasStandings} />
        </div>
        {hasStandings ? (
          <div className="standing-cards">
            {standings?.home && <StandingCard standing={standings.home} side="LOCAL" />}
            {standings?.away && <StandingCard standing={standings.away} side="VISITANTE" />}
          </div>
        ) : <div className="empty-state">La clasificación no está cubierta para la liga o temporada de este encuentro.</div>}
      </section>

      <section className="statistical-section">
        <div className="statistical-section-heading">
          <div><Users size={19} /><span><small>RENDIMIENTO INDIVIDUAL</small><h2>Jugadores con mayor evidencia</h2></span></div>
          <EvidenceStatus coverage={coverage} section="players" hasData={hasPlayers} />
        </div>
        {hasPlayers ? (
          <>
            <div className="player-context-grid">
              <PlayerTable players={players?.home ?? []} title={match.home_team} />
              <PlayerTable players={players?.away ?? []} title={match.away_team} />
            </div>
            {players && <PlayerLeaderboards context={players} />}
          </>
        ) : <div className="empty-state">No hay estadísticas individuales verificadas en la respuesta actual.</div>}
      </section>

      <div className="evidence-signal-grid">
        <section className="statistical-section">
          <div className="statistical-section-heading">
            <div><Target size={19} /><span><small>CONTRASTE</small><h2>Predicción del proveedor</h2></span></div>
          </div>
          {prediction ? <PredictionCard prediction={prediction} match={match} /> : (
            <div className="empty-state compact-empty-state">Sin predicción del proveedor para este partido.</div>
          )}
          <EvidenceStatus coverage={coverage} section="provider_prediction" hasData={Boolean(prediction)} />
        </section>

        <section className="statistical-section">
          <div className="statistical-section-heading">
            <div><CircleDollarSign size={19} /><span><small>MERCADO OBSERVADO</small><h2>Cuotas verificadas</h2></span></div>
          </div>
          {verifiedOdds.length ? <OddsEvidence odds={verifiedOdds} /> : (
            <div className="empty-state compact-empty-state">No hay cuotas verificadas; no se calcula valor esperado con una cuota inventada.</div>
          )}
          <EvidenceStatus coverage={coverage} section="verified_odds" hasData={verifiedOdds.length > 0} />
        </section>
      </div>

      {consensus && <ConsensusCard consensus={consensus} />}

      <section className="statistical-section coverage-section">
        <div className="statistical-section-heading">
          <div><Database size={19} /><span><small>TRAZABILIDAD</small><h2>Cobertura y procedencia</h2></span></div>
        </div>
        {coverage.length ? (
          <div className="coverage-list">
            {coverage.map((item, index) => (
              <article key={`${item.section}-${index}`}>
                <strong>{coverageLabels[item.section] ?? item.section.replaceAll("_", " ")}</strong>
                <DataFreshness availability={item} provenance={item.provenance} />
              </article>
            ))}
          </div>
        ) : <div className="empty-state compact-empty-state">La respuesta actual no incluye el mapa de cobertura. Los bloques ausentes se mantienen como no disponibles.</div>}
      </section>
    </div>
  );
}
