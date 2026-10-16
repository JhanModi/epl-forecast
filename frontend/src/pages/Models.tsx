import { useMemo, useState } from "react";

import type { LoggedForecast, SeasonReport } from "../api/client";
import { useBacktest, useLive, useMeta, useModels, usePredictionLog } from "../api/hooks";
import { CalibrationChart } from "../components/charts/CalibrationChart";
import { Heatmap } from "../components/charts/Heatmap";
import { IntervalPlot } from "../components/charts/IntervalPlot";
import { LineChart } from "../components/charts/LineChart";
import { ProbabilityBar } from "../components/charts/ProbabilityBar";
import { TipRow, TipTitle } from "../components/charts/tooltip";
import {
  Card,
  CardHeader,
  ChartCard,
  ChartPanel,
  ErrorState,
  Loading,
  PageHeader,
  Segmented,
  Select,
  Table,
} from "../components/ui";
import { fixed, kickoffDay, modelLabel, pct, pValue, seasonName, signed } from "../lib/format";

const OUTCOME_LABELS = ["Home win", "Draw", "Away win"];

export function Models() {
  const backtest = useBacktest();
  const report = backtest.data;
  const seasons = report ? Object.keys(report.seasons) : [];
  const [season, setSeason] = useState<string | undefined>(undefined);
  const active = season ?? (report ? String(report.holdout_season) : undefined);
  const seasonReport = active && report ? report.seasons[active] : undefined;

  const role = (s: string) =>
    report && Number(s) === report.tuning_season
      ? "tuning"
      : report && Number(s) === report.holdout_season
        ? "holdout"
        : "live";

  return (
    <>
      <PageHeader
        title="Model evaluation"
        subtitle="Walk-forward backtest: before every week of fixtures each model is refitted on results up to that week, then forecasts with those results hidden. Hyperparameters were tuned on 2024/25 only, so 2025/26 is an untouched out-of-sample test."
      >
        {report && (
          <Segmented
            label="Evaluation season"
            value={active ?? ""}
            onChange={setSeason}
            options={seasons.map((s) => ({ value: s, label: `${seasonName(Number(s))} · ${role(s)}` }))}
          />
        )}
      </PageHeader>

      {backtest.isPending ? (
        <Loading />
      ) : backtest.isError ? (
        <ErrorState error={backtest.error} />
      ) : seasonReport ? (
        <SeasonView report={seasonReport} role={role(active!)} />
      ) : null}

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <LiveCard />
        <RegistryCard />
        <TrackRecordCard />
      </div>
    </>
  );
}

