import type { ReactNode } from "react";

import { Card, CardHeader, PageHeader } from "../components/ui";

const PIPELINE = [
  { name: "Extract", detail: "Demo generator, openfootball or (locally) the Premier League site → CSV snapshots" },
  { name: "Validate & load", detail: "Schema + football-logic checks → idempotent upserts (SQLite / PostgreSQL)" },
  { name: "Features", detail: "Point-in-time as-of joins: form, match stats, Elo" },
  { name: "Models", detail: "Dixon-Coles, Elo logit, logistic regression, boosting, ensemble" },
  { name: "Evaluate", detail: "Weekly walk-forward backtest, bootstrap CIs, paired tests" },
  { name: "Serve", detail: "Model registry → FastAPI → this dashboard; Monte Carlo cache" },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} />
      <div className="space-y-3 text-[14px] leading-relaxed text-ink-2 [&_strong]:font-semibold [&_strong]:text-ink">
        {children}
      </div>
    </Card>
  );
}

export function Methodology() {
  return (
    <>
      <PageHeader
        title="Methodology"
        subtitle="How the forecasts are made, how they are evaluated, and what they cannot do."
      />

      <Card className="mb-6">
        <CardHeader
          title="Pipeline"
          subtitle="Each stage is a CLI command and is idempotent; the weekly refresh runs them all."
        />
        <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-6">
          {PIPELINE.map((step, i) => (
            <li key={step.name} className="rounded-lg border border-line bg-page p-3">
              <p className="text-[11px] font-medium text-muted">Step {i + 1}</p>
              <p className="text-[14px] font-semibold text-ink">{step.name}</p>
              <p className="mt-1 text-[12px] text-ink-2">{step.detail}</p>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Section title="The goals model">
          <p>
            Each side's goals follow a Poisson distribution whose rate combines a league intercept, home advantage, the
            team's <strong>attack</strong> and the opponent's <strong>defence</strong>. The Dixon-Coles correction
            adjusts the four low-scoring results (0–0, 1–0, 0–1, 1–1) that independent Poissons mis-price.
          </p>
          <p>
            Matches are weighted by <strong>exp(−ξ · days ago)</strong> so recent form counts more, and team parameters
            have a Gaussian prior that shrinks sparse teams towards average. Newly promoted sides share a learned
            "promoted team" offset, so a team with no top-flight matches starts as a typical promoted team.
          </p>
          <p>
            The model is fitted by penalised maximum likelihood with an analytic gradient (checked against finite
            differences in the test suite), which makes a refit take milliseconds.
          </p>
        </Section>

        <Section title="No look-ahead, by construction">
          <p>
            Features are built with <strong>as-of joins</strong>: each fixture receives the state of each team after its
            latest match strictly before kickoff. Training rows and live fixtures go through the same function, so there
            is no training/serving skew, and tests prove that changing a match's result never changes its own features.
          </p>
          <p>
            The backtest refits every model before each week of fixtures using only earlier results, with the week's
            results hidden. A test double asserts the harness never shows a model a match from after its cutoff.
          </p>
        </Section>

        <Section title="Evaluation">
          <p>
            The headline metric is the <strong>ranked probability score</strong>, the standard for football forecasts:
            it respects the ordering home &gt; draw &gt; away. Log loss, Brier score, accuracy and calibration error are
            reported alongside, each with a bootstrap 95% interval.
          </p>
          <p>
            Hyperparameters are tuned on <strong>2024/25 only</strong>. 2025/26 is never used for any decision, so its
            numbers are an honest out-of-sample estimate. Model differences are tested with a paired test on per-match
            scores, because two models scored on the same matches are not independent.
          </p>
          <p>
            <strong>Oracle.</strong> On the synthetic demo league the true probabilities are known, so the report adds
            the score of a forecaster that knew them. It is the ceiling no model can beat on average, and shows how much
            of the achievable improvement each model captures.
          </p>
        </Section>

        <Section title="Limitations">
          <p>
            <strong>No market benchmark yet.</strong> Bookmaker odds are the real bar to clear; the base-rate baseline
            is only a floor.
          </p>
          <p>
            <strong>Limited inputs.</strong> There is no expected-goals, lineup, injury or transfer information; the
            match statistics used are shots, shots on target, big chances and possession.
          </p>
          <p>
            <strong>Static simulations.</strong> Team strengths are frozen at the simulation date, so the spread of
            outcomes far from the end of a season is slightly too narrow.
          </p>
          <p>
            <strong>Three seasons.</strong> Historical depth is limited, so confidence intervals are wide: the gap
            between the best models is usually not statistically significant.
          </p>
        </Section>
      </div>
    </>
  );
}
