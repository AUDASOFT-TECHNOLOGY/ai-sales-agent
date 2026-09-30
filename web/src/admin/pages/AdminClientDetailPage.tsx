/** Client detail: GET /admin/v1/clients/:clientId. Configuration only -- secrets are never in the
 *  response, so nothing here can render one (see src/sales_app/api/admin_clients.py: `*_env`
 *  fields are the name of the environment variable, never its value). The LLM section is the one
 *  writable field: PUT /admin/v1/clients/:clientId/llm, gated to providers actually wired into
 *  this deployment and models the deployment's LLMRegistry allows -- never a free choice, never an
 *  API key. */

import { useEffect, useId, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminGetClient,
  adminGetLLMSettings,
  adminUpdateClientLLM,
  type ClientDetail,
  type ClientLLM,
  type LLMSettings,
} from "../api";

// Mirrors WRITE_ROLES in src/sales_app/api/admin_clients.py: owner, operator and editor may
// change a client's LLM configuration; a viewer may only look at it.
const CAN_EDIT_LLM_ROLES = new Set(["owner", "operator", "editor"]);

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
                <Field label="API key" value={client.llm.api_key_env} secretName />
              </dl>
              <LLMConfigForm clientId={client.client_id} llm={client.llm} onUpdated={(llm) => setClient((c) => (c ? { ...c, llm } : c))} />
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

type LLMFormState = "loading" | "ready" | "error";

/** Provider + model editor for one client, gated to what `LLMSettings` actually allows: a wired
 *  provider, and (when the deployment allow-lists models) one of its allowed models. Never shows
 *  or asks for an API key -- that stays operator wiring (`client.llm.api_key_env`, shown above). */
function LLMConfigForm({ clientId, llm, onUpdated }: { clientId: string; llm: ClientLLM; onUpdated: (llm: ClientLLM) => void }) {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_LLM_ROLES.has(admin.role);
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [state, setState] = useState<LLMFormState>("loading");
  const [provider, setProvider] = useState(llm.provider ?? "");
  const [model, setModel] = useState(llm.model ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminGetLLMSettings()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        setState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "loading") return <p className="c-2 t-body-sm">Loading providers…</p>;
  if (state === "error" || !settings) return <p className="c-danger t-body-sm">Could not load the available LLM providers.</p>;

  const wiredProviders = settings.providers.filter((p) => p.wired);
  const allowedModels = settings.providers.find((p) => p.provider === provider)?.allowed_models ?? null;
  const noModelsAllowed = allowedModels !== null && allowedModels.length === 0;
  const dirty = provider !== (llm.provider ?? "") || model !== (llm.model ?? "");

  function onProviderChange(next: string) {
    setProvider(next);
    setSaved(false);
    const models = settings?.providers.find((p) => p.provider === next)?.allowed_models ?? null;
    if (models && models.length > 0 && !models.includes(model)) setModel(models[0] ?? "");
  }

  async function onSave() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await adminUpdateClientLLM(clientId, { provider, model });
      onUpdated(updated);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof AdminApiError ? err.message : "Could not update the LLM configuration.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="admin-llm-config">
      {wiredProviders.length === 0 ? (
        <p className="c-danger t-body-sm">No LLM provider is wired into this deployment.</p>
      ) : (
        <>
          <SelectField label="Provider" value={provider} onChange={onProviderChange} disabled={!canEdit}>
            {wiredProviders.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.provider}
              </option>
            ))}
          </SelectField>

          {allowedModels === null ? (
            <FieldShell id="llm-model-input" label="Model" helper="Any model is accepted for this provider.">
              <input
                id="llm-model-input"
                className="input well"
                value={model}
                disabled={!canEdit}
                onChange={(e) => {
                  setModel(e.target.value);
                  setSaved(false);
                }}
              />
            </FieldShell>
          ) : noModelsAllowed ? (
            <p className="c-danger t-body-sm">No model is allow-listed for this provider yet.</p>
          ) : (
            <SelectField
              label="Model"
              value={allowedModels.includes(model) ? model : ""}
              disabled={!canEdit}
              onChange={(v) => {
                setModel(v);
                setSaved(false);
              }}
            >
              <option value="" disabled>
                Select a model…
              </option>
              {allowedModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </SelectField>
          )}

          {saveError ? (
            <p className="c-danger t-body-sm" role="alert">
              {saveError}
            </p>
          ) : null}
          {saved ? (
            <p className="c-success t-body-sm" role="status">
              Saved.
            </p>
          ) : null}

          <Button
            variant="primary"
            size="sm"
            loading={saving}
            disabled={!provider || !model || noModelsAllowed || !dirty}
            disabledReason={!canEdit ? "You do not have permission to edit this client's LLM configuration." : undefined}
            onClick={onSave}
          >
            Save
          </Button>
        </>
      )}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  disabled,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <FieldShell id={id} label={label}>
      <select id={id} className="input well" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </FieldShell>
  );
}
