import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  get,
  type BacktestReport,
  type EloPoint,
  type Fixture,
  type LivePerformance,
  type Matchup,
  type Meta,
  type ModelCard,
  type PredictionLog,
  type Simulation,
  type StandingRow,
  type Team,
  type TeamRating,
  type UpcomingPredictions,
} from "./client";

// Forecasts only change when the weekly pipeline runs, so data can be cached generously.
const FIVE_MINUTES = 5 * 60 * 1000;

export const useMeta = () =>
  useQuery({ queryKey: ["meta"], queryFn: ({ signal }) => get<Meta>("/meta", {}, signal), staleTime: FIVE_MINUTES });

export const useUpcoming = (limit = 10) =>
  useQuery({
    queryKey: ["upcoming", limit],
    queryFn: ({ signal }) => get<UpcomingPredictions>("/predictions/upcoming", { limit }, signal),
    staleTime: FIVE_MINUTES,
  });

export const useFixtures = (season: number | undefined, status?: "finished" | "scheduled") =>
  useQuery({
    queryKey: ["fixtures", season, status],
    queryFn: ({ signal }) => get<Fixture[]>(`/seasons/${season}/fixtures`, { status }, signal),
    enabled: season !== undefined,
    staleTime: FIVE_MINUTES,
  });

export const useStandings = (season: number | undefined, asOf?: number) =>
  useQuery({
    queryKey: ["standings", season, asOf],
    queryFn: ({ signal }) => get<StandingRow[]>(`/seasons/${season}/standings`, { as_of_matchweek: asOf }, signal),
    enabled: season !== undefined,
    staleTime: FIVE_MINUTES,
  });

/** Holds the previous simulation on screen while a new matchweek loads (no skeleton flash). */
export const useSimulation = (season: number | undefined, asOf?: number, runs = 10_000) =>
  useQuery({
    queryKey: ["simulation", season, asOf, runs],
    queryFn: ({ signal }) => get<Simulation>(`/seasons/${season}/simulation`, { as_of_matchweek: asOf, runs }, signal),
    enabled: season !== undefined,
    staleTime: FIVE_MINUTES,
    placeholderData: keepPreviousData,
  });

export const useMatchup = (home: string | undefined, away: string | undefined) =>
  useQuery({
    queryKey: ["matchup", home, away],
    queryFn: ({ signal }) => get<Matchup>("/predictions/matchup", { home, away }, signal),
    enabled: Boolean(home && away && home !== away),
    staleTime: FIVE_MINUTES,
    placeholderData: keepPreviousData,
  });

export const useBacktest = () =>
  useQuery({
    queryKey: ["backtest"],
    queryFn: ({ signal }) => get<BacktestReport>("/evaluation/backtest", {}, signal),
    staleTime: Infinity,
  });

export const useModels = () =>
  useQuery({ queryKey: ["models"], queryFn: ({ signal }) => get<ModelCard[]>("/models", {}, signal) });

export const useRatings = () =>
  useQuery({
    queryKey: ["ratings"],
    queryFn: ({ signal }) => get<TeamRating[]>("/ratings", {}, signal),
    staleTime: FIVE_MINUTES,
  });

export const useTeams = () =>
  useQuery({ queryKey: ["teams"], queryFn: ({ signal }) => get<Team[]>("/teams", {}, signal), staleTime: Infinity });

export const useElo = (teamId: number | undefined) =>
  useQuery({
    queryKey: ["elo", teamId],
    queryFn: ({ signal }) => get<EloPoint[]>(`/teams/${teamId}/elo`, {}, signal),
    enabled: teamId !== undefined,
    staleTime: FIVE_MINUTES,
  });

/** One page of the forecast track record; the page on screen stays visible while the next one loads. */
export const usePredictionLog = (season: number | undefined, offset: number, limit: number) =>
  useQuery({
    queryKey: ["prediction-log", season, limit, offset],
    queryFn: ({ signal }) => get<PredictionLog>("/predictions/log", { season, limit, offset }, signal),
    enabled: season !== undefined,
    staleTime: FIVE_MINUTES,
    placeholderData: keepPreviousData,
  });

export const useLive = () =>
  useQuery({
    queryKey: ["live"],
    queryFn: ({ signal }) => get<LivePerformance>("/predictions/live", {}, signal),
    staleTime: FIVE_MINUTES,
  });
