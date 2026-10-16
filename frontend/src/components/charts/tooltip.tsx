import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

export interface TooltipState {
  x: number;
  y: number;
  content: ReactNode;
}

/** Position state for a tooltip inside a `relative` container. */
export function useTooltip() {
  const [tip, setTip] = useState<TooltipState | null>(null);
  const show = useCallback((x: number, y: number, content: ReactNode) => setTip({ x, y, content }), []);
  const hide = useCallback(() => setTip(null), []);
  return { tip, show, hide };
}

/** Values lead (strong), labels follow (secondary); rows are keyed by a short line of the series colour. */
export function Tooltip({ tip, width }: { tip: TooltipState | null; width: number }) {
  if (!tip) return null;
  const flip = tip.x > width - 190;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 min-w-[140px] rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-lg"
      style={{ left: flip ? undefined : tip.x + 12, right: flip ? width - tip.x + 12 : undefined, top: tip.y + 12 }}
    >
      {tip.content}
    </div>
  );
}

export function TipRow({ color, label, value }: { color?: string; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-0.5">
      <span className="flex items-center gap-1.5 text-ink-2">
        {color && <span aria-hidden className="h-[2px] w-3 rounded-full" style={{ background: color }} />}
        {label}
      </span>
      <span className="tabular font-semibold text-ink">{value}</span>
    </div>
  );
}

export function TipTitle({ children }: { children: ReactNode }) {
  return <p className="mb-1 font-medium text-ink">{children}</p>;
}

/** Width of an element, kept in sync with ResizeObserver, so SVG charts can lay out responsively. */
export function useWidth<T extends HTMLElement>(fallback = 640): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width || fallback);
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fallback]);
  return [ref, width];
}
