/** Admin landing page: a few real KPIs and nothing else. The top row is computed from the same
 *  endpoints the other Admin pages use (GET /admin/v1/clients and /admin/v1/agents). The activity
 *  metrics have no backing source yet, so they render "—" rather than a fake zero. */

import { useEffect, useState } from "react";
import { Users, Cpu, Bot, Wallet, Send, MessageSquare, Target, TrendingUp, Sparkles, MessageCircle } from "lucide-react";
import { Glass, type Tint } from "../../ui/Glass";
import {
  AdminApiError,
  adminListAgents,
  adminListClients,
  type AgentSummary,
  type ClientSummary,
} from "../api";

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
  const activeAgents = data.agents.filter((a) => a.status === "active").length;
  return (
    <>
      <div className="admin-stat-grid">
        <Stat label="Clients" icon={Users} tint="action" value={String(data.clients.length)} />
        <Stat label="LLM Models configured" icon={Cpu} tint="success" value={String(models.size)} />
        <Stat label="Active Agents" icon={Bot} tint="agent" value={String(activeAgents)} />
        <Stat label="Total Revenue" icon={Wallet} tint="action" value={NO_DATA} unavailable />
      </div>

      <div className="admin-dashboard__activity-head">
        <div>
          <h2 className="t-h2 admin-dashboard__section">Activity</h2>
          <p className="c-3 t-body-sm">Per-client figures appear here once usage is tracked.</p>
        </div>
        <label className="admin-dashboard__filter">
          <span className="c-2 t-body-sm">Client</span>
          <select
            className="input well"
            value={clientFilter}
            onChange={(e) => setClientFilter(e.target.value)}
          >
            <option value={ALL}>ALL</option>
            {data.clients.map((c) => (
              <option key={c.client_id} value={c.client_id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* No per-client activity source exists yet, so every scope shows "—" until one does. */}
      <div className="admin-stat-grid">
        <Stat label="Prospects contacted" icon={Send} tint="action" value={NO_DATA} unavailable />
        <Stat label="Conversations" icon={MessageSquare} tint="action" value={NO_DATA} unavailable />
        <Stat label="Conversions" icon={Target} tint="action" value={NO_DATA} unavailable />
        <Stat label="Revenue" icon={TrendingUp} tint="action" value={NO_DATA} unavailable />
        <Stat label="LLM cost" icon={Sparkles} tint="action" value={NO_DATA} unavailable />
        <Stat label="WhatsApp cost" icon={MessageCircle} tint="action" value={NO_DATA} unavailable />
      </div>
    </>
  );
}

function Stat({
  label,
  icon: Icon,
  tint,
  value,
  unavailable,
}: {
  label: string;
  icon: typeof Users;
  tint: Tint;
  value: string;
  unavailable?: boolean;
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
    </Glass>
  );
}
