/**
 * User-flow tests: the real routes, hooks and components against a fake API.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient, routes } from "../app";
import { mockApi, predictionLog, problem } from "./api";

function renderAt(path: string) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, refetchOnWindowFocus: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("overview", () => {
  it("shows a loading state, then forecasts and an honest demo-data notice", async () => {
    mockApi();
    renderAt("/");

    expect(screen.getAllByRole("status").length).toBeGreaterThan(0); // loading placeholders
    expect(await screen.findByText(/Ashbury Rovers/, { selector: "span" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Ashbury Rovers 52%, Draw 25%, Brackenford United 23%" })).toBeTruthy();
    expect(screen.getByRole("note").textContent).toMatch(/fictional clubs/);
    expect(await screen.findByRole("heading", { name: "2026/27 demo league forecast" })).toBeTruthy();
  });

  it("contains a failing section without breaking the rest of the page", async () => {
    mockApi([[/\/predictions\/upcoming$/, () => problem(503, "Model not available", "run `epl train`")]]);
    renderAt("/");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Model not available: run `epl train`/);
    expect(await screen.findByText("Projected final table")).toBeTruthy(); // other sections still render
  });
});

describe("season simulator", () => {
  it("replays a finished season from matchweek 20 and marks actual finishes", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    renderAt("/simulator");

    await user.selectOptions(await screen.findByLabelText("Season"), "2025");

    await waitFor(() =>
      expect(
        calls.some(
          (u) => u.pathname.endsWith("/seasons/2025/simulation") && u.searchParams.get("as_of_matchweek") === "20",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Outlined cell = where the team actually finished/)).toBeTruthy();
    expect(screen.getByText("Actual champion: Ashbury Rovers")).toBeTruthy();
  });

  it("switches between chart and table views with the keyboard", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt("/simulator");

    const chart = await screen.findByRole("radio", { name: "Chart" });
    expect(screen.getByRole("grid", { name: "Finishing position probabilities" })).toBeTruthy();

    chart.focus();
    await user.keyboard("{ArrowRight}");

    const table = screen.getByRole("radio", { name: "Table" });
    expect(table.getAttribute("aria-checked")).toBe("true");
    expect(document.activeElement).toBe(table); // focus follows the selection
    expect(screen.queryByRole("grid", { name: "Finishing position probabilities" })).toBeNull();
    expect(within(screen.getByRole("table")).getByText("Brackenford United")).toBeTruthy();
  });
});

describe("match lab", () => {
  it("swaps home and away and requests the reversed fixture", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    renderAt("/match");

    expect(await screen.findByRole("heading", { name: "Ashbury Rovers v Brackenford United" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Swap home and away" }));

    expect(await screen.findByRole("heading", { name: "Brackenford United v Ashbury Rovers" })).toBeTruthy();
    const last = calls.filter((u) => u.pathname.endsWith("/predictions/matchup")).at(-1)!;
    expect([last.searchParams.get("home"), last.searchParams.get("away")]).toEqual([
      "Brackenford United",
      "Ashbury Rovers",
    ]);
  });
});

describe("model evaluation", () => {
  it("shows the oracle as a ceiling and lets a reviewer inspect another model", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt("/models");

    expect(await screen.findByRole("heading", { name: "Dixon-Coles" })).toBeTruthy(); // best deployable, not the oracle
    await user.selectOptions(screen.getByLabelText("Model"), "prior");

    expect(await screen.findByRole("heading", { name: "Baseline (base rates)" })).toBeTruthy();
    expect(screen.getByText(/The reference model/)).toBeTruthy();
  });
});

describe("forecast track record", () => {
  const trackRecord = async () =>
    (await screen.findByRole("heading", { name: "Forecast track record" })).closest("section")!;
  const logOffsets = (calls: URL[]) =>
    calls.filter((u) => u.pathname.endsWith("/predictions/log")).map((u) => u.searchParams.get("offset"));

  it("pages through the log, keeping the shown page visible while the next one loads", async () => {
    let release = () => {};
    const calls = mockApi([
      [
        /\/predictions\/log$/,
        // Hold the second page open until the test releases it, to observe the in-between state.
        (u) =>
          u.searchParams.get("offset") === "10"
            ? new Promise((resolve) => (release = () => resolve(predictionLog(u))))
            : predictionLog(u),
      ],
    ]);
    const user = userEvent.setup();
    renderAt("/models");

    const card = await trackRecord();
    expect(await within(card).findByText("1–10 of 25")).toBeTruthy();
    const previous = within(card).getByRole<HTMLButtonElement>("button", { name: "Previous page" });
    const next = within(card).getByRole<HTMLButtonElement>("button", { name: "Next page" });
    expect([previous.disabled, next.disabled]).toEqual([true, false]); // first page
    const firstRow = within(card).getAllByRole("row")[1]!;
    expect(within(firstRow).getByText("Ashbury Rovers – Brackenford United")).toBeTruthy();
    expect(within(firstRow).getByText("Scheduled")).toBeTruthy(); // not played yet: no score, no RPS
    expect(within(firstRow).getByText("—")).toBeTruthy();

    await user.click(next);
    // Page 2 is in flight: page 1 stays on screen, marked as updating, and both buttons pause.
    expect(await within(card).findByText(/1–10 of 25 · updating/)).toBeTruthy();
    expect(within(card).getAllByRole("row")).toHaveLength(11);
    expect([previous.disabled, next.disabled]).toEqual([true, true]);

    release();
    expect(await within(card).findByText("11–20 of 25")).toBeTruthy();
    expect([previous.disabled, next.disabled]).toEqual([false, false]);
    const played = within(card).getAllByRole("row")[1]!; // match 11 of 25: finished 1–1, scored
    expect([within(played).getByText("1–1"), within(played).getByText("0.150")]).toHaveLength(2);

    await user.click(next);
    expect(await within(card).findByText("21–25 of 25")).toBeTruthy();
    expect([previous.disabled, next.disabled]).toEqual([false, true]); // last page
    expect(within(card).getAllByRole("row")).toHaveLength(6);

    await user.click(previous);
    expect(await within(card).findByText("11–20 of 25")).toBeTruthy();
    expect(logOffsets(calls)).toEqual(["0", "10", "20"]); // page 2 came back from the cache
  });

  it("explains an empty log", async () => {
    mockApi([[/\/predictions\/log$/, (u) => predictionLog(u, [])]]);
    renderAt("/models");

    const card = await trackRecord();
    expect(await within(card).findByText(/No forecasts have been logged for 2026\/27 yet/)).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Next page" })).toBeNull();
  });

  it("shows a failure inside the card and recovers on retry", async () => {
    let failures = 1;
    mockApi([
      [
        /\/predictions\/log$/,
        (u) => (failures-- > 0 ? problem(503, "Database unavailable", "try later") : predictionLog(u)),
      ],
    ]);
    const user = userEvent.setup();
    renderAt("/models");

    const card = await trackRecord();
    expect((await within(card).findByRole("alert")).textContent).toMatch(/Database unavailable: try later/);
    await user.click(within(card).getByRole("button", { name: "Try again" }));
    expect(await within(card).findByText("1–10 of 25")).toBeTruthy();
  });
});

describe("chrome", () => {
  it("applies and remembers the chosen theme", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt("/");

    await user.selectOptions(screen.getByLabelText("Colour theme"), "dark");

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
  });

  it("opens the mobile menu and navigates", async () => {
    mockApi();
    const user = userEvent.setup();
    const router = renderAt("/");

    const toggle = screen.getByRole("button", { name: "Menu" });
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    const mobileNav = document.getElementById("mobile-nav")!;
    await user.click(within(mobileNav).getByRole("link", { name: "Team ratings" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/teams"));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("shows a not-found page for unknown routes", async () => {
    mockApi();
    renderAt("/does-not-exist");

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeTruthy();
  });
});
