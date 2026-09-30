/** Thin client for `/admin/v1/auth/*` (see src/sales_app/api/admin.py). The session lives in an
 *  httpOnly cookie the browser manages on its own: this module never reads or stores it, and
 *  never touches the password beyond the one POST body that carries it to the server. */

const BASE = "/admin/v1";

export type AdminRole = "owner" | "operator" | "editor" | "viewer";

export interface AdminSession {
  username: string;
  role: AdminRole;
  client_ids: string[];
}

export class AdminApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include", // send/receive the session cookie even if this ever becomes cross-origin
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new AdminApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    throw new AdminApiError(response.status, await errorDetail(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string") {
      return (body as { detail: string }).detail;
    }
  } catch {
    /* not JSON, or no `detail` field: fall through to the generic message below */
  }
  return response.statusText || `request failed (${response.status})`;
}

export function adminMe(): Promise<AdminSession> {
  return request<AdminSession>("/auth/me");
}

export function adminLogin(username: string, password: string): Promise<AdminSession> {
  return request<AdminSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function adminLogout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

export type ClientStatus = "active" | "disabled";

export interface ClientProviders {
  sheets: string | null;
  llm: string | null;
  whatsapp: string | null;
}

export interface ClientSummary {
  client_id: string;
  display_name: string;
  status: ClientStatus;
  mode: string | null;
  timezone: string | null;
  daily_action_limit: number | null;
  providers: ClientProviders;
}

export interface ClientSheets {
  provider: string | null;
  spreadsheet_id: string | null;
  prospects_tab: string | null;
  actions_tab: string | null;
  messages_tab: string | null;
  log_tab: string | null;
  credentials_env: string | null;
}

export interface ClientLLM {
  provider: string | null;
  model: string | null;
  api_key_env: string | null;
}

export interface ClientWhatsApp {
  provider: string | null;
  api_version: string | null;
  token_env: string | null;
  phone_number_id_env: string | null;
  app_secret_env: string | null;
}

export interface ClientBrain {
  factory: string | null;
  settings_version: number | null;
}

export interface ClientSchedule {
  daily_at: string | null;
  days: string[];
  prepare_every_minutes: number | null;
  dispatch_every_minutes: number | null;
  recover_every_minutes: number | null;
}

export interface ClientDetail extends ClientSummary {
  max_attempts: number | null;
  opt_out_keywords: string[];
  sheets: ClientSheets;
  llm: ClientLLM;
  whatsapp: ClientWhatsApp;
  brain: ClientBrain;
  schedule: ClientSchedule;
}

export function adminListClients(): Promise<ClientSummary[]> {
  return request<ClientSummary[]>("/clients");
}

export function adminGetClient(clientId: string): Promise<ClientDetail> {
  return request<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`);
}

// `agent_id === client_id` today: there is exactly one Brain per client, presented here as "the
// client's agent" (see src/sales_app/api/admin_agents.py). Both fields are kept on the response
// so a future move to several agents per client only has to stop treating them as equal.
export type AgentStatus = "active" | "disabled";

export interface AgentBrain {
  factory: string | null;
  settings_version: number | null;
}

export interface AgentLLM {
  provider: string | null;
  model: string | null;
}

export interface AgentSummary {
  agent_id: string;
  client_id: string;
  display_name: string;
  status: AgentStatus;
  mode: string | null;
  brain: AgentBrain;
  llm: AgentLLM;
}

export interface AgentChannels {
  sheets: string | null;
  whatsapp: string | null;
}

export interface AgentSchedule {
  daily_at: string | null;
  days: string[];
  prepare_every_minutes: number | null;
  dispatch_every_minutes: number | null;
  recover_every_minutes: number | null;
}

export interface AgentDetail extends AgentSummary {
  timezone: string | null;
  daily_action_limit: number | null;
  max_attempts: number | null;
  channels: AgentChannels;
  schedule: AgentSchedule;
}

export function adminListAgents(): Promise<AgentSummary[]> {
  return request<AgentSummary[]>("/agents");
}

export function adminGetAgent(agentId: string): Promise<AgentDetail> {
  return request<AgentDetail>(`/agents/${encodeURIComponent(agentId)}`);
}
