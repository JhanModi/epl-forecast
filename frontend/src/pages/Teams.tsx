import { useQueries } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { get, type EloPoint } from "../api/client";
import { useRatings, useTeams } from "../api/hooks";
import { LineChart } from "../components/charts/LineChart";
import { ScatterPlot } from "../components/charts/ScatterPlot";
import { Card, CardHeader, ChartCard, ChartPanel, ErrorState, Loading, PageHeader, Table } from "../components/ui";
import { fixed, longDate } from "../lib/format";

const SLOTS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const MAX_TEAMS = 4;

export function Teams() {
  const ratings = useRatings();
  const teams = useTeams();
  const rated = ratings.data ?? [];
  const [chosen, setChosen] = useState<string[] | null>(null);
  const selected = chosen ?? rated.slice(0, 3).map((r) => r.team);

  // A team keeps its colour slot while selected, however the selection changes (colour follows the entity).
  const [slots, setSlots] = useState<Record<string, number>>({});
  const slotOf = (team: string) => slots[team] ?? selected.indexOf(team);

  function toggle(team: string) {
    const current = selected;
    if (current.includes(team)) {
      setChosen(current.filter((t) => t !== team));
      return;
    }
    if (current.length >= MAX_TEAMS) return;
    const used = new Set(current.map(slotOf));
    const free = [0, 1, 2, 3].find((s) => !used.has(s)) ?? 0;
    setSlots({ ...Object.fromEntries(current.map((t) => [t, slotOf(t)])), [team]: free });
    setChosen([...current, team]);
  }

  const ids = useMemo(() => new Map((teams.data ?? []).map((t) => [t.name, t.id])), [teams.data]);
  const shortNames = useMemo(() => new Map((teams.data ?? []).map((t) => [t.name, t.short_name])), [teams.data]);
  const histories = useQueries({
    queries: selected.map((team) => ({
      queryKey: ["elo", ids.get(team)],
      queryFn: ({ signal }: { signal: AbortSignal }) => get<EloPoint[]>(`/teams/${ids.get(team)}/elo`, {}, signal),
      enabled: ids.has(team),
      staleTime: 5 * 60 * 1000,
    })),
  });

  const series = selected.flatMap((team, i) => {
    const data = histories[i]?.data;
    if (!data) return [];
    return [
      {
        key: team,
        label: team,
        color: SLOTS[slotOf(team)] ?? SLOTS[0]!,
        points: data.map((p) => ({ x: new Date(p.kickoff).getTime(), y: p.elo })),
      },
    ];
  });

  const best = new Set(rated.slice(0, 5).map((r) => r.team));
  const worst = new Set(rated.slice(-3).map((r) => r.team));

  return (
    <>
      <PageHeader
        title="Team ratings"
        subtitle="Two independent views of strength: Dixon-Coles attack and defence (expected goals against an average side at a neutral ground) and an Elo rating that carries across seasons."
      />
      {ratings.isPending ? (
        <Loading />
      ) : ratings.isError ? (
        <ErrorState error={ratings.error} />
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <ChartCard
            title="Attack vs defence"
            subtitle="Right = scores more, up = concedes less. Top five and bottom three are labelled."
            chart={
              <ScatterPlot
                points={rated
                  .filter((r) => r.attack !== null && r.defence !== null)
                  .map((r) => ({
                    key: r.team,
                    label: r.team,
                    short: shortNames.get(r.team) ?? r.team,
                    x: r.attack!,
                    y: r.defence!,
                    emphasis: best.has(r.team) || worst.has(r.team),
                  }))}
                xLabel="Goals scored per match"
                yLabel="Goals conceded per match"
                format={(v) => fixed(v, 1)}
                invertY
              />
            }
            table={
              <Table
                head={["Team", "Scores", "Concedes", "Net", "Elo"]}
                align={["l", "r", "r", "r", "r"]}
                rows={rated.map((r) => [
                  r.team,
                  r.attack !== null ? fixed(r.attack) : "—",
                  r.defence !== null ? fixed(r.defence) : "—",
                  r.rating !== null ? fixed(r.rating) : "—",
                  fixed(r.elo, 0),
                ])}
              />
            }
          />

          <Card>
            <CardHeader
              title="Elo rating over time"
              subtitle={`Pick up to ${MAX_TEAMS} teams. Ratings regress towards 1500 each summer.`}
            />
            <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Teams to plot">
              {rated.map((r) => {
                const on = selected.includes(r.team);
                return (
                  <button
                    key={r.team}
                    type="button"
                    aria-pressed={on}
                    disabled={!on && selected.length >= MAX_TEAMS}
                    onClick={() => toggle(r.team)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[12px] text-ink-2 enabled:hover:text-ink disabled:opacity-40 aria-pressed:bg-raised aria-pressed:text-ink"
                  >
                    {on && (
                      <span
                        aria-hidden
                        className="h-2 w-2 rounded-full"
                        style={{ background: SLOTS[slotOf(r.team)] }}
                      />
                    )}
                    {r.team}
                  </button>
                );
              })}
            </div>
            {histories.some((h) => h.isPending && h.fetchStatus !== "idle") ? (
              <Loading />
            ) : (
              <ChartPanel
                title="Ratings"
                subtitle="after each match"
                chart={
                  <LineChart
                    series={series}
                    xType="time"
                    height={300}
                    formatX={(x) => new Date(x).toLocaleDateString("en-GB", { month: "short", year: "2-digit" })}
                    formatY={(y) => fixed(y, 0)}
                    yLabel="Elo rating over time"
                    reference={{ y: 1500, label: "average" }}
                  />
                }
                table={
                  <Table
                    head={["Date", "Team", "Elo"]}
                    align={["l", "l", "r"]}
                    rows={series
                      .flatMap((s) => s.points.map((p) => ({ team: s.label, ...p })))
                      .sort((a, b) => b.x - a.x)
                      .map((p) => [longDate(new Date(p.x).toISOString()), p.team, fixed(p.y, 0)])}
                  />
                }
              />
            )}
          </Card>
        </div>
      )}
    </>
  );
}
