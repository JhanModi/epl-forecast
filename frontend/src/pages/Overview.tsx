import { Link } from "react-router-dom";

import type { FixturePrediction, SimulatedTeam } from "../api/client";
import { useBacktest, useMeta, useSimulation, useUpcoming } from "../api/hooks";
import { ProbabilityBar, ProbabilityLegend } from "../components/charts/ProbabilityBar";
import { Card, CardHeader, ErrorState, Loading, PageHeader, StatTile } from "../components/ui";
import { fixed, kickoffDay, kickoffTime, longDate, pct, seasonName } from "../lib/format";

const competition = (synthetic: boolean | undefined) => (synthetic ? "demo league" : "Premier League");

export function Overview() {
  const meta = useMeta();
  const season = meta.data?.current_season;
  const upcoming = useUpcoming(10);
  const simulation = useSimulation(season);
  const backtest = useBacktest();

  const favourite = simulation.data?.teams.reduce<SimulatedTeam | undefined>(
    (best, t) => (!best || t.p_title > best.p_title ? t : best),
    undefined,
  );
  const holdout = backtest.data ? backtest.data.seasons[String(backtest.data.holdout_season)] : undefined;
  const production = holdout?.models["dixon_coles"];
  const baseline = holdout?.models["prior"];

  return (
    <>
      <PageHeader
        title={season ? `${seasonName(season)} ${competition(meta.data?.dataset.synthetic)} forecast` : "Forecast"}
        subtitle={
          <>
            Match probabilities from a time-weighted Dixon-Coles goals model and 10,000 Monte Carlo simulations of the
            rest of the season.
            {meta.data?.last_result && <> Results through {longDate(meta.data.last_result)}.</>}
          </>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Title favourite"
          value={favourite ? favourite.team : "—"}
          detail={favourite ? `${pct(favourite.p_title)} chance of winning the league` : undefined}
        />
        <StatTile
          label="Season progress"
          value={
            meta.data ? `${meta.data.matches_played} / ${meta.data.matches_played + meta.data.matches_scheduled}` : "—"
          }
          detail="matches played"
        />
        <StatTile
          label={`Out-of-sample RPS, ${holdout ? seasonName(backtest.data!.holdout_season) : "holdout"}`}
          value={production ? fixed(production.metrics.rps!.mean, 3) : "—"}
          detail={
            baseline
              ? `vs ${fixed(baseline.metrics.rps!.mean, 3)} for the base-rate baseline (lower is better)`
              : undefined
          }
        />
        <StatTile
          label="Out-of-sample accuracy"
          value={production ? pct(production.metrics.accuracy!.mean, 1) : "—"}
          detail={baseline ? `vs ${pct(baseline.metrics.accuracy!.mean, 1)} always picking the home side` : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card>
          <CardHeader
            title="Next fixtures"
            subtitle="Pre-kickoff probabilities and expected goals"
            actions={<ProbabilityLegend />}
          />
          {upcoming.isPending ? (
            <Loading />
          ) : upcoming.isError ? (
            <ErrorState error={upcoming.error} />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {upcoming.data.predictions.map((p) => (
                <FixtureRow key={p.match_id} p={p} />
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Projected final table"
            subtitle={
              simulation.data
                ? `${simulation.data.n_runs.toLocaleString()} simulations of the ${simulation.data.matches_remaining} remaining fixtures`
                : "Monte Carlo simulation of the remaining fixtures"
            }
            actions={
              <Link to="/simulator" className="text-[12px] font-medium text-accent hover:underline">
                Full simulator →
              </Link>
            }
          />
          {simulation.isPending ? (
            <Loading label="Simulating" />
          ) : simulation.isError ? (
            <ErrorState error={simulation.error} />
          ) : (
            <ProjectedTable teams={simulation.data.teams} />
          )}
        </Card>
      </div>
    </>
  );
}

function FixtureRow({ p }: { p: FixturePrediction }) {
  const top = p.top_scorelines[0];
  return (
    <li className="py-3">
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[13px]">
        <span className="min-w-0 truncate font-medium text-ink">
          {p.home_team} <span className="text-muted">v</span> {p.away_team}
        </span>
        <span className="tabular shrink-0 text-[12px] text-muted">
          {kickoffDay(p.kickoff)} {kickoffTime(p.kickoff)}
        </span>
      </div>
      <ProbabilityBar home={p.p_home} draw={p.p_draw} away={p.p_away} homeTeam={p.home_team} awayTeam={p.away_team} />
      <div className="tabular mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-ink-2">
        <span>
          xG {fixed(p.expected_home_goals, 2)} – {fixed(p.expected_away_goals, 2)}
        </span>
        {top && (
          <span>
            Likeliest score {top.home}–{top.away} ({pct(top.probability)})
          </span>
        )}
        <span>Over 2.5 goals {pct(p.over_2_5)}</span>
      </div>
    </li>
  );
}

function Meter({ value, color }: { value: number; color: string }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="tabular w-10 text-right">{pct(value)}</span>
      <span aria-hidden className="hidden h-1.5 w-10 overflow-hidden rounded-full bg-[var(--seq-zero)] xl:block">
        <span className="block h-full rounded-full" style={{ width: `${value * 100}%`, background: color }} />
      </span>
    </div>
  );
}

function ProjectedTable({ teams }: { teams: SimulatedTeam[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-[12px] text-muted">
            <th scope="col" className="px-2 py-2 font-medium">
              #
            </th>
            <th scope="col" className="px-2 py-2 font-medium">
              Team
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              Pts
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              Projected (90%)
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              Title
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              Top 4
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              Relegated
            </th>
          </tr>
        </thead>
        <tbody>
          {teams.map((t, i) => (
            <tr key={t.team} className="border-b border-line last:border-0">
              <td className="tabular px-2 py-1.5 text-muted">{i + 1}</td>
              <td className="whitespace-nowrap px-2 py-1.5 font-medium text-ink">{t.team}</td>
              <td className="tabular px-2 py-1.5 text-right text-ink">{t.points}</td>
              <td className="tabular whitespace-nowrap px-2 py-1.5 text-right text-ink">
                {fixed(t.expected_points, 0)}{" "}
                <span className="text-muted">
                  ({fixed(t.points_p05, 0)}–{fixed(t.points_p95, 0)})
                </span>
              </td>
              <td className="px-2 py-1.5 text-ink">
                <Meter value={t.p_title} color="var(--series-1)" />
              </td>
              <td className="px-2 py-1.5 text-ink">
                <Meter value={t.p_top_four} color="var(--series-1)" />
              </td>
              <td className="px-2 py-1.5 text-ink">
                <Meter value={t.p_relegation} color="var(--away)" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
