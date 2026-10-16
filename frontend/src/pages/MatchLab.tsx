import { useMemo, useState } from "react";

import { useMatchup, useRatings } from "../api/hooks";
import { Heatmap, SequentialLegend } from "../components/charts/Heatmap";
import { ProbabilityBar, ProbabilityLegend } from "../components/charts/ProbabilityBar";
import { TipRow, TipTitle } from "../components/charts/tooltip";
import {
  Card,
  CardHeader,
  ChartCard,
  ErrorState,
  Loading,
  PageHeader,
  Select,
  StatTile,
  Table,
} from "../components/ui";
import { fixed, pct } from "../lib/format";

export function MatchLab() {
  const ratings = useRatings();
  const teams = useMemo(() => (ratings.data ?? []).map((r) => r.team).sort(), [ratings.data]);
  const [home, setHome] = useState<string>("");
  const [away, setAway] = useState<string>("");
  const h = home || teams[0] || "";
  const a = away || teams.find((t) => t !== h) || "";
  const matchup = useMatchup(h, a);
  const m = matchup.data;

  const strength = (team: string) => ratings.data?.find((r) => r.team === team);

  return (
    <>
      <PageHeader
        title="Match lab"
        subtitle="Forecast any pairing at a home ground today. The scoreline grid is the model's full joint distribution; every other number on this page is derived from it."
      >
        {teams.length > 0 && (
          <>
            <Select label="Home" value={h} onChange={setHome} options={teams.map((t) => ({ value: t, label: t }))} />
            <button
              type="button"
              onClick={() => {
                setHome(a);
                setAway(h);
              }}
              className="h-9 rounded-lg border border-line bg-surface px-3 text-[13px] text-ink hover:bg-raised"
              aria-label="Swap home and away"
            >
              ⇄ Swap
            </button>
            <Select
              label="Away"
              value={a}
              onChange={setAway}
              options={teams.filter((t) => t !== h).map((t) => ({ value: t, label: t }))}
            />
          </>
        )}
      </PageHeader>

      {ratings.isError && <ErrorState error={ratings.error} />}
      {matchup.isError && <ErrorState error={matchup.error} />}
      {!m ? (
        matchup.isFetching || ratings.isPending ? (
          <Loading />
        ) : null
      ) : (
        <div className={matchup.isPlaceholderData ? "opacity-60" : undefined}>
          <Card className="mb-6">
            <CardHeader
              title={`${m.home_team} v ${m.away_team}`}
              subtitle="Result probabilities"
              actions={<ProbabilityLegend />}
            />
            <ProbabilityBar
              home={m.p_home}
              draw={m.p_draw}
              away={m.p_away}
              height={30}
              homeTeam={m.home_team}
              awayTeam={m.away_team}
            />
            <div className="tabular mt-2 flex justify-between text-[13px] text-ink-2">
              <span>
                {m.home_team} {pct(m.p_home, 1)}
              </span>
              <span>Draw {pct(m.p_draw, 1)}</span>
              <span>
                {m.away_team} {pct(m.p_away, 1)}
              </span>
            </div>
          </Card>

          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Expected goals"
              value={`${fixed(m.expected_home_goals, 2)} – ${fixed(m.expected_away_goals, 2)}`}
              detail="model scoring rates"
            />
            <StatTile label="Over 2.5 goals" value={pct(m.over_2_5)} detail={`Under: ${pct(1 - m.over_2_5)}`} />
            <StatTile label="Both teams score" value={pct(m.both_teams_score)} />
            <StatTile
              label="Clean sheet"
              value={`${pct(m.home_clean_sheet)} / ${pct(m.away_clean_sheet)}`}
              detail={`${m.home_team} / ${m.away_team}`}
            />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
            <ChartCard
              title="Scoreline probabilities"
              subtitle={`Rows: ${m.home_team} goals · columns: ${m.away_team} goals`}
              actions={<SequentialLegend max={Math.max(...m.score_matrix.flat())} format={(v) => pct(v)} label="P" />}
              chart={
                <Heatmap
                  label="Scoreline probability grid"
                  rows={m.score_matrix.map((_, i) => `${m.home_team.split(" ")[0]} ${i}`)}
                  columns={m.score_matrix[0]!.map((_, j) => String(j))}
                  values={m.score_matrix}
                  format={(v) => (v * 100).toFixed(v >= 0.1 ? 0 : 1)}
                  minLabel={0.01}
                  rowHeader={110}
                  tooltip={(i, j) => (
                    <>
                      <TipTitle>
                        {m.home_team} {i}–{j} {m.away_team}
                      </TipTitle>
                      <TipRow label="Probability" value={pct(m.score_matrix[i]![j]!, 1)} />
                    </>
                  )}
                />
              }
              table={
                <Table
                  head={["Home \\ Away", ...m.score_matrix[0]!.map((_, j) => String(j))]}
                  align={["l", ...m.score_matrix[0]!.map(() => "r" as const)]}
                  rows={m.score_matrix.map((row, i) => [String(i), ...row.map((v) => pct(v, 1))])}
                />
              }
            />

            <Card>
              <CardHeader title="Most likely scorelines" />
              <Table
                head={["Score", "Probability"]}
                align={["l", "r"]}
                rows={m.top_scorelines.map((s) => [`${s.home}–${s.away}`, pct(s.probability, 1)])}
              />
              <CardHeader title="Team strength" subtitle="Expected goals vs an average side at a neutral ground" />
              <Table
                head={["Team", "Scores", "Concedes", "Elo"]}
                align={["l", "r", "r", "r"]}
                rows={[m.home_team, m.away_team].map((t) => {
                  const s = strength(t);
                  return [
                    t,
                    s?.attack ? fixed(s.attack) : "—",
                    s?.defence ? fixed(s.defence) : "—",
                    s ? fixed(s.elo, 0) : "—",
                  ];
                })}
              />
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
