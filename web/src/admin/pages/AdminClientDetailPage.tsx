/** Client sheet: GET /admin/v1/clients/:clientId. A business view of the client -- name, login,
 *  status, WhatsApp number, revenue, prospects contacted, daily quota and the LLM model in use.
 *  No runtime/agent internals and no Meta/WhatsApp or other credentials are shown or editable here;
 *  only the client's Meta onboarding status, which is never "connected" until a real onboarding exists.
 *  The LLM model is the one writable field: PUT /admin/v1/clients/:clientId/llm, restricted to the
 *  models the deployment allows for the client's provider (the provider itself is not edited here
 *  and no API key is ever asked for). */

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell, TextInput } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import { AdminClientActivationCard } from "./AdminEmailActivation";
import { AdminClientMailboxCard } from "./AdminClientMailboxCard";
import { AdminClientTelegramCard } from "./AdminClientTelegramCard";
import { AdminClientSendCapCard } from "./AdminClientSendCapCard";
import { AdminClientSourcesCard } from "./AdminClientSourcesCard";
import {
  AdminApiError,
  adminConnectClientTelegram,
  adminDisconnectClientTelegram,
  adminGetClient,
  adminGetClientPlan,
  adminGetLLMSettings,
  adminListClientCredentials,
  adminListPlans,
  adminSaveClientCredential,
  adminSetClientPlan,
  adminTestClientTelegram,
  adminUpdateClient,
  adminUpdateClientLLM,
  type ClientDetail,
  type ClientLLM,
  type ClientPlan,
  type LLMSettings,
  type Plan,
  type UpdateClientRequest,
} from "../api";

// Mirrors require_owner on PUT /clients/:id/llm in src/sales_app/api/admin_clients.py: only an
// owner assigns a client's LLM model; any other role may only look at it.
const CAN_EDIT_LLM_ROLES = new Set(["owner"]);

// Mirrors require_owner on PATCH /clients/:id: only an owner may edit a client's profile.
const CAN_EDIT_CLIENT_ROLES = new Set(["owner"]);

// Mirrors require_owner on PUT /clients/:id/plan: only an owner puts a client on a plan.
const CAN_EDIT_PLAN_ROLES = new Set(["owner"]);

const MAX_LOGO_BYTES = 256 * 1024;

const NOT_AVAILABLE = "—";

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
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

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
          setError(
            err instanceof AdminApiError
              ? err.message
              : "Could not load this client.",
          );
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
        <p
          className="admin-alert admin-alert--info"
          role="status"
          aria-live="polite"
        >
          Loading…
        </p>
      ) : null}
      {state === "not-found" ? (
        <Glass
          level={1}
          className="admin-placeholder admin-placeholder--center"
        >
          <h2 className="t-h2">Client not found</h2>
          <p className="c-2">
            No client matches &ldquo;{clientId}&rdquo;, or you do not have
            access to it.
          </p>
        </Glass>
      ) : null}
      {state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && client && editing ? (
        <ClientEditForm
          client={client}
          onCancel={() => setEditing(false)}
          onSaved={(updated) => {
            setClient(updated);
            setEditing(false);
            setNotice("Client mis à jour.");
          }}
        />
      ) : null}
      {state === "ready" && client && !editing ? (
        <ClientSheet
          client={client}
          notice={notice}
          onEdit={() => {
            setNotice(null);
            setEditing(true);
          }}
          onLLMUpdated={(llm) => setClient((c) => (c ? { ...c, llm } : c))}
        />
      ) : null}
    </div>
  );
}

/** Loads which Telegram pieces are stored (never their values) and mounts the bot card with its calls.
 *  Only an owner may change a client's bot: the routes are owner-only on the server. */
