/** Email activation requests: a client asks, the Admin creates the mailbox by hand at the provider and
 *  records it here.
 *
 *  - `AdminEmailActivationRequests` (Settings): the requests that wait for the Admin, with the client,
 *    the Sender Name it confirmed, the date and the status.
 *  - `AdminClientActivationCard` (client sheet): moves one request: start it, reject it (a reason is
 *    required and the client reads it), or activate it with the address and password of the mailbox
 *    just created. Activating really tries both logins first; if either fails nothing is saved and the
 *    answer says which. The password is write-only: it is sent once and never shown again.
 *
 *  The Sender Name is a display name, not an address: the Admin types the address of the mailbox. */

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";
import type { Tint } from "../../ui/Glass";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminActivateEmail,
  adminDeactivateEmailActivation,
  adminGetEmailActivation,
  adminListEmailActivations,
  adminRejectEmailActivation,
  adminStartEmailActivation,
  type EmailActivation,
  type EmailActivationStatus,
} from "../api";

const STATUSES: Record<EmailActivationStatus, [string, Tint]> = {
  pending: ["Pending", "attention"],
  in_progress: ["In progress", "agent"],
  rejected: ["Rejected", "danger"],
  active: ["Active", "success"],
};

const when = (iso: string) => new Date(iso).toLocaleString();

function StatusChip({ status }: { status: EmailActivationStatus }) {
  const [label, tint] = STATUSES[status];
  return <Chip tint={tint}>{label}</Chip>;
}

// ---- Settings: what waits for the Admin ---------------------------------------------------------

