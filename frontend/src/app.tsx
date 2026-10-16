import { QueryClient } from "@tanstack/react-query";
import type { RouteObject } from "react-router-dom";

import { ApiError } from "./api/client";
import { Layout } from "./components/Layout";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/Overview";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        // Client errors (4xx) won't succeed on retry; transient server errors might.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  });
}

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      // The landing page ships in the main bundle; every other page is a separate chunk loaded on navigation.
      { path: "/", element: <Overview /> },
      { path: "/simulator", lazy: async () => ({ Component: (await import("./pages/Simulator")).Simulator }) },
      { path: "/match", lazy: async () => ({ Component: (await import("./pages/MatchLab")).MatchLab }) },
      { path: "/models", lazy: async () => ({ Component: (await import("./pages/Models")).Models }) },
      { path: "/teams", lazy: async () => ({ Component: (await import("./pages/Teams")).Teams }) },
      { path: "/method", lazy: async () => ({ Component: (await import("./pages/Methodology")).Methodology }) },
      { path: "*", element: <NotFound /> },
    ],
  },
];