function ClientTelegramSection({ clientId, canEdit }: { clientId: string; canEdit: boolean }) {
  const [stored, setStored] = useState<{ token: boolean; connected: boolean } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    adminListClientCredentials(clientId)
      .then((list) => {
        if (!alive) return;
        const has = (key: string) => list.some((c) => c.key === key && c.configured);
        setStored({ token: has("telegram.bot_token"), connected: has("telegram.webhook_secret") });
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [clientId]);
  if (failed) {
    return (
      <p className="admin-alert admin-alert--danger" role="alert">
        Could not load this client&apos;s Telegram status.
      </p>
    );
  }
  if (stored === null) return <p className="c-3 t-body-sm">Loading…</p>;
  return (
    <AdminClientTelegramCard
      clientId={clientId}
      canEdit={canEdit}
      tokenConfigured={stored.token}
      connected={stored.connected}
      onSaveToken={(token) => adminSaveClientCredential(clientId, "telegram.bot_token", token)}
      onConnect={() => adminConnectClientTelegram(clientId)}
      onTest={() => adminTestClientTelegram(clientId)}
      onDisconnect={() => adminDisconnectClientTelegram(clientId)}
    />
  );
}

function ClientSheet({
  client,
  notice,
  onEdit,
  onLLMUpdated,
}: {
  client: ClientDetail;
  notice: string | null;
  onEdit: () => void;
  onLLMUpdated: (llm: ClientLLM) => void;
}) {
  const { admin } = useAdminAuth();
  const canEditClient = admin !== null && CAN_EDIT_CLIENT_ROLES.has(admin.role);
  const active = client.status === "active";
  const whatsapp = client.account?.whatsapp_number ?? null;
  return (
    <>
      <Glass level={2} className="admin-client-detail__header">
        <div className="admin-client-detail__identity">
          {client.account?.logo ? (
            <img
              className="admin-client-detail__logo"
              src={client.account.logo}
              alt={`Logo ${client.display_name}`}
            />
          ) : (
            <span className="admin-client-detail__avatar" aria-hidden>
              {client.display_name.slice(0, 2).toUpperCase()}
            </span>
          )}
          <div>
            <h2 className="t-h1">{client.display_name}</h2>
            <p className="c-3 t-body-sm tabular">
              {client.account?.login ?? client.client_id}
            </p>
          </div>
        </div>
        <div className="admin-client-detail__actions">
          <Chip tint={active ? "success" : "neutral"}>
            {active ? "Active" : "Inactive"}
          </Chip>
          {canEditClient ? (
            <Button variant="primary" size="sm" onClick={onEdit}>
              Modifier
            </Button>
          ) : null}
        </div>
      </Glass>

      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {notice}
        </p>
      ) : null}
      {client.restart_required ? (
        <p className="admin-alert admin-alert--info">
          Le changement de statut sera appliqué au prochain redémarrage de
          l&apos;application.
        </p>
      ) : null}

      <div className="admin-client-detail__kpis">
        <Kpi
          label="Revenue"
          value={
            client.revenue === null ? NOT_AVAILABLE : String(client.revenue)
          }
          unavailable={client.revenue === null}
        />
        <Kpi
          label="Prospects contacted"
          value={
            client.prospects_contacted === null
              ? NOT_AVAILABLE
              : String(client.prospects_contacted)
          }
          unavailable={client.prospects_contacted === null}
        />
        <Kpi
          label="Daily quota"
          value={
            client.daily_action_limit === null
              ? NOT_AVAILABLE
              : String(client.daily_action_limit)
          }
          unavailable={client.daily_action_limit === null}
        />
      </div>

      <div className="admin-detail-grid">
        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Client</h3>
          <dl>
            <Field label="Name" value={client.display_name} />
            <Field label="Login" value={client.account?.login ?? null} mono />
            <Field label="Status" value={active ? "Active" : "Inactive"} />
            <Field label="WhatsApp number" value={whatsapp} />
            <Field
              label="Meta"
              value={
                client.meta?.status === "credentials_stored"
                  ? "Credentials stored (not verified)"
                  : "Not connected"
              }
            />
          </dl>
          {!client.account ? (
            <p className="c-3 t-body-sm">
              No account profile is on record for this client.
            </p>
          ) : !whatsapp ? (
            <p className="c-3 t-body-sm">
              No WhatsApp number is on record for this client.
            </p>
          ) : null}
          {client.meta?.status === "not_connected" ? (
            <p className="c-3 t-body-sm">
              The WhatsApp onboarding with Meta has not been run for this
              number, so the agent cannot send messages yet. The official
              onboarding is not implemented in Revansa yet.
            </p>
          ) : null}
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">LLM model</h3>
          <p className="admin-detail-card__value">
            {client.llm.model ?? NOT_AVAILABLE}
          </p>
          <LLMModelForm
            clientId={client.client_id}
            llm={client.llm}
            onUpdated={onLLMUpdated}
          />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Plan</h3>
          <ClientPlanCard clientId={client.client_id} />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Daily email limit</h3>
          <AdminClientSendCapCard clientId={client.client_id} />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Prospect sources</h3>
          <AdminClientSourcesCard clientId={client.client_id} />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Email activation</h3>
          <AdminClientActivationCard clientId={client.client_id} />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Email mailbox</h3>
          <AdminClientMailboxCard clientId={client.client_id} />
        </Glass>

        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Telegram bot</h3>
          <ClientTelegramSection clientId={client.client_id} canEdit={admin !== null && CAN_EDIT_LLM_ROLES.has(admin.role)} />
        </Glass>
      </div>
    </>
  );
}

