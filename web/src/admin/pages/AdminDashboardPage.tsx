/** Admin landing page: real KPIs and nothing else. The top row is computed from the same
 *  endpoints the other Admin pages use (GET /admin/v1/clients and /admin/v1/agents). The activity,
 *  revenue and cost figures and the six chart families come from GET /admin/v1/dashboard; a figure
 *  that is not tracked renders "—" with its reason, never a fake zero. */

import { useEffect, useState } from "react";
import { Users, Cpu, Bot, Wallet, Send, MessageSquare, Target, TrendingUp, Sparkles, MessageCircle } from "lucide-react";
import { Glass, type Tint } from "../../ui/Glass";
import {
  AdminApiError,
  adminDashboard,
  adminListAgents,
  adminListClients,
  type AgentSummary,
  type ClientSummary,
} from "../api";
import { indicatorText, moneyMap, monthDefaults } from "../../dashboard/format";
import type { AdminDashboard, Indicator } from "../../dashboard/types";
import { AdminDashboardInsights } from "./AdminDashboardInsights";

type LoadState = "loading" | "ready" | "error";

const NO_DATA = "—";
const ALL = "ALL";

interface Loaded {
  clients: ClientSummary[];
  agents: AgentSummary[];
}

export function AdminDashboardPage() {
  const [data, setData] = useState<Loaded | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([adminListClients(), adminListAgents()])
      .then(([clients, agents]) => {
        if (cancelled) return;
        setData({ clients, agents });
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load dashboard data.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="admin-dashboard">
      {state === "loading" ? (
        <p className="admin-alert admin-alert--info" role="status" aria-live="polite">
          Loading dashboard…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {state === "ready" && data ? <DashboardBody data={data} /> : null}
    </div>
  );
}

function DashboardBody({ data }: { data: Loaded }) {
  const models = new Set(data.clients.map((c) => c.llm_model).filter((m): m is string => Boolean(m)));
  const [clientFilter, setClientFilter] = useState(ALL);
  const defaults = monthDefaults();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);
  const [insights, setInsights] = useState<{ key: string; value?: AdminDashboard; error?: string } | null>(null);
  const activeAgents = data.agents.filter((a) => a.status === "active").length;

  const key = `${from}|${to}|${clientFilter}`;
  useEffect(() => {
    let cancelled = false;
    adminDashboard({ from, to, client_id: clientFilter === ALL ? undefined : clientFilter })
      .then((value) => {
        if (!cancelled) setInsights({ key, value });
      })
      .catch((err: unknown) => {
        if (!cancelled) setInsights({ key, error: err instanceof AdminApiError ? err.message : "Could not load the figures." });
      });
    return () => {
      cancelled = true;
    };
  }, [from, to, clientFilter, key]);

  const current = insights?.key === key ? insights : null;
  const d = current?.value;
  const a = d?.activity;
  const sum = (name: string) => d?.costs.providers?.find((p) => p.provider === name);
  const costStat = (name: string) => {
    const p = sum(name);
    if (!p || p.availability === "not_tracked") return { value: NO_DATA, unavailable: true, note: p ? "Not tracked: price or amount not provided" : undefined };
    return { value: moneyMap(p.totals), unavailable: false, note: p.availability === "estimated" ? "Estimated" : p.complete === false ? "Lower bound" : undefined };
  };
  const stat = (ind: Indicator | undefined) => ({
    value: indicatorText(ind),
    unavailable: !ind || ind.value === null,
    note: ind?.availability === "not_tracked" ? ind.reason : ind?.availability === "estimated" ? "Estimated" : undefined,
  });
  const revenue = d?.finance.totals
    ? { value: moneyMap(Object.fromEntries(d.finance.totals.map((t) => [t.currency, t.collected]))), unavailable: false, note: "Collected (paid invoices only)" }
    : { value: NO_DATA, unavailable: true, note: d?.finance.reason };
  const loadingNote = current === null ? "Loading figures…" : undefined;

  return (
    <>
      <div className="admin-stat-grid">
        <Stat label="Clients" icon={Users} tint="action" value={String(data.clients.length)} />
        <Stat label="LLM Models configured" icon={Cpu} tint="success" value={String(models.size)} />
        <Stat label="Active Agents" icon={Bot} tint="agent" value={String(activeAgents)} />
        <Stat label="Total Revenue" icon={Wallet} tint="action" {...revenue} note={loadingNote ?? revenue.note} />
      </div>

      <div className="admin-dashboard__activity-head">
        <div>
          <h2 className="t-h2 admin-dashboard__section">Activity</h2>
          <p className="c-3 t-body-sm">
            {d ? `From ${d.period.from} to ${d.period.to}.` : "Figures for the selected period and client."}
          </p>
        </div>
        <div className="dv-filters">
          <label className="admin-dashboard__filter">
            <span className="c-2 t-body-sm">From</span>
            <input className="input well" type="month" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} />
          </label>
          <label className="admin-dashboard__filter">
            <span className="c-2 t-body-sm">To</span>
            <input className="input well" type="month" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} />
          </label>
          <label className="admin-dashboard__filter">
            <span className="c-2 t-body-sm">Client</span>
            <select className="input well" value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}>
              <option value={ALL}>ALL</option>
              {data.clients.map((c) => (
                <option key={c.client_id} value={c.client_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {current?.error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {current.error}
        </p>
      ) : null}
      <div className="admin-stat-grid">
        <Stat label="Prospects contacted" icon={Send} tint="action" {...stat(a?.prospects_contacted)} note={loadingNote ?? stat(a?.prospects_contacted).note} />
        <Stat label="Conversations" icon={MessageSquare} tint="action" {...stat(a?.conversations)} note={loadingNote ?? stat(a?.conversations).note} />
        <Stat label="Conversions" icon={Target} tint="action" {...stat(a?.conversions)} note={loadingNote ?? stat(a?.conversions).note} />
        <Stat label="Revenue" icon={TrendingUp} tint="action" {...revenue} note={loadingNote ?? revenue.note} />
        <Stat label="LLM cost" icon={Sparkles} tint="action" {...costStat("openai")} />
        <Stat label="WhatsApp cost" icon={MessageCircle} tint="action" {...costStat("meta_whatsapp")} />
      </div>
      {d ? <AdminDashboardInsights data={d} /> : null}
    </>
  );
}

function Stat({
  label,
  icon: Icon,
  tint,
  value,
  unavailable,
  note,
}: {
  label: string;
  icon: typeof Users;
  tint: Tint;
  value: string;
  unavailable?: boolean;
  note?: string;
}) {
  return (
    <Glass level={1} tint={tint} className={unavailable ? "admin-stat-card admin-stat-card--unavailable" : "admin-stat-card"}>
      <div className="admin-stat-card__head">
        <p className="c-2 t-body-sm">{label}</p>
        <span className="admin-stat-card__icon">
          <Icon size={18} aria-hidden />
        </span>
      </div>
      <p className="admin-stat-card__value tabular">{value}</p>
      {note ? <p className="c-3 t-body-sm">{note}</p> : null}
    </Glass>
  );
}
