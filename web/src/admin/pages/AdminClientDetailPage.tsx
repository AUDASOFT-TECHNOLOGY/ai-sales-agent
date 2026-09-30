/** Read-only client detail: GET /admin/v1/clients/:clientId. Configuration only -- secrets are
 *  never in the response, so nothing here can render one (see src/sales_app/api/admin_clients.py:
 *  `*_env` fields are the name of the environment variable, never its value). */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { AdminApiError, adminGetClient, type ClientDetail } from "../api";

type LoadState = "loading" | "ready" | "error" | "not-found";

export function AdminClientDetailPage() {
  const { clientId } = useParams<{ clientId: string }>();
  // Remounts the view on every clientId change, so "loading" is always the initial state instead
  // of a setState called synchronously inside the effect to reset a stale previous client.
  return <ClientDetailView key={clientId} clientId={clientId} />;
}

function ClientDetailView({ clientId }: { clientId: string | undefined }) {
  const [client, setClient] = useState<ClientDetail | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!clientId) return;
    let cancelled = false;
    adminGetClient(clientId)
      .then((data) => {
        if (cancelled) return;
        setClient(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof AdminApiError && err.status === 404) {
          setState("not-found");
        } else {
          setError(err instanceof AdminApiError ? err.message : "Could not load this client.");
          setState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  return (
    <div className="admin-client-detail">
      <Link to="/admin/clients" className="admin-client-detail__back">
        <ArrowLeft size={16} aria-hidden />
        Back to Clients
      </Link>

      {state === "loading" ? (
        <p className="c-2" role="status" aria-live="polite">
          Loading…
        </p>
      ) : null}
      {state === "not-found" ? (
        <Glass level={1} className="admin-placeholder">
          <h2 className="t-h2">Client not found</h2>
          <p className="c-2">No client matches &ldquo;{clientId}&rdquo;, or you do not have access to it.</p>
        </Glass>
      ) : null}
      {state === "error" ? (
        <p className="c-danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && client ? (
        <>
          <Glass level={2} className="admin-client-detail__header">
            <div>
              <h2 className="t-h1">{client.display_name}</h2>
              <p className="c-3 t-body-sm tabular">{client.client_id}</p>
            </div>
            <Chip tint={client.status === "active" ? "success" : "neutral"}>{client.status === "active" ? "Active" : "Disabled"}</Chip>
          </Glass>

          <div className="admin-detail-grid">
            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">General</h3>
              <dl>
                <Field label="Mode" value={client.mode} />
                <Field label="Timezone" value={client.timezone} />
                <Field label="Daily action limit" value={client.daily_action_limit} />
                <Field label="Max attempts" value={client.max_attempts} />
                <Field label="Opt-out keywords" value={client.opt_out_keywords.join(", ") || null} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Sheets</h3>
              <dl>
                <Field label="Provider" value={client.sheets.provider} />
                <Field label="Spreadsheet ID" value={client.sheets.spreadsheet_id} mono />
                <Field
                  label="Tabs"
                  value={[client.sheets.prospects_tab, client.sheets.actions_tab, client.sheets.messages_tab, client.sheets.log_tab].filter(Boolean).join(" · ") || null}
                />
                <Field label="Credentials" value={client.sheets.credentials_env} secretName />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">LLM</h3>
              <dl>
                <Field label="Provider" value={client.llm.provider} />
                <Field label="Model" value={client.llm.model} />
                <Field label="API key" value={client.llm.api_key_env} secretName />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">WhatsApp</h3>
              <dl>
                <Field label="Provider" value={client.whatsapp.provider} />
                <Field label="API version" value={client.whatsapp.api_version} />
                <Field label="Access token" value={client.whatsapp.token_env} secretName />
                <Field label="Phone number ID" value={client.whatsapp.phone_number_id_env} secretName />
                <Field label="App secret" value={client.whatsapp.app_secret_env} secretName />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Brain</h3>
              <dl>
                <Field label="Factory" value={client.brain.factory} mono />
                <Field label="Settings version" value={client.brain.settings_version} />
              </dl>
            </Glass>

            <Glass level={1} className="admin-detail-card">
              <h3 className="t-label c-2">Schedule</h3>
              <dl>
                <Field label="Daily run" value={client.schedule.daily_at} />
                <Field label="Days" value={client.schedule.days.join(", ") || null} />
                <Field label="Prepare every" value={formatMinutes(client.schedule.prepare_every_minutes)} />
                <Field label="Dispatch every" value={formatMinutes(client.schedule.dispatch_every_minutes)} />
                <Field label="Recover every" value={formatMinutes(client.schedule.recover_every_minutes)} />
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

function Field({ label, value, mono, secretName }: { label: string; value: string | number | null; mono?: boolean; secretName?: boolean }) {
  return (
    <div className="admin-field-row">
      <dt className="c-3 t-body-sm">{label}</dt>
      <dd className={mono || secretName ? "c-2 t-body-sm admin-field-row__mono" : "c-2"}>
        {value ?? "—"}
        {secretName && value ? <span className="c-3 admin-field-row__hint"> (env var name only)</span> : null}
      </dd>
    </div>
  );
}
