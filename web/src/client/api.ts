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
    if (body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string") {
      return (body as { detail: string }).detail;
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