function SeasonView({ report, role }: { report: SeasonReport; role: string }) {
  const [selected, setSelected] = useState<string>(report.best);
  const model = report.models[selected] ?? report.models[report.best]!;
  const baseline = report.models[report.reference]!;

  const leaderboard = report.ranking.map((name) => {
    const rps = report.models[name]!.metrics.rps!;
    return {
      key: name,
      label: modelLabel(name),
      mean: rps.mean,
      low: rps.low,
      high: rps.high,
      emphasis: name === report.best,
    };
  });

  const weekly = useMemo(() => {
    // Ten matches a week is too few to read; a trailing 5-matchweek mean shows the trend.
    const toPoints = (m: string) => {
      const raw = Object.entries(report.models[m]!.rps_by_matchweek)
        .map(([k, v]) => ({ x: Number(k), y: v }))
        .sort((p, q) => p.x - q.x);
      return raw.map((point, i) => {
        const window = raw.slice(Math.max(0, i - 4), i + 1);
        return { x: point.x, y: window.reduce((sum, w) => sum + w.y, 0) / window.length };
      });
    };
    const keys = [...new Set([selected, report.reference])];
    const colors = ["var(--series-1)", "var(--series-2)"];
    return keys.map((k, i) => ({ key: k, label: modelLabel(k), color: colors[i]!, points: toPoints(k) }));
  }, [report, selected]);

  const confusion = model.confusion_matrix;
  const rowTotals = confusion.map((r) => r.reduce((a, b) => a + b, 0));
  const vsBaseline = model.vs_reference;

  return (
    <>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <ChartCard
          title="Leaderboard: ranked probability score"
          subtitle={
            <>
              {`${report.n_matches} matches${role === "live" ? " so far (season in progress)" : ""}. Actual outcomes: home ${pct(report.outcome_rates.home!)}, draw ${pct(report.outcome_rates.draw!)}, away ${pct(report.outcome_rates.away!)}.`}
              {report.ceiling && (
                <>
                  {" "}
                  The <strong>oracle</strong> forecasts with the true probabilities the synthetic league was generated
                  from: the best score any model could expect.
                </>
              )}
            </>
          }
          chart={
            <IntervalPlot
              rows={leaderboard}
              format={(v) => fixed(v, 3)}
              axisLabel="RPS by model"
              reference={{ value: baseline.metrics.rps!.mean, label: "baseline RPS" }}
            />
          }
          table={
            <Table
              head={["Model", "RPS", "95% CI", "Log loss", "Brier", "Accuracy", "ECE", "vs baseline"]}
              align={["l", "r", "r", "r", "r", "r", "r", "r"]}
              rows={report.ranking.map((name) => {
                const m = report.models[name]!;
                return [
                  modelLabel(name),
                  fixed(m.metrics.rps!.mean, 4),
                  `${fixed(m.metrics.rps!.low, 3)}–${fixed(m.metrics.rps!.high, 3)}`,
                  fixed(m.metrics.log_loss!.mean, 3),
                  fixed(m.metrics.brier!.mean, 3),
                  pct(m.metrics.accuracy!.mean, 1),
                  fixed(m.calibration.ece, 3),
                  name === report.reference ? "—" : pValue(m.vs_reference.p_value),
                ];
              })}
            />
          }
        />

        <Card>
          <CardHeader
            title="All metrics"
            subtitle="Lower is better except accuracy. ECE = expected calibration error."
          />
          <div className="overflow-x-auto">
            <Table
              head={["Model", "RPS", "Log loss", "Brier", "Acc.", "ECE"]}
              align={["l", "r", "r", "r", "r", "r"]}
              rows={report.ranking.map((name) => {
                const m = report.models[name]!;
                return [
                  <button
                    key={name}
                    type="button"
                    onClick={() => setSelected(name)}
                    className={name === selected ? "font-semibold text-accent" : "text-ink hover:text-accent"}
                  >
                    {modelLabel(name)}
                  </button>,
                  fixed(m.metrics.rps!.mean, 4),
                  fixed(m.metrics.log_loss!.mean, 3),
                  fixed(m.metrics.brier!.mean, 3),
                  pct(m.metrics.accuracy!.mean, 1),
                  fixed(m.calibration.ece, 3),
                ];
              })}
            />
          </div>
          <p className="mt-3 text-[12px] text-ink-2">Select a model to inspect it below.</p>
        </Card>
      </div>

      <Card className="mt-6">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold text-ink">{modelLabel(selected)}</h2>
            <p className="mt-0.5 text-[13px] text-ink-2">
              {selected === report.reference
                ? "The reference model: always forecasts the historical base rates."
                : `RPS ${signed(vsBaseline.difference.mean, 4)} vs baseline (95% CI ${signed(vsBaseline.difference.low, 4)} to ${signed(vsBaseline.difference.high, 4)}, paired t-test ${pValue(vsBaseline.p_value)}).`}
            </p>
          </div>
          <Select
            label="Model"
            value={selected}
            onChange={setSelected}
            options={report.ranking.map((n) => ({ value: n, label: modelLabel(n) }))}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 2xl:grid-cols-2">
          <div>
            <ChartPanel
              title="Calibration"
              subtitle={`ECE ${fixed(model.calibration.ece, 3)}`}
              chart={<CalibrationChart curves={model.calibration.curves} />}
              table={
                <Table
                  head={["Outcome", "Mean forecast", "Observed", "Matches"]}
                  align={["l", "r", "r", "r"]}
                  rows={Object.entries(model.calibration.curves).flatMap(([outcome, points]) =>
                    points.map((p) => [outcome, pct(p.forecast, 1), pct(p.observed, 1), p.count]),
                  )}
                />
              }
            />
            <p className="mt-2 text-[11px] text-muted">
              Points on the diagonal are perfectly calibrated. Bins with fewer than 5 matches are hidden.
            </p>
          </div>
          <div className="space-y-6">
            <ChartPanel
              title="RPS by matchweek"
              subtitle="trailing 5-matchweek mean"
              chart={
                <LineChart
                  series={weekly}
                  formatX={(x) => `MW ${x}`}
                  formatY={(y) => fixed(y, 2)}
                  yLabel="Mean RPS by matchweek"
                />
              }
              table={
                <Table
                  head={["Matchweek", ...weekly.map((w) => w.label)]}
                  align={["l", ...weekly.map(() => "r" as const)]}
                  rows={(weekly[0]?.points ?? []).map((p, i) => [
                    p.x,
                    ...weekly.map((w) => (w.points[i] ? fixed(w.points[i]!.y, 3) : "—")),
                  ])}
                />
              }
            />
            <div>
              <ChartPanel
                title="Confusion matrix"
                subtitle="share of each actual outcome"
                chart={
                  <Heatmap
                    label="Confusion matrix"
                    rows={OUTCOME_LABELS.map((l) => `Actual ${l.toLowerCase()}`)}
                    columns={OUTCOME_LABELS.map((l) => `Pred. ${l.toLowerCase()}`)}
                    values={confusion.map((row, i) => row.map((v) => v / Math.max(rowTotals[i]!, 1)))}
                    max={1}
                    rowHeader={130}
                    format={(v) => pct(v)}
                    minLabel={0}
                    tooltip={(i, j) => (
                      <>
                        <TipTitle>
                          Actual {OUTCOME_LABELS[i]!.toLowerCase()} → predicted {OUTCOME_LABELS[j]!.toLowerCase()}
                        </TipTitle>
                        <TipRow label="Matches" value={String(confusion[i]![j])} />
                        <TipRow label="Share of row" value={pct(confusion[i]![j]! / Math.max(rowTotals[i]!, 1), 1)} />
                      </>
                    )}
                  />
                }
                table={
                  <Table
                    head={["Actual vs predicted", ...OUTCOME_LABELS]}
                    align={["l", "r", "r", "r"]}
                    rows={confusion.map((row, i) => [OUTCOME_LABELS[i]!, ...row])}
                  />
                }
              />
              <p className="mt-2 text-[11px] text-muted">
                Draws are rarely the single most likely outcome, so a well-calibrated model seldom "predicts" one: this
                is expected, not a bug.
              </p>
            </div>
          </div>
        </div>
      </Card>
    </>
  );
}