function Kpi({
  label,
  value,
  unavailable,
}: {
  label: string;
  value: string;
  unavailable?: boolean;
}) {
  return (
    <Glass level={1} className="admin-kpi">
      <p className="t-label c-2">{label}</p>
      <p className="admin-kpi__value">{value}</p>
      {unavailable ? <p className="c-3 t-body-sm">Not available</p> : null}
    </Glass>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number | null;
  mono?: boolean;
}) {
  return (
    <div className="admin-field-row">
      <dt className="c-3 t-body-sm">{label}</dt>
      <dd className={mono ? "c-2 t-body-sm admin-field-row__mono" : "c-2"}>
        {value ?? NOT_AVAILABLE}
      </dd>
    </div>
  );
}

type LLMFormState = "loading" | "ready" | "error";

interface ModelOption {
  key: string; // provider + model: the same model id may exist under two providers
  provider: string;
  model: string;
  label: string; // the name Settings gives the model, else its id
}

const optionKey = (provider: string, model: string) => `${provider}::${model}`;

/** The models a client may use: those explicitly enabled in Settings for a provider wired here. */
function modelOptions(settings: LLMSettings): ModelOption[] {
  return settings.providers
    .filter((p) => p.wired)
    .flatMap((p) =>
      (p.allowed_models ?? []).map((model) => ({
        key: optionKey(p.provider, model),
        provider: p.provider,
        model,
        label: p.models?.find((m) => m.id === model)?.name ?? model,
      })),
    );
}

/** Model picker for one client. Options come only from Settings (`GET /settings/llm`): the models
 *  explicitly enabled for a provider wired in this deployment. Nothing is hard-coded here and this
 *  form cannot add a model -- that belongs to Settings. A provider without an allow-list contributes
 *  no option. Never shows or asks for an API key. */
/** The client's plan (GET/PUT /admin/v1/clients/:id/plan). What is shown is the assignment: the
 *  terms as they were when the client was put on the plan, which a later edit of the plan does not
 *  change. Only an owner assigns. */
