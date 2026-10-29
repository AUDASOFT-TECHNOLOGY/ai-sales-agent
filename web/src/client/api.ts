/** Thin client for `/client/v1/*` (see src/sales_app/api/client.py). The session lives in an
 *  httpOnly cookie the browser manages on its own: this module never reads or stores it, and
 *  never sends a client id -- the server knows who is logged in. Passwords only ever travel in the
 *  one POST body that carries them, and no response contains one. */

import { appFetch } from "../demo/transport";
import { isDemo } from "../demo/demoMode";

const BASE = "/client/v1";

export interface ClientProfile {
  name: string;
  login: string;
  whatsapp_number: string | null;
  status: "active" | "inactive";
  must_change_password: boolean;
  /** True after the number was changed: the new one is not connected to WhatsApp yet. */
  whatsapp_reconnect_required: boolean;
}

/** The only fields a client may edit about itself. Status is deliberately not here. */
export interface ProfileChanges {
  name?: string;
  login?: string;
  whatsapp_number?: string;
}

export class ClientApiError extends Error {
  status: number;
  /** What the server says is still missing, when it refuses an action for that reason (409 on start). */
  reasons: ReadinessCheck[];
  /** A stable code when the server gives one with its refusal (`{code, message}`), e.g. `not_in_human_mode`. */
  code: string | null;

  constructor(status: number, message: string, reasons: ReadinessCheck[] = [], code: string | null = null) {
    super(message);
    this.name = "ClientApiError";
    this.status = status;
    this.reasons = reasons;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await appFetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    const { message, reasons, code } = await errorDetail(response);
    throw new ClientApiError(response.status, message, reasons, code);
  }
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<{ message: string; reasons: ReadinessCheck[]; code: string | null }> {
  const fallback = response.statusText || `request failed (${response.status})`;
  try {
    const body: unknown = await response.json();
    const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
    if (typeof detail === "string") return { message: detail, reasons: [], code: null };
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      // `{message, reasons}`: a refusal that lists what is missing (the agent cannot start yet).
      // `{code, message}`: a refusal with a stable code (a message that could not be sent).
      const { message, reasons, code } = detail as { message?: unknown; reasons?: unknown; code?: unknown };
      return {
        message: typeof message === "string" ? message : fallback,
        reasons: Array.isArray(reasons) ? reasons.filter(isCheck) : [],
        code: typeof code === "string" ? code : null,
      };
    }
    if (Array.isArray(detail)) {
      // A 422 lists every problem: plain sentences, or `{loc, msg}` rules that never repeat the input.
      const lines = detail.flatMap((item): string[] => {
        if (typeof item === "string") return [item];
        if (item && typeof item === "object" && typeof (item as { msg?: unknown }).msg === "string") {
          const loc = (item as { loc?: unknown }).loc;
          const where = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
          return [where ? `${where}: ${(item as { msg: string }).msg}` : (item as { msg: string }).msg];
        }
        return [];
      });
      if (lines.length > 0) return { message: lines.join("; "), reasons: [], code: null };
    }
  } catch {
    /* not JSON, or no `detail` string (e.g. a 422 list): fall through to the generic message */
  }
  return { message: fallback, reasons: [], code: null };
}

function isCheck(value: unknown): value is ReadinessCheck {
  return !!value && typeof value === "object" && typeof (value as { id?: unknown }).id === "string";
}

export function clientMe(): Promise<ClientProfile> {
  return request<ClientProfile>("/auth/me");
}

export function clientLogin(login: string, password: string): Promise<ClientProfile> {
  return request<ClientProfile>("/auth/login", { method: "POST", body: JSON.stringify({ login, password }) });
}

export function clientLogout(): Promise<unknown> {
  return request<unknown>("/auth/logout", { method: "POST" });
}

export function clientChangePassword(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string,
): Promise<ClientProfile> {
  return request<ClientProfile>("/profile/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    }),
  });
}

export function clientUpdateProfile(changes: ProfileChanges): Promise<ClientProfile> {
  return request<ClientProfile>("/profile", { method: "PATCH", body: JSON.stringify(changes) });
}

/* ── Channel (see src/sales_app/api/client_channel.py) ──
 * Nothing technical is the client's: no server, password, token or webhook is read or written here.
 * The platform connects the channel; the client only chooses it and words its messages. */

export interface ChannelOption {
  key: string;
  label: string;
}

/** How the client's e-mails are written. Nothing technical: the mailbox itself is the platform's.
 *  `mode` says whose mailbox it is: "platform" (provided by Revansa; the Sender Name is confirmed by
 *  the client) or "custom" (the historical mailbox; the field is the client's own, as before). */
/** The client's request to have its e-mail activated by the Admin. `reason` is the Admin's, shown
 *  when the request was rejected. Nothing here names a mailbox. */
export type EmailActivationStatus = "pending" | "in_progress" | "rejected" | "active";

export interface EmailActivation {
  status: EmailActivationStatus;
  requested_at: string;
  updated_at: string;
  reason: string | null;
}

