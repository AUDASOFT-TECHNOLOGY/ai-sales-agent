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

  constructor(status: number, message: string) {
    super(message);
    this.name = "ClientApiError";
    this.status = status;
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
    throw new ClientApiError(response.status, await errorDetail(response));
  }
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
    if (typeof detail === "string") return detail;
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
      if (lines.length > 0) return lines.join("; ");
    }
  } catch {
    /* not JSON, or no `detail` string (e.g. a 422 list): fall through to the generic message */
  }
  return response.statusText || `request failed (${response.status})`;
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
 * No response carries a password or a token: only whether one is stored. The secrets travel in the
 * one PUT body that writes them and are never kept by this module. */

export interface ChannelOption {
  key: string;
  label: string;
}

export interface EmailSettings {
  from_address: string | null;
  from_name: string | null;
  username: string | null;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_security: string | null;
  imap_host: string | null;
  imap_port: number | null;
  imap_security: string | null;
  mailbox: string | null;
  subject: string | null;
}

export interface TelegramSettings {
  contact_prompt: string;
  contact_button: string;
  not_found_text: string;
  connected: boolean;
}

export interface CredentialStatus {
  label: string;
  configured: boolean;
  updated_at: string | null;
}

export interface ChannelState {
  version: number | null;
  channel: string;
  authorized: boolean;
  options: ChannelOption[];
  email: EmailSettings | null;
  telegram: TelegramSettings | null;
  credential: CredentialStatus | null;
  problem: string | null;
  updated_at: string | null;
  updated_by: "client" | "administrator" | null;
}

export interface ChannelUpdate {
  expected_version: number | null;
  channel: string;
  email?: Partial<Record<keyof EmailSettings, string | number>>;
  telegram?: Partial<Record<"contact_prompt" | "contact_button" | "not_found_text", string>>;
}

/** The outcome of a real call to the sending/receiving service (never a simulation). */
export interface ChannelTestResult {
  channel: string;
  ok: boolean;
  message: string;
  details: Record<string, string>;
  checked_at: string;
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

/** Write-only: the answer says whether a secret is stored, never what it is. */
export function clientSaveCredential(field: "password" | "bot_token", value: string): Promise<CredentialStatus> {
  return request<CredentialStatus>("/channel/credentials", { method: "PUT", body: JSON.stringify({ [field]: value }) });
}

export function clientRemoveCredential(): Promise<CredentialStatus> {
  return request<CredentialStatus>("/channel/credentials", { method: "DELETE" });
}

export function clientTestChannel(): Promise<ChannelTestResult> {
  return request<ChannelTestResult>("/channel/test", { method: "POST" });
}

export function clientConnectTelegram(): Promise<ChannelTestResult> {
  return request<ChannelTestResult>("/channel/connect", { method: "POST" });
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
  credential: CredentialStatus | null;
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

/** Write-only, like the channel's secrets: the answer says whether one is stored, never what it is. */
export function clientSaveSourceToken(value: string): Promise<CredentialStatus> {
  return request<CredentialStatus>("/source/credentials", { method: "PUT", body: JSON.stringify({ access_token: value }) });
}

export function clientRemoveSourceToken(): Promise<CredentialStatus> {
  return request<CredentialStatus>("/source/credentials", { method: "DELETE" });
}