function ClientPlanCard({ clientId }: { clientId: string }) {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_PLAN_ROLES.has(admin.role);
  const selectId = useId();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [plan, setPlan] = useState<ClientPlan | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([adminGetClientPlan(clientId), adminListPlans()])
      .then(([assignment, list]) => {
        if (cancelled) return;
        setPlan(assignment);
        setPlans(list);
        setSelected(assignment.current?.plan_id ?? "");
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (state === "loading") return <p className="c-2 t-body-sm">Loading…</p>;
  if (state === "error" || !plan)
    return <p className="admin-alert admin-alert--danger">Could not load the client&apos;s plan.</p>;

  const current = plan.current;
  const currentId = current?.plan_id ?? "";
  // The current plan stays selectable even if it was switched off since: say so rather than hide it.
  const options = plans.filter((p) => p.active || p.plan_id === currentId);
  const dirty = selected !== currentId;

  async function onSave() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await adminSetClientPlan(clientId, selected || null);
      setPlan(updated);
      setSelected(updated.current?.plan_id ?? "");
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof AdminApiError ? err.message : "Could not update the plan.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="admin-llm-config">
      <p className="admin-detail-card__value">{current ? current.plan_name : NOT_AVAILABLE}</p>
      {current ? (
        <dl>
          <Field label="Monthly price" value={`${current.monthly_price} ${current.currency}`} />
          <Field label="Included credits" value={String(current.included_credits)} />
          <Field label="Daily quota" value={String(current.daily_quota)} />
          <Field label="Since" value={new Date(current.started_at).toLocaleDateString()} />
        </dl>
      ) : (
        <p className="c-3 t-body-sm">This client is not on a plan.</p>
      )}
      <FieldShell id={selectId} label="Plan">
        <select
          id={selectId}
          className="input well"
          value={selected}
          disabled={!canEdit}
          onChange={(e) => {
            setSelected(e.target.value);
            setSaved(false);
          }}
        >
          <option value="">Aucun plan</option>
          {options.map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              {p.active ? p.name : `${p.name} (inactive)`}
            </option>
          ))}
        </select>
      </FieldShell>
      {saveError ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {saveError}
        </p>
      ) : null}
      {saved ? (
        <p className="admin-alert admin-alert--success" role="status">
          Saved.
        </p>
      ) : null}
      <Button
        variant="primary"
        size="sm"
        loading={saving}
        disabled={!dirty}
        disabledReason={!canEdit ? "Only an owner can change a client's plan." : undefined}
        onClick={onSave}
      >
        Save plan
      </Button>
    </div>
  );
}

function LLMModelForm({
  clientId,
  llm,
  onUpdated,
}: {
  clientId: string;
  llm: ClientLLM;
  onUpdated: (llm: ClientLLM) => void;
}) {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_LLM_ROLES.has(admin.role);
  const selectId = useId();
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [state, setState] = useState<LLMFormState>("loading");
  const [selected, setSelected] = useState(
    llm.provider && llm.model ? optionKey(llm.provider, llm.model) : "",
  );
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

  if (state === "loading") return <p className="c-2 t-body-sm">Loading…</p>;
  if (state === "error" || !settings)
    return (
      <p className="admin-alert admin-alert--danger">
        Could not load the models configured in Settings.
      </p>
    );

  const options = modelOptions(settings);
  const multiProvider = new Set(options.map((o) => o.provider)).size > 1;

  const chosen = options.find((o) => o.key === selected);
  const dirty =
    selected !==
    (llm.provider && llm.model ? optionKey(llm.provider, llm.model) : "");
  // The client's saved model may no longer be enabled in Settings: say so rather than hide it.
  const currentlyDisabled =
    llm.model !== null &&
    !options.some(
      (o) => o.key === optionKey(llm.provider ?? "", llm.model as string),
    );

  async function onSave() {
    if (!chosen) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await adminUpdateClientLLM(clientId, {
        provider: chosen.provider,
        model: chosen.model,
      });
      onUpdated(updated);
      setSaved(true);
    } catch (err) {
      setSaveError(
        err instanceof AdminApiError
          ? err.message
          : "Could not update the LLM model.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="admin-llm-config">
      {currentlyDisabled ? (
        <p className="admin-alert admin-alert--info">
          This client&apos;s current model is no longer enabled in Settings.
        </p>
      ) : null}
      <FieldShell
        id={selectId}
        label="Modèle LLM"
        helper={
          options.length === 0
            ? "No active LLM model is configured in Settings."
            : undefined
        }
      >
        <select
          id={selectId}
          className="input well"
          value={chosen ? selected : ""}
          disabled={!canEdit || options.length === 0}
          onChange={(e) => {
            setSelected(e.target.value);
            setSaved(false);
          }}
        >
          <option value="" disabled>
            {options.length === 0 ? "No model available" : "Select a model…"}
          </option>
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {multiProvider ? `${o.label} (${o.provider})` : o.label}
            </option>
          ))}
        </select>
      </FieldShell>

      {saveError ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {saveError}
        </p>
      ) : null}
      {saved ? (
        <p className="admin-alert admin-alert--success" role="status">
          Saved.
        </p>
      ) : null}

      <Button
        variant="primary"
        size="sm"
        loading={saving}
        disabled={!chosen || !dirty}
        disabledReason={
          !canEdit
            ? "Only an owner can assign a model to a client."
            : undefined
        }
        onClick={onSave}
      >
        Save
      </Button>
    </div>
  );
}

