/** Consultation of the central audit log (owner and operator). Read-only. */

import { useEffect, useState } from "react";
import { fetchAudit, type AuditPage } from "../auditApi";

const PAGE_SIZE = 50;

export function AdminAuditPage() {
  const [clientId, setClientId] = useState("");
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    fetchAudit({
      client_id: clientId.trim() || undefined,
      action: action.trim() || undefined,
      since: since ? new Date(since).toISOString() : undefined,
      until: until ? new Date(until).toISOString() : undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then((result) => {
        if (current) {
          setPage(result);
          setError(null);
        }
      })
      .catch((e: Error) => current && setError(e.message === "403" ? "Your role cannot read the audit log." : "The audit log could not be loaded."));
    return () => {
      current = false;
    };
  }, [clientId, action, since, until, offset]);

  const reset = (setter: (v: string) => void) => (e: { target: { value: string } }) => {
    setter(e.target.value);
    setOffset(0);
  };

  return (
    <section aria-label="Audit log">
      <form className="admin-audit__filters" onSubmit={(e) => e.preventDefault()}>
        <label>
          Client <input value={clientId} onChange={reset(setClientId)} />
        </label>
        <label>
          Action <input value={action} placeholder="client.* or plan.create" onChange={reset(setAction)} />
        </label>
        <label>
          From <input type="datetime-local" value={since} onChange={reset(setSince)} />
        </label>
        <label>
          To <input type="datetime-local" value={until} onChange={reset(setUntil)} />
        </label>
      </form>
      {error && <p role="alert">{error}</p>}
      {page && (
        <>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>Client</th>
                <th>Action</th>
                <th>Target</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id ?? `${item.at}-${item.action}`}>
                  <td>{new Date(item.at).toLocaleString()}</td>
                  <td>
                    {item.actor} <span className="c-3">({item.actor_role})</span>
                  </td>
                  <td>{item.client_id ?? "-"}</td>
                  <td>{item.action}</td>
                  <td>{item.target}</td>
                  <td>{item.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="c-3 t-body-sm">
            {page.total === 0 ? "No entries." : `${offset + 1}-${Math.min(offset + PAGE_SIZE, page.total)} of ${page.total}`}
          </p>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </button>
          <button type="button" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
            Next
          </button>
        </>
      )}
    </section>
  );
}
