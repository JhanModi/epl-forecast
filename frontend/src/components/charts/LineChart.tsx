import { extent, max as d3max, min as d3min } from "d3-array";
import { scaleLinear, scaleTime } from "d3-scale";
import { line } from "d3-shape";
import { useMemo, useState } from "react";

import { LegendItem } from "../ui";
import { TipRow, TipTitle, Tooltip, useTooltip, useWidth } from "./tooltip";

export interface Series {
  key: string;
  label: string;
  color: string;
  points: { x: number; y: number }[];
}

const MARGIN = { top: 12, right: 96, bottom: 28, left: 44 };

interface XScale {
  at: (v: number) => number;
  invert: (px: number) => number;
  ticks: (n: number) => number[];
}

/** One numeric interface over linear and time scales (time values are epoch milliseconds). */
function xScale(type: "linear" | "time", domain: [number, number], range: [number, number]): XScale {
  if (type === "time") {
    const s = scaleTime()
      .domain(domain.map((d) => new Date(d)))
      .range(range);
    return {
      at: (v) => s(new Date(v)),
      invert: (px) => s.invert(px).getTime(),
      ticks: (n) => s.ticks(n).map((d) => d.getTime()),
    };
  }
  const s = scaleLinear().domain(domain).range(range);
  return { at: (v) => s(v), invert: (px) => s.invert(px), ticks: (n) => s.ticks(n) };
}

/**
 * Multi-series line chart: 2px lines, a crosshair that snaps to the nearest x, one tooltip
 * listing every series, a legend for >= 2 series and selective end labels (<= 4 series).
 */
export function LineChart({
  series,
  height = 260,
  xType = "linear",
  formatX,
  formatY,
  yLabel,
  reference,
}: {
  series: Series[];
  height?: number;
  xType?: "linear" | "time";
  formatX: (x: number) => string;
  formatY: (y: number) => string;
  yLabel?: string;
  reference?: { y: number; label: string };
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();
  const [hoverX, setHoverX] = useState<number | null>(null);

  const all = useMemo(() => series.flatMap((s) => s.points), [series]);
  const { x, y, xs } = useMemo(() => {
    const [x0, x1] = extent(all, (p) => p.x) as [number, number];
    const lo = d3min([...all.map((p) => p.y), reference?.y ?? Infinity]) ?? 0;
    const hi = d3max([...all.map((p) => p.y), reference?.y ?? -Infinity]) ?? 1;
    const pad = (hi - lo) * 0.08 || 1;
    const xs_ = xScale(xType, [x0 ?? 0, x1 ?? 1], [MARGIN.left, Math.max(MARGIN.left + 10, width - MARGIN.right)]);
    const yScale = scaleLinear()
      .domain([lo - pad, hi + pad])
      .nice()
      .range([height - MARGIN.bottom, MARGIN.top]);
    const uniqueX = [...new Set(all.map((p) => p.x))].sort((a, b) => a - b);
    return { x: xs_, y: yScale, xs: uniqueX };
  }, [all, width, height, xType, reference?.y]);

  if (!all.length) return <p className="text-[13px] text-muted">No data.</p>;

  const path = line<{ x: number; y: number }>()
    .x((p) => x.at(p.x))
    .y((p) => y(p.y));
  const yTicks = y.ticks(5);
  const xTicks = x.ticks(Math.max(2, Math.floor(width / 110)));
  const endLabels = series.length <= 4;

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left;
    const target = x.invert(px);
    const nearest = xs.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a), xs[0] ?? 0);
    setHoverX(nearest);
    show(
      x.at(nearest),
      e.clientY - box.top,
      <>
        <TipTitle>{formatX(nearest)}</TipTitle>
        {series.map((s) => {
          const point = s.points.find((p) => p.x === nearest);
          return point ? <TipRow key={s.key} color={s.color} label={s.label} value={formatY(point.y)} /> : null;
        })}
      </>,
    );
  }

  return (
    <div ref={ref} className="relative w-full">
      {series.length >= 2 && (
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          {series.map((s) => (
            <LegendItem key={s.key} color={s.color} label={s.label} />
          ))}
        </div>
      )}
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={yLabel ?? "Line chart"}
        onPointerMove={onMove}
        onPointerLeave={() => {
          hide();
          setHoverX(null);
        }}
        className="block touch-pan-y"
      >
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
            <text
              x={MARGIN.left - 8}
              y={y(t)}
              dy="0.32em"
              textAnchor="end"
              className="tabular fill-[var(--text-muted)] text-[11px]"
            >
              {formatY(t)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text
            key={t}
            x={x.at(t)}
            y={height - 8}
            textAnchor="middle"
            className="tabular fill-[var(--text-muted)] text-[11px]"
          >
            {formatX(t)}
          </text>
        ))}
        <line
          x1={MARGIN.left}
          x2={width - MARGIN.right}
          y1={height - MARGIN.bottom}
          y2={height - MARGIN.bottom}
          stroke="var(--axis)"
        />
        {reference && (
          <g>
            <line
              x1={MARGIN.left}
              x2={width - MARGIN.right}
              y1={y(reference.y)}
              y2={y(reference.y)}
              stroke="var(--text-muted)"
              strokeWidth={1}
            />
            <text
              x={width - MARGIN.right + 6}
              y={y(reference.y)}
              dy="0.32em"
              className="fill-[var(--text-muted)] text-[11px]"
            >
              {reference.label}
            </text>
          </g>
        )}
        {series.map((s) => (
          <path
            key={s.key}
            d={path(s.points) ?? ""}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {endLabels &&
          series.map((s) => {
            const last = s.points[s.points.length - 1];
            if (!last) return null;
            return (
              <g key={s.key}>
                <circle
                  cx={x.at(last.x)}
                  cy={y(last.y)}
                  r={4}
                  fill={s.color}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
                <text
                  x={x.at(last.x) + 8}
                  y={y(last.y)}
                  dy="0.32em"
                  className="fill-[var(--text-secondary)] text-[11px]"
                >
                  {s.label.length > 14 ? `${s.label.slice(0, 13)}…` : s.label}
                </text>
              </g>
            );
          })}
        {hoverX !== null && (
          <g pointerEvents="none">
            <line
              x1={x.at(hoverX)}
              x2={x.at(hoverX)}
              y1={MARGIN.top}
              y2={height - MARGIN.bottom}
              stroke="var(--axis)"
            />
            {series.map((s) => {
              const point = s.points.find((p) => p.x === hoverX);
              return point ? (
                <circle
                  key={s.key}
                  cx={x.at(point.x)}
                  cy={y(point.y)}
                  r={4}
                  fill={s.color}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
              ) : null;
            })}
          </g>
        )}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