/** Edit mode of the client sheet: PATCH /admin/v1/clients/:clientId. Only the fields that changed are
 *  sent; an empty "new password" keeps the current one. The LLM list comes from Settings, as in
 *  `LLMModelForm`. There is no field for any Meta credential: the WhatsApp number is profile data. */
function ClientEditForm({
  client,
  onCancel,
  onSaved,
}: {
  client: ClientDetail;
  onCancel: () => void;
  onSaved: (client: ClientDetail) => void;
}) {
  const formId = useId();
  const modelId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const currentModelKey =
    client.llm.provider && client.llm.model
      ? optionKey(client.llm.provider, client.llm.model)
      : "";
  const currentLogo = client.account?.logo ?? null;
  const currentStatus = client.status === "active" ? "active" : "inactive";
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  const [name, setName] = useState(client.display_name);
  const [logo, setLogo] = useState<string | null>(currentLogo);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [login, setLogin] = useState(client.account?.login ?? "");
  const [newPassword, setNewPassword] = useState("");
  const [whatsapp, setWhatsapp] = useState(
    client.account?.whatsapp_number ?? "",
  );
  const [model, setModel] = useState(currentModelKey);
  const [quota, setQuota] = useState(
    client.daily_action_limit === null ? "" : String(client.daily_action_limit),
  );
  const [status, setStatus] = useState<"active" | "inactive">(currentStatus);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasProfile = client.account !== null;

  useEffect(() => {
    let cancelled = false;
    adminGetLLMSettings()
      .then((data) => {
        if (!cancelled) setSettings(data);
      })
      .catch(() => {
        if (!cancelled) setSettingsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options = settings ? modelOptions(settings) : [];
  const multiProvider = new Set(options.map((o) => o.provider)).size > 1;
  const chosen = options.find((o) => o.key === model);
  const quotaNumber = Number(quota);
  const quotaValid =
    quota.trim() !== "" &&
    Number.isInteger(quotaNumber) &&
    quotaNumber >= 1 &&
    quotaNumber <= 1000;

  function onLogoChange(file: File | undefined) {
    setLogoError(null);
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setLogoError("Logo must be a PNG, JPEG or WebP image.");
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError(`Logo must be at most ${MAX_LOGO_BYTES / 1024} KB.`);
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () =>
      setLogo(typeof reader.result === "string" ? reader.result : currentLogo);
    reader.onerror = () => setLogoError("Could not read this image.");
    reader.readAsDataURL(file);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const body: UpdateClientRequest = {};
    if (name.trim() !== client.display_name) body.display_name = name.trim();
    if (
      hasProfile ||
      login.trim() !== "" ||
      newPassword !== "" ||
      whatsapp.trim() !== ""
    ) {
      if (logo !== currentLogo) body.logo = logo;
      if (login.trim() !== client.account?.login) body.login = login.trim();
      if (newPassword) body.new_password = newPassword;
      if (whatsapp.trim() !== (client.account?.whatsapp_number ?? ""))
        body.whatsapp_number = whatsapp.trim();
    }
    if (chosen && model !== currentModelKey) {
      body.llm_provider = chosen.provider;
      body.llm_model = chosen.model;
    }
    if (quotaValid && quotaNumber !== client.daily_action_limit)
      body.daily_action_limit = quotaNumber;
    if (status !== currentStatus) body.status = status;

    setSaving(true);
    setError(null);
    try {
      const updated =
        Object.keys(body).length === 0
          ? client
          : await adminUpdateClient(client.client_id, body);
      onSaved(updated);
    } catch (err) {
      setError(
        err instanceof AdminApiError
          ? err.message
          : "Could not update the client.",
      );
      setSaving(false);
    }
  }

  const profileFilled = [login.trim(), newPassword, whatsapp.trim()].filter(
    (v) => v !== "",
  ).length;
  const profileOk = hasProfile
    ? login.trim() !== "" && whatsapp.trim() !== ""
    : profileFilled === 0 || profileFilled === 3;
  const ready = name.trim() !== "" && quotaValid && profileOk;

  return (
    <Glass level={2} className="admin-detail-card admin-detail-card--form">
      <h3 className="t-h2">Modifier le client</h3>
      <form
        id={formId}
        className="admin-form admin-form--grid"
        onSubmit={(e) => void onSubmit(e)}
      >
        <TextInput
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          required
          autoComplete="off"
        />

        {!hasProfile ? (
          <p className="admin-alert admin-alert--info admin-form__full">
            No account profile is on record for this client yet. Fill in login,
            new password and WhatsApp number together to create it.
          </p>
        ) : null}
        {
          <>
            <div className="field admin-form__full">
              <label className="t-label c-2" htmlFor={`${formId}-logo`}>
                Logo <span className="c-3">· optionnel</span>
              </label>
              <input
                id={`${formId}-logo`}
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(e) => onLogoChange(e.target.files?.[0])}
              />
              {logo ? (
                <img
                  className="admin-form__logo"
                  src={logo}
                  alt="Aperçu du logo"
                />
              ) : null}
              {logo ? (
                <Button
                  variant="ghost"
                  size="sm"
                  type="button"
                  onClick={() => {
                    setLogo(null);
                    if (fileRef.current) fileRef.current.value = "";
                  }}
                >
                  Remove logo
                </Button>
              ) : null}
              {logoError ? (
                <p className="field__msg admin-field-error" role="alert">
                  {logoError}
                </p>
              ) : null}
            </div>

            <TextInput
              label="Login"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              required={hasProfile}
              autoComplete="off"
            />
            <TextInput
              label="New password"
              optional="optional"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              minLength={8}
              autoComplete="new-password"
              required={
                !hasProfile && (login.trim() !== "" || whatsapp.trim() !== "")
              }
              helper={
                hasProfile
                  ? "Leave empty to keep the current password. 8 characters minimum."
                  : "8 characters minimum."
              }
            />
          </>
        }

        <TextInput
          label="WhatsApp number"
          type="tel"
          value={whatsapp}
          onChange={(e) => setWhatsapp(e.target.value)}
          placeholder="+216 22 768 307"
          maxLength={40}
          required={hasProfile}
          autoComplete="off"
          helper="Le numéro que l'agent commercial utilisera avec les prospects."
        />

        <FieldShell
          id={modelId}
          label="Modèle LLM"
          helper={
            settingsFailed
              ? "Could not load the models configured in Settings."
              : settings && options.length === 0
                ? "No active LLM model is configured in Settings."
                : undefined
          }
        >
          <select
            id={modelId}
            className="input well"
            value={chosen ? model : ""}
            disabled={options.length === 0}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="" disabled>
              {options.length === 0 ? "No model available" : "Select a model…"}
            </option>
            {options.map((o) => (
              <option key={o.key} value={o.key}>
                {multiProvider ? `${o.label} (${o.provider})` : o.label}
              </option>
            ))}
          </select>
        </FieldShell>

        <TextInput
          label="Daily quota"
          type="number"
          min={1}
          max={1000}
          step={1}
          value={quota}
          onChange={(e) => setQuota(e.target.value)}
          required
          error={
            quota !== "" && !quotaValid
              ? "Daily quota must be a whole number between 1 and 1000."
              : undefined
          }
        />

        <fieldset className="admin-form__choice">
          <legend className="t-label c-2">Status</legend>
          <label>
            <input
              type="radio"
              name={`${formId}-status`}
              checked={status === "active"}
              onChange={() => setStatus("active")}
            />{" "}
            Active
          </label>
          <label>
            <input
              type="radio"
              name={`${formId}-status`}
              checked={status === "inactive"}
              onChange={() => setStatus("inactive")}
            />{" "}
            Inactive
          </label>
        </fieldset>

        {error ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {error}
          </p>
        ) : null}

        <div className="admin-form__actions">
          <Button
            variant="ghost"
            type="button"
            onClick={onCancel}
            disabled={saving}
          >
            Annuler
          </Button>
          <Button
            variant="primary"
            type="submit"
            loading={saving}
            disabled={!ready}
          >
            Enregistrer
          </Button>
        </div>
      </form>
    </Glass>
  );
}
