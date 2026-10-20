/** Read-only client for `GET /client/v1/dashboard` (session decides the client; no id is sent). */

import { ClientApiError } from "../api";
import type { ClientDashboard } from "../../dashboard/types";

export async function clientDashboard(range: { from?: string; to?: string } = {}): Promise<ClientDashboard> {
  const query = new URLSearchParams();
  if (range.from) query.set("from", range.from);
  if (range.to) query.set("to", range.to);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  let response: Response;
  try {
    response = await fetch(`/client/v1/dashboard${suffix}`, { credentials: "include" });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) throw new ClientApiError(response.status, response.statusText);
  return (await response.json()) as ClientDashboard;
}
