/** Read-only client for the Client's invoices and subscription (`/client/v1/invoices`,
 *  `/client/v1/subscription`; see src/sales_app/api/client_billing.py and docs/finance-contracts.md).
 *  The session is an httpOnly cookie; no client id is ever sent. Nothing here changes an invoice. */

import { ClientApiError } from "../api";

export type InvoiceStatus = "PENDING" | "PAID" | "FAILED" | "FOR_FREE" | "DISCOUNTED_100";

export interface ClientInvoice {
  invoice_id: number;
  number: string;
  period: string;
  currency: string;
  /** Billed amount, before any discount (decimal string). */
  amount: string;
  amount_after_discount: string | null;
  discount_amount: string | null;
  free_amount: string | null;
  status: InvoiceStatus;
  /** Money received: only PAID. A free or discounted invoice is never collected. */
  collected: boolean;
  due_at: string;
  overdue: boolean;
  created_at: string;
  status_changed_at: string | null;
}

export interface ClientInvoiceLine {
  plan_name: string;
  monthly_price: string;
  currency: string;
  days: number;
  days_in_period: number;
  amount: string;
}

export interface ClientInvoiceDetail extends ClientInvoice {
  lines: ClientInvoiceLine[];
}

export interface SubscriptionPlan {
  plan_id: string;
  name: string;
  monthly_price: string;
  currency: string;
  billing_period: "monthly" | "annual";
  included_credits: number;
  daily_quota: number;
  since: string;
}

export interface Subscription {
  status: "active" | "no_plan";
  plan: SubscriptionPlan | null;
  next_billing_at: string | null;
  unpaid_invoices: number;
  overdue_invoices: number;
  last_payment: {
    invoice_number: string;
    period: string;
    status: InvoiceStatus;
    collected: boolean;
    settled_at: string | null;
  } | null;
}

async function get<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/client/v1${path}`, { credentials: "include" });
  } catch {
    throw new ClientApiError(0, "Could not reach the server.");
  }
  if (!response.ok) throw new ClientApiError(response.status, response.statusText);
  return (await response.json()) as T;
}

export const clientInvoices = async (): Promise<ClientInvoice[]> =>
  (await get<{ invoices: ClientInvoice[] }>("/invoices")).invoices;

export const clientInvoice = (id: number): Promise<ClientInvoiceDetail> => get(`/invoices/${id}`);

export const clientSubscription = (): Promise<Subscription> => get("/subscription");
