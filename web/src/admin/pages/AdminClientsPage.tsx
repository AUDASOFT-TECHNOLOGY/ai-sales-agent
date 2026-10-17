/** Clients: a table of the real Revansa clients (GET /admin/v1/clients -- every row exists in
 *  `clients/<id>/client.yaml`), a "View details" link per row, and the "+ Ajouter un client" form
 *  (POST /admin/v1/clients). No fixture data. The form asks for the client's WhatsApp number (the
 *  number their sales agent writes from); it never asks for any Meta credential. */

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell, TextInput } from "../../ui/Controls";
import { Modal } from "../../ui/Overlays";
import {
  AdminApiError,
  adminCreateClient,
  adminGetLLMSettings,
  adminListClients,
  adminListPlans,
  type ClientSummary,
  type LLMSettings,
  type Plan,
} from "../api";

type LoadState = "loading" | "ready" | "error";

const MAX_LOGO_BYTES = 256 * 1024;

export function AdminClientsPage() {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminListClients()
      .then((data) => {
        if (cancelled) return;
        setClients(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load clients.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function onCreated(created: ClientSummary) {
    setClients((current) => [...current, created].sort((a, b) => a.client_id.localeCompare(b.client_id)));
    setAdding(false);
  }

  return (
    <div className="admin-clients">
      <div className="admin-clients__header">
        <div className="admin-clients__title">
          <h2 className="t-h2">Clients</h2>
          {state === "ready" ? <span className="admin-count tabular">{clients.length}</span> : null}
        </div>
        <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
          + Ajouter un client
        </Button>
      </div>

      {state === "loading" ? (
        <p className="admin-alert admin-alert--info" role="status" aria-live="polite">
          Loading clients…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && clients.length === 0 ? (
        <Glass level={1} className="admin-placeholder admin-placeholder--center">
          <p className="c-2">No clients are configured yet.</p>
        </Glass>
      ) : null}

      {state === "ready" && clients.length > 0 ? (
        <Glass level={1} className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Client</th>
                <th scope="col">LLM</th>
                <th scope="col" className="admin-table__num">
                  Daily quota
                </th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.client_id}>
                  <th scope="row" className="admin-table__name">
                    {c.display_name}
                  </th>
                  <td className="c-2">{c.llm_model ?? "—"}</td>
                  <td className="admin-table__num tabular">{c.daily_action_limit ?? "—"}</td>
                  <td>
                    <Chip tint={c.status === "active" ? "success" : "neutral"}>{c.status === "active" ? "Active" : "Inactive"}</Chip>
                  </td>
                  <td className="admin-table__action">
                    <Link to={`/admin/clients/${encodeURIComponent(c.client_id)}`} aria-label={`View details of ${c.display_name}`}>
                      View details
                      <ChevronRight size={14} aria-hidden />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Glass>
      ) : null}

      {adding ? <AddClientModal onClose={() => setAdding(false)} onCreated={onCreated} /> : null}
    </div>
  );
}

function AddClientModal({ onClose, onCreated }: { onClose: () => void; onCreated: (client: ClientSummary) => void }) {
  const formId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  const [name, setName] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [logoName, setLogoName] = useState<string | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<"active" | "inactive">("inactive");
  const [whatsappNumber, setWhatsappNumber] = useState("");
  const [plans, setPlans] = useState<Plan[]>([]);
  const [planId, setPlanId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminGetLLMSettings()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        const first = data.providers.find((p) => p.wired);
        if (first) {
          setProvider(first.provider);
          setModel(first.allowed_models?.[0] ?? "");
        }
      })
      .catch(() => {
        if (!cancelled) setSettingsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The plan is optional: when the plans cannot be loaded the client is simply created without one.
  useEffect(() => {
    let cancelled = false;
    adminListPlans()
      .then((data) => {
        if (!cancelled) setPlans(data.filter((p) => p.active));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const wired = settings?.providers.filter((p) => p.wired) ?? [];
  const allowedModels = settings?.providers.find((p) => p.provider === provider)?.allowed_models ?? null;

  function onProviderChange(next: string) {
    setProvider(next);
    const models = settings?.providers.find((p) => p.provider === next)?.allowed_models ?? null;
    setModel(models?.[0] ?? "");
  }

  function onLogoChange(file: File | undefined) {
    setLogoError(null);
    setLogo(null);
    setLogoName(null);
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
    reader.onload = () => {
      setLogo(typeof reader.result === "string" ? reader.result : null);
      setLogoName(file.name);
    };
    reader.onerror = () => setLogoError("Could not read this image.");
    reader.readAsDataURL(file);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await adminCreateClient({
        display_name: name.trim(),
        logo,
        login: login.trim(),
        temporary_password: password,
        llm_provider: provider,
        llm_model: model.trim(),
        status,
        whatsapp_number: whatsappNumber.trim(),
        ...(planId ? { plan_id: planId } : {}),
      });
      onCreated(created);
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not create the client.");
      setSaving(false);
    }
  }

  const ready = name.trim() && login.trim() && password && provider && model.trim() && whatsappNumber.trim();

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
      title="Ajouter un client"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Annuler
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={saving} disabled={!ready}>
            Ajouter un client
          </Button>
        </>
      }
    >
      <form id={formId} className="admin-form" onSubmit={(e) => void onSubmit(e)}>
        <TextInput label="Nom du client" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoComplete="off" />

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
          {logo ? <img className="admin-form__logo" src={logo} alt={`Aperçu du logo ${logoName ?? ""}`.trim()} /> : null}
          {logoError ? (
            <p className="field__msg admin-field-error" role="alert">
              {logoError}
            </p>
          ) : null}
        </div>

        <TextInput label="Login" value={login} onChange={(e) => setLogin(e.target.value)} required autoComplete="off" />
        <TextInput
          label="Mot de passe temporaire"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
          autoComplete="new-password"
          helper="8 caractères minimum."
        />

        {settingsFailed ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            Could not load the available LLM models.
          </p>
        ) : wired.length === 0 && settings ? (
          <p className="admin-alert admin-alert--danger">No LLM provider is wired into this deployment.</p>
        ) : (
          <>
            {wired.length > 1 ? (
              <SelectField label="Fournisseur LLM" value={provider} onChange={onProviderChange}>
                {wired.map((p) => (
                  <option key={p.provider} value={p.provider}>
                    {p.provider}
                  </option>
                ))}
              </SelectField>
            ) : null}
            <ModelSelect models={allowedModels ?? []} value={model} onChange={setModel} />
          </>
        )}

        <fieldset className="admin-form__choice">
          <legend className="t-label c-2">Statut</legend>
          <label>
            <input type="radio" name={`${formId}-status`} checked={status === "active"} onChange={() => setStatus("active")} /> Actif
          </label>
          <label>
            <input type="radio" name={`${formId}-status`} checked={status === "inactive"} onChange={() => setStatus("inactive")} /> Inactif
          </label>
        </fieldset>

        <TextInput
          label="WhatsApp number"
          type="tel"
          value={whatsappNumber}
          onChange={(e) => setWhatsappNumber(e.target.value)}
          placeholder="+216 22 768 307"
          maxLength={40}
          required
          autoComplete="off"
          helper="Le numéro que l'agent commercial utilisera avec les prospects."
        />

        {plans.length > 0 ? (
          <SelectField label="Plan" value={planId} onChange={setPlanId}>
            <option value="">Aucun plan</option>
            {plans.map((p) => (
              <option key={p.plan_id} value={p.plan_id}>
                {p.name} — {p.monthly_price} {p.currency}
              </option>
            ))}
          </SelectField>
        ) : null}

        {error ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}

/** Always a dropdown, fed only by the models enabled in Settings; empty (and disabled) when none is. */
function ModelSelect({ models, value, onChange }: { models: string[]; value: string; onChange: (value: string) => void }) {
  const id = useId();
  const empty = models.length === 0;
  return (
    <FieldShell id={id} label="Modèle LLM" helper={empty ? "No active LLM model is configured in Settings." : undefined}>
      <select id={id} className="input well" value={value} disabled={empty} onChange={(e) => onChange(e.target.value)}>
        {empty ? <option value="">No model available</option> : null}
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

function SelectField({ label, value, onChange, children }: { label: string; value: string; onChange: (value: string) => void; children: ReactNode }) {
  const id = useId();
  return (
    <FieldShell id={id} label={label}>
      <select id={id} className="input well" value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </FieldShell>
  );
}
