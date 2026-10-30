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
    const {
      message,
      client_ids: clientIds,
      problems,
    } = detail as { message?: unknown; client_ids?: unknown; problems?: unknown };
    if (typeof message === "string") {
      const clients = Array.isArray(clientIds) ? clientIds.filter((id): id is string => typeof id === "string") : [];
      // `problems`: fixed sentences that say what failed (e.g. which login of a mailbox).
      const why = Array.isArray(problems) ? problems.filter((p): p is string => typeof p === "string") : [];
      const withWhy = why.length > 0 ? `${message}: ${why.join(" ")}` : message;
      return clients.length > 0 ? `${withWhy} (clients: ${clients.join(", ")})` : withWhy;
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
  /** Both or neither: the model is assigned when the Admin has one to give. */
  llm_provider?: string;
  llm_model?: string;
  status: "active" | "inactive";
  whatsapp_number?: string;
  /** Optional: the (active) plan the client starts on. */
  plan_id?: string | null;
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

// A client's Telegram bot (owner only). Connect and disconnect tell Telegram where to deliver; test is a
// real, read-only question to Telegram. None of them sends a message to a prospect.
export function adminConnectClientTelegram(clientId: string): Promise<ConnectionTest> {
  return request<ConnectionTest>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/connect`, {
    method: "POST",
  });
}

export function adminTestClientTelegram(clientId: string): Promise<ConnectionTest> {
  return request<ConnectionTest>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/test`, {
    method: "POST",
  });
}

export function adminDisconnectClientTelegram(clientId: string): Promise<ConnectionTest> {
  return request<ConnectionTest>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/disconnect`, {
    method: "POST",
  });
}

// A client's own Telegram account (the client signs in itself; the Admin sees only where it stands).
export interface TelegramAccountAdmin {
  platform_configured: boolean;
  platform_missing: string[];
  state: "not_connected" | "connected" | "reauth_required" | "unavailable";
  limited: boolean;
  message: string;
}

export function adminClientTelegramAccount(clientId: string): Promise<TelegramAccountAdmin> {
  return request<TelegramAccountAdmin>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/account`);
}

