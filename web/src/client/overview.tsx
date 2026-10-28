/** What the shell, the Home and the setup guide all need to know about the client's agent, read once:
 *  the agent, what still blocks it, the business settings, the channel, the source, the figures and
 *  what waits for a decision. A piece that could not be read is `null` and is said so by the screen
 *  that shows it, never replaced by a guess. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  clientAgent,
  clientChannel,
  clientLanes,
  clientPayments,
  clientProposals,
  clientReadiness,
  clientSettings,
  clientSourceState,
  clientSummary,
  type AgentState,
  type ChannelState,
  type ClientSummary,
  type LanesState,
  type Readiness,
  type SettingsState,
  type SourceState,
} from "./api";
import { useClientAuth } from "./ClientAuthContext";
import { failureOf } from "./kit";
import { buildSteps, type Step } from "./setupModel";
import type { Doc } from "./settingsModel";

export interface Overview {
  agent: AgentState | null;
  readiness: Readiness | null;
  settings: SettingsState | null;
  channel: ChannelState | null;
  source: SourceState | null;
  lanes: LanesState | null;
  summary: ClientSummary | null;
  /** Messages waiting for approval. */
  toApprove: number | null;
  /** Payments declared by a prospect and not confirmed yet. */
  toConfirm: number | null;
}

/** The answer when the call worked and has the expected shape; null otherwise (never a guess). */
const ok = <T,>(r: PromiseSettledResult<T>, shaped: (v: T) => boolean = () => true): T | null => (r.status === "fulfilled" && r.value && shaped(r.value) ? r.value : null);
const has = (key: string) => (v: unknown) => !!v && typeof v === "object" && key in (v as object);

export async function loadOverview(): Promise<{ overview: Overview; signedOut: boolean }> {
  const results = await Promise.allSettled([
    clientAgent(),
    clientReadiness(),
    clientSettings(),
    clientChannel(),
    clientSourceState(),
    clientLanes(),
    clientSummary(),
    clientProposals(),
    clientPayments(),
  ]);
  const [agent, readiness, settings, channel, source, lanes, summary, proposals, payments] = results;
  const signedOut = results.some((r) => r.status === "rejected" && failureOf(r.reason).signedOut);
  const proposalList = ok(proposals!, has("proposals")) as { proposals: unknown[] } | null;
  const paymentList = ok(payments!, has("cases")) as { cases: Array<{ progress: string; confirmed_at: string | null }> } | null;
  return {
    signedOut,
    overview: {
      agent: ok(agent!, has("state")) as AgentState | null,
      readiness: ok(readiness!, has("checks")) as Readiness | null,
      settings: ok(settings!, has("settings")) as SettingsState | null,
      channel: ok(channel!, has("options")) as ChannelState | null,
      source: ok(source!, has("options")) as SourceState | null,
      lanes: ok(lanes!, has("lanes")) as LanesState | null,
      summary: ok(summary!, has("by_status")) as ClientSummary | null,
      toApprove: proposalList && Array.isArray(proposalList.proposals) ? proposalList.proposals.length : null,
      toConfirm: paymentList && Array.isArray(paymentList.cases) ? paymentList.cases.filter((c) => c.confirmed_at === null && c.progress !== "paid").length : null,
    },
  };
}

/** The seven steps of the guide: the five that gate the agent, then "link it together" and "start". */
export interface GuideStep {
  id: "business" | "products" | "payments" | "prospects" | "channel" | "circuit" | "start";
  state: "done" | "todo" | "waiting";
  required: boolean;
  to: string;
  value?: string | number;
}

export function guideSteps(o: Overview): GuideStep[] {
  const commercial = o.settings?.settings.commercial as Doc | undefined;
  const base: Step[] = buildSteps({ commercial, readiness: o.readiness, channel: o.channel, source: o.source });
  const byId = (id: Step["id"]) => base.find((s) => s.id === id)!;
  const prospects = byId("prospects");
  const channel = byId("channel");
  const lanes = o.lanes?.lanes ?? [];
  // A circuit that already exists is not enough: it counts once what it links is done too, the
  // products, the prospects and the channel.
  const circuitDone = lanes.length > 0 && byId("products").state === "done" && prospects.state === "done" && channel.state === "done";
  const started = o.agent?.state === "running" || o.agent?.state === "paused";
  return [
    { ...byId("business"), to: "/offer/rules" },
    { ...byId("products"), to: "/offer/products" },
    { ...byId("payments"), to: "/offer/payments" },
    { ...prospects, to: "/circuits" },
    { ...channel, to: "/connections" },
    { id: "circuit", state: circuitDone ? "done" : "todo", required: true, to: "/circuits" },
    { id: "start", state: started ? "done" : "todo", required: true, to: "/home" },
  ];
}

interface Ctx {
  data: Overview | null;
  reload: () => Promise<void>;
}

const OverviewContext = createContext<Ctx | null>(null);

export function OverviewProvider({ children }: { children: ReactNode }) {
  const { logout } = useClientAuth();
  const [data, setData] = useState<Overview | null>(null);

  const reload = useCallback(async () => {
    const { overview, signedOut } = await loadOverview();
    if (signedOut) void logout();
    setData(overview);
  }, [logout]);

  useEffect(() => {
    let cancelled = false;
    void loadOverview().then(({ overview, signedOut }) => {
      if (cancelled) return;
      if (signedOut) void logout();
      setData(overview);
    });
    return () => {
      cancelled = true;
    };
  }, [logout]);

  const value = useMemo(() => ({ data, reload }), [data, reload]);
  return <OverviewContext.Provider value={value}>{children}</OverviewContext.Provider>;
}

export function useOverview(): Ctx {
  const ctx = useContext(OverviewContext);
  if (!ctx) throw new Error("useOverview must be used within OverviewProvider");
  return ctx;
}
