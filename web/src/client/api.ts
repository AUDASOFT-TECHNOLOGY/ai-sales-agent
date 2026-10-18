/** Thin client for `/client/v1/*` (see src/sales_app/api/client.py). The session lives in an
 *  httpOnly cookie the browser manages on its own: this module never reads or stores it, and
 *  never sends a client id -- the server knows who is logged in. Passwords only ever travel in the
 *  one POST body that carries them, and no response contains one. */

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

  constructor(status: number, message: string, reasons: ReadinessCheck[] = []) {
    super(message);
    this.name = "ClientApiError";
    this.status = status;
    this.reasons = reasons;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    const { message, reasons } = await errorDetail(response);
    throw new ClientApiError(response.status, message, reasons);
  }
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<{ message: string; reasons: ReadinessCheck[] }> {
  const fallback = response.statusText || `request failed (${response.status})`;
  try {
    const body: unknown = await response.json();
    const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
    if (typeof detail === "string") return { message: detail, reasons: [] };
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      // `{message, reasons}`: a refusal that lists what is missing (the agent cannot start yet).
      const { message, reasons } = detail as { message?: unknown; reasons?: unknown };
      return {
        message: typeof message === "string" ? message : fallback,
        reasons: Array.isArray(reasons) ? reasons.filter(isCheck) : [],
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
      if (lines.length > 0) return { message: lines.join("; "), reasons: [] };
    }
  } catch {
    /* not JSON, or no `detail` string (e.g. a 422 list): fall through to the generic message */
  }
  return { message: fallback, reasons: [] };
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
  contact_prompt: string;
  contact_button: string;
  not_found_text: string;
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
  telegram?: Partial<Record<"contact_prompt" | "contact_button" | "not_found_text", string>>;
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

export function clientTelegramInvitations(): Promise<TelegramInvitations> {
  return request<TelegramInvitations>("/channel/invitations");
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

export interface SourceState {
  version: number | null;
  /** Null: nothing to connect. Otherwise whether the platform has connected the CRM account. */
  access: "ready" | "waiting_platform" | null;
  last_test: { ok: boolean; checked_at: string; current: boolean } | null;
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
}

export interface ProspectsPage {
  /** All the client's prospects, not just this page. */
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
}

export interface ConversationHistory {
  prospect_id: string;
  conversation_id: string;
  origin: "outbound" | "inbound" | "unknown";
  /** Oldest first; empty when nothing was exchanged yet. */
  messages: ConversationMessage[];
}

/** `limit` is 1..100, `offset` >= 0: the server refuses anything else. */
export function clientProspects(limit: number, offset: number): Promise<ProspectsPage> {
  return request<ProspectsPage>(`/prospects?limit=${limit}&offset=${offset}`);
}

export function clientConversation(prospectId: string): Promise<ConversationHistory> {
  return request<ConversationHistory>(`/conversations/${encodeURIComponent(prospectId)}`);
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
