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