export interface EmailSettings {
  mode: "platform" | "custom" | "gmail";
  from_name: string | null;
  /** Platform mode: the client has confirmed `from_name` (saving it is the confirmation). */
  sender_name_confirmed: boolean;
  subject: string | null;
  /** Platform mode: null until the client has asked for the activation. */
  activation?: EmailActivation | null;
  /** Whether emails can leave today against the daily limit Revansa sets (nothing technical). */
  sending?: EmailSending | null;
}

export interface EmailSending {
  state: "ok" | "not_set" | "reached" | "unavailable";
  message: string | null;
  daily_limit: number | null;
  sent_today: number | null;
}

export interface TelegramSettings {
  /** `bot`: the Revansa bot; `account`: the client's own Telegram account. */
  mode: "bot" | "account";
  contact_prompt: string;
  contact_button: string;
  not_found_text: string;
  /** Account mode: at most this many new conversations a day (never more than 5). */
  max_new_conversations_per_day: number;
}

/** Where a channel stands: the platform finishes setting it up, or the client has something to choose. */
export type ChannelStatusName = "ready" | "waiting_platform" | "action_required";

export interface ChannelState {
  version: number | null;
  channel: string;
  authorized: boolean;
  options: ChannelOption[];
  status: ChannelStatusName;
  /** A sentence with nothing technical in it, when the channel is not ready. */
  status_message: string | null;
  email: EmailSettings | null;
  telegram: TelegramSettings | null;
  /** The client's business name, offered as a starting Sender Name. A proposal, not a confirmation. */
  sender_name_proposal: string | null;
  updated_at: string | null;
  updated_by: "client" | "administrator" | null;
}

export interface ChannelUpdate {
  expected_version: number | null;
  channel: string;
  /** `use_gmail`: true to use the client's own Gmail, false to go back to a Revansa mailbox. */
  email?: { from_name?: string; subject?: string; use_gmail?: boolean };
  telegram?: Partial<Record<"contact_prompt" | "contact_button" | "not_found_text", string>> & {
    /** true: write from the client's own Telegram account; false: back to the Revansa bot. */
    use_account?: boolean;
    max_new_conversations_per_day?: number;
  };
}

export interface TelegramInvitations {
  bot_username: string;
  bot_link: string;
  waiting: number;
  invitations: Array<{ prospect_id: string; display_name: string; phone: string | null; email: string | null; link: string | null }>;
}

export function clientChannel(): Promise<ChannelState> {
  return request<ChannelState>("/channel");
}

export function clientSaveChannel(update: ChannelUpdate): Promise<ChannelState> {
  return request<ChannelState>("/channel", { method: "PUT", body: JSON.stringify(update) });
}

/** Ask for the e-mail to be activated (the Sender Name must be confirmed first). Answers the channel. */
export function clientRequestEmailActivation(): Promise<ChannelState> {
  return request<ChannelState>("/channel/email-activation", { method: "POST" });
}

export type GmailStateName = "not_connected" | "connected" | "reauth_required" | "unavailable";

/** Where the client's Gmail connection stands. `address` is the client's own Gmail, shown to it only. */
export interface GmailStatus {
  state: GmailStateName;
  address: string | null;
  send_cap_configured: boolean;
  /** Connected AND the daily sending limit is set: the only state in which mail leaves. */
  send_ready: boolean;
  message: string;
  mailbox_sync: "not_started" | "ok" | "failing";
  last_sync_at: string | null;
}

export function clientGmailStatus(): Promise<GmailStatus> {
  return request<GmailStatus>("/channel/google");
}

/** The Google consent URL: the browser is sent there; the client types nothing. */
export function clientGmailStart(): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/channel/google/start", { method: "POST" });
}

export function clientGmailDisconnect(): Promise<GmailStatus> {
  return request<GmailStatus>("/channel/google/disconnect", { method: "POST" });
}

/** Google Sheets by the client's own Google authorisation (see src/sales_app/api/client_sheets_oauth.py). */
export interface SheetsAccess {
  state: "not_connected" | "connected" | "reauth_required" | "unavailable";
  address: string | null;
  /** The link of the spreadsheet is saved: the client can authorise. */
  spreadsheet_saved: boolean;
  message: string;
}

export function clientSheetsAccess(): Promise<SheetsAccess> {
  return request<SheetsAccess>("/sheets-access");
}

/** The Google consent URL: the browser is sent there; the client types nothing. */
export function clientSheetsAccessStart(): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/sheets-access/start", { method: "POST" });
}

export function clientSheetsAccessDisconnect(): Promise<SheetsAccess> {
  return request<SheetsAccess>("/sheets-access/disconnect", { method: "POST" });
}

/** HubSpot by the client's own HubSpot authorisation (see src/sales_app/api/client_hubspot_oauth.py). */
export interface HubSpotAccess {
  state: "not_connected" | "connected" | "reauth_required" | "unavailable";
  /** The client's own HubSpot account, shown to that client only. */
  account: string | null;
  message: string;
}

