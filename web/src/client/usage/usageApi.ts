/** Read-only client for `GET /client/v1/usage` (session decides the client; no id is sent). */

import { appFetch } from "../../demo/transport";

import { ClientApiError } from "../api";

export interface UsageDay {
  day: string;
  actions: number;
  /** null: the model register could not be read. */
  credits: number | null;
}

export interface ClientUsage {
  plan_name: string | null;
  day_utc: string;
  month: string;
  /** Daily and monthly quota of commercial actions. null limit: not set yet. */
  daily_limit: number | null;
  daily_used: number | null;
  daily_remaining: number | null;
  daily_percent: number | null;
  month_limit: number | null;
  month_actions_used: number | null;
  month_percent: number | null;
  /** Credits included in the plan for the month, and what this month has consumed. */
  included_credits: number | null;
  credits_used: number | null;
  credits_today: number | null;
  credits_remaining: number | null;
  credits_percent: number | null;
  credits_basis: "measured" | "unavailable";
  calls: number | null;
  calls_without_figures: number | null;
  state: "ok" | "warning" | "reached" | "not_configured" | "unavailable";
  message: string;
  action: string;
  days: UsageDay[];
}

export async function clientUsage(): Promise<ClientUsage> {
  let response: Response;
  try {
    response = await appFetch("/client/v1/usage", { credentials: "include" });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) throw new ClientApiError(response.status, response.statusText);
  return (await response.json()) as ClientUsage;
}
