/** The public side of `/client/v1` (see src/sales_app/api/client_signup.py and client_plan_payment.py):
 *  the plans a visitor sees, the free sign-up, the texts the administrator wrote, and paying for a plan
 *  by bank transfer. Plans are never written here: they come from what the administrator configured. */

import { request } from "./api";

export interface PublicPlan {
  plan_id: string;
  name: string;
  monthly_price: string;
  currency: string;
  billing_period: string;
  included_credits: number;
  daily_quota: number;
}

export interface ClientAccess {
  tier: "free" | "paid";
  free_credits?: number | null;
  credits_remaining?: number | null;
}

export interface PublicPage {
  key: string;
  locale: string;
  title: string;
  body: string;
}

export interface PaymentRequest {
  request_id: number;
  plan_id: string;
  plan_name: string;
  amount: string;
  currency: string;
  reference: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
  reason: string | null;
}

export async function publicPlans(): Promise<PublicPlan[]> {
  return (await request<{ plans: PublicPlan[] }>("/public/plans")).plans;
}

export async function publicPageKeys(): Promise<string[]> {
  return (await request<{ pages: string[] }>("/public/pages")).pages;
}

export function publicPage(key: string, locale: string): Promise<PublicPage> {
  return request<PublicPage>(`/public/pages/${encodeURIComponent(key)}?locale=${encodeURIComponent(locale)}`);
}

export function clientSignup(body: { name: string; email: string; password: string; website?: string }): Promise<{ status: string }> {
  return request<{ status: string }>("/auth/signup", { method: "POST", body: JSON.stringify(body) });
}

export function clientAccess(): Promise<ClientAccess> {
  return request<ClientAccess>("/account/access");
}

export function bankDetails(locale: string): Promise<{ title: string; body: string }> {
  return request<{ title: string; body: string }>(`/account/bank-details?locale=${encodeURIComponent(locale)}`);
}

export async function paymentRequests(): Promise<PaymentRequest[]> {
  return (await request<{ requests: PaymentRequest[] }>("/account/payment-requests")).requests;
}

export function sendPaymentRequest(body: { plan_id: string; reference: string; receipt: string }): Promise<PaymentRequest> {
  return request<PaymentRequest>("/account/payment-requests", { method: "POST", body: JSON.stringify(body) });
}
