import clsx from "clsx";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { ApiError } from "../api/client";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={clsx("rounded-xl border border-line bg-surface p-4 sm:p-5", className)}>{children}</section>
  );
}

export function CardHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        {subtitle && <p className="mt-0.5 text-[13px] text-ink-2">{subtitle}</p>}
      </div>
      {actions}
    </header>
  );
}

/** A chart with a table-view twin: every value in the chart is reachable without hovering. */
export function ChartCard({
  title,
  subtitle,
  chart,
  table,
  className,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card className={className}>
      <CardHeader
        title={title}
        subtitle={subtitle}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {actions}
            <Segmented
              label={`${title} view`}
              value={view}
              onChange={setView}
              options={[
                { value: "chart", label: "Chart" },
                { value: "table", label: "Table" },
              ]}
            />
          </div>
        }
      />
      {view === "chart" ? chart : <div className="max-h-[480px] overflow-auto">{table}</div>}
    </Card>
  );
}

/**
 * A single-choice toggle following the ARIA radio-group pattern: Tab enters the group at the
 * selected option, arrow keys move (and select) within it.
 */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const current = options.findIndex((o) => o.value === value);
    const next = (current + step + options.length) % options.length;
    onChange(options[next]!.value);
    refs.current[next]?.focus();
  }
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="inline-flex rounded-lg border border-line bg-page p-0.5"
    >
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          tabIndex={value === o.value ? 0 : -1}
          onClick={() => onChange(o.value)}
          className={clsx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
            value === o.value ? "bg-surface text-ink shadow-sm" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A chart with a table-view twin, for use inside an existing card. */
export function ChartPanel({
  title,
  subtitle,
  chart,
  table,
}: {
  title: string;
  subtitle?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section aria-label={title}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-ink">
          {title} {subtitle && <span className="font-normal text-ink-2">· {subtitle}</span>}
        </h3>
        <Segmented
          label={`${title} view`}
          value={view}
          onChange={setView}
          options={[
            { value: "chart", label: "Chart" },
            { value: "table", label: "Table" },
          ]}
        />
      </div>
      {view === "chart" ? chart : <div className="max-h-[420px] overflow-auto">{table}</div>}
    </section>
  );
}

export function Select<T extends string | number>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-[12px] font-medium text-ink-2">
      {label}
      <select
        id={id}
        value={String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          const match = options.find((o) => String(o.value) === raw);
          if (match) onChange(match.value);
        }}
        className="h-9 min-w-[10rem] rounded-lg border border-line bg-surface px-2.5 text-[14px] text-ink"
      >
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function StatTile({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <Card className="!p-4">
      <p className="text-[12px] font-medium text-ink-2">{label}</p>
      <p className="mt-1 text-[26px] leading-tight font-semibold text-ink">{value}</p>
      {detail && <p className="mt-1 text-[12px] text-ink-2">{detail}</p>}
    </Card>
  );
}

export function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[24px] font-semibold tracking-tight text-ink sm:text-[28px]">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-[14px] text-ink-2">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-end gap-3">{children}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex h-40 items-center justify-center text-[13px] text-muted">
      {label}…
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? `${error.problem.title ?? "Error"}${error.problem.detail ? `: ${error.problem.detail}` : ""}`
      : error instanceof Error
        ? error.message
        : "Something went wrong";
  return (
    <div role="alert" className="rounded-lg border border-line bg-raised p-4 text-[13px] text-ink-2">
      <p className="font-medium text-ink">Couldn't load this section</p>
      <p className="mt-1">{message}</p>
    </div>
  );
}

export function Table({ head, rows, align }: { head: string[]; rows: ReactNode[][]; align?: ("l" | "r")[] }) {
  return (
    <table className="w-full border-collapse text-[13px]">
      <thead>
        <tr className="border-b border-line text-left text-[12px] text-muted">
          {head.map((h, i) => (
            <th key={h} scope="col" className={clsx("px-2 py-2 font-medium", align?.[i] === "r" && "text-right")}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r} className="border-b border-line last:border-0">
            {row.map((cell, i) => (
              <td key={i} className={clsx("px-2 py-1.5 text-ink", align?.[i] === "r" && "tabular text-right")}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Legend entry: the mark carries the colour, the text stays in ink. */
export function LegendItem({
  color,
  label,
  shape = "line",
}: {
  color: string;
  label: string;
  shape?: "line" | "rect";
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
      <span
        aria-hidden
        className={shape === "line" ? "h-[2px] w-3.5 rounded-full" : "h-2.5 w-2.5 rounded-[3px]"}
        style={{ background: color }}
      />
      {label}
    </span>
  );
}
