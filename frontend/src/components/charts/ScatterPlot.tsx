import { scaleLinear } from "d3-scale";
import { useState } from "react";

import { TipRow, TipTitle, Tooltip, useTooltip, useWidth } from "./tooltip";

export interface ScatterPoint {
  key: string;
  label: string;
  short: string;
  x: number;
  y: number;
  emphasis?: boolean;
}

const MARGIN = { top: 16, right: 24, bottom: 40, left: 52 };

/**
 * Labelled scatter with nearest-point hover (no pinpoint targets). Only emphasised points
 * get a direct label; every point's name is in the tooltip and the table view.
 */
export function ScatterPlot({
  points,
  xLabel,
  yLabel,
  format,
  invertY = false,
  height = 380,
}: {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  format: (v: number) => string;
  invertY?: boolean;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();
  const [active, setActive] = useState<string | null>(null);

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const padX = (Math.max(...xs) - Math.min(...xs)) * 0.08 || 0.1;
  const padY = (Math.max(...ys) - Math.min(...ys)) * 0.08 || 0.1;
  const x = scaleLinear()
    .domain([Math.min(...xs) - padX, Math.max(...xs) + padX])
    .range([MARGIN.left, width - MARGIN.right]);
  const yRange: [number, number] = invertY
    ? [MARGIN.top, height - MARGIN.bottom]
    : [height - MARGIN.bottom, MARGIN.top];
  const y = scaleLinear()
    .domain([Math.min(...ys) - padY, Math.max(...ys) + padY])
    .range(yRange);

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left;
    const py = e.clientY - box.top;
    let best: ScatterPoint | undefined;
    let bestDistance = Infinity;
    for (const p of points) {
      const d = (x(p.x) - px) ** 2 + (y(p.y) - py) ** 2;
      if (d < bestDistance) [best, bestDistance] = [p, d];
    }
    if (!best || bestDistance > 60 ** 2) {
      hide();
      setActive(null);
      return;
    }
    setActive(best.key);
    show(
      x(best.x),
      y(best.y),
      <>
        <TipTitle>{best.label}</TipTitle>
        <TipRow label={xLabel} value={format(best.x)} />
        <TipRow label={yLabel} value={format(best.y)} />
      </>,
    );
  }

  return (
    <div ref={ref} className="relative w-full">
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={`${yLabel} against ${xLabel}`}
        onPointerMove={onMove}
        onPointerLeave={() => {
          hide();
          setActive(null);
        }}
        className="block"
      >
        {x.ticks(6).map((t) => (
          <g key={`x${t}`}>
            <line x1={x(t)} x2={x(t)} y1={MARGIN.top} y2={height - MARGIN.bottom} stroke="var(--grid)" />
            <text
              x={x(t)}
              y={height - MARGIN.bottom + 16}
              textAnchor="middle"
              className="tabular fill-[var(--text-muted)] text-[11px]"
            >
              {format(t)}
            </text>
          </g>
        ))}
        {y.ticks(6).map((t) => (
          <g key={`y${t}`}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
            <text
              x={MARGIN.left - 8}
              y={y(t)}
              dy="0.32em"
              textAnchor="end"
              className="tabular fill-[var(--text-muted)] text-[11px]"
            >
              {format(t)}
            </text>
          </g>
        ))}
        <text
          x={(MARGIN.left + width - MARGIN.right) / 2}
          y={height - 4}
          textAnchor="middle"
          className="fill-[var(--text-secondary)] text-[12px]"
        >
          {xLabel} →
        </text>
        <text
          transform={`translate(14 ${(MARGIN.top + height - MARGIN.bottom) / 2}) rotate(-90)`}
          textAnchor="middle"
          className="fill-[var(--text-secondary)] text-[12px]"
        >
          {invertY ? `← ${yLabel}` : `${yLabel} →`}
        </text>
        {points.map((p) => {
          const focused = active === p.key;
          return (
            <g key={p.key}>
              <circle
                cx={x(p.x)}
                cy={y(p.y)}
                r={focused ? 6 : 5}
                fill={p.emphasis || focused ? "var(--series-1)" : "var(--text-muted)"}
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
              {(p.emphasis || focused) && (
                <text
                  x={x(p.x) + 8}
                  y={y(p.y)}
                  dy="0.32em"
                  className="fill-[var(--text-primary)] text-[11.5px] font-medium"
                >
                  {p.short}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
