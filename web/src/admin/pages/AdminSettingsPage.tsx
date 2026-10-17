/** Settings: the technical integrations Revansa really uses, configured without touching code.
 *
 *  - OpenAI API: key, and the list of models (name, id, active)
 *    offered to clients. The active models listed here feed the "Modèle LLM" picker of a client.
 *  - Meta / WhatsApp Cloud API: Revansa-wide only (app secret, verify token, API version). A
 *    client's own access token and phone number id come from the WhatsApp onboarding of that client's
 *    number, not from this page; the phone number id is Meta's id, NOT the client's WhatsApp number.
 *  - Email: the servers of the mailboxes Revansa provides. Only the servers are described here;
 *    which mailbox a client gets is decided client by client, elsewhere.
 *  - Muse Spark: no adapter exists in the backend yet, so nothing can be configured; it says so.
 *
 *  Everything is stored by the backend's credential store. A sensitive value is write-only: it is
 *  never displayed again, only "configured". Connection tests call the real services. */

import { useEffect, useId, useState } from "react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminListGlobalCredentials,
  adminRemoveGlobalCredential,
  adminSaveGlobalCredential,
  adminAddOpenAIModel,
  adminDeleteOpenAIModel,
  adminListOpenAIModels,
  adminTestEmailServers,
  adminTestOpenAI,
  adminUpdateOpenAIModel,
  type ConnectionTest,
  type Credential,
  type OpenAIModel,
} from "../api";

interface FieldDef {
  key: string;
  label: string;
  help: string;
  placeholder?: string;
}

const OPENAI_FIELDS: FieldDef[] = [
  { key: "openai.api_key", label: "API Key", help: "Stored encrypted. Never shown again once saved." },
];

const EMAIL_FIELDS: FieldDef[] = [
  { key: "email.platform_domain", label: "Mail domain", help: "The domain of the mailboxes Revansa provides.", placeholder: "mail.example.com" },
  { key: "email.platform_smtp_host", label: "SMTP host", help: "The server messages are sent through.", placeholder: "smtp.example.com" },
  { key: "email.platform_smtp_port", label: "SMTP port", help: "25, 465, 587 or 2525.", placeholder: "587" },
  { key: "email.platform_smtp_security", label: "SMTP security", help: "starttls or ssl. A mailbox is never reached in the clear.", placeholder: "starttls" },
  { key: "email.platform_imap_host", label: "IMAP host", help: "The server replies are read from.", placeholder: "imap.example.com" },
  { key: "email.platform_imap_port", label: "IMAP port", help: "143 or 993.", placeholder: "993" },
  { key: "email.platform_imap_security", label: "IMAP security", help: "ssl or starttls.", placeholder: "ssl" },
];

const META_GLOBAL_FIELDS: FieldDef[] = [
  { key: "whatsapp.app_secret", label: "App Secret", help: "Revansa-wide. Signs the webhook calls Meta sends." },
  { key: "whatsapp.verify_token", label: "Verify Token", help: "Revansa-wide. The token you give Meta when registering the webhook." },
  { key: "whatsapp.api_version", label: "API Version", help: "Revansa-wide Graph API version.", placeholder: "v21.0" },
];

export function AdminSettingsPage() {
  const { admin } = useAdminAuth();
  const canEdit = admin?.role === "owner";
  return (
    <div className="admin-settings">
      <Glass level={1} className="admin-settings__notice" role="note">
        <p className="c-2 t-body-sm">
          Technical integrations used by Revansa. Secret values are write-only: once saved they are never displayed again.
          {canEdit ? "" : " Only an owner can change them."}
        </p>
      </Glass>
      <OpenAISection canEdit={canEdit} />
      <MetaSection canEdit={canEdit} />
      <EmailSection canEdit={canEdit} />
      <MuseSparkSection />
    </div>
  );
}

// ---- OpenAI ---------------------------------------------------------------------------------

