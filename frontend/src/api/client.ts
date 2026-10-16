import type { components } from "./schema";

type Schemas = components["schemas"];
export type Meta = Schemas["Meta"];
export type Fixture = Schemas["Fixture"];
export type FixturePrediction = Schemas["FixturePrediction"];
export type UpcomingPredictions = Schemas["UpcomingPredictions"];
export type Matchup = Schemas["Matchup"];
export type StandingRow = Schemas["StandingRow"];
export type Simulation = Schemas["Simulation"];
export type SimulatedTeam = Schemas["SimulatedTeam"];
export type TeamRating = Schemas["TeamRating"];
export type Team = Schemas["Team"];
export type EloPoint = Schemas["EloPoint"];
export type ModelCard = Schemas["ModelCardOut"];
export type BacktestReport = Schemas["BacktestReport"];
export type SeasonReport = Schemas["SeasonReport"];
export type ModelReport = Schemas["ModelReport"];
export type Estimate = Schemas["Estimate"];
export type LivePerformance = Schemas["LivePerformance"];
export type PredictionLog = Schemas["PredictionLog"];
export type LoggedForecast = Schemas["LoggedForecast"];
export type Problem = Schemas["Problem"];

/** An RFC 9457 problem returned by the API, surfaced as a typed error. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Partial<Problem>,
  ) {
    super(problem.detail ?? problem.title ?? `Request failed (${status})`);
    this.name = "ApiError";
  }
}

const BASE = "/api/v1";

export type Params = Record<string, string | number | undefined | null>;

export function buildUrl(path: string, params: Params = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const qs = query.toString();
  return `${BASE}${path}${qs ? `?${qs}` : ""}`;
}

export async function get<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
  const response = await fetch(buildUrl(path, params), { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({ title: response.statusText }));
    throw new ApiError(response.status, problem as Partial<Problem>);
  }
  return (await response.json()) as T;
}
