/** Shapes of `GET /admin/v1/dashboard` and `GET /client/v1/dashboard` (see docs/dashboards.md).
 *  Every indicator carries how it is known; a `not_tracked` one has a null value and a reason and
 *  is never drawn as 0. */

export type Availability = "measured" | "estimated" | "not_tracked";

export interface Indicator {
  value: number | null;
  availability: Availability;
  reason?: string;
}

export interface Series<P> {
  availability: Availability;
  reason?: string | null;
  granularity?: string;
  points: P[];
}

export interface ActivityBlock {
  prospects_total: Indicator;
  prospects_contacted: Indicator;
  conversations: Indicator;
  messages_sent: Indicator;
  replies_received: Indicator;
  prospects_replied: Indicator;
  reply_rate_percent: Indicator;
  needs_human: Indicator;
  with_errors: Indicator;
  conversions: Indicator;
  payments: { declared: Indicator; confirmed: Indicator; awaiting_confirmation: Indicator };
  by_status: Record<string, number>;
  by_circuit: Record<string, number>;
  by_product: Record<string, number>;
  by_channel: Record<string, number>;
  by_source: Record<string, number>;
  messages_by_channel: Record<string, Record<string, number>>;
}

export interface ActivityPoint {
  period: string;
  sent: number;
  received: number;
}

export interface PerformancePoint {
  period: string;
  prospects_contacted: Indicator;
  prospects_replied: Indicator;
  reply_rate_percent: Indicator;
  conversions: Indicator;
}

export interface IncidentPoint {
  period: string;
  incidents: number;
}

export interface IncidentBlock {
  open: Indicator;
  by_severity: Record<string, number>;
  by_kind: Record<string, number>;
  sync_failures: Indicator;
  connection_problems: Indicator;
  items: {
    id: number;
    created_at: string;
    kind: string;
    severity: string;
    title: string;
    message: string;
    action: string;
  }[];
}

export interface UsageStatus {
  availability: Availability;
  reason?: string;
  state?: string;
  message?: string;
  action?: string;
  plan_name?: string | null;
  daily_limit?: number | null;
  daily_used?: number | null;
  daily_percent?: number | null;
  month_used?: number | null;
}

/* ---- Admin ---- */

export interface FinanceTotals {
  currency: string;
  billed: string;
  after_discount: string;
  collected: string;
  free: string;
  discounted: string;
  pending: string;
  overdue: string;
  invoices_issued: number;
  invoices_paid: number;
  invoices_free: number;
  invoices_discounted: number;
  invoices_pending: number;
  invoices_overdue: number;
}

export interface FinanceBlock {
  availability: Availability;
  reason?: string;
  totals?: FinanceTotals[];
  timeline?: { period: string; currency: string; billed: string; collected: string }[];
  subscriptions?: { period: string; active: number; inactive: number }[];
}

export interface ProviderCost {
  provider: string;
  basis: string;
  availability: Availability;
  totals: Record<string, string>;
  lines?: number;
  unavailable_lines?: number;
  complete?: boolean;
}

export interface CostsBlock {
  availability: Availability;
  reason?: string;
  basis?: string;
  complete?: boolean;
  providers?: ProviderCost[];
  totals?: Record<string, string>;
  months?: string[];
  series?: {
    period: string;
    by_provider: ProviderCost[];
    totals: Record<string, string>;
    complete: boolean;
  }[];
}

export interface Profitability {
  status: "available" | "partial" | "unavailable";
  reason?: string | null;
  basis?: string;
  margin: Record<string, string>;
  revenue?: Record<string, string>;
  costs_known?: Record<string, string>;
}

export interface AdminDashboard {
  generated_at: string;
  period: { from: string; to: string; months: string[]; granularity: string };
  client_id: string | null;
  scope: "all" | "scoped";
  clients: { total: Indicator; active: Indicator; inactive: Indicator };
  llm_models: { configured: Indicator; models: string[] };
  agents: { total: Indicator; active: Indicator };
  clients_list: { client_id: string; display_name: string; status: string }[];
  activity: ActivityBlock;
  finance: FinanceBlock;
  costs: CostsBlock;
  profitability: Profitability;
  usage: {
    availability: Availability;
    reason?: string;
    clients: {
      client_id: string;
      display_name: string;
      plan_name: string | null;
      state: string;
      daily_limit: number | null;
      daily_used: number | null;
      daily_percent: number | null;
      month_used: number | null;
    }[];
  };
  reliability: IncidentBlock & {
    failed_actions: Indicator;
    health: { degraded_clients: Indicator; clients: { client_id: string; status: string }[] };
  };
  integrations: {
    ready: Indicator;
    not_ready: Indicator;
    clients: { client_id: string; readiness: string; missing: number }[];
  };
  series: {
    activity: Series<ActivityPoint>;
    performance: Series<PerformancePoint>;
    finance: { availability: Availability; timeline: NonNullable<FinanceBlock["timeline"]> };
    subscriptions: { availability: Availability; points: NonNullable<FinanceBlock["subscriptions"]> };
    costs: { availability: Availability; points: NonNullable<CostsBlock["series"]> };
    reliability: Series<IncidentPoint>;
  };
}

/* ---- Client ---- */

export interface ClientDashboard {
  generated_at: string;
  period: { from: string; to: string; months: string[]; granularity: string };
  agent: {
    availability: Availability;
    reason?: string;
    state?: "not_started" | "running" | "paused";
    active?: boolean;
    ready?: boolean;
    integrations?: { id: string; ok: boolean; message: string | null }[];
  };
  activity: ActivityBlock;
  human_requests: Indicator;
  incidents: IncidentBlock;
  usage: UsageStatus;
  billing: {
    availability: Availability;
    reason?: string;
    subscription?: {
      status: "active" | "no_plan";
      plan_name: string | null;
      monthly_price: string | null;
      currency: string | null;
      next_billing_at: string | null;
    };
    invoices?: {
      issued: Indicator;
      paid: Indicator;
      unpaid: Indicator;
      overdue: Indicator;
      recent: {
        number: string;
        period: string;
        currency: string;
        amount: string;
        amount_after_discount: string | null;
        status: string;
        collected: boolean;
        overdue: boolean;
      }[];
    };
  };
  series: {
    activity: Series<ActivityPoint>;
    performance: Series<PerformancePoint>;
    incidents: Series<IncidentPoint>;
  };
}
