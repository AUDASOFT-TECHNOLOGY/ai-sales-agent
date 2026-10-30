/** The public side of `/client/v1` (see src/sales_app/api/client_signup.py): the plans a visitor sees,
 *  the free sign-up, and the Free account's way to a paid plan. Plans are never written here: they come
 *  from what the administrator configured. */

import { request } from "./api";
import type { ClientProfile } from "./api";

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

export async function publicPlans(): Promise<PublicPlan[]> {
  return (await request<{ plans: PublicPlan[] }>("/public/plans")).plans;
}

export function clientSignup(body: { name: string; email: string; password: string; website?: string }): Promise<{ status: string }> {
  return request<{ status: string }>("/auth/signup", { method: "POST", body: JSON.stringify(body) });
}

export function clientAccess(): Promise<ClientAccess> {
  return request<ClientAccess>("/account/access");
}

export function requestPlan(planId: string): Promise<{ status: string; plan_id: string }> {
  return request<{ status: string; plan_id: string }>("/account/plan-request", { method: "POST", body: JSON.stringify({ plan_id: planId }) });
}

export type { ClientProfile };
