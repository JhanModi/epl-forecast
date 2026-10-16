import { pct } from "../../lib/format";

const SEGMENTS = [
  { key: "home", color: "var(--home)", label: "Home win" },
  { key: "draw", color: "var(--draw)", label: "Draw" },
  { key: "away", color: "var(--away)", label: "Away win" },
] as const;

/**
 * Home / draw / away as one diverging stacked bar: blue and red poles, grey midpoint.
 * Segments are separated by a 2px surface gap; a percentage is printed inside a segment
 * only when it fits, otherwise it lives in the accessible label and tooltip.
 */
export function ProbabilityBar({
  home,
  draw,
  away,
  height = 22,
  homeTeam,
  awayTeam,
}: {
  home: number;
  draw: number;
  away: number;
  height?: number;
  homeTeam?: string;
  awayTeam?: string;
}) {
  const values = { home, draw, away };
  const names = { home: homeTeam ?? "Home", draw: "Draw", away: awayTeam ?? "Away" };
  const summary = SEGMENTS.map((s) => `${s.key === "draw" ? "Draw" : names[s.key]} ${pct(values[s.key])}`).join(", ");
  return (
    <div role="img" aria-label={summary} className="flex w-full gap-[2px]" style={{ height }}>
      {SEGMENTS.map((s, i) => {
        const value = values[s.key];
        const fits = value >= 0.13;
        return (
          <div
            key={s.key}
            title={`${s.key === "draw" ? "Draw" : `${names[s.key]} win`}: ${pct(value, 1)}`}
            className="flex min-w-[3px] items-center justify-center overflow-visible text-[11px] font-semibold"
            style={{
              width: `${value * 100}%`,
              background: s.color,
              color: s.key === "draw" ? "var(--text-primary)" : "#fff",
              borderRadius: i === 0 ? "4px 0 0 4px" : i === 2 ? "0 4px 4px 0" : 0,
            }}
          >
            {fits && <span className="tabular">{pct(value)}</span>}
          </div>
        );
      })}
    </div>
  );
}

export function ProbabilityLegend() {
  return (
    <div className="flex flex-wrap gap-3 text-[12px] text-ink-2">
      {SEGMENTS.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}
