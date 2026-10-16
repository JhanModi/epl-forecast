import { useMemo, useState } from "react";

import { useFixtures, useMeta, useSimulation } from "../api/hooks";
import { Heatmap, SequentialLegend } from "../components/charts/Heatmap";
import { TipRow, TipTitle } from "../components/charts/tooltip";
import { ChartCard, ErrorState, Loading, PageHeader, Select, StatTile, Table } from "../components/ui";
import { fixed, longDate, pct, seasonName } from "../lib/format";

const LATEST = 0;

export function Simulator() {
  const meta = useMeta();
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [asOf, setAsOf] = useState<number>(LATEST);
  const activeSeason = season ?? meta.data?.current_season;
  const isCurrent = activeSeason === meta.data?.current_season;

  const finished = useFixtures(activeSeason, "finished");
  const simulation = useSimulation(activeSeason, asOf === LATEST ? undefined : asOf);
  const sim = simulation.data;

  const matchweeks = useMemo(() => {
    const played = new Set((finished.data ?? []).map((f) => f.matchweek));
    const last = played.size ? Math.max(...played) : 0;
    const upto = isCurrent ? Math.min(38, last + 1) : 38;
    return Array.from({ length: upto }, (_, i) => i + 1);
  }, [finished.data, isCurrent]);

  const positions = sim ? sim.teams.map((_, i) => String(i + 1)) : [];
  const actual = sim?.actual_final_positions ?? null;
  const calibration = useMemo(() => {
    if (!sim || !actual) return null;
    // Log score of the realised finishing positions, and how often the champion was the favourite.
    const logScore =
      sim.teams.reduce(
        (sum, t, i) => sum - Math.log(Math.max(sim.position_probabilities[i]![actual[t.team]! - 1]!, 1e-4)),
        0,
      ) / sim.teams.length;
    const champion = Object.entries(actual).find(([, pos]) => pos === 1)?.[0];
    const championRow = sim.teams.findIndex((t) => t.team === champion);
    return { logScore, champion, pChampion: championRow >= 0 ? sim.teams[championRow]!.p_title : null };
  }, [sim, actual]);

  return (
    <>
      <PageHeader
        title="Season simulator"
        subtitle="Pick any season and matchweek: the goals model is refitted on only the results known at that point, then the rest of the season is simulated 10,000 times. For finished seasons you can see how the forecast compared with what happened."
      >
        {meta.data && (
          <Select
            label="Season"
            value={activeSeason ?? meta.data.current_season}
            onChange={(s) => {
              setSeason(s);
              setAsOf(s === meta.data!.current_season ? LATEST : 20);
            }}
            options={meta.data.seasons.map((s) => ({ value: s, label: seasonName(s) }))}
          />
        )}
        <Select
          label="Simulate from"
          value={asOf}
          onChange={setAsOf}
          options={[
            ...(isCurrent ? [{ value: LATEST, label: "Latest results" }] : []),
            ...matchweeks.map((m) => ({ value: m, label: `Before matchweek ${m}` })),
          ]}
        />
      </PageHeader>

      {simulation.isPending ? (
        <Loading label="Simulating" />
      ) : simulation.isError ? (
        <ErrorState error={simulation.error} />
      ) : sim ? (
        <div className={simulation.isPlaceholderData ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Matches known"
              value={sim.matches_played}
              detail={sim.cutoff ? `before ${longDate(sim.cutoff)}` : "all results so far"}
            />
            <StatTile
              label="Fixtures simulated"
              value={sim.matches_remaining}
              detail={`${sim.n_runs.toLocaleString()} runs each`}
            />
            <StatTile
              label="Title favourite"
              value={sim.teams.reduce((a, b) => (b.p_title > a.p_title ? b : a)).team}
              detail={pct(Math.max(...sim.teams.map((t) => t.p_title)))}
            />
            {calibration ? (
              <StatTile
                label={`Actual champion: ${calibration.champion}`}
                value={calibration.pChampion !== null ? pct(calibration.pChampion) : "—"}
                detail={`title chance at that point · mean log score ${fixed(calibration.logScore, 2)}`}
              />
            ) : (
              <StatTile
                label="Simulation cache"
                value={sim.cached ? "Hit" : "Miss"}
                detail="results are cached per data and model version"
              />
            )}
          </div>

          <ChartCard
            title="Finishing-position probabilities"
            subtitle={
              actual
                ? "Each row is a team, each column a final position. Outlined cell = where the team actually finished."
                : "Each row is a team, each column a final position."
            }
            actions={<SequentialLegend max={1} format={(v) => pct(v)} label="Probability" />}
            chart={
              <Heatmap
                label="Finishing position probabilities"
                rows={sim.teams.map((t) => t.team)}
                columns={positions}
                values={sim.position_probabilities}
                max={Math.min(1, Math.max(...sim.position_probabilities.flat()))}
                format={(v) => (v >= 0.995 ? "99" : (v * 100).toFixed(0))}
                minLabel={0.05}
                highlight={(i, j) => (actual ? actual[sim.teams[i]!.team] === j + 1 : false)}
                tooltip={(i, j) => (
                  <>
                    <TipTitle>
                      {sim.teams[i]!.team} · finishes {j + 1}
                    </TipTitle>
                    <TipRow label="Probability" value={pct(sim.position_probabilities[i]![j]!, 1)} />
                    {actual && <TipRow label="Actual finish" value={String(actual[sim.teams[i]!.team])} />}
                  </>
                )}
              />
            }
            table={
              <Table
                head={[
                  "Team",
                  "Pts now",
                  "Exp. pts",
                  "90% range",
                  "Title",
                  "Top 4",
                  "Relegated",
                  ...(actual ? ["Actual"] : []),
                ]}
                align={["l", "r", "r", "r", "r", "r", "r", "r"]}
                rows={sim.teams.map((t) => [
                  t.team,
                  t.points,
                  fixed(t.expected_points, 1),
                  `${fixed(t.points_p05, 0)}–${fixed(t.points_p95, 0)}`,
                  pct(t.p_title, 1),
                  pct(t.p_top_four, 1),
                  pct(t.p_relegation, 1),
                  ...(actual ? [actual[t.team] ?? "—"] : []),
                ])}
              />
            }
          />
        </div>
      ) : null}
    </>
  );
}
