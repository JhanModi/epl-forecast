import { scaleLinear } from "d3-scale";
import { line } from "d3-shape";

import { pct } from "../../lib/format";
import { TipRow, TipTitle, Tooltip, useTooltip, useWidth } from "./tooltip";

interface Point {
  forecast: number;
  observed: number;
  count: number;
}

const OUTCOMES = [
  { key: "home", label: "Home win", color: "var(--home)" },
  { key: "draw", label: "Draw", color: "var(--draw)" },
  { key: "away", label: "Away win", color: "var(--away)" },
] as const;

/** Reliability diagrams as small multiples, one per outcome. On the diagonal = calibrated. */
export function CalibrationChart({ curves }: { curves: Record<string, Point[]> }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {OUTCOMES.map((o) => (
        <Panel key={o.key} label={o.label} color={o.color} points={(curves[o.key] ?? []).filter((p) => p.count >= 5)} />
      ))}
    </div>
  );
}

function Panel({ label, color, points }: { label: string; color: string; points: Point[] }) {
  const [ref, width] = useWidth<HTMLDivElement>(260);
  const { tip, show, hide } = useTooltip();
  const size = Math.min(width, 300);
  const m = 32;
  const x = scaleLinear()
    .domain([0, 1])
    .range([m, size - 10]);
  const y = scaleLinear()
    .domain([0, 1])
    .range([size - m, 10]);
  const path = line<Point>()
    .x((p) => x(p.forecast))
    .y((p) => y(p.observed));
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div ref={ref} className="relative" onPointerLeave={hide}>
      <p className="mb-1 text-[12px] font-medium text-ink-2">{label}</p>
      <svg width={size} height={size} role="img" aria-label={`Calibration for ${label}`} className="block">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="var(--grid)" />
            <line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
            <text
              x={x(t)}
              y={size - m + 14}
              textAnchor="middle"
              className="tabular fill-[var(--text-muted)] text-[10px]"
            >
              {pct(t)}
            </text>
            <text
              x={m - 6}
              y={y(t)}
              dy="0.32em"
              textAnchor="end"
              className="tabular fill-[var(--text-muted)] text-[10px]"
            >
              {pct(t)}
            </text>
          </g>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--axis)" strokeWidth={1} />
        <path d={path(points) ?? ""} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
        {points.map((p) => (
          <circle
            key={p.forecast}
            cx={x(p.forecast)}
            cy={y(p.observed)}
            r={4.5}
            fill={color}
            stroke="var(--surface-1)"
            strokeWidth={2}
            onPointerMove={(e) => {
              const box = ref.current?.getBoundingClientRect();
              if (box)
                show(
                  e.clientX - box.left,
                  e.clientY - box.top,
                  <>
                    <TipTitle>{label}</TipTitle>
                    <TipRow label="Forecast" value={pct(p.forecast, 1)} />
                    <TipRow label="Observed" value={pct(p.observed, 1)} />
                    <TipRow label="Matches" value={String(p.count)} />
                  </>,
                );
            }}
          />
        ))}
        <text x={x(0.5)} y={size - 2} textAnchor="middle" className="fill-[var(--text-secondary)] text-[10.5px]">
          Forecast probability
        </text>
      </svg>
      <Tooltip tip={tip} width={size} />
    </div>
  );
}
