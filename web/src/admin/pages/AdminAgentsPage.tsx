/** Read-only agent list: GET /admin/v1/agents (see src/sales_app/api/admin_agents.py).
 *  Today an "agent" is the client's single Brain -- `agent_id === client_id` -- so this mirrors
 *  AdminClientsPage exactly; only the fields shown differ. No fixture data. */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import { AdminApiError, adminListAgents, type AgentSummary } from "../api";

type LoadState = "loading" | "ready" | "error";

export function AdminAgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    adminListAgents()
      .then((data) => {
        if (cancelled) return;
        setAgents(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load agents.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => a.agent_id.toLowerCase().includes(q) || a.display_name.toLowerCase().includes(q));
  }, [agents, query]);

  return (
    <div className="admin-clients">
      <div className="admin-clients__toolbar">
        <TextInput
          label="Search agents"
          placeholder="Search by name or agent ID…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {state === "ready" ? (
          <span className="c-3 t-body-sm admin-clients__count">
            {filtered.length} of {agents.length}
          </span>
        ) : null}
      </div>

      {state === "loading" ? (
        <p className="c-2" role="status" aria-live="polite">
          Loading agents…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="c-danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && filtered.length === 0 ? (
        <Glass level={1} className="admin-placeholder">
          <p className="c-2">{agents.length === 0 ? "No agents are configured yet." : "No agent matches your search."}</p>
        </Glass>
      ) : null}

      {state === "ready" && filtered.length > 0 ? (
        <ul className="admin-client-list">
          {filtered.map((a) => (
            <li key={a.agent_id}>
              <Glass as={Link} to={`/admin/agents/${encodeURIComponent(a.agent_id)}`} level={1} className="admin-client-card">
                <div className="admin-client-card__head">
                  <span className="t-label c-1">{a.display_name}</span>
                  <Chip tint={a.status === "active" ? "success" : "neutral"}>{a.status === "active" ? "Active" : "Disabled"}</Chip>
                </div>
                <p className="c-3 t-body-sm tabular">{a.agent_id}</p>
                <dl className="admin-client-card__meta">
                  <div>
                    <dt className="c-3 t-body-sm">Mode</dt>
                    <dd className="c-2">{a.mode ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="c-3 t-body-sm">Brain</dt>
                    <dd className="c-2 tabular">{a.brain.factory ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="c-3 t-body-sm">LLM</dt>
                    <dd className="c-2">{a.llm.model ?? "—"}</dd>
                  </div>
                </dl>
              </Glass>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
