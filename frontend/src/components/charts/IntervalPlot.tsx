import { scaleLinear } from "d3-scale";

import { TipRow, TipTitle, Tooltip, useTooltip, useWidth } from "./tooltip";

export interface IntervalRow {
  key: string;
  label: string;
  mean: number;
  low: number;
  high: number;
  emphasis?: boolean;
}

const ROW = 34;
const MARGIN = { left: 170, right: 72, top: 8, bottom: 26 };

/**
 * Point estimate with a 95% interval per row (a forest plot). One series, so one hue:
 * the highlighted row is in the accent, the rest recede to the muted ink.
 */
export function IntervalPlot({
  rows,
  format,
  axisLabel,
  reference,
}: {
  rows: IntervalRow[];
  format: (v: number) => string;
  axisLabel: string;
  reference?: { value: number; label: string };
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();
  const height = MARGIN.top + rows.length * ROW + MARGIN.bottom;
  const lo = Math.min(...rows.map((r) => r.low), reference?.value ?? Infinity);
  const hi = Math.max(...rows.map((r) => r.high), reference?.value ?? -Infinity);
  const pad = (hi - lo) * 0.06;
  const x = scaleLinear()
    .domain([lo - pad, hi + pad])
    .nice()
    .range([MARGIN.left, Math.max(MARGIN.left + 40, width - MARGIN.right)]);

  return (
    <div ref={ref} className="relative w-full" onPointerLeave={hide}>
      <svg width={width} height={height} role="img" aria-label={axisLabel} className="block">
        {x.ticks(5).map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={MARGIN.top} y2={height - MARGIN.bottom} stroke="var(--grid)" />
            <text x={x(t)} y={height - 8} textAnchor="middle" className="tabular fill-[var(--text-muted)] text-[11px]">
              {format(t)}
            </text>
          </g>
        ))}
        {reference && (
          <line
            x1={x(reference.value)}
            x2={x(reference.value)}
            y1={MARGIN.top}
            y2={height - MARGIN.bottom}
            stroke="var(--text-muted)"
          />
        )}
        {rows.map((r, i) => {
          const cy = MARGIN.top + i * ROW + ROW / 2;
          const color = r.emphasis ? "var(--series-1)" : "var(--text-muted)";
          return (
            <g
              key={r.key}
              onPointerMove={(e) => {
                const box = ref.current?.getBoundingClientRect();
                if (box)
                  show(
                    e.clientX - box.left,
                    e.clientY - box.top,
                    <>
                      <TipTitle>{r.label}</TipTitle>
                      <TipRow label="Mean" value={format(r.mean)} />
                      <TipRow label="95% interval" value={`${format(r.low)} – ${format(r.high)}`} />
                    </>,
                  );
              }}
            >
              <rect x={0} y={cy - ROW / 2} width={width} height={ROW} fill="transparent" />
              <text
                x={MARGIN.left - 12}
                y={cy}
                dy="0.32em"
                textAnchor="end"
                className="fill-[var(--text-primary)] text-[12.5px]"
              >
                {r.label}
              </text>
              <line x1={x(r.low)} x2={x(r.high)} y1={cy} y2={cy} stroke={color} strokeWidth={2} strokeLinecap="round" />
              <circle cx={x(r.mean)} cy={cy} r={5} fill={color} stroke="var(--surface-1)" strokeWidth={2} />
              {/* Values sit in a fixed right-hand column so they never collide with marks or the rule. */}
              <text
                x={width - 8}
                y={cy}
                dy="0.32em"
                textAnchor="end"
                className="tabular fill-[var(--text-secondary)] text-[11.5px]"
              >
                {format(r.mean)}
              </text>
            </g>
          );
        })}
      </svg>
      {reference && (
        <p className="mt-1 text-[11px] text-muted">
          Vertical rule: {reference.label}. Lower is better. Bars are bootstrap 95% intervals over matches.
        </p>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
