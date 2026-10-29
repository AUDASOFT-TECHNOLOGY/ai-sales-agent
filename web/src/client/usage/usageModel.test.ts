import { describe, expect, it } from "vitest";
import { chartMax, forecast, untilReset } from "./usageModel";

describe("forecast", () => {
  it("projects the month from the pace so far and names the day the credits run out", () => {
    const f = forecast(2000, 5000, "2026-10-10")!; // 200 a day, 31 days
    expect(f.projected).toBe(6200);
    expect(f.runOutDay).toBe(25);
    expect(f.daysInMonth).toBe(31);
  });
  it("says nothing of a run-out when the credits last the month", () => {
    expect(forecast(500, 5000, "2026-10-10")!.runOutDay).toBeNull();
  });
  it("never puts the run-out before today", () => {
    expect(forecast(6000, 5000, "2026-10-10")!.runOutDay).toBe(10);
  });
  it("has no forecast without figures", () => {
    expect(forecast(null, 5000, "2026-10-10")).toBeNull();
    expect(forecast(10, null, "2026-10-10")).toBeNull();
  });
});

describe("untilReset", () => {
  it("counts to the next UTC midnight", () => {
    expect(untilReset(new Date("2026-10-10T18:30:00Z"))).toEqual({ hours: 5, minutes: 30 });
  });
});

describe("chartMax", () => {
  it("keeps the quota line inside the chart", () => {
    const days = [{ day: "2026-10-01", actions: 5, credits: 100 }];
    expect(chartMax(days, "actions", 20)).toBe(20);
    expect(chartMax(days, "credits", 20)).toBe(100);
  });
});