function OpenAISection({ canEdit }: { canEdit: boolean }) {
  const store = useGlobalCredentials();
  const configured = isConfigured(store.credentials, "openai.api_key");
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-openai">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-openai">
          OpenAI API
        </h2>
        <span className="admin-settings__status">
          <span className="c-3 t-body-sm">Connection status</span>
          <Chip tint={configured ? "success" : "neutral"}>{configured ? "Configured" : "Not configured"}</Chip>
        </span>
      </div>
      <CredentialGroup store={store} fields={OPENAI_FIELDS} canEdit={canEdit} />
      <TestButton label="Test connection" run={adminTestOpenAI} />
      <OpenAIModels canEdit={canEdit} />
    </Glass>
  );
}

function OpenAIModels({ canEdit }: { canEdit: boolean }) {
  const [models, setModels] = useState<OpenAIModel[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  // null: no form; "" : adding; otherwise the id of the model being edited.
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ownerOnly = !canEdit ? "Only an owner can change integrations." : undefined;

  useEffect(() => {
    let cancelled = false;
    adminListOpenAIModels()
      .then((list) => {
        if (cancelled) return;
        setModels(list);
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

  /** Runs one change; resolves true when it was stored, so a form can close. */
  async function apply(change: () => Promise<OpenAIModel[]>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      setModels(await change());
      return true;
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not save the model.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const editingModel = editing ? models.find((m) => m.id === editing) : undefined;

  return (
    <div className="admin-settings__models-section">
      <div className="admin-settings__head">
        <h3 className="t-h3" id="settings-openai-models">
          Available OpenAI Models
        </h3>
        <Button variant="secondary" size="sm" disabled={busy || editing !== null} disabledReason={ownerOnly} onClick={() => setEditing("")}>
          + Add model
        </Button>
      </div>
      <p className="c-3 t-body-sm">These are the OpenAI models that can be selected for a client in Client Details.</p>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {editing !== null ? (
        <ModelForm
          key={editing}
          initial={editingModel}
          busy={busy}
          onCancel={() => setEditing(null)}
          onSubmit={async (model) => {
            const ok = await apply(() => (editingModel ? adminUpdateOpenAIModel(editingModel.id, model) : adminAddOpenAIModel(model)));
            if (ok) setEditing(null);
          }}
        />
      ) : null}
      {state === "loading" ? (
        <p className="c-2 t-body-sm" role="status">
          Loading…
        </p>
      ) : state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          Could not load the OpenAI models.
        </p>
      ) : models.length === 0 ? (
        <p className="c-2 t-body-sm">No OpenAI models configured.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table" aria-labelledby="settings-openai-models">
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">Model ID</th>
                <th scope="col">Status</th>
                <th scope="col" className="admin-table__action">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id}>
                  <th scope="row">{model.name}</th>
                  <td className="admin-field-row__mono">{model.id}</td>
                  <td>
                    <Chip tint={model.active ? "success" : "neutral"}>{model.active ? "Active" : "Inactive"}</Chip>
                  </td>
                  <td className="admin-table__action">
                    <div className="admin-settings__row admin-settings__row--end">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        disabledReason={ownerOnly}
                        aria-label={`${model.active ? "Deactivate" : "Activate"} ${model.name}`}
                        onClick={() => apply(() => adminUpdateOpenAIModel(model.id, { ...model, active: !model.active }))}
                      >
                        {model.active ? "Deactivate" : "Activate"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy || editing !== null}
                        disabledReason={ownerOnly}
                        aria-label={`Edit ${model.name}`}
                        onClick={() => setEditing(model.id)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        disabledReason={ownerOnly}
                        aria-label={`Delete ${model.name}`}
                        onClick={() => {
                          if (window.confirm(`Delete the model "${model.name}"?`)) void apply(() => adminDeleteOpenAIModel(model.id));
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ModelForm({
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  initial?: OpenAIModel;
  busy: boolean;
  onSubmit: (model: OpenAIModel) => void;
  onCancel: () => void;
}) {
  const nameId = useId();
  const idId = useId();
  const [name, setName] = useState(initial?.name ?? "");
  const [modelId, setModelId] = useState(initial?.id ?? "");
  const [active, setActive] = useState(initial?.active ?? true);
  const valid = name.trim() !== "" && modelId.trim() !== "";

  return (
    <form
      className="admin-llm-config"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSubmit({ name: name.trim(), id: modelId.trim(), active });
      }}
    >
      <FieldShell id={nameId} label="Model name" helper="The name an admin sees, for example in Client Details.">
        <input id={nameId} className="input well" value={name} autoComplete="off" onChange={(e) => setName(e.target.value)} />
      </FieldShell>
      <FieldShell id={idId} label="Model ID" helper="The exact model id your OpenAI account serves.">
        <input id={idId} className="input well" value={modelId} autoComplete="off" spellCheck={false} onChange={(e) => setModelId(e.target.value)} />
      </FieldShell>
      <label className="admin-settings__check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        <span>Active</span>
      </label>
      <div className="admin-settings__row">
        <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!valid}>
          {initial ? "Save model" : "Add model"}
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ---- Meta / WhatsApp ------------------------------------------------------------------------

function MetaSection({ canEdit }: { canEdit: boolean }) {
  const store = useGlobalCredentials();
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-meta">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-meta">
          Meta / WhatsApp API
        </h2>
      </div>
      <h3 className="admin-settings__subhead">Revansa-wide</h3>
      <CredentialGroup store={store} fields={META_GLOBAL_FIELDS} canEdit={canEdit} />
      <p className="c-3 t-body-sm">
        The App Secret and Verify Token are only proven when Meta calls the Revansa webhook, so they have no standalone
        connection test.
      </p>
      <p className="c-3 t-body-sm">
        A client&apos;s own Meta credentials are not entered here: they come from the WhatsApp onboarding of that client&apos;s
        number.
      </p>
    </Glass>
  );
}

// ---- Email ----------------------------------------------------------------------------------

function EmailSection({ canEdit }: { canEdit: boolean }) {
  const store = useGlobalCredentials();
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-email">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-email">
          Email servers
        </h2>
      </div>
      <p className="c-2 t-body-sm">
        The servers of the mailboxes Revansa provides. Mailboxes are assigned to clients one by one by an administrator; this
        page only describes the servers.
      </p>
      <CredentialGroup store={store} fields={EMAIL_FIELDS} canEdit={canEdit} />
      <TestButton label="Test servers" run={adminTestEmailServers} />
      <p className="c-3 t-body-sm">
        The test opens an encrypted connection to each server and closes it. It does not log in and sends or reads no message,
        so it does not prove that a mailbox works.
      </p>
    </Glass>
  );
}

// ---- Muse Spark -----------------------------------------------------------------------------

function MuseSparkSection() {
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-muse">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-muse">
          Muse Spark (Meta LLM)
        </h2>
        <Chip tint="neutral">Not implemented</Chip>
      </div>
      <p className="c-2 t-body-sm">
        There is no Muse Spark adapter in the Revansa backend yet, so nothing can be configured or tested here and no client
        can use it. This section will hold its credentials once the integration exists.
      </p>
    </Glass>
  );
}

// ---- shared pieces --------------------------------------------------------------------------

interface CredentialStore {
  credentials: Credential[];
  state: "loading" | "ready" | "error";
  error: string | null;
  save: (key: string, value: string) => Promise<void>;
  remove: (key: string) => Promise<void>;
}

function useGlobalCredentials(): CredentialStore {
  return useCredentialStore(adminListGlobalCredentials, adminSaveGlobalCredential, adminRemoveGlobalCredential);
}

function useCredentialStore(
  load: () => Promise<Credential[]>,
  saveOne: (key: string, value: string) => Promise<Credential>,
  removeOne: (key: string) => Promise<Credential>,
): CredentialStore {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [state, setState] = useState<CredentialStore["state"]>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((list) => {
        if (cancelled) return;
        setCredentials(list);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load the settings.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` is stable per scope; the store is re-created (keyed) when the scope changes
  }, []);

  const replace = (updated: Credential) => setCredentials((list) => list.map((c) => (c.key === updated.key ? updated : c)));
  return {
    credentials,
    state,
    error,
    save: async (key, value) => replace(await saveOne(key, value)),
    remove: async (key) => replace(await removeOne(key)),
  };
}

const isConfigured = (credentials: Credential[], key: string) => credentials.some((c) => c.key === key && c.configured);

function CredentialGroup({
  store,
  fields,
  canEdit,
  onChanged,
}: {
  store: CredentialStore;
  fields: FieldDef[];
  canEdit: boolean;
  onChanged?: () => void;
}) {
  if (store.state === "loading") {
    return (
      <p className="c-2 t-body-sm" role="status">
        Loading…
      </p>
    );
  }
  if (store.state === "error") {
    return (
      <p className="admin-alert admin-alert--danger" role="alert">
        {store.error}
      </p>
    );
  }
  return (
    <div className="admin-llm-config">
      {fields.map((field) => {
        const credential = store.credentials.find((c) => c.key === field.key);
        return credential ? (
          <CredentialRow key={field.key} field={field} credential={credential} store={store} canEdit={canEdit} onChanged={onChanged} />
        ) : null;
      })}
    </div>
  );
}

function CredentialRow({
  field,
  credential,
  store,
  canEdit,
  onChanged,
}: {
  field: FieldDef;
  credential: Credential;
  store: CredentialStore;
  canEdit: boolean;
  onChanged?: () => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(credential.sensitive ? "" : (credential.value ?? ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function run(action: () => Promise<void>, after?: () => void) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await action();
      after?.();
      onChanged?.();
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  const unchanged = credential.sensitive ? draft.trim() === "" : draft.trim() === (credential.value ?? "");
  const placeholder = credential.sensitive && credential.configured ? "Configured — enter a new value to replace it" : field.placeholder;

  return (
    <FieldShell
      id={id}
      label={field.label}
      helper={field.help}
      error={error ?? undefined}
      badge={<Chip tint={credential.configured ? "success" : "neutral"}>{credential.configured ? "Configured" : "Not set"}</Chip>}
    >
      <div className="admin-settings__row">
        <input
          id={id}
          className="input well"
          type={credential.sensitive ? "password" : "text"}
          autoComplete="off"
          spellCheck={false}
          value={draft}
          placeholder={placeholder}
          disabled={!canEdit || busy}
          aria-describedby={`${id}-msg`}
          onChange={(e) => {
            setDraft(e.target.value);
            setSaved(false);
          }}
        />
        <Button
          variant="primary"
          size="sm"
          loading={busy}
          disabled={unchanged}
          disabledReason={!canEdit ? "Only an owner can change integrations." : undefined}
          onClick={() =>
            run(
              () => store.save(credential.key, draft),
              () => {
                setSaved(true);
                if (credential.sensitive) setDraft("");
              },
            )
          }
        >
          Save
        </Button>
        {credential.configured ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            disabledReason={!canEdit ? "Only an owner can change integrations." : undefined}
            onClick={() =>
              window.confirm(`Remove "${field.label}"?`)
                ? run(
                    () => store.remove(credential.key),
                    () => setDraft(""),
                  )
                : undefined
            }
          >
            Remove
          </Button>
        ) : null}
      </div>
      {saved ? (
        <p className="admin-alert admin-alert--success" role="status">
          Saved.
        </p>
      ) : null}
    </FieldShell>
  );
}

function TestButton({ label, run }: { label: string; run: () => Promise<ConnectionTest> }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConnectionTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      setResult(await run());
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "The test could not be run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-llm-provider-card__models">
      <div>
        <Button variant="secondary" size="sm" loading={busy} onClick={onClick}>
          {label}
        </Button>
      </div>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <div role="status">
          <p className={result.ok ? "admin-alert admin-alert--success" : "admin-alert admin-alert--danger"}>
            {result.ok ? "Connection OK. " : "Connection failed. "}
            {result.message}
          </p>
          {Object.entries(result.details).map(([name, value]) => (
            <p key={name} className="c-3 t-body-sm">
              {name}: {value}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