function LiveCard() {
  const live = useLive();
  return (
    <Card>
      <CardHeader
        title="Live monitoring"
        subtitle="Forecasts logged before kickoff by the weekly job, scored once results arrive."
      />
      {live.isPending ? (
        <Loading />
      ) : live.isError ? (
        <ErrorState error={live.error} />
      ) : live.data.n === 0 ? (
        <p className="text-[13px] text-ink-2">
          {live.data.pending
            ? `${live.data.pending} logged forecasts are waiting for their matches to be played.`
            : "No logged forecasts have been scored yet."}
        </p>
      ) : (
        <Table
          head={["Metric", "Mean", "95% CI"]}
          align={["l", "r", "r"]}
          rows={Object.entries(live.data.metrics ?? {}).map(([k, v]) => [
            k.replace("_", " "),
            fixed(v.mean, 3),
            `${fixed(v.low, 3)}–${fixed(v.high, 3)}`,
          ])}
        />
      )}
    </Card>
  );
}

const PAGE_SIZE = 10;
const PAGER_BUTTON =
  "h-8 rounded-lg border border-line bg-surface px-3 text-[13px] text-ink hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40";

function result(row: LoggedForecast): string {
  if (row.home_goals === null || row.away_goals === null) return row.status[0]!.toUpperCase() + row.status.slice(1);
  return `${row.home_goals}–${row.away_goals}`;
}

