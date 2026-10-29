/** What the usage screen derives from the server's figures: the pace of the month and when the day resets.
 *  Pure functions: the clock is passed in. */

import type { UsageDay } from "./usageApi";

export interface Forecast {
  /** Credits expected at the end of the month at the current pace. */
  projected: number;
  /** The day of the month on which the credits would run out, or null when they last the month. */
  runOutDay: number | null;
  /** The month's last day. */
  daysInMonth: number;
}

/** The pace is the average of the days elapsed so far (today included). null: too early or nothing used. */
export function forecast(creditsUsed: number | null, included: number | null, dayUtc: string): Forecast | null {
  if (creditsUsed == null || included == null || included <= 0) return null;
  const [year, month, day] = dayUtc.split("-").map(Number) as [number, number, number];
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (creditsUsed <= 0) return { projected: 0, runOutDay: null, daysInMonth };
  const perDay = creditsUsed / day;
  const projected = Math.round(perDay * daysInMonth);
  const runOut = Math.ceil(included / perDay);
  return { projected, runOutDay: projected > included && runOut <= daysInMonth ? Math.max(runOut, day) : null, daysInMonth };
}

/** Time left before the next UTC midnight, when the daily quota starts again. */
export function untilReset(now: Date): { hours: number; minutes: number } {
  const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  const minutes = Math.max(0, Math.ceil((next - now.getTime()) / 60_000));
  return { hours: Math.floor(minutes / 60), minutes: minutes % 60 };
}

/** The tallest bar's value, never below the quota line so the line stays inside the chart. */
export function chartMax(days: UsageDay[], metric: "credits" | "actions", line: number | null): number {
  const tallest = days.reduce((max, row) => Math.max(max, row[metric] ?? 0), 0);
  return Math.max(tallest, metric === "actions" ? (line ?? 0) : 0, 1);
}
