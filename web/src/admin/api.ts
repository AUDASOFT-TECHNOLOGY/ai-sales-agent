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

/**
 * What the server said went wrong. `detail` is a sentence, or, when a change is refused because
 * clients use something (a model in use: 409), an object `{ message, client_ids }`: the message is
 * shown with the clients it names, so the Admin knows who is affected.
 */
export function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const { message, client_ids: clientIds } = detail as { message?: unknown; client_ids?: unknown };
    if (typeof message === "string") {
      const clients = Array.isArray(clientIds) ? clientIds.filter((id): id is string => typeof id === "string") : [];
      return clients.length > 0 ? `${message} (clients: ${clients.join(", ")})` : message;
    }
  }
  return null;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const text = body && typeof body === "object" ? detailText((body as { detail?: unknown }).detail) : null;
    if (text !== null) return text;
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

// What the registry already reports for an active client (null for a disabled one: no runtime).
export interface ClientHealth {
  status: string; // "ready" | "degraded"
  last_error: string | null;
  last_error_at: string | null;
  consecutive_failures: number;
  last_runs: Record<string, string>;
}

export interface ClientSummary {
  client_id: string;
  display_name: string;
  status: ClientStatus;
  mode: string | null;
  timezone: string | null;
  daily_action_limit: number | null;
  providers: ClientProviders;
  llm_model: string | null;
  health: ClientHealth | null;
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

// The client's account profile (never a password or its hash). `whatsapp_number` is the business
// number the client's sales agent writes from -- NOT Meta's phone number id.
export interface ClientAccount {
  login: string;
  whatsapp_number: string | null;
  logo: string | null;
  must_change_password: boolean;
}

// Where the client's number stands with Meta. Never "connected": no official Meta onboarding exists
// in Revansa yet. Booleans only -- never a token or a Meta id.
export interface ClientMeta {
  status: "not_connected" | "credentials_stored";
  credentials_stored: boolean;
}

export interface ClientDetail extends ClientSummary {
  restart_required?: boolean; // the stored status differs from what is running until the next start
  account: ClientAccount | null;
  meta: ClientMeta;
  revenue: number | null; // null: no revenue data source exists yet
  prospects_contacted: number | null; // null: not readable right now
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

export interface CreateClientRequest {
  display_name: string;
  logo: string | null;
  login: string;
  temporary_password: string;
  llm_provider: string;
  llm_model: string;
  status: "active" | "inactive";
  whatsapp_number: string;
}

// POST /admin/v1/clients (owner only). The WhatsApp number is profile data; it grants no Meta access.
export function adminCreateClient(body: CreateClientRequest): Promise<ClientSummary> {
  return request<ClientSummary>("/clients", { method: "POST", body: JSON.stringify(body) });
}

// A partial edit: only the fields present are changed. `logo: null` removes the logo; an absent
// `new_password` keeps the current one. There is no field for any Meta credential.
export interface UpdateClientRequest {
  display_name?: string;
  logo?: string | null;
  login?: string;
  new_password?: string;
  whatsapp_number?: string;
  llm_provider?: string;
  llm_model?: string;
  daily_action_limit?: number;
  status?: "active" | "inactive";
}

// PATCH /admin/v1/clients/:clientId (owner only). Returns the refreshed client detail.
export function adminUpdateClient(clientId: string, body: UpdateClientRequest): Promise<ClientDetail> {
  return request<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export interface UpdateClientLLMRequest {
  provider: string;
  model: string;
}

// PUT /admin/v1/clients/:clientId/llm (see src/sales_app/api/admin_clients.py). Requires an owner:
// only an owner assigns a model; any other role gets 403. Never send an API key here -- there is
// no field for one.
export function adminUpdateClientLLM(clientId: string, body: UpdateClientLLMRequest): Promise<ClientLLM> {
  return request<ClientLLM>(`/clients/${encodeURIComponent(clientId)}/llm`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
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

// Platform-level LLM configuration (see src/sales_app/api/admin_settings.py). Read-only: this
// reports which providers are wired and which models each is allowed, but changing a *client's*
// provider/model is done per client (see `adminUpdateClientLLM`). None of this ever carries a
// secret value -- only provider names and allow-listed model ids.
export interface LLMProviderSetting {
  provider: string;
  wired: boolean;
  // null: no allow-list is configured, so any model is accepted for this provider. An array:
  // only these model ids are allowed (empty means this provider is wired but not allow-listed).
  allowed_models: string[] | null;
  // The readable names Settings gives to some of those ids.
  models?: { id: string; name: string }[];
}

export interface LLMSettings {
  read_only: boolean;
  restricted: boolean;
  providers: LLMProviderSetting[];
}

export function adminGetLLMSettings(): Promise<LLMSettings> {
  return request<LLMSettings>("/settings/llm");
}

// Credentials and settings for the services Revansa talks to (see
// src/sales_app/api/admin_integrations.py). Write-only: a sensitive credential reports only
// whether it is configured and when it changed -- its value is never returned. A non-sensitive one
// (a model id, an API version, a phone number id) comes back in `value`. Writes are owner-only.
export interface Credential {
  key: string;
  label: string;
  scope: "global" | "client";
  sensitive: boolean;
  configured: boolean;
  value: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

// The outcome of a real call to OpenAI or Meta; it never carries a credential.
export interface ConnectionTest {
  ok: boolean;
  message: string;
  details: Record<string, string>;
}

const globalPath = (key: string) => `/settings/integrations/${encodeURIComponent(key)}`;
const clientPath = (clientId: string, key: string) =>
  `/clients/${encodeURIComponent(clientId)}/integrations/${encodeURIComponent(key)}`;

export function adminListGlobalCredentials(): Promise<Credential[]> {
  return request<Credential[]>("/settings/integrations");
}

export function adminSaveGlobalCredential(key: string, value: string): Promise<Credential> {
  return request<Credential>(globalPath(key), { method: "PUT", body: JSON.stringify({ value }) });
}

export function adminRemoveGlobalCredential(key: string): Promise<Credential> {
  return request<Credential>(`${globalPath(key)}?confirm=true`, { method: "DELETE" });
}

export function adminListClientCredentials(clientId: string): Promise<Credential[]> {
  return request<Credential[]>(`/clients/${encodeURIComponent(clientId)}/integrations`);
}

export function adminSaveClientCredential(clientId: string, key: string, value: string): Promise<Credential> {
  return request<Credential>(clientPath(clientId, key), { method: "PUT", body: JSON.stringify({ value }) });
}

export function adminRemoveClientCredential(clientId: string, key: string): Promise<Credential> {
  return request<Credential>(`${clientPath(clientId, key)}?confirm=true`, { method: "DELETE" });
}

export function adminTestOpenAI(): Promise<ConnectionTest> {
  return request<ConnectionTest>("/settings/integrations/openai/test", { method: "POST" });
}

export function adminTestClientWhatsApp(clientId: string): Promise<ConnectionTest> {
  return request<ConnectionTest>(`/clients/${encodeURIComponent(clientId)}/integrations/whatsapp/test`, {
    method: "POST",
  });
}

// The OpenAI models Settings offers to clients (see src/sales_app/api/admin_settings.py). Every
// change answers with the full, updated list. Changing it is owner-only.
export interface OpenAIModel {
  name: string;
  id: string;
  active: boolean;
}

const modelPath = (id: string) => `/settings/openai/models/${encodeURIComponent(id)}`;

export async function adminListOpenAIModels(): Promise<OpenAIModel[]> {
  return (await request<{ models: OpenAIModel[] }>("/settings/openai/models")).models;
}

export async function adminAddOpenAIModel(model: OpenAIModel): Promise<OpenAIModel[]> {
  return (await request<{ models: OpenAIModel[] }>("/settings/openai/models", { method: "POST", body: JSON.stringify(model) })).models;
}

export async function adminUpdateOpenAIModel(currentId: string, model: OpenAIModel): Promise<OpenAIModel[]> {
  return (await request<{ models: OpenAIModel[] }>(modelPath(currentId), { method: "PUT", body: JSON.stringify(model) })).models;
}

export async function adminDeleteOpenAIModel(id: string): Promise<OpenAIModel[]> {
  return (await request<{ models: OpenAIModel[] }>(modelPath(id), { method: "DELETE" })).models;
}