export function AdminEmailActivationRequests() {
  const [rows, setRows] = useState<EmailActivation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminListEmailActivations("open")
      .then((list) => !cancelled && setRows(list))
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof AdminApiError ? err.message : "Could not load the requests.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="admin-llm-config" aria-label="Email activation requests">
      <h3 className="admin-settings__subhead">Email activation requests</h3>
      <p className="c-3 t-body-sm">
        Clients who asked for their email to be activated. Create the mailbox at your provider, then open the client to enter
        its address and password and activate it.
      </p>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : rows === null ? (
        <p className="c-3 t-body-sm" role="status">
          Loading…
        </p>
      ) : rows.length === 0 ? (
        <p className="c-3 t-body-sm">No request is waiting.</p>
      ) : (
        <div className="admin-table-wrap"><table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Client</th>
              <th scope="col">Sender name</th>
              <th scope="col">Requested</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.client_id}>
                <td>
                  <Link to={`/admin/clients/${encodeURIComponent(row.client_id)}`}>{row.client_name ?? row.client_id}</Link>
                </td>
                <td>{row.sender_name ?? <span className="c-3">Not confirmed yet</span>}</td>
                <td>{when(row.requested_at)}</td>
                <td>
                  <StatusChip status={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

// ---- Client sheet: answering one request --------------------------------------------------------

export function AdminClientActivationCard({ clientId }: { clientId: string }) {
  const { admin } = useAdminAuth();
  const canEdit = admin?.role === "owner";
  const [request, setRequest] = useState<EmailActivation | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminGetEmailActivation(clientId)
      .then((found) => !cancelled && setRequest(found))
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof AdminApiError ? err.message : "Could not load the request.");
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (loadError) {
    return (
      <p className="admin-alert admin-alert--danger" role="alert">
        {loadError}
      </p>
    );
  }
  if (request === undefined) {
    return (
      <p className="c-2 t-body-sm" role="status">
        Loading…
      </p>
    );
  }
  if (request === null) {
    return <p className="c-3 t-body-sm">This client has not asked for its email to be activated.</p>;
  }
  return <ActivationDetail clientId={clientId} request={request} canEdit={canEdit} onChange={setRequest} />;
}

function ActivationDetail({
  clientId,
  request,
  canEdit,
  onChange,
}: {
  clientId: string;
  request: EmailActivation;
  canEdit: boolean;
  onChange: (next: EmailActivation) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const only = "Only an owner can answer an activation request.";

  const run = useCallback(
    async (action: () => Promise<EmailActivation>) => {
      setBusy(true);
      setError(null);
      try {
        onChange(await action());
        return true;
      } catch (err) {
        setError(err instanceof AdminApiError ? err.message : "The action could not be done.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onChange],
  );

  return (
    <div className="admin-llm-config">
      <span className="admin-settings__status">
        <span className="c-3 t-body-sm">Activation request</span>
        <StatusChip status={request.status} />
      </span>
      <dl>
        <dt className="c-3">Sender name (confirmed by the client)</dt>
        <dd>{request.sender_name ?? "Not confirmed yet"}</dd>
        <dt className="c-3">Requested</dt>
        <dd>{when(request.requested_at)}</dd>
        {request.reason ? (
          <>
            <dt className="c-3">Reason</dt>
            <dd>{request.reason}</dd>
          </>
        ) : null}
      </dl>
      {!request.email_allowed ? (
        <p className="admin-alert admin-alert--info" role="status">
          Email is not allowed for this client. Allow it before activating (this is a separate decision from the request).
        </p>
      ) : null}
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}

      {request.status === "pending" ? (
        <div>
          <Button
            variant="secondary"
            size="sm"
            loading={busy}
            disabled={!canEdit}
            disabledReason={!canEdit ? only : undefined}
            onClick={() => run(() => adminStartEmailActivation(clientId))}
          >
            Mark in progress
          </Button>
        </div>
      ) : null}
      {request.status === "in_progress" ? (
        <ActivateForm
          sender={request.sender_name}
          busy={busy}
          canEdit={canEdit}
          onActivate={(address, password) => run(() => adminActivateEmail(clientId, address, password))}
        />
      ) : null}
      {request.status === "pending" || request.status === "in_progress" ? (
        <ReasonForm
          label="Reject this request"
          button="Reject"
          help="The client reads this reason."
          busy={busy}
          canEdit={canEdit}
          onSubmit={(reason) => run(() => adminRejectEmailActivation(clientId, reason))}
        />
      ) : null}
      {request.status === "active" ? (
        <ReasonForm
          label="Deactivate"
          button="Deactivate"
          help="Removes the mailbox from this client. The client sees this reason and may ask again."
          busy={busy}
          canEdit={canEdit}
          confirm="Deactivate this client's email? Its mailbox will be removed and its agent will stop sending emails."
          onSubmit={(reason) => run(() => adminDeactivateEmailActivation(clientId, reason))}
        />
      ) : null}
    </div>
  );
}

function ActivateForm({
  sender,
  busy,
  canEdit,
  onActivate,
}: {
  sender: string | null;
  busy: boolean;
  canEdit: boolean;
  onActivate: (address: string, password: string) => Promise<boolean>;
}) {
  const addressId = useId();
  const passwordId = useId();
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const ready = address.trim() !== "" && password !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    const ok = await onActivate(address.trim(), password);
    if (!ok) setPassword(""); // never keep a rejected password on screen
  }

  return (
    <form className="admin-llm-config" onSubmit={submit} aria-label="Activate the mailbox">
      <p className="c-3 t-body-sm">
        Create the mailbox at your provider{sender ? <> for the sender name “{sender}”</> : null}, then enter its address and
        password. Both logins are tried first; nothing is saved unless both are accepted.
      </p>
      <FieldShell id={addressId} label="Mailbox address" helper="The address you created, for example name@mail.example.com.">
        <input
          id={addressId}
          className="input well"
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={address}
          disabled={!canEdit || busy}
          onChange={(e) => setAddress(e.target.value)}
        />
      </FieldShell>
      <FieldShell id={passwordId} label="Mailbox password" helper="Stored encrypted. Never shown again.">
        <input
          id={passwordId}
          className="input well"
          type="password"
          autoComplete="new-password"
          value={password}
          disabled={!canEdit || busy}
          onChange={(e) => setPassword(e.target.value)}
        />
      </FieldShell>
      <div>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={busy}
          disabled={!ready}
          disabledReason={!canEdit ? "Only an owner can activate a mailbox." : undefined}
        >
          Activate
        </Button>
      </div>
    </form>
  );
}

function ReasonForm({
  label,
  button,
  help,
  busy,
  canEdit,
  confirm,
  onSubmit,
}: {
  label: string;
  button: string;
  help: string;
  busy: boolean;
  canEdit: boolean;
  confirm?: string;
  onSubmit: (reason: string) => Promise<boolean>;
}) {
  const id = useId();
  const [reason, setReason] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim() === "") return;
    if (confirm && !window.confirm(confirm)) return;
    if (await onSubmit(reason.trim())) setReason("");
  }

  return (
    <form className="admin-llm-config" onSubmit={submit} aria-label={label}>
      <FieldShell id={id} label={`${label} — reason`} helper={help}>
        <input
          id={id}
          className="input well"
          type="text"
          maxLength={500}
          value={reason}
          disabled={!canEdit || busy}
          onChange={(e) => setReason(e.target.value)}
        />
      </FieldShell>
      <div>
        <Button
          type="submit"
          variant="ghost"
          size="sm"
          loading={busy}
          disabled={reason.trim() === ""}
          disabledReason={!canEdit ? "Only an owner can answer an activation request." : undefined}
        >
          {button}
        </Button>
      </div>
    </form>
  );
}