export function clientHubSpotAccess(): Promise<HubSpotAccess> {
  return request<HubSpotAccess>("/hubspot-access");
}

/** The HubSpot consent URL: the browser is sent there; the client types nothing. */
export function clientHubSpotAccessStart(): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/hubspot-access/start", { method: "POST" });
}

export function clientHubSpotAccessDisconnect(): Promise<HubSpotAccess> {
  return request<HubSpotAccess>("/hubspot-access/disconnect", { method: "POST" });
}

export function clientTelegramInvitations(): Promise<TelegramInvitations> {
  return request<TelegramInvitations>("/channel/invitations");
}

/** Telegram from the client's own account (see src/sales_app/api/client_channel.py). The client signs in
 *  with Telegram itself; the phone number, the code and the password are sent once and never come back. */
export interface TelegramAccount {
  /** The platform has set Telegram accounts up. */
  available: boolean;
  state: "not_connected" | "connected" | "reauth_required" | "unavailable";
  /** Telegram limited the account for unsolicited messages: no first message is sent, replies go on. */
  limited: boolean;
  /** A sign-in is under way and waits for this. */
  login: "code" | "password" | null;
  new_today: number | null;
  daily_limit: number | null;
}

export function clientTelegramAccount(): Promise<TelegramAccount> {
  return request<TelegramAccount>("/channel/telegram/account");
}

