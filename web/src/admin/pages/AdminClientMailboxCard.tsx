/** Client sheet → Email mailbox: assigns one of the platform's mailboxes to this client.
 *
 *  The administrator gives the mailbox address and its password. The servers are the platform's
 *  (Settings → Email servers); the client types nothing technical. The password is write-only: it is
 *  never displayed again, only "Configured". The test logs in with this mailbox's own address and
 *  password (nothing is sent, nothing is read), and the status says where the mailbox stands:
 *  servers missing, not assigned, password missing, not authenticated, or ready. */

import { useCallback, useEffect, useId, useState } from "react";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";
import type { Tint } from "../../ui/Glass";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminClientMailboxStatus,
  adminListClientCredentials,
  adminRemoveClientCredential,
  adminSaveClientCredential,
  adminTestClientMailbox,
  type ConnectionTest,
  type Credential,
  type EmailMailboxStatus,
} from "../api";

const MAILBOX = "email.platform_mailbox";
const PASSWORD = "email.password";

const STATES: Record<EmailMailboxStatus["state"], [string, Tint]> = {
  missing_configuration: ["Email servers not configured", "neutral"],
  mailbox_not_assigned: ["No mailbox assigned", "neutral"],
  password_missing: ["Password missing", "attention"],
  not_authenticated: ["Not authenticated", "attention"],
  ready: ["Ready", "success"],
};

export function AdminClientMailboxCard({ clientId }: { clientId: string }) {
  const { admin } = useAdminAuth();
  const canEdit = admin?.role === "owner";
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [status, setStatus] = useState<EmailMailboxStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const refresh = useCallback(() => {
    adminClientMailboxStatus(clientId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [clientId]);

  // After a save, log in with the mailbox as it now is (nothing is sent or read): the state shown
  // is a proof, not the presence of the fields. The server answers without connecting while the
  // mailbox is incomplete.
  const verifyAfterSave = useCallback(async () => {
    setChecking(true);
    try {
      await adminTestClientMailbox(clientId).catch(() => undefined);
    } finally {
      setChecking(false);
      refresh();
    }
  }, [clientId, refresh]);

  useEffect(() => {
    let cancelled = false;
    adminListClientCredentials(clientId)
      .then((list) => !cancelled && setCredentials(list))
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof AdminApiError ? err.message : "Could not load the mailbox.");
      });
    refresh();
    return () => {
      cancelled = true;
    };
  }, [clientId, refresh]);

  const replace = (updated: Credential) =>
    setCredentials((list) => (list ?? []).map((c) => (c.key === updated.key ? updated : c)));

  if (error) {
    return (
      <p className="admin-alert admin-alert--danger" role="alert">
        {error}
      </p>
    );
  }
  if (credentials === null) {
    return (
      <p className="c-2 t-body-sm" role="status">
        Loading…
      </p>
    );
  }
  const address = credentials.find((c) => c.key === MAILBOX);
  const password = credentials.find((c) => c.key === PASSWORD);
  const [label, tint] = status ? STATES[status.state] : ["Unknown", "neutral" as Tint];

  return (
    <div className="admin-llm-config">
      <span className="admin-settings__status">
        <span className="c-3 t-body-sm">Mailbox status</span>
        <Chip tint={tint}>{label}</Chip>
      </span>
      {checking ? (
        <p className="c-3 t-body-sm" role="status">
          Checking the logins…
        </p>
      ) : null}
      {status?.smtp || status?.imap ? (
        <div>
          <p className="c-3 t-body-sm">
            SMTP: {status.smtp?.replace("_", " ")}
            {status.smtp_problem ? ` — ${status.smtp_problem}` : ""}
          </p>
          <p className="c-3 t-body-sm">
            IMAP: {status.imap?.replace("_", " ")}
            {status.imap_problem ? ` — ${status.imap_problem}` : ""}
          </p>
        </div>
      ) : status?.problem ? (
        <p className="c-3 t-body-sm">{status.problem}</p>
      ) : null}
      {address ? (
        <MailboxField
          label="Mailbox address"
          help="The address Revansa sends from and reads for this client. It belongs to this client only."
          placeholder="name@mail.example.com"
          credential={address}
          canEdit={canEdit}
          save={async (value) => replace(await adminSaveClientCredential(clientId, MAILBOX, value))}
          remove={async () => replace(await adminRemoveClientCredential(clientId, MAILBOX))}
          onChanged={verifyAfterSave}
        />
      ) : null}
      {password ? (
        <MailboxField
          label="Mailbox password"
          help="The password of this mailbox. Stored encrypted. Never shown again once saved."
          credential={password}
          canEdit={canEdit}
          save={async (value) => replace(await adminSaveClientCredential(clientId, PASSWORD, value))}
          remove={async () => replace(await adminRemoveClientCredential(clientId, PASSWORD))}
          onChanged={verifyAfterSave}
        />
      ) : null}
      <MailboxTest clientId={clientId} canEdit={canEdit} onDone={refresh} />
    </div>
  );
}

function MailboxField({
  label,
  help,
  placeholder,
  credential,
  canEdit,
  save,
  remove,
  onChanged,
}: {
  label: string;
  help: string;
  placeholder?: string;
  credential: Credential;
  canEdit: boolean;
  save: (value: string) => Promise<void>;
  remove: () => Promise<void>;
  onChanged: () => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(credential.sensitive ? "" : (credential.value ?? ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const unchanged = credential.sensitive ? draft.trim() === "" : draft.trim() === (credential.value ?? "");

  async function run(action: () => Promise<void>, after: () => void) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await action();
      after();
      onChanged();
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <FieldShell
      id={id}
      label={label}
      helper={help}
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
          placeholder={credential.sensitive && credential.configured ? "Configured — enter a new value to replace it" : placeholder}
          disabled={!canEdit || busy}
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
          disabledReason={!canEdit ? "Only an owner can change a client's mailbox." : undefined}
          onClick={() =>
            run(
              () => save(draft),
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
            disabledReason={!canEdit ? "Only an owner can change a client's mailbox." : undefined}
            onClick={() => (window.confirm(`Remove "${label}"?`) ? run(remove, () => setDraft("")) : undefined)}
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

function MailboxTest({ clientId, canEdit, onDone }: { clientId: string; canEdit: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConnectionTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      setResult(await adminTestClientMailbox(clientId));
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "The test could not be run.");
    } finally {
      setBusy(false);
      onDone();
    }
  }

  return (
    <div className="admin-llm-provider-card__models">
      <div>
        <Button
          variant="secondary"
          size="sm"
          loading={busy}
          disabledReason={!canEdit ? "Only an owner can test a client's mailbox." : undefined}
          disabled={!canEdit}
          onClick={run}
        >
          Test mailbox login
        </Button>
      </div>
      <p className="c-3 t-body-sm">
        Logs in to the sending server and to the mailbox with this mailbox&apos;s own address and password. Nothing is sent
        and nothing is read.
      </p>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <div role="status">
          <p className={result.ok ? "admin-alert admin-alert--success" : "admin-alert admin-alert--danger"}>
            {result.ok ? "Login accepted. " : "Login failed. "}
            {result.message}
          </p>
        </div>
      ) : null}
    </div>
  );
}
