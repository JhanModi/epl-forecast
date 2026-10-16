import type { ReactNode } from "react";

import { Tooltip, useTooltip, useWidth } from "./tooltip";

// One hue, seven steps (sequential). Step 1 sits nearest the surface in both themes; values under
// 0.5% of the maximum count as zero and recede to the neutral grey.
const STEPS = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"];

export function sequentialStep(value: number, max: number): { background: string; ink: string } {
  if (max <= 0 || value < max * 0.005) return { background: "var(--seq-zero)", ink: "var(--text-muted)" };
  const index = Math.min(STEPS.length - 1, Math.floor((value / max) * STEPS.length));
  return { background: `var(${STEPS[index]})`, ink: index >= 3 ? "var(--seq-ink-dark)" : "var(--seq-ink-light)" };
}

export function Heatmap({
  rows,
  columns,
  values,
  format,
  tooltip,
  label,
  highlight,
  rowHeader = 150,
  minLabel = 0.1,
  max,
}: {
  rows: string[];
  columns: string[];
  values: number[][];
  format: (v: number) => string;
  tooltip: (row: number, col: number) => ReactNode;
  label: string;
  highlight?: (row: number, col: number) => boolean;
  rowHeader?: number;
  minLabel?: number;
  max?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();
  const top = max ?? Math.max(...values.flat(), 1e-9);
  // On narrow screens the row labels shrink and the grid keeps a legible minimum cell size,
  // scrolling sideways inside its card rather than overflowing the page.
  const header = width < 520 ? Math.min(rowHeader, 104) : rowHeader;
  const MIN_CELL = 22;
  const cell = Math.max(MIN_CELL, (width - header) / columns.length);
  const showText = cell >= 26;

  return (
    <div ref={ref} className="relative w-full overflow-x-auto" onPointerLeave={hide}>
      <div
        role="grid"
        aria-label={label}
        className="grid gap-[2px]"
        style={{
          gridTemplateColumns: `${header}px repeat(${columns.length}, minmax(0, 1fr))`,
          minWidth: header + columns.length * MIN_CELL,
        }}
      >
        <div />
        {columns.map((c) => (
          <div key={c} role="columnheader" className="tabular pb-1 text-center text-[11px] text-muted">
            {c}
          </div>
        ))}
        {rows.map((r, i) => (
          <div key={r} role="row" className="contents">
            <div role="rowheader" className="truncate pr-2 text-right text-[12px] leading-[26px] text-ink-2">
              {r}
            </div>
            {columns.map((c, j) => {
              const v = values[i]?.[j] ?? 0;
              const { background, ink } = sequentialStep(v, top);
              const marked = highlight?.(i, j);
              return (
                <div
                  key={c}
                  role="gridcell"
                  tabIndex={-1}
                  aria-label={`${r}, ${c}: ${format(v)}`}
                  className="tabular flex h-[26px] items-center justify-center rounded-[3px] text-[10.5px] font-medium transition-[filter] hover:brightness-110"
                  style={{
                    background,
                    color: ink,
                    boxShadow: marked ? "inset 0 0 0 2px var(--text-primary)" : undefined,
                  }}
                  onPointerMove={(e) => {
                    const box = ref.current?.getBoundingClientRect();
                    if (box) show(e.clientX - box.left, e.clientY - box.top, tooltip(i, j));
                  }}
                >
                  {showText && v >= minLabel ? format(v) : ""}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}

export function SequentialLegend({
  max,
  format,
  label,
}: {
  max: number;
  format: (v: number) => string;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted">
      <span>{label}</span>
      <span className="tabular">0</span>
      <div className="flex gap-[2px]" aria-hidden>
        {STEPS.map((s) => (
          <span key={s} className="h-2.5 w-5 rounded-[2px]" style={{ background: `var(${s})` }} />
        ))}
      </div>
      <span className="tabular">{format(max)}</span>
    </div>
  );
}
