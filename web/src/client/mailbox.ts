/** `/client/v1/channel/mailbox` (see src/sales_app/api/client_mailbox.py): where the client's independent
 *  mailbox stands, the login the Admin chose to communicate, and the password on explicit request.
 *  Nothing technical (no server, no port) ever comes back, and the password is never stored here. */

export type MailboxStatus = "not_requested" | "pending" | "in_progress" | "active" | "error" | "rejected";

export interface MailboxInfo {
  status: MailboxStatus;
  message: string;
  reason: string | null;
  address: string | null;
  login: string | null;
  credentials_shared: boolean;
  password_revealable: boolean;
  updated_at: string | null;
}

export class MailboxApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "MailboxApiError";
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/client/v1/channel/mailbox${path}`, {
      ...init,
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    throw new MailboxApiError(0, "network");
  }
  if (!response.ok) throw new MailboxApiError(response.status, response.statusText || "error");
  return (await response.json()) as T;
}

export function clientMailbox(): Promise<MailboxInfo> {
  return call<MailboxInfo>("");
}

/** Asks the server to show the password. The caller shows it briefly and drops it. */
export function clientRevealMailboxPassword(): Promise<{ password: string; shown_for_seconds: number }> {
  return call("/reveal-password", { method: "POST", body: JSON.stringify({ confirm: true }) });
}
