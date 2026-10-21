import type {
  ClientSettings,
  ConversationControl,
  ConversationMessage,
  FollowUpSettings,
  Lane,
  PaymentCase,
  Proposal,
  ProspectRow,
  ReportSettings,
} from "../client/api";
import type { ClientInvoiceDetail } from "../client/billing/billingApi";

/** A prospect with the few extra facts the demo needs to filter and report (all fictitious). */
export interface DemoProspect {
  row: ProspectRow;
  product: string;
  summary: string;
}

export interface DemoThread {
  prospect_id: string;
  origin: "outbound" | "inbound";
  messages: ConversationMessage[];
  control: ConversationControl;
  /** Why the agent handed it to a person, when it did. */
  handoff_reason: string | null;
}

export interface DemoNotification {
  id: number;
  created_at: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string;
  action: string;
  read_at: string | null;
}

export interface DemoData {
  generatedAt: string;
  agent: { state: "not_started" | "running" | "paused"; changed_at: string | null };
  settings: ClientSettings;
  lanes: Lane[];
  prospects: DemoProspect[];
  threads: DemoThread[];
  proposals: Proposal[];
  payments: PaymentCase[];
  notifications: DemoNotification[];
  invoices: ClientInvoiceDetail[];
  followUp: Omit<FollowUpSettings, "version">;
  reportSettings: ReportSettings;
  /** Delivered daily reports that exist already (day -> sent_at). */
  reportDays: string[];
}
