/** Formatting helpers. Every number on screen goes through one of these. */

export function pct(p: number, digits = 0): string {
  if (p > 0 && p < 0.005 && digits === 0) return "<1%";
  if (p < 1 && p > 0.995 && digits === 0) return ">99%";
  return `${(p * 100).toFixed(digits)}%`;
}

export const fixed = (x: number, digits = 2): string => x.toFixed(digits);

export function seasonName(season: number): string {
  return `${season}/${String((season + 1) % 100).padStart(2, "0")}`;
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** Kickoffs come from the API in UK local time without an offset; show them as such. */
export const kickoffDay = (iso: string): string => DAY.format(new Date(iso));
export const kickoffTime = (iso: string): string => TIME.format(new Date(iso));
export const longDate = (iso: string): string => DATE.format(new Date(iso));

export function signed(x: number, digits = 3): string {
  return `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(digits)}`;
}

export function pValue(p: number): string {
  if (!Number.isFinite(p)) return "—";
  return p < 0.001 ? "p < 0.001" : `p = ${p.toFixed(3)}`;
}

export const MODEL_LABELS: Record<string, string> = {
  prior: "Baseline (base rates)",
  elo_logit: "Elo → ordered logit",
  logistic_regression: "Logistic regression",
  gradient_boosting: "Gradient boosting",
  dixon_coles: "Dixon-Coles",
  ensemble: "Ensemble",
  oracle: "Oracle (true probabilities)",
};

export const modelLabel = (name: string): string => MODEL_LABELS[name] ?? name;
