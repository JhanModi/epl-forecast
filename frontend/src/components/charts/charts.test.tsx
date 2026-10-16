import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { sequentialStep } from "./Heatmap";
import { ProbabilityBar } from "./ProbabilityBar";

afterEach(cleanup);

describe("ProbabilityBar", () => {
  it("describes every outcome in its accessible label", () => {
    render(<ProbabilityBar home={0.62} draw={0.24} away={0.14} homeTeam="Arsenal" awayTeam="Coventry City" />);

    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("Arsenal 62%, Draw 24%, Coventry City 14%");
  });

  it("only prints a percentage inside segments wide enough to hold it", () => {
    render(<ProbabilityBar home={0.85} draw={0.1} away={0.05} />);

    expect(screen.getByText("85%")).toBeTruthy();
    expect(screen.queryByText("10%")).toBeNull();
    expect(screen.queryByText("5%")).toBeNull();
  });
});

describe("sequentialStep", () => {
  it("maps zero to the recessive grey and larger values to darker steps", () => {
    expect(sequentialStep(0, 1).background).toBe("var(--seq-zero)");
    expect(sequentialStep(0.001, 1).background).toBe("var(--seq-zero)");
    expect(sequentialStep(0.05, 1).background).toBe("var(--seq-1)");
    expect(sequentialStep(1, 1).background).toBe("var(--seq-7)");
  });

  it("switches label ink between the light and dark halves of the ramp", () => {
    expect(sequentialStep(0.1, 1).ink).toBe("var(--seq-ink-light)");
    expect(sequentialStep(0.9, 1).ink).toBe("var(--seq-ink-dark)");
  });
});
