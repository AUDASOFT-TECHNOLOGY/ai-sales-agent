/** Settings: the technical integrations Revansa really uses, configured without touching code.
 *
 *  - OpenAI API: key, and the list of models (name, id, active)
 *    offered to clients. The active models listed here feed the "Modèle LLM" picker of a client.
 *  - Meta / WhatsApp Cloud API: Revansa-wide only (app secret, verify token, API version). A
 *    client's own access token and phone number id come from the WhatsApp onboarding of that client's
 *    number, not from this page; the phone number id is Meta's id, NOT the client's WhatsApp number.
 *  - Email: the servers of the mailboxes Revansa provides, and a test login for each, so that a real
 *    authentication can be checked (never a client's mailbox: that is set on the client's page).
 *  - Muse Spark: no adapter exists in the backend yet, so nothing can be configured; it says so.
 *
 *  Everything is stored by the backend's credential store. A sensitive value is write-only: it is
 *  never displayed again, only "configured". Connection tests call the real services. */

import { useEffect, useId, useState } from "react";
import { Glass, type Tint } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import { AdminEmailActivationRequests } from "./AdminEmailActivation";
import {
  AdminApiError,
  adminListGlobalCredentials,
  adminRemoveGlobalCredential,
  adminSaveGlobalCredential,
  adminAddOpenAIModel,
  adminDeleteOpenAIModel,
  adminListOpenAIModels,
  adminEmailServiceStatus,
  adminGmailPlatformStatus,
  adminSetEmailForNewClients,
  adminTestEmailImap,
  adminTestEmailServers,
  adminTestEmailSmtp,
  adminTestOpenAI,
  adminUpdateOpenAIModel,
  type ConnectionTest,
  type Credential,
  type EmailServiceStatus,
  type GmailPlatformStatus,
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

const EMAIL_DOMAIN_FIELDS: FieldDef[] = [
  { key: "email.platform_domain", label: "Mail domain", help: "The domain of the mailboxes Revansa provides.", placeholder: "mail.example.com" },
];

const EMAIL_SMTP_FIELDS: FieldDef[] = [
  { key: "email.platform_smtp_host", label: "SMTP host", help: "The server messages are sent through.", placeholder: "smtp.example.com" },
  { key: "email.platform_smtp_port", label: "SMTP port", help: "25, 465, 587 or 2525.", placeholder: "587" },
  { key: "email.platform_smtp_security", label: "SMTP security", help: "starttls (usually port 587) or ssl (usually port 465). An unencrypted connection is not offered: a mailbox is never reached in the clear.", placeholder: "starttls" },
  { key: "email.platform_smtp_username", label: "SMTP username", help: "The login used to check that the server accepts an authentication.", placeholder: "login@example.com" },
  { key: "email.platform_smtp_password", label: "SMTP password", help: "Stored encrypted. Never shown again once saved." },
];

const EMAIL_IMAP_FIELDS: FieldDef[] = [
  { key: "email.platform_imap_host", label: "IMAP host", help: "The server replies are read from.", placeholder: "imap.example.com" },
  { key: "email.platform_imap_port", label: "IMAP port", help: "143 or 993.", placeholder: "993" },
  { key: "email.platform_imap_security", label: "IMAP security", help: "ssl (usually port 993) or starttls (usually port 143). Never unencrypted.", placeholder: "ssl" },
  { key: "email.platform_imap_username", label: "IMAP username", help: "The login used to check that the server accepts an authentication.", placeholder: "login@example.com" },
  { key: "email.platform_imap_password", label: "IMAP password", help: "Stored encrypted. Never shown again once saved." },
];

const GMAIL_FIELDS: FieldDef[] = [
  { key: "gmail.oauth_client_id", label: "Google OAuth client id", help: "Stored encrypted. Never shown again once saved." },
  { key: "gmail.oauth_client_secret", label: "Google OAuth client secret", help: "Stored encrypted. Never shown again once saved." },
  { key: "gmail.oauth_state_key", label: "State signing key", help: "At least 16 random characters. Signs the return from Google. Stored encrypted." },
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
      <GmailSection canEdit={canEdit} />
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

// ---- Gmail (clients connecting their own mailbox) ---------------------------------------------

function GmailSection({ canEdit }: { canEdit: boolean }) {
  const store = useGlobalCredentials();
  const [status, setStatus] = useState<GmailPlatformStatus | null>(null);
  const credentialsSeen = store.credentials.map((c) => `${c.key}:${c.configured}`).join(",");
  useEffect(() => {
    adminGmailPlatformStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [credentialsSeen]);
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-gmail">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-gmail">
          Gmail (clients&apos; own mailbox)
        </h2>
        <span className="admin-settings__status">
          <span className="c-3 t-body-sm">Configuration</span>
          <Chip tint={status?.configured ? "success" : "neutral"}>
            {status ? (status.configured ? "Configured, not verified by Google" : "Not configured") : "Unknown"}
          </Chip>
        </span>
      </div>
      <p className="c-2 t-body-sm">
        The Google application that lets a client connect its own Gmail. The clients type no password: Google asks for their
        permission on its own page. Stored is not verified: nothing is checked against Google until a client connects.
      </p>
      <CredentialGroup store={store} fields={GMAIL_FIELDS} canEdit={canEdit} />
      {status && status.missing.length > 0 ? (
        <p className="admin-alert admin-alert--info" role="status">
          Still missing: {status.missing.join(", ")}.
        </p>
      ) : null}
      {status?.redirect_uri ? (
        <p className="c-3 t-body-sm">
          Authorised redirect URI to enter in Google Cloud: <code>{status.redirect_uri}</code>
        </p>
      ) : (
        <p className="c-3 t-body-sm">
          Set the public base URL of the platform (SALES_AGENT_PUBLIC_BASE_URL, https) to get the redirect URI to enter in Google
          Cloud.
        </p>
      )}
      <p className="c-3 t-body-sm">
        Reading and sending mail uses a restricted Google scope: until Google has verified the application, only the test users
        you list in Google Cloud (at most 100) can connect.
      </p>
    </Glass>
  );
}

// ---- Email ----------------------------------------------------------------------------------

const EMAIL_STATE_LABELS: Record<EmailServiceStatus["state"], [string, Tint]> = {
  missing_configuration: ["Configuration missing", "neutral"],
  server_not_authenticated: ["Servers not authenticated", "attention"],
  ready: ["Ready", "success"],
};

function NewClientsAccess({
  status,
  canEdit,
  onChanged,
}: {
  status: EmailServiceStatus | null;
  canEdit: boolean;
  onChanged: (next: EmailServiceStatus) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!status) return null;
  const on = status.new_clients_access === true;
  const awaiting = status.clients_awaiting_mailbox ?? [];
  const ready = status.state === "ready";
  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      onChanged(await adminSetEmailForNewClients(!on));
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not change this.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="admin-llm-config">
      <h3 className="admin-settings__subhead">Email for new clients</h3>
      <p className="c-3 t-body-sm">
        When this is on, every client created from now on can use Email as soon as its account exists. Clients that already
        exist are not changed: give them Email on their own page. Revansa does not create mailboxes at the provider: each
        client still needs a mailbox assigned below.
      </p>
      <span className="admin-settings__status">
        <span className="c-3 t-body-sm">New clients</span>
        <Chip tint={on ? "success" : "neutral"}>{on ? "Email on" : "Email off"}</Chip>
        <Button
          variant="secondary"
          size="sm"
          loading={busy}
          disabled={!on && !ready}
          disabledReason={
            !canEdit ? "Only an owner can change integrations." : !on && !ready ? "Both logins must be accepted first." : undefined
          }
          onClick={toggle}
        >
          {on ? "Turn off for new clients" : "Turn on for new clients"}
        </Button>
      </span>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {awaiting.length > 0 ? (
        <p className="admin-alert admin-alert--info" role="status">
          Waiting for a mailbox ({awaiting.length}): {awaiting.join(", ")}. Assign
          one on each client&apos;s page.
        </p>
      ) : null}
    </div>
  );
}

const LOGIN_STATE_LABELS: Record<string, string> = {
  not_configured: "not configured",
  not_tested: "not tested",
  authenticated: "authenticated",
  failed: "login failed",
};

function EmailSection({ canEdit }: { canEdit: boolean }) {
  const store = useGlobalCredentials();
  const [status, setStatus] = useState<EmailServiceStatus | null>(null);
  const refreshStatus = () => {
    adminEmailServiceStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  };
  // After a save the logins are checked at once (no message is sent or read), so the state shown
  // is the proof of what was just saved and not the mere presence of the fields. A removal or an
  // incomplete configuration is answered by the server without connecting anywhere.
  const [checking, setChecking] = useState(false);
  const verifyAfterSave = async () => {
    setChecking(true);
    try {
      await adminTestEmailSmtp().catch(() => undefined);
      await adminTestEmailImap().catch(() => undefined);
    } finally {
      setChecking(false);
      refreshStatus();
    }
  };
  useEffect(refreshStatus, []);
  const [stateLabel, tint] = status ? EMAIL_STATE_LABELS[status.state] : ["Unknown", "neutral" as const];
  return (
    <Glass level={1} className="admin-settings__card" aria-labelledby="settings-email">
      <div className="admin-settings__head">
        <h2 className="t-h2" id="settings-email">
          Email servers
        </h2>
        <span className="admin-settings__status">
          <span className="c-3 t-body-sm">Service status</span>
          <Chip tint={tint}>{stateLabel}</Chip>
        </span>
      </div>
      <p className="c-2 t-body-sm">
        The servers of the mailboxes Revansa provides. Mailboxes are assigned to clients one by one by an administrator; this
        page only describes the servers.
      </p>
      {checking ? (
        <p className="c-3 t-body-sm" role="status">
          Checking the logins…
        </p>
      ) : null}
      {status ? (
        <div role="status">
          <p className="c-3 t-body-sm">
            SMTP: {LOGIN_STATE_LABELS[status.smtp]}
            {status.smtp_problem ? ` — ${status.smtp_problem}` : ""}
          </p>
          <p className="c-3 t-body-sm">
            IMAP: {LOGIN_STATE_LABELS[status.imap]}
            {status.imap_problem ? ` — ${status.imap_problem}` : ""}
          </p>
          {status.missing.length > 0 ? <p className="c-3 t-body-sm">Missing: {status.missing.join(", ")}</p> : null}
        </div>
      ) : null}
      <NewClientsAccess status={status} canEdit={canEdit} onChanged={setStatus} />
      <AdminEmailActivationRequests />
      <CredentialGroup store={store} fields={EMAIL_DOMAIN_FIELDS} canEdit={canEdit} onChanged={verifyAfterSave} />
      <h3 className="admin-settings__subhead">Sending (SMTP)</h3>
      <CredentialGroup store={store} fields={EMAIL_SMTP_FIELDS} canEdit={canEdit} onChanged={verifyAfterSave} />
      <TestButton label="Test SMTP login" run={adminTestEmailSmtp} verdict={["Login accepted. ", "Login failed. "]} onDone={refreshStatus} />
      <h3 className="admin-settings__subhead">Receiving (IMAP)</h3>
      <CredentialGroup store={store} fields={EMAIL_IMAP_FIELDS} canEdit={canEdit} onChanged={verifyAfterSave} />
      <TestButton label="Test IMAP login" run={adminTestEmailImap} verdict={["Login accepted. ", "Login failed. "]} onDone={refreshStatus} />
      <p className="c-3 t-body-sm">
        A login test connects over an encrypted connection and logs in with the saved username and password. It sends no message
        and reads or changes none. These logins only check that the servers accept an authentication: each client mailbox has
        its own address and password, set on the client&apos;s page.
      </p>
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

function TestButton({
  label,
  run,
  verdict = ["Connection OK. ", "Connection failed. "],
  onDone,
}: {
  label: string;
  run: () => Promise<ConnectionTest>;
  verdict?: [string, string];
  onDone?: () => void;
}) {
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
      onDone?.();
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
            {result.ok ? verdict[0] : verdict[1]}
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