export function adminDisconnectClientTelegramAccount(clientId: string): Promise<TelegramAccountAdmin> {
  return request<TelegramAccountAdmin>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/account/disconnect`, {
    method: "POST",
  });
}

export function adminClearClientTelegramLimit(clientId: string): Promise<TelegramAccountAdmin> {
  return request<TelegramAccountAdmin>(`/clients/${encodeURIComponent(clientId)}/integrations/telegram/account/clear-limit`, {
    method: "POST",
  });
}

export function adminTestOpenAI(): Promise<ConnectionTest> {
  return request<ConnectionTest>("/settings/integrations/openai/test", { method: "POST" });
}

// Reaches the platform mail servers over an encrypted connection and closes it: no login, no message.
export function adminTestEmailServers(): Promise<ConnectionTest> {
  return request<ConnectionTest>("/settings/integrations/email/test", { method: "POST" });
}

// Logs in to one server with the stored username and password: no message is sent, none is read.
export function adminTestEmailSmtp(): Promise<ConnectionTest> {
  return request<ConnectionTest>("/settings/integrations/email/test-smtp", { method: "POST" });
}

export function adminTestEmailImap(): Promise<ConnectionTest> {
  return request<ConnectionTest>("/settings/integrations/email/test-imap", { method: "POST" });
}

// Where the platform's mail servers stand. `state` is "ready" only when both logins were accepted
// with the settings as they are now.
export interface EmailServiceStatus {
  state: "missing_configuration" | "server_not_authenticated" | "ready";
  smtp: "not_configured" | "not_tested" | "authenticated" | "failed";
  imap: "not_configured" | "not_tested" | "authenticated" | "failed";
  missing: string[];
  smtp_problem: string | null; // why SMTP is not authenticated, as a fixed sentence
  imap_problem: string | null;
  /** Clients created from now on are given Email access. Existing clients are never changed. */
  new_clients_access: boolean;
  /** Clients that may use Email and have no mailbox assigned yet (an administrator assigns it). */
  clients_awaiting_mailbox: string[];
}

// The platform's Google application for the Gmail mailbox mode. Config-only: "configured" is not
// "verified" (Google accepts the application only after a real consent, and verifies it for others).
export interface GmailPlatformStatus {
  configured: boolean;
  missing: string[];
  redirect_uri: string | null; // to copy into the Google Cloud OAuth client; not a secret
  sheets_redirect_uri?: string | null; // the "Connect Google Sheets" callback, registered on the same client
  verified: boolean;
  note: string;
}

export function adminGmailPlatformStatus(): Promise<GmailPlatformStatus> {
  return request<GmailPlatformStatus>("/settings/integrations/gmail/status");
}

// The platform's HubSpot application, which the clients connect their own account with. Config-only:
// "configured" is not "verified" (HubSpot accepts the application only after a real consent).
export interface HubSpotPlatformStatus {
  configured: boolean;
  missing: string[];
  redirect_uri: string | null; // to register in the HubSpot developer account; not a secret
  scopes: string[]; // the permissions the HubSpot app must declare
  verified: boolean;
  note: string;
}

export function adminHubSpotPlatformStatus(): Promise<HubSpotPlatformStatus> {
  return request<HubSpotPlatformStatus>("/settings/integrations/hubspot/status");
}

// The platform's Telegram application (my.telegram.org), which the clients sign in to their own account
// with. Config-only: "configured" is not "verified" (Telegram accepts it only once a client has signed in).
export interface TelegramPlatformStatus {
  configured: boolean;
  missing: string[];
  note: string;
}

export function adminTelegramPlatformStatus(): Promise<TelegramPlatformStatus> {
  return request<TelegramPlatformStatus>("/settings/integrations/telegram-account/status");
}

// A client's sources, from the Admin's side.
export interface ClientSourceSettings {
  provider: string;
  configured: boolean;
  settings: Record<string, unknown> | null;
}

export function adminGetClientSource(clientId: string): Promise<ClientSourceSettings> {
  return request<ClientSourceSettings>(`/clients/${encodeURIComponent(clientId)}/source`);
}

export function adminSaveClientSource(clientId: string, settings: Record<string, unknown>): Promise<ClientSourceSettings> {
  return request<ClientSourceSettings>(`/clients/${encodeURIComponent(clientId)}/source`, { method: "PUT", body: JSON.stringify(settings) });
}

export interface HubSpotHealth {
  client_id: string;
  uses_hubspot: boolean;
  can_resume: boolean;
  connection: {
    state: "not_connected" | "connected" | "reauth_required" | "unavailable";
    platform_configured: boolean;
    message: string;
    action: string;
    steps: string[];
    client_path: string | null;
  } | null;
  statuses: { configured: boolean; status_property: string | null; values: Record<string, string[]>; problem: string | null } | null;
  writeback: {
    ledger_available: boolean;
    applied: number;
    waiting: number;
    failed: number;
    overruled: number;
    problems: Array<{ code: string; message: string; action: string }>;
  } | null;
}

export function adminHubSpotHealth(clientId: string): Promise<HubSpotHealth> {
  return request<HubSpotHealth>(`/clients/${encodeURIComponent(clientId)}/hubspot/health`);
}

export function adminCheckHubSpotMapping(clientId: string, settings: Record<string, unknown>): Promise<{ ok: boolean; problems: string[] }> {
  return request(`/clients/${encodeURIComponent(clientId)}/hubspot/status-mapping/check`, { method: "POST", body: JSON.stringify(settings) });
}

export function adminResumeHubSpotSync(clientId: string): Promise<{ ran: boolean; message: string; counters: Record<string, number>; problems: string[] }> {
  return request(`/clients/${encodeURIComponent(clientId)}/hubspot/resume-sync`, { method: "POST" });
}

export function adminAdoptLanes(clientId: string, confirmNewIds = false): Promise<{ lane: string; source: string; channel: string; version: number }> {
  return request(`/clients/${encodeURIComponent(clientId)}/lanes/adopt`, { method: "POST", body: JSON.stringify({ confirm_new_ids: confirmNewIds }) });
}

export function adminEmailServiceStatus(): Promise<EmailServiceStatus> {
  return request<EmailServiceStatus>("/settings/integrations/email/status");
}

// Turn Email access on (or off) for the clients created from now on. Needs a "ready" service.
export function adminSetEmailForNewClients(enabled: boolean): Promise<EmailServiceStatus> {
  return request<EmailServiceStatus>("/settings/integrations/email/new-clients", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

// One client's assigned mailbox. No address, no password: only where it stands.
export interface EmailMailboxStatus {
  state: "missing_configuration" | "mailbox_not_assigned" | "password_missing" | "not_authenticated" | "ready";
  smtp: "not_tested" | "authenticated" | "failed" | null;
  imap: "not_tested" | "authenticated" | "failed" | null;
  problem: string | null;
  smtp_problem: string | null;
  imap_problem: string | null;
}

const clientEmail = (clientId: string, tail: string) =>
  `/clients/${encodeURIComponent(clientId)}/integrations/email/${tail}`;

export function adminClientMailboxStatus(clientId: string): Promise<EmailMailboxStatus> {
  return request<EmailMailboxStatus>(clientEmail(clientId, "status"));
}

export function adminTestClientMailbox(clientId: string): Promise<ConnectionTest> {
  return request<ConnectionTest>(clientEmail(clientId, "test"), { method: "POST" });
}

// A client's request to have its e-mail activated, which the Admin answers by hand. `sender_name` is
// the name the client confirmed (not an address); `mailbox` says where the mailbox stands. No
// address and no password ever comes back.
export type EmailActivationStatus = "pending" | "in_progress" | "rejected" | "active";

export interface EmailActivation {
  client_id: string;
  client_name: string | null;
  sender_name: string | null;
  status: EmailActivationStatus;
  requested_at: string;
  updated_at: string;
  updated_by: string;
  reason: string | null;
  email_allowed: boolean;
  mailbox: EmailMailboxStatus["state"];
}

const activationPath = (clientId: string, tail = "") =>
  `/clients/${encodeURIComponent(clientId)}/email-activation${tail}`;

/** The requests that wait for the Admin (`open`, the default) or every request (`all`). */
export function adminListEmailActivations(scope: "open" | "all" = "open"): Promise<EmailActivation[]> {
  return request<EmailActivation[]>(`/email-activations?status=${scope}`);
}

export function adminGetEmailActivation(clientId: string): Promise<EmailActivation | null> {
  return request<EmailActivation | null>(activationPath(clientId));
}

export function adminStartEmailActivation(clientId: string): Promise<EmailActivation> {
  return request<EmailActivation>(activationPath(clientId, "/start"), { method: "POST" });
}

export function adminRejectEmailActivation(clientId: string, reason: string): Promise<EmailActivation> {
  return request<EmailActivation>(activationPath(clientId, "/reject"), {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function adminDeactivateEmailActivation(clientId: string, reason: string): Promise<EmailActivation> {
  return request<EmailActivation>(activationPath(clientId, "/deactivate"), {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

/** Both logins are tried first; a failure answers an error that says which one and stores nothing. */
export function adminActivateEmail(clientId: string, address: string, password: string): Promise<EmailActivation> {
  return request<EmailActivation>(activationPath(clientId, "/activate"), {
    method: "POST",
    body: JSON.stringify({ address, password }),
  });
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

// ---- commercial plans (src/sales_app/api/admin_plans.py) ------------------------------------------
// Amounts are decimal strings, never floats. Reading is open to every admin; creating, changing,
// deactivating and assigning need an owner. A plan is deactivated, never deleted.

export interface Plan {
  plan_id: string;
  name: string;
  monthly_price: string;
  currency: string;
  included_credits: number;
  daily_quota: number;
  active: boolean;
  created_at: string;
  updated_at: string;
  updated_by: string;
  /** Clients currently on this plan, among those the signed-in admin can reach. */
  client_count: number;
}

export interface PlanInput {
  name: string;
  monthly_price: string;
  currency: string;
  included_credits: number;
  daily_quota: number;
  active: boolean;
}

/** The terms as they were when the client was put on the plan, not as the plan is today. */
export interface PlanAssignment {
  plan_id: string;
  plan_name: string;
  monthly_price: string;
  currency: string;
  included_credits: number;
  daily_quota: number;
  started_at: string;
  ended_at: string | null;
  assigned_by: string;
}

export interface ClientPlan {
  client_id: string;
  current: PlanAssignment | null;
  history: PlanAssignment[];
}

export async function adminListPlans(): Promise<Plan[]> {
  return (await request<{ plans: Plan[] }>("/plans")).plans;
}

export function adminCreatePlan(body: PlanInput): Promise<Plan> {
  return request<Plan>("/plans", { method: "POST", body: JSON.stringify(body) });
}

export function adminUpdatePlan(planId: string, body: PlanInput): Promise<Plan> {
  return request<Plan>(`/plans/${encodeURIComponent(planId)}`, { method: "PUT", body: JSON.stringify(body) });
}

export function adminGetClientPlan(clientId: string): Promise<ClientPlan> {
  return request<ClientPlan>(`/clients/${encodeURIComponent(clientId)}/plan`);
}

/** `planId: null` takes the client off any plan. */
export function adminSetClientPlan(clientId: string, planId: string | null): Promise<ClientPlan> {
  return request<ClientPlan>(`/clients/${encodeURIComponent(clientId)}/plan`, {
    method: "PUT",
    body: JSON.stringify({ plan_id: planId }),
  });
}

// ---- a client's daily e-mail cap (src/sales_app/api/admin_send_cap.py) --------------------------------
// The cap is the Admin's alone (owner to set or remove); there is no default and no "unlimited".
// The effective cap is the smaller of the cap and the daily quota of the client's current plan.

export type SendCapStatus = "ok" | "not_configured" | "limit_reached";

export interface SendCap {
  client_id: string;
  channel: string;
  daily_cap: number | null;
  plan_daily_quota: number | null;
  effective_cap: number | null;
  used_today: number | null;
  remaining_today: number | null;
  day_utc: string;
  status: SendCapStatus;
  max_cap: number;
}

export function adminGetSendCap(clientId: string): Promise<SendCap> {
  return request<SendCap>(`/clients/${encodeURIComponent(clientId)}/send-cap`);
}

export function adminSetSendCap(clientId: string, dailyCap: number): Promise<SendCap> {
  return request<SendCap>(`/clients/${encodeURIComponent(clientId)}/send-cap`, {
    method: "PUT",
    body: JSON.stringify({ daily_cap: dailyCap }),
  });
}

export function adminRemoveSendCap(clientId: string): Promise<SendCap> {
  return request<SendCap>(`/clients/${encodeURIComponent(clientId)}/send-cap`, { method: "DELETE" });
}

// ---- monthly invoices (src/sales_app/api/admin_billing.py) ----------------------------------------
// Amounts are decimal strings. An invoice carries its own copy of the plan terms it was built from,
// so it never changes when a plan does. PAID / FOR_FREE / DISCOUNTED_100 are the Admin's declaration (once,
// owner only): no payment is collected or checked, and only PAID counts as collected.

export type InvoiceStatus = "PENDING" | "PAID" | "FOR_FREE" | "DISCOUNTED_100" | "FAILED";
/** How an invoice was paid: declared by an Admin, or confirmed by a verified payment notification. */
export type PaymentOrigin = "manual" | "electronic";

export interface InvoiceLine {
  plan_id: string;
  plan_name: string;
  monthly_price: string;
  currency: string;
  included_credits: number;
  daily_quota: number;
  amount: string;
}

export interface Invoice {
  invoice_id: number;
  number: string;
  client_id: string;
  /** The billing cycle date, YYYY-MM-DD (an older monthly invoice: YYYY-MM). */
  period: string;
  /** The invoice date. */
  issued_on?: string;
  currency: string;
  amount: string;
  status: InvoiceStatus;
  lines: InvoiceLine[];
  created_at: string;
  created_by: string;
  status_changed_at: string | null;
  status_changed_by: string | null;
  payment_origin?: PaymentOrigin | null;
  payment_transaction_id?: string | null;
  payment_event_id?: string | null;
  /** `amount` is the billed amount before any discount. */
  discount_percent?: number;
  amount_after_discount?: string | null;
  discount_amount?: string | null;
  /** Given free (FOR_FREE and DISCOUNTED_100). */
  free_amount?: string | null;
  discount_reason?: string | null;
  /** Money received: only PAID. */
  collected?: boolean;
  /** Computed by the server: unpaid (PENDING/FAILED) 15 days after the invoice date. */
  due_at?: string;
  overdue?: boolean;
}

export interface InvoiceFilters {
  client_id?: string;
  /** One month (YYYY-MM) or one cycle date (YYYY-MM-DD); takes the place of the range. */
  period?: string;
  period_from?: string;
  period_to?: string;
  status?: InvoiceStatus;
}

export interface GenerateInvoicesResult {
  /** The invoices due at this moment were issued. */
  as_of: string;
  created: Invoice[];
  already_invoiced: number;
  skipped: { client_id: string; reason: string }[];
}

export async function adminListInvoices(filters: InvoiceFilters = {}): Promise<Invoice[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) query.set(key, value);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return (await request<{ invoices: Invoice[] }>(`/billing/invoices${suffix}`)).invoices;
}

/** Issues every invoice that is due now and not issued yet (never a duplicate). */
export function adminGenerateInvoices(): Promise<GenerateInvoicesResult> {
  return request<GenerateInvoicesResult>("/billing/invoices/generate", { method: "POST", body: JSON.stringify({}) });
}

export function adminSettleInvoice(invoiceId: number, status: "PAID" | "FOR_FREE" | "DISCOUNTED_100", reason?: string): Promise<Invoice> {
  return request<Invoice>(`/billing/invoices/${invoiceId}/status`, {
    method: "PUT",
    body: JSON.stringify(reason ? { status, reason } : { status }),
  });
}

/** The aggregated dashboard (`GET /admin/v1/dashboard`): real figures only, each with its availability. */
export function adminDashboard(filters: { from?: string; to?: string; client_id?: string } = {}): Promise<import("../dashboard/types").AdminDashboard> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) query.set(key, value);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return request(`/dashboard${suffix}`);
}

const sharingPath = (clientId: string) =>
  `/clients/${encodeURIComponent(clientId)}/email-activation/credentials-sharing`;

/** Whether this client may read the login of its mailbox (the owner's decision; off by default). */
export function adminGetCredentialsSharing(clientId: string): Promise<{ client_id: string; shared: boolean }> {
  return request(sharingPath(clientId));
}

export function adminSetCredentialsSharing(clientId: string, shared: boolean): Promise<{ client_id: string; shared: boolean }> {
  return request(sharingPath(clientId), { method: "PUT", body: JSON.stringify({ shared }) });
}
