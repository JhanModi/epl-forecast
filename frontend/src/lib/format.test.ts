import { describe, expect, it } from "vitest";

import { buildUrl } from "../api/client";
import { modelLabel, pct, pValue, seasonName, signed } from "./format";

describe("format", () => {
  it("formats probabilities, guarding the extremes", () => {
    expect(pct(0.4567)).toBe("46%");
    expect(pct(0.4567, 1)).toBe("45.7%");
    expect(pct(0.001)).toBe("<1%");
    expect(pct(0.999)).toBe(">99%");
    expect(pct(0)).toBe("0%");
  });

  it("names seasons by their start and end year", () => {
    expect(seasonName(2025)).toBe("2025/26");
    expect(seasonName(2099)).toBe("2099/00");
  });

  it("signs differences with a true minus", () => {
    expect(signed(0.0123, 3)).toBe("+0.012");
    expect(signed(-0.0123, 3)).toBe("−0.012");
  });

  it("reports tiny p-values as a bound", () => {
    expect(pValue(0.00001)).toBe("p < 0.001");
    expect(pValue(0.0421)).toBe("p = 0.042");
    expect(pValue(Number.NaN)).toBe("—");
  });

  it("labels known models and passes unknown ones through", () => {
    expect(modelLabel("dixon_coles")).toBe("Dixon-Coles");
    expect(modelLabel("new_model")).toBe("new_model");
  });
});

describe("buildUrl", () => {
  it("drops empty parameters and encodes the rest", () => {
    expect(
      buildUrl("/predictions/matchup", { home: "Brighton and Hove Albion", away: "Spurs", x: undefined, y: "" }),
    ).toBe("/api/v1/predictions/matchup?home=Brighton+and+Hove+Albion&away=Spurs");
    expect(buildUrl("/meta")).toBe("/api/v1/meta");
  });
});
