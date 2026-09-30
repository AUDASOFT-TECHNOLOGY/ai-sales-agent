/** Read-only agent detail: GET /admin/v1/agents/:agentId. Configuration only -- secrets are never
 *  in the response, so nothing here can render one (see src/sales_app/api/admin_agents.py: only
 *  provider names and the Brain's factory/settings version are shown, never a `*_env` value). */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { AdminApiError, adminGetAgent, type AgentDetail } from "../api";

type LoadState = "loading" | "ready" | "error" | "not-found";

export function AdminAgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  // Remounts the view on every agentId change, so "loading" is always the initial state instead
  // of a setState called synchronously inside the effect to reset a stale previous agent.
  return <AgentDetailView key={agentId} agentId={agentId} />;
}

function AgentDetailView({ agentId }: { agentId: string | undefined }) {
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    adminGetAgent(agentId)
      .then((data) => {
        if (cancelled) return;
        setAgent(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof AdminApiError && err.status === 404) {
          setState("not-found");
        } else {
          setError(err instanceof AdminApiError ? err.message : "Could not load this agent.");
          setState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return (
    <div className="admin-client-detail">
      <Link to="/admin/agents" className="admin-client-detail__back">
        <ArrowLeft size={16} aria-hidden />
        Back to Agents
      </Link>

      {state === "loading" ? (
        <p className="c-2" role="status" aria-live="polite">
          Loading…
        </p>
      ) : null}
      {state === "not-found" ? (
        <Glass level={1} className="admin-placeholder">
          <h2 className="t-h2">Agent not found</h2>
          <p className="c-2">No agent matches &ldquo;{agentId}&rdquo;, or you do not have access to it.</p>
        </Glass>
      ) : null}
      {state === "error" ? (
        <p className="c-danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && agent ? (
        <>
          <Glass level={2} className="admin-client-detail__header">
            <div>
              <h2 className="t-h1">{agent.display_name}</h2>
              <p className="c-3 t-body-sm tabular">{agent.agent_id}</p>
            </div>
            <Chip tint={agent.status === "active" ? "success" : "neutral"}>{agent.status === "active" ? "Active" : "Disabled"}</Chip>
          </Glass>

          <div className="admin-detail-grid">
            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">General</h3>
              <dl>
                <Field label="Client" value={agent.client_id} mono />
                <Field label="Mode" value={agent.mode} />
                <Field label="Timezone" value={agent.timezone} />
                <Field label="Daily action limit" value={agent.daily_action_limit} />
                <Field label="Max attempts" value={agent.max_attempts} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Brain</h3>
              <dl>
                <Field label="Factory" value={agent.brain.factory} mono />
                <Field label="Settings version" value={agent.brain.settings_version} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">LLM</h3>
              <dl>
                <Field label="Provider" value={agent.llm.provider} />
                <Field label="Model" value={agent.llm.model} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Channels</h3>
              <dl>
                <Field label="Sheets" value={agent.channels.sheets} />
                <Field label="WhatsApp" value={agent.channels.whatsapp} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Schedule</h3>
              <dl>
                <Field label="Daily run" value={agent.schedule.daily_at} />
                <Field label="Days" value={agent.schedule.days.join(", ") || null} />
                <Field label="Prepare every" value={formatMinutes(agent.schedule.prepare_every_minutes)} />
                <Field label="Dispatch every" value={formatMinutes(agent.schedule.dispatch_every_minutes)} />
                <Field label="Recover every" value={formatMinutes(agent.schedule.recover_every_minutes)} />
              </dl>
            </Glass>
          </div>
        </>
      ) : null}
    </div>
  );
}

function formatMinutes(minutes: number | null): string | null {
  return minutes === null ? null : `${minutes} min`;
}

function Field({ label, value, mono }: { label: string; value: string | number | null; mono?: boolean }) {
  return (
    <div className="admin-field-row">
      <dt className="c-3 t-body-sm">{label}</dt>
      <dd className={mono ? "c-2 t-body-sm admin-field-row__mono" : "c-2"}>{value ?? "—"}</dd>
    </div>
  );
}