/** Each match's latest forecast logged before kickoff, and how it scored: proof the forecasts came first. */
function TrackRecordCard() {
  const season = useMeta().data?.current_season;
  const [offset, setOffset] = useState(0);
  const log = usePredictionLog(season, offset, PAGE_SIZE);
  const page = log.data;
  // While the next page loads, the previous one stays on screen (placeholder data). The label and the buttons
  // follow the page that is shown, and navigation pauses, so repeated clicks cannot skip past it.
  const updating = log.isPlaceholderData;
  const shownFrom = page?.offset ?? 0;
  const shownTo = shownFrom + (page?.items.length ?? 0);

  const pager = page && page.total > 0 && (
    <div className="flex items-center gap-2">
      <span className="tabular text-[12px] text-muted" aria-live="polite">
        {shownTo > shownFrom ? `${shownFrom + 1}–${shownTo} of ${page.total}` : `0 of ${page.total}`}
        {updating && " · updating…"}
      </span>
      <button
        type="button"
        className={PAGER_BUTTON}
        aria-label="Previous page"
        disabled={updating || shownFrom === 0}
        onClick={() => setOffset(Math.max(0, shownFrom - PAGE_SIZE))}
      >
        Previous
      </button>
      <button
        type="button"
        className={PAGER_BUTTON}
        aria-label="Next page"
        disabled={updating || shownTo >= page.total}
        onClick={() => setOffset(shownFrom + PAGE_SIZE)}
      >
        Next
      </button>
    </div>
  );

  return (
    <Card className="xl:col-span-2">
      <CardHeader
        title="Forecast track record"
        subtitle={`The latest forecast logged before each kickoff${season ? ` of ${seasonName(season)}` : ""}, newest first, scored once the result is in (lower RPS is better).`}
        actions={pager}
      />
      {log.isPending ? (
        <Loading />
      ) : log.isError ? (
        <div className="space-y-3">
          <ErrorState error={log.error} />
          <button type="button" className={PAGER_BUTTON} onClick={() => void log.refetch()}>
            Try again
          </button>
        </div>
      ) : log.data.total === 0 ? (
        <p className="text-[13px] text-ink-2">
          No forecasts have been logged for {seasonName(log.data.season)} yet. The weekly job logs them before kickoff.
        </p>
      ) : (
        <div className={updating ? "opacity-60 transition-opacity" : undefined}>
          <div className="overflow-x-auto">
            <Table
              head={["Date", "Fixture", "Forecast", "Result", "RPS"]}
              align={["l", "l", "l", "r", "r"]}
              rows={log.data.items.map((row) => [
                kickoffDay(row.kickoff),
                `${row.home_team} – ${row.away_team}`,
                <div key="p" className="min-w-40">
                  <ProbabilityBar
                    home={row.p_home}
                    draw={row.p_draw}
                    away={row.p_away}
                    height={14}
                    homeTeam={row.home_team}
                    awayTeam={row.away_team}
                  />
                </div>,
                result(row),
                row.rps === null ? "—" : fixed(row.rps, 3),
              ])}
            />
          </div>
        </div>
      )}
    </Card>
  );
}

function RegistryCard() {
  const models = useModels();
  return (
    <Card>
      <CardHeader title="Model registry" subtitle="Latest version of each model, with the data it was trained on" />
      {models.isPending ? (
        <Loading />
      ) : models.isError ? (
        <ErrorState error={models.error} />
      ) : (
        <div className="overflow-x-auto">
          <Table
            head={["Model", "Version", "Matches", "Trained through", "Data hash"]}
            align={["l", "l", "r", "l", "l"]}
            rows={models.data.map((c) => [
              modelLabel(c.name),
              <code key="v" className="text-[12px]">
                {c.version}
              </code>,
              c.n_matches,
              c.trained_through.slice(0, 10),
              <code key="h" className="text-[12px] text-ink-2">
                {c.data_fingerprint.slice(0, 10)}
              </code>,
            ])}
          />
        </div>
      )}
    </Card>
  );
}
