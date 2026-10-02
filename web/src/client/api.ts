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
