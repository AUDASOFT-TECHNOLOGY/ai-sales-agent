/** View-model types for the prototype. These are NOT the API contract and NOT the Core contracts:
 *  they exist so the visual components have something to render. Real adapters replace `src/mock/`. */

import type { MessageKey } from "../i18n";

export type AgentStateKey =
  | "idle"
  | "working"
  | "writing"
  | "waiting"
  | "observing"
  | "off_hours"
  | "paused"
  | "degraded"
  | "not_live";

export type Mode = "observe" | "ask" | "auto";
export type Role = "owner" | "viewer";

export type ProspectStatus = "new" | "active" | "paused" | "lost" | "closed";
export type Attention = "none" | "working" | "waiting" | "won" | "lost";

/** Why a prospect was marked lost: said no outright, went silent through the whole follow-up
 *  cadence, or the agent judged — without an explicit statement — that they were not interested
 *  or uncomfortable continuing. All three close the opportunity the same way (doc: Lost). */
export type LostReason = "declined" | "no_response" | "inferred";

export type FactStatus = "confirmed" | "discovered" | "hypothesis";
export type FactSource = "prospect" | "conversation" | "client_data" | "human" | "system" | "brain_inference";

export interface Fact {
  key: string;
  value: string;
  status: FactStatus;
  source: FactSource;
  excerpt?: string;
  when?: string;
}

/** Starting register for the agent's first message; it adapts from there (see agent/Strategy.tsx). */
export type Tone = "formal" | "neutral" | "friendly";

/** One step of the follow-up cadence: how many days after the previous reminder, and its label. */
export interface CadenceStep {
  days: number;
  label: string;
}

export interface Stage {
  id: string;
  label: MessageKey;
  /** Generic milestone a stage may carry; the client's own label is what is shown. */
  milestone?: "won" | "lost";
}

export interface Prospect {
  id: string;
  name: string;
  initials: string;
  place: string;
  status: ProspectStatus;
  stage: string;
  attention: Attention;
  /** Set only while `status` is "lost"; cleared when the prospect is reopened. */
  lostReason?: LostReason;
  last: MessageKey;
  read: MessageKey;
  next: MessageKey;
  interest: string;
  /** What REVANSA recommends doing next, in the client's own words. Empty for prospects with no clear next move yet. */
  recommendation?: MessageKey;
  facts: Fact[];
  step: number;
}

export type NeedKind = "draft" | "handoff" | "payment" | "failed";
export interface NeedItem {
  id: string;
  kind: NeedKind;
  prospectId: string;
  reason: MessageKey;
  waiting: MessageKey;
  preview?: string;
  /** Why REVANSA is asking now, short bullets shown in the detail view (section "Why now?"). */
  factors?: MessageKey[];
}

/** How a need was settled, for the notification history: a draft is approved or declined, a
 *  handoff is handled, a payment is confirmed, a failed action is dismissed once it's dealt with. */
export type NeedResolution = "approved" | "declined" | "handled" | "confirmed" | "dismissed";

/** A need that left the "needs you" list, kept for the notification history so nothing a person
 *  acted on is lost once it's off the active list. */
export interface ResolvedNeed extends NeedItem {
  resolution: NeedResolution;
  resolvedAt: MessageKey;
}

export type ThreadKind = "sent" | "received" | "fact" | "decision" | "handoff" | "payment" | "scheduled" | "human";
export interface ThreadEvent {
  id: string;
  time: string;
  kind: ThreadKind;
  who: string;
  voice: MessageKey;
  detail: MessageKey;
  /** Future intentions are ghosts. */
  future?: boolean;
  needsYou?: boolean;
}

export interface Kpi {
  id: "treated" | "replies" | "appointments" | "conversions";
  value: number | null;
  series: number[];
  ghost: number[];
  delta: number | null;
  direction: "good" | "bad" | "neutral";
  caption?: string;
}

export interface FunnelStep {
  id: "contacted" | "replied" | "qualified" | "appointment" | "converted";
  count: number | null;
  rate: string | null;
}

export type Author = "prospect" | "agent" | "human" | "system";
export interface MessagePart {
  text: string;
  /** Exact values (prices, payment details) inserted verbatim by deterministic code. */
  verbatim?: boolean;
}
export interface Message {
  id: string;
  author: Author;
  who?: string;
  time: string;
  parts: MessagePart[];
  lang?: string;
  media?: { name: string; size: string; possibleProof?: boolean };
}
export interface Conversation {
  id: string;
  prospectId: string;
  needsYou: boolean;
  preview: string;
  messages: Message[];
  notes: {
    learned: Fact[];
    decided: MessageKey[];
    handoff?: MessageKey;
  };
  payment: "none" | "claimed" | "proof_received" | "paid";
  draft?: MessagePart[];
  /** Why the agent is recommending what it's recommending right now, and how sure it is. */
  reasoning?: { factors: MessageKey[]; confidence: "high" | "medium" | "low" };
}

export type OfferAssetKind = "doc" | "image" | "video" | "testimonial" | "link";

/** One piece of sales material a CEO can hand the agent besides the one-line summary —
 *  a spec sheet, a photo, a demo video, a customer testimonial — so REVANSA has more than
 *  a sentence to work with when a prospect asks for details. */
export interface OfferAsset {
  id: string;
  kind: OfferAssetKind;
  label: string;
  url: string;
}

export interface Offer {
  id: string;
  name: string;
  summary: string;
  status: "active" | "coming_soon" | "inactive";
  price: string;
  markets: string[];
  assets: OfferAsset[];
  completeness: number;
  missing: MessageKey[];
}

/** How a client gets paid in one target market. Payment varies by country (different bank
 *  details, different methods available), so this is keyed per market rather than global. */
export interface PaymentProfile {
  market: string;
  methods: string[];
  instructions: string;
  /** Verbatim URL for the "online payment link" method; empty when that method isn't used. */
  link: string;
}

/** One period bucket for the Dashboard's filterable KPI row and trend chart (section "Dashboard").
 *  `contacted` counts prospects reached for the first time in this window only — it excludes
 *  follow-ups and reminders on prospects already in conversation, which is a different, unrelated
 *  quota tracked per day (see ACTIONS_TODAY and the daily budget). `trend` always sums to exactly
 *  `contacted`, bucketed at `trendUnit` grain (a day for the 7-day window, a week for 30 days and
 *  3 months, a month for "since the start"), so the chart never disagrees with the headline number
 *  above it. */
export interface DashboardPeriodStat {
  label: MessageKey;
  contacted: number;
  responseRate: number;
  conversionRate: number;
  revenue: string;
  trend: number[];
  trendLabels: string[];
  trendUnit: "day" | "week" | "month";
}

/** One row of the "prospects by stage" breakdown on the Dashboard. */
export interface StageSlice {
  id: string;
  label: MessageKey;
  count: number;
  tint: "action" | "agent" | "attention" | "success";
}

/** Since-start performance of one catalogue product, keyed by its Offer id. */
export interface ProductPerformance {
  offerId: string;
  prospectsOriented: number;
  conversionRate: number | null;
  revenueValue: number | null;
  revenueLabel: string | null;
}

/** One past day's report, for the Report history screen. Today's own report is computed live from
 *  state, the same way Results.tsx already does it — only the days before it are hand-authored here. */
export interface ReportDay {
  date: MessageKey;
  time: string;
  contacted: number;
  replies: number;
  conversationsOpen: number;
  paymentsToConfirm: number;
  interventions: number;
  highlight: MessageKey;
}