export function clientTelegramLoginStart(phone: string): Promise<TelegramAccount> {
  return request<TelegramAccount>("/channel/telegram/account/start", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
}

export function clientTelegramLoginCode(code: string): Promise<TelegramAccount> {
  return request<TelegramAccount>("/channel/telegram/account/code", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function clientTelegramLoginPassword(password: string): Promise<TelegramAccount> {
  return request<TelegramAccount>("/channel/telegram/account/password", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function clientTelegramLoginCancel(): Promise<TelegramAccount> {
  return request<TelegramAccount>("/channel/telegram/account/cancel", { method: "POST" });
}

export function clientTelegramDisconnect(): Promise<{ disconnected: boolean; ended_at_telegram: boolean }> {
  return request("/channel/telegram/account", { method: "DELETE" });
}

/* ── Approvals (see src/sales_app/api/client_approvals.py) ──
 * What the agent proposes and its client approves or rejects before anything is sent. Which client
 * is asking comes only from the session cookie; an id is only ever one the server listed. */

export interface Proposal {
  id: string;
  /** Null when the prospect is no longer in the source. */
  prospect_name: string | null;
  text: string;
  language: string | null;
  due_at: string;
}

export type ProposalDecision = "approve" | "reject";

export function clientProposals(): Promise<{ proposals: Proposal[] }> {
  return request<{ proposals: Proposal[] }>("/approvals");
}

export function clientDecideProposal(id: string, decision: ProposalDecision): Promise<{ id: string; decision: string }> {
  return request<{ id: string; decision: string }>(`/approvals/${encodeURIComponent(id)}/${decision}`, { method: "POST" });
}

/* ── Lanes (see src/sales_app/api/client_lanes.py) ──
 * A lane = one source + one channel + its products. Every write names the version it was based on;
 * the server validates, answers 409 on a conflict and writes nothing that is not valid. */

/** The products value that means "the whole catalog, including what is added later". */
export const ALL_PRODUCTS = "ALL";

export interface Lane {
  id: string;
  position: number;
  source: {
    id: string;
    source: string;
    label: string;
    google_sheets: { spreadsheet_id: string; prospects_tab: string } | null;
  };
  channel: { channel: string; label: string; authorized: boolean; problem: string | null };
  products: string[];
  offers_all_products: boolean;
}

export interface LanesState {
  version: number | null;
  /** False: the client runs one implicit `default` lane, shown read-only. */
  editable: boolean;
  lanes: Lane[];
  source_options: ChannelOption[];
  /** Only the channels this client may choose. */
  channel_options: ChannelOption[];
  catalog: Array<{ id: string; name: string | null }>;
  /** The platform's address a spreadsheet must be shared with (an identifier, not a secret); null when unknown. */
  share_with: string | null;
}

export interface SheetsChanges {
  spreadsheet?: string;
  prospects_tab?: string;
}

export interface LaneCreate {
  expected_version: number | null;
  id: string;
  channel: string;
  products: string[];
  source: { id: string; provider: string; google_sheets?: SheetsChanges };
}

export interface LaneUpdate {
  expected_version: number | null;
  channel?: string;
  products?: string[];
  google_sheets?: SheetsChanges;
}

export function clientLanes(): Promise<LanesState> {
  return request<LanesState>("/lanes");
}

export function clientCreateLane(body: LaneCreate): Promise<Lane> {
  return request<Lane>("/lanes", { method: "POST", body: JSON.stringify(body) });
}

export function clientUpdateLane(id: string, body: LaneUpdate): Promise<Lane> {
  return request<Lane>(`/lanes/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
}

/* ── The results copied back into a lane's source (see src/sales_app/api/client_lane_sync.py) ──
 * Nothing is copied until the client names, one by one, the existing column or property of each
 * result. Copying is permanent: there is no switch, only the mapping. */

export type SyncResultKey = "status" | "last_contacted_at" | "last_inbound_at" | "follow_up_count" | "next_action_type" | "next_action_due_at" | "summary";

export interface SheetsSync {
  columns: Partial<Record<SyncResultKey, string>>;
  propagate_opt_out?: boolean;
}

export interface HubSpotSync {
  status?: boolean;
  properties?: Partial<Record<Exclude<SyncResultKey, "status">, string>>;
  propagate_opt_out?: boolean;
}

export interface LaneSync {
  version: number | null;
  lane: string;
  source: string;
  supported: boolean;
  state: "off" | "configured";
  message: string;
  results: Array<{ key: SyncResultKey; label: string }>;
  google_sheets: SheetsSync | null;
  hubspot: HubSpotSync | null;
}

export interface LaneSyncUpdate {
  expected_version: number | null;
  google_sheets?: SheetsSync;
  hubspot?: HubSpotSync;
}

export function clientLaneSync(id: string): Promise<LaneSync> {
  return request<LaneSync>(`/lanes/${encodeURIComponent(id)}/sync`);
}

export function clientSaveLaneSync(id: string, body: LaneSyncUpdate): Promise<LaneSync> {
  return request<LaneSync>(`/lanes/${encodeURIComponent(id)}/sync`, { method: "PUT", body: JSON.stringify(body) });
}

/** Answers the lanes that remain. */
export function clientDeleteLane(id: string, expectedVersion: number | null): Promise<LanesState> {
  const query = expectedVersion === null ? "" : `?expected_version=${expectedVersion}`;
  return request<LanesState>(`/lanes/${encodeURIComponent(id)}${query}`, { method: "DELETE" });
}

/* ── Readiness and connection test (client_agent.py, client_source.py) ── */

export interface ReadinessCheck {
  id: string;
  ok: boolean;
  /** What to do next, only when the check is not ok. */
  message: string | null;
}

export interface Readiness {
  ready: boolean;
  checks: ReadinessCheck[];
}

export interface SourceTest {
  source: string;
  ok: boolean;
  message: string;
  details: Record<string, string>;
  checked_at: string;
  /** One result per lane, for a client that runs lanes. */
  lanes: Array<{ lane: string; ok: boolean; message: string }> | null;
}

export interface SourceOption {
  key: string;
  label: string;
  /** False: it can be set up and tested, but the agent cannot run on it yet. */
  runnable: boolean;
}

export interface SourceState {
  version: number | null;
  /** The chosen source's key. */
  source: string;
  runnable: boolean;
  options: SourceOption[];
  google_sheets: { spreadsheet_id: string; prospects_tab: string; share_with: string | null } | null;
  /** Null: nothing to connect. Otherwise whether the platform has connected the CRM account. */
  access: "ready" | "waiting_platform" | null;
  last_test: { ok: boolean; checked_at: string; current: boolean } | null;
  updated_at: string | null;
  updated_by: "client" | "administrator" | null;
}

export interface SourceUpdate {
  expected_version: number | null;
  source: string;
  google_sheets?: SheetsChanges;
}

export function clientReadiness(): Promise<Readiness> {
  return request<Readiness>("/readiness");
}

export function clientTestSources(): Promise<SourceTest> {
  return request<SourceTest>("/source/test", { method: "POST" });
}

export function clientSourceState(): Promise<SourceState> {
  return request<SourceState>("/source");
}

export function clientSaveSource(body: SourceUpdate): Promise<SourceState> {
  return request<SourceState>("/source", { method: "PUT", body: JSON.stringify(body) });
}

/* ── Business settings and the agent's switch (client_agent.py) ──
 * `commercial` is the Brain's own document and is replaced as a whole by a write: a screen edits the
 * parts it knows and sends back everything else unchanged. */

export interface ClientSettings {
  timezone?: string;
  opt_out_keywords?: string[];
  schedule?: { daily_at?: string; days?: string[] };
  commercial?: Record<string, unknown>;
}

export interface SettingsState {
  /** Null: nothing published yet. A write names the version it was based on. */
  version: number | null;
  settings: ClientSettings;
  updated_at: string | null;
  updated_by: "client" | "administrator" | null;
}

export function clientSettings(): Promise<SettingsState> {
  return request<SettingsState>("/settings");
}

export function clientSaveSettings(expectedVersion: number | null, settings: ClientSettings): Promise<SettingsState> {
  return request<SettingsState>("/settings", {
    method: "PUT",
    body: JSON.stringify({ expected_version: expectedVersion, settings }),
  });
}

export interface AgentState {
  /** The client's own switch. */
  state: "not_started" | "running" | "paused";
  /** Works now: started by the client, enabled by the Admin and ready. */
  active: boolean;
  ready: boolean;
  changed_at: string | null;
  reasons: ReadinessCheck[];
}

export function clientAgent(): Promise<AgentState> {
  return request<AgentState>("/agent");
}

export function clientStartAgent(): Promise<AgentState> {
  return request<AgentState>("/agent/start", { method: "POST" });
}

export function clientPauseAgent(): Promise<AgentState> {
  return request<AgentState>("/agent/pause", { method: "POST" });
}

/* ── Prospects and conversations, read-only (see src/sales_app/api/client_prospects.py) ──
 * Only what the platform stores is returned; the server answers for this client alone. */

export interface ProspectRow {
  id: string;
  display_name: string;
  status: "new" | "active" | "paused" | "opted_out" | "closed";
  stage: string | null;
  locale: string | null;
  lane_id: string | null;
  phone: string | null;
  email: string | null;
  preferred_channel: string | null;
  next_action_type: string | null;
  next_action_due_at: string | null;
  last_contacted_at: string | null;
  last_inbound_at: string | null;
  follow_up_count: number;
  /** Added with the read model; absent from older servers. */
  source_id?: string | null;
  last_activity_at?: string | null;
  /** Paused: waiting for a person. */
  needs_human?: boolean;
  status_reason?: string | null;
  /** null: the server could not tell (it listed through the agent). */
  has_error?: boolean | null;
}

/** The narrowing both lists accept (see src/sales_app/api/client_prospects.py). Empty = no narrowing. */
export interface ProspectFilters {
  q?: string;
  status?: ProspectRow["status"][];
  /** The circuit (a lane id). */
  lane?: string;
  product?: string;
  channel?: string;
  source?: string;
  needs_human?: boolean;
  has_error?: boolean;
  /** ISO 8601; last activity in [since, until). */
  active_since?: string;
  active_until?: string;
  sort?: "name" | "recent";
}

export interface ProspectsPage {
  /** All the prospects matching the filters, not just this page. */
  total: number;
  limit: number;
  offset: number;
  prospects: ProspectRow[];
}

export interface ConversationMessage {
  id: string;
  direction: "inbound" | "outbound";
  channel: string;
  text: string;
  timestamp: string;
  /** What WhatsApp reported for a message we sent; absent until it reports something. */
  delivery?: "sent" | "delivered" | "read" | "failed" | null;
  /** Who wrote it; absent for older servers. `human` is a person of the client who took the conversation over. */
  author?: "prospect" | "agent" | "human" | null;
}

/** Who is in charge of a conversation. No row on the server is the agent, in charge since the start. */
export interface ConversationControl {
  mode: "agent" | "human";
  /** The agent handed the conversation over and nobody took it yet. */
  awaiting_human: boolean;
  taken_by: string | null;
  taken_at: string | null;
  reason: string | null;
  released_at: string | null;
}

export interface ConversationHistory {
  prospect_id: string;
  conversation_id: string;
  origin: "outbound" | "inbound" | "unknown";
  /** In the requested `order`; empty when nothing was exchanged yet. */
  messages: ConversationMessage[];
  /** All the messages of the conversation (this page is `messages.length` of them). */
  total_messages?: number | null;
  limit?: number | null;
  offset?: number | null;
  order?: "asc" | "desc" | null;
  prospect?: ProspectRow | null;
  next_follow_up?: { action_type: string; due_at: string } | null;
  errors?: ConversationActionError[];
  /** Absent or null: not known (older server, or the control store could not be read). */
  control?: ConversationControl | null;
}

export interface ConversationActionError {
  action_id: string;
  action_type: string;
  due_at: string | null;
  attempts: number;
  reason: string | null;
}

export interface ConversationListItem {
  prospect: ProspectRow;
  /** null: not known (the server listed through the agent). */
  message_count: number | null;
  last_message_at: string | null;
  last_message_direction: "inbound" | "outbound" | null;
}

export interface ConversationsPage {
  total: number;
  limit: number;
  offset: number;
  conversations: ConversationListItem[];
}

/** The query string of a list: `limit` and `offset` first, then only the filters that are set. */
export function listQuery(limit: number, offset: number, filters: ProspectFilters = {}): string {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  for (const status of filters.status ?? []) params.append("status", status);
  for (const key of ["lane", "product", "channel", "source", "active_since", "active_until", "sort"] as const) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  if (filters.needs_human) params.set("needs_human", "true");
  if (filters.has_error) params.set("has_error", "true");
  return params.toString();
}

/** `limit` is 1..100, `offset` >= 0: the server refuses anything else. */
export function clientProspects(limit: number, offset: number, filters?: ProspectFilters): Promise<ProspectsPage> {
  return request<ProspectsPage>(`/prospects?${listQuery(limit, offset, filters)}`);
}

/** Prospects with at least one message, latest first by default. */
export function clientConversations(limit: number, offset: number, filters?: ProspectFilters): Promise<ConversationsPage> {
  return request<ConversationsPage>(`/conversations?${listQuery(limit, offset, filters)}`);
}

/** Without `page`, the server's default page (the first 200 messages, oldest first). With it, `limit`
 *  (1..500) messages from `offset` in `order`. */
export function clientConversation(
  prospectId: string,
  page?: { limit: number; offset: number; order: "asc" | "desc" },
): Promise<ConversationHistory> {
  const query = page ? `?limit=${page.limit}&offset=${page.offset}&order=${page.order}` : "";
  return request<ConversationHistory>(`/conversations/${encodeURIComponent(prospectId)}${query}`);
}

/* ── A person in charge of a conversation (client_conversation_control.py) ── */

export function clientTakeOver(prospectId: string, reason?: string): Promise<ConversationControl> {
  return request<ConversationControl>(`/conversations/${encodeURIComponent(prospectId)}/take-over`, {
    method: "POST",
    body: JSON.stringify(reason ? { reason } : {}),
  });
}

export interface ReleaseResult {
  /** false: the agent was already in charge. */
  released: boolean;
  control: ConversationControl;
  /** The prospect's status afterwards. */
  status: string | null;
  /** When the agent comes back; null when nothing is scheduled. */
  next_follow_up_at: string | null;
}

export function clientReleaseConversation(prospectId: string): Promise<ReleaseResult> {
  return request<ReleaseResult>(`/conversations/${encodeURIComponent(prospectId)}/release`, { method: "POST" });
}

export interface SentMessage {
  message_id: string;
  channel: string;
  sent_at: string | null;
  /** true: this key had already sent it; nothing new went out. */
  replayed: boolean;
}

/** `idempotencyKey` is chosen by the caller (8..64 of letters, digits, `_ . : -`): the same key never sends twice.
 *  A refusal carries `ClientApiError.code` (not_in_human_mode, channel_not_ready, opted_out, not_now, ...). */
export function clientSendMessage(prospectId: string, text: string, idempotencyKey: string): Promise<SentMessage> {
  return request<SentMessage>(`/conversations/${encodeURIComponent(prospectId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ text, idempotency_key: idempotencyKey }),
  });
}

/* ── Follow-up settings (client_followup.py) ── */

export interface FollowUpWindow {
  /** HH:MM. */
  start: string;
  end: string;
  /** An IANA name. */
  timezone: string;
}

export interface FollowUpSettings {
  version: number | null;
  /** false: the business settings are not written yet, so nothing can be saved. */
  configured: boolean;
  max_followups: number;
  gap_days: number;
  window: FollowUpWindow | null;
  /** The client's own time zone, offered as the window's default. */
  timezone: string | null;
  /** The cadence has uneven waits: saving replaces it by an even one. */
  custom_schedule: boolean;
  defaults: { max_followups: number; gap_days: number };
  bounds: { max_followups: [number, number]; gap_days: [number, number] };
}

export interface FollowUpChange {
  max_followups: number;
  gap_days: number;
  /** null: any hour. `timezone` null: the client's own. */
  window: { start: string; end: string; timezone?: string | null } | null;
}

export function clientFollowUpSettings(): Promise<FollowUpSettings> {
  return request<FollowUpSettings>("/followup-settings");
}

export function clientSaveFollowUpSettings(expectedVersion: number | null, change: FollowUpChange): Promise<FollowUpSettings> {
  return request<FollowUpSettings>("/followup-settings", {
    method: "PUT",
    body: JSON.stringify({ expected_version: expectedVersion, ...change }),
  });
}

/* ── Dashboard summary, read-only (see src/sales_app/api/client_prospects.py) ──
 * Counts the server computed from the client's own prospects. No money, rate or funnel is here
 * because the platform stores none; the agent's own state is `clientAgent()`. */

export interface ClientSummary {
  total_prospects: number;
  /** Every status is present; 0 means the client really has none. */
  by_status: Record<ProspectRow["status"], number>;
  upcoming_actions: {
    /** New or active prospects that have a next action recorded. */
    scheduled: number;
    /** Only the types present. */
    by_type: Record<string, number>;
    /** The earliest due date among them; null when nothing is scheduled. */
    next_due_at: string | null;
  };
}

export function clientSummary(): Promise<ClientSummary> {
  return request<ClientSummary>("/summary");
}

/* ── Daily reporting (client_reporting.py) ── */

export type ReportStatus = "pending" | "sending" | "sent" | "failed" | "undeliverable";

export interface ReportSettings {
  enabled: boolean;
  channel: string;
  recipient: string;
  /** HH:MM wall-clock time in `timezone`. */
  send_time: string;
  /** An explicit IANA name; never assumed from the browser. */
  timezone: string;
  language: string;
  updated_at: string;
}

export interface ReportChannelOption {
  key: string;
  available: boolean;
  /** `not_supported` (WhatsApp) or `not_authorized` (the platform has not opened it). */
  reason: string | null;
}

export interface ReportSummary {
  day: string;
  status: ReportStatus;
  channel: string | null;
  recipient_hint: string | null;
  generated_at: string;
  sent_at: string | null;
  attempts: number;
  next_attempt_at: string | null;
  error_code: string | null;
  problem: string | null;
  period_start: string;
  period_end: string;
  timezone: string;
  messages_sent: number;
  replies_received: number;
}

export interface ReportDetail extends ReportSummary {
  language: string;
  figures: {
    prospects: { total: number; by_status: Record<string, number> };
    activity: {
      messages_sent: number;
      prospects_contacted: number;
      new_contacts: number;
      follow_ups_sent: number;
      answers_sent: number;
      replies_received: number;
      prospects_replied: number;
    };
    follow_ups: { scheduled: number; due_next_24h: number };
    purchase_intents: { tracked: boolean };
    appointments: { tracked: boolean };
    conversions: { tracked: boolean };
    quota: { status: string; cap?: number; used?: number; remaining?: number; day_utc: string } | null;
    actions_required: Array<{ code: string; count?: number }>;
  };
  text: string;
}

export interface ReportingState {
  settings: ReportSettings | null;
  channels: ReportChannelOption[];
  languages: string[];
  latest: ReportSummary | null;
}

export type ReportSettingsBody = Omit<ReportSettings, "updated_at">;

export function clientReporting(): Promise<ReportingState> {
  return request<ReportingState>("/reporting");
}

export function clientSaveReporting(body: ReportSettingsBody): Promise<ReportSettings> {
  return request<ReportSettings>("/reporting/settings", { method: "PUT", body: JSON.stringify(body) });
}

export function clientReports(limit = 30): Promise<{ reports: ReportSummary[] }> {
  return request<{ reports: ReportSummary[] }>(`/reports?limit=${limit}`);
}

export function clientReport(day: string): Promise<ReportDetail> {
  return request<ReportDetail>(`/reports/${encodeURIComponent(day)}`);
}

export function clientGenerateReport(): Promise<ReportDetail> {
  return request<ReportDetail>("/reports/generate", { method: "POST" });
}

export function clientResendReport(day: string): Promise<ReportDetail> {
  return request<ReportDetail>(`/reports/${encodeURIComponent(day)}/resend`, { method: "POST" });
}

/* ── Product resources (see src/sales_app/api/client_resources.py) ──
 * Documents and links that back a product up. A resource is stored per client and per product; the
 * agent can use one only if `status` is "ready" (text was really extracted from it). Anything else
 * is kept for the client but is not read by the agent, and says why. */

export type ResourceCategory = "web" | "pdf" | "spreadsheet" | "document" | "image" | "video" | "testimonial" | "other";
export type ResourceStatus = "ready" | "stored_only" | "failed";

export interface ProductResource {
  id: string;
  product_id: string;
  kind: "link" | "file";
  category: ResourceCategory;
  title: string;
  url: string | null;
  filename: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  status: ResourceStatus;
  /** Why the agent cannot use it (or could not read it), as a fixed code. */
  status_detail: string | null;
  /** How much text the agent can search in it. */
  text_chars: number;
  created_at: string;
}

export interface ResourceLimits {
  max_file_bytes: number;
  max_per_product: number;
  accepted_extensions: string[];
}

export interface ResourceList {
  resources: ProductResource[];
  limits: ResourceLimits;
}

const productPath = (id: string) => `/products/${encodeURIComponent(id)}/resources`;

export function clientResources(productId: string): Promise<ResourceList> {
  return request<ResourceList>(productPath(productId));
}

export function clientAddResourceLink(productId: string, body: { title: string; url: string; category: ResourceCategory }): Promise<ProductResource> {
  return request<ProductResource>(`${productPath(productId)}/link`, { method: "POST", body: JSON.stringify(body) });
}

export function clientDeleteResource(productId: string, resourceId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`${productPath(productId)}/${encodeURIComponent(resourceId)}`, { method: "DELETE" });
}

export function clientRetryResource(productId: string, resourceId: string): Promise<ProductResource> {
  return request<ProductResource>(`${productPath(productId)}/${encodeURIComponent(resourceId)}/retry`, { method: "POST" });
}

/** Text the client writes or pastes (a testimonial, an FAQ, a video's transcript): read like any document. */
export function clientAddResourceNote(productId: string, body: { title: string; text: string; category: "testimonial" | "document" | "other" }): Promise<ProductResource> {
  return request<ProductResource>(`${productPath(productId)}/note`, { method: "POST", body: JSON.stringify(body) });
}

/* ── Training material (see src/sales_app/api/client_training.py) ──
 * What the agent can really read of each product, and draft lessons found in it. */

export interface UnreadResource {
  id: string;
  title: string;
  category: ResourceCategory;
  /** Why it is not read, as a fixed code (video, image, scanned_pdf, fetch_failed, ...). */
  reason: string | null;
}

export interface ProductMaterial {
  product_id: string;
  total: number;
  readable: number;
  readable_chars: number;
  /** Testimonials the agent can read. */
  testimonials: number;
  by_category: Record<string, number>;
  unread: UnreadResource[];
}

export function clientTrainingMaterial(): Promise<{ products: ProductMaterial[] }> {
  return request<{ products: ProductMaterial[] }>("/training/material");
}

export interface LessonSuggestion {
  kind: "faq" | "proof";
  text: string;
  /** The question, for a frequent question. */
  when: string | null;
  /** The document it comes from. */
  source: string;
  attribution: string | null;
}

export function clientTrainingSuggestions(productId: string): Promise<{ suggestions: LessonSuggestion[]; left_out: number }> {
  return request<{ suggestions: LessonSuggestion[]; left_out: number }>(`/training/suggestions/${encodeURIComponent(productId)}`);
}

/** The file travels as the raw request body (no multipart): its name and title ride in the headers. */
export async function clientUploadResource(productId: string, file: File, title: string): Promise<ProductResource> {
  let response: Response;
  try {
    response = await appFetch(`${BASE}${productPath(productId)}/file`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-File-Name": encodeURIComponent(file.name),
        "X-Resource-Title": encodeURIComponent(title),
      },
      body: file,
    });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    const { message, reasons, code } = await errorDetail(response);
    throw new ClientApiError(response.status, message, reasons, code);
  }
  return (await response.json()) as ProductResource;
}

export function resourceDownloadUrl(productId: string, resourceId: string): string {
  // Demo: a link that stays in the browser; the real address would reach the server.
  if (isDemo()) return "data:text/plain;charset=utf-8,Document%20fictif%20de%20d%C3%A9monstration.";
  return `${BASE}${productPath(productId)}/${encodeURIComponent(resourceId)}/download`;
}

/* ── Payments to confirm (see src/sales_app/api/client_payments.py) ── */

export type PaymentProgress = "claimed" | "proof_received" | "paid";

export interface PaymentCase {
  prospect_id: string;
  prospect_name: string | null;
  progress: PaymentProgress;
  /** When the prospect said they paid or sent a receipt, if known. */
  signalled_at: string | null;
  /** Set once a person confirmed it. */
  confirmed_at: string | null;
  confirmed_by: string | null;
  amount: string | null;
  currency: string | null;
  reference: string | null;
}

export interface PaymentsState {
  cases: PaymentCase[];
}

export interface PaymentConfirmBody {
  amount: string;
  currency: string;
  reference: string;
}

export function clientPayments(): Promise<PaymentsState> {
  return request<PaymentsState>("/payments");
}

export function clientConfirmPayment(prospectId: string, body: PaymentConfirmBody): Promise<PaymentCase> {
  return request<PaymentCase>(`/payments/${encodeURIComponent(prospectId)}/confirm`, { method: "POST", body: JSON.stringify(body) });
}

/** What the client says about the accounts it wants to connect, before Revansa can connect them, and what
 *  it can choose on a circuit today (see src/sales_app/api/client_connections.py). */
export type HintKind = "gmail" | "sheets" | "hubspot" | "telegram";

export interface ConnectionsInfo {
  hints: Record<HintKind, string | null>;
  selectable: { channels: Record<string, boolean>; sources: Record<string, boolean> };
}

export function clientConnections(): Promise<ConnectionsInfo> {
  return request<ConnectionsInfo>("/connections");
}

export function clientSetHint(kind: HintKind, value: string): Promise<ConnectionsInfo> {
  return request<ConnectionsInfo>(`/connections/hints/${kind}`, { method: "PUT", body: JSON.stringify({ value }) });
}

export function clientClearHint(kind: HintKind): Promise<ConnectionsInfo> {
  return request<ConnectionsInfo>(`/connections/hints/${kind}`, { method: "DELETE" });
}

/** Testing the agent on a person the client names (`/client/v1/agent-test`). */
export interface AgentTest {
  prospect_id: string;
  name: string;
  contact: string;
  channel: string | null;
  circuit_id: string | null;
  status: string;
  messages: number;
  replies: number;
  last_message_at: string | null;
}

export interface AgentTestMessage {
  direction: "outbound" | "inbound";
  text: string;
  sent_at: string;
}

export interface AgentTestStart {
  prospect_id: string;
  /** `sent`: the first message is out. `waiting`: nothing sent yet (limit reached, or the person must open the chat). `not_sent`: refused. */
  state: "sent" | "waiting" | "not_sent";
  detail: string | null;
}

export function clientStartAgentTest(body: { contact: string; name?: string; circuit_id?: string }): Promise<AgentTestStart> {
  return request<AgentTestStart>("/agent-test", { method: "POST", body: JSON.stringify(body) });
}

export function clientAgentTests(): Promise<{ tests: AgentTest[] }> {
  return request<{ tests: AgentTest[] }>("/agent-test");
}

export function clientAgentTest(id: string): Promise<{ test: AgentTest; messages: AgentTestMessage[] }> {
  return request<{ test: AgentTest; messages: AgentTestMessage[] }>(`/agent-test/${encodeURIComponent(id)}`);
}
