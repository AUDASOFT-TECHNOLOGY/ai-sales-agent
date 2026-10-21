/** Calls for the WhatsApp connection: the Client's (`/client/v1/channel/whatsapp`, session cookie,
 *  never a client id) and the Admin's (`/admin/v1/clients/{id}/whatsapp`). No response carries a
 *  token, a PIN or the app secret; the Client's carries no identifier of Meta's either. */

import { appFetch } from "../demo/transport";

export class WhatsAppApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "WhatsAppApiError";
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await appFetch(path, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new WhatsAppApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    let detail = response.statusText || `request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* no JSON detail: the status text stands */
    }
    throw new WhatsAppApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---- Client ----------------------------------------------------------------------------------

export type WhatsAppState = "not_connected" | "connected" | "reauth_required" | "unavailable";

export interface ClientWhatsAppStatus {
  state: WhatsAppState;
  number: string | null;
  verified_name: string | null;
  status: "ready" | "waiting_platform" | "action_required";
  message: string | null;
  can_start_conversations: boolean;
  delivered_7d: number;
  failed_7d: number;
}

export interface WhatsAppStart {
  app_id: string;
  config_id: string;
  api_version: string;
  state: string;
}

export interface WhatsAppComplete {
  state: string;
  code: string;
  waba_id?: string;
  phone_number_id?: string;
}

const CLIENT = "/client/v1/channel/whatsapp";

export const clientWhatsAppStatus = () => call<ClientWhatsAppStatus>(CLIENT);
export const clientWhatsAppStart = () => call<WhatsAppStart>(`${CLIENT}/start`, { method: "POST" });
export const clientWhatsAppComplete = (body: WhatsAppComplete) =>
  call<ClientWhatsAppStatus>(`${CLIENT}/complete`, { method: "POST", body: JSON.stringify(body) });
export const clientWhatsAppDisconnect = () =>
  call<ClientWhatsAppStatus>(`${CLIENT}/disconnect`, { method: "POST" });

// ---- Admin -----------------------------------------------------------------------------------

export interface AdminWhatsAppAccount {
  state: WhatsAppState;
  waba_id: string | null;
  phone_number_id: string | null;
  display_number: string | null;
  verified_name: string | null;
  quality: string | null;
  connected_at: string | null;
  opening_template: string | null;
  templates: Record<string, number>;
  deliveries_7d: Record<string, number>;
  recent_failures: Array<{ message_id: string; status: string; at: string; error_code: string; error_title: string }>;
}

export interface AdminWhatsAppTemplate {
  name: string;
  language: string;
  category: string;
  status: string;
  body: string;
  parameters: string[];
  reason: string;
  opening: boolean;
  usable_as_opening: boolean;
}

export interface AdminWhatsAppPlatform {
  ready: boolean;
  missing: string[];
  webhook_url: string | null;
  deauthorize_url: string | null;
  data_deletion_url: string | null;
  api_version: string;
}

export interface NewTemplate {
  name: string;
  language: string;
  category: string;
  body: string;
  examples: string[];
}

const admin = (clientId: string) => `/admin/v1/clients/${encodeURIComponent(clientId)}/whatsapp`;

export const adminWhatsAppPlatform = () => call<AdminWhatsAppPlatform>("/admin/v1/whatsapp/platform");
export const adminWhatsAppAccount = (clientId: string) => call<AdminWhatsAppAccount>(admin(clientId));
export const adminWhatsAppRefresh = (clientId: string) =>
  call<AdminWhatsAppAccount>(`${admin(clientId)}/refresh`, { method: "POST" });
export const adminWhatsAppTemplates = (clientId: string) =>
  call<AdminWhatsAppTemplate[]>(`${admin(clientId)}/templates`);
export const adminWhatsAppSync = (clientId: string) =>
  call<AdminWhatsAppTemplate[]>(`${admin(clientId)}/templates/sync`, { method: "POST" });
export const adminWhatsAppCreateTemplate = (clientId: string, body: NewTemplate) =>
  call<AdminWhatsAppTemplate>(`${admin(clientId)}/templates`, { method: "POST", body: JSON.stringify(body) });
export const adminWhatsAppDeleteTemplate = (clientId: string, name: string) =>
  call<void>(`${admin(clientId)}/templates/${encodeURIComponent(name)}`, { method: "DELETE" });
export const adminWhatsAppSetOpening = (clientId: string, name: string, language: string) =>
  call<AdminWhatsAppAccount>(`${admin(clientId)}/templates/opening`, {
    method: "PUT",
    body: JSON.stringify({ name, language }),
  });
export const adminWhatsAppDisconnect = (clientId: string, releaseNumber: boolean) =>
  call<{ steps: Record<string, string> }>(`${admin(clientId)}/disconnect`, {
    method: "POST",
    body: JSON.stringify({ release_number: releaseNumber }),
  });
