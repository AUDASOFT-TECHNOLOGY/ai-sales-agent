/** Thin client for `/admin/v1/audit` and `/admin/v1/notifications` (src/sales_app/api/admin_audit.py).
 *  Nothing here holds a secret: the server redacts audit documents before keeping them. */

export interface AuditItem {
  id: number | null;
  at: string;
  actor: string;
  actor_role: string;
  client_id: string | null;
  action: string;
  target: string;
  outcome: string;
  before: unknown;
  after: unknown;
}

export interface AuditPage {
  items: AuditItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminNotification {
  id: number;
  created_at: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  client_id: string | null;
  title: string;
  message: string;
  action: string;
  read_at: string | null;
  resolved_at: string | null;
}

export interface AdminNotificationPage {
  items: AdminNotification[];
  total: number;
  unread_count: number;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/admin/v1${path}`, { credentials: "include", ...init });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as T;
}

export interface AuditFilters {
  client_id?: string;
  action?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export function fetchAudit(filters: AuditFilters): Promise<AuditPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return call<AuditPage>(`/audit?${params.toString()}`);
}

export const fetchNotifications = (): Promise<AdminNotificationPage> => call("/notifications?include_resolved=false&limit=20");
export const markNotificationRead = (id: number): Promise<unknown> => call(`/notifications/${id}/read`, { method: "POST" });
export const markAllNotificationsRead = (): Promise<unknown> => call("/notifications/read-all", { method: "POST" });
