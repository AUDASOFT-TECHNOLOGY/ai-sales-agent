import { describe, expect, it } from "vitest";
import { guideSteps, type Overview } from "./overview";

describe("guideSteps", () => {
  it("does not mark 'link everything' done while the products, prospects and channel are not", () => {
    const overview = { lanes: { lanes: [{ id: "lane-1" }] } } as unknown as Overview;
    const states = Object.fromEntries(guideSteps(overview).map((s) => [s.id, s.state]));
    expect(states.circuit).toBe("todo");
    expect(states.products).toBe("todo");
  });
});
