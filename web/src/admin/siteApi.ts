/** `/admin/v1/site-pages` and `/admin/v1/payment-requests` (see src/sales_app/api/admin_site.py). */

import { AdminApiError, detailText } from "./api";

const BASE = "/admin/v1";

export type SiteKey = "contact" | "privacy" | "terms" | "bank";
export type SiteLocale = "en" | "fr" | "ar";

export interface SitePage {
  key: SiteKey;
  locale: SiteLocale;
  title: string;
  body: string;
  updated_at: string;
  updated_by: string;
}

export interface PaymentRequest {
  request_id: number;
  client_id: string;
  client_name: string;
  plan_id: string;
  plan_name: string;
  amount: string;
  currency: string;
  reference: string;
  receipt_type: string;
  receipt_size: number;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  reason: string | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new AdminApiError(0, "Could not reach the server.");
  }
  if (!response.ok) {
    let message = response.statusText || `request failed (${response.status})`;
    try {
      message = detailText(((await response.json()) as { detail?: unknown }).detail) ?? message;
    } catch {
      /* the status text stays */
    }
    throw new AdminApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export async function adminSitePages(): Promise<SitePage[]> {
  return (await call<{ pages: SitePage[] }>("/site-pages")).pages;
}

export async function adminSaveSitePage(key: SiteKey, locale: SiteLocale, title: string, body: string): Promise<SitePage[]> {
  return (await call<{ pages: SitePage[] }>(`/site-pages/${key}/${locale}`, { method: "PUT", body: JSON.stringify({ title, body }) })).pages;
}

export async function adminPaymentRequests(status?: PaymentRequest["status"]): Promise<PaymentRequest[]> {
  return (await call<{ requests: PaymentRequest[] }>(`/payment-requests${status ? `?status=${status}` : ""}`)).requests;
}

export function adminApprovePayment(id: number): Promise<PaymentRequest> {
  return call<PaymentRequest>(`/payment-requests/${id}/approve`, { method: "POST" });
}

export function adminRejectPayment(id: number, reason: string): Promise<PaymentRequest> {
  return call<PaymentRequest>(`/payment-requests/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) });
}

/** The receipt opens in its own tab: the session cookie goes with the request. */
export function receiptUrl(id: number): string {
  return `${BASE}/payment-requests/${id}/receipt`;
}
