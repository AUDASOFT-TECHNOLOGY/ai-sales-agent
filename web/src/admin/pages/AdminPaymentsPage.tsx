/** Offline plan payments: the receipts clients sent, waiting for the administrator to check them.
 *  Approving puts the client on the plan and marks its invoice paid; rejecting needs a reason the client
 *  reads. Only an owner decides (the server enforces it). */

import { useEffect, useState } from "react";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput, Segmented } from "../../ui/Controls";
import { Glass } from "../../ui/Glass";
import { AdminApiError } from "../api";
import { adminApprovePayment, adminPaymentRequests, adminRejectPayment, receiptUrl, type PaymentRequest } from "../siteApi";

type Filter = PaymentRequest["status"];
const TINT = { pending: "attention", approved: "success", rejected: "danger" } as const;

export function AdminPaymentsPage() {
  const [filter, setFilter] = useState<Filter>("pending");
  const [loaded, setLoaded] = useState<{ filter: Filter; list: PaymentRequest[] } | null>(null);
  const requests = loaded?.filter === filter ? loaded.list : null; // another filter: loading again
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  const [epoch, setEpoch] = useState(0); // a decision asks for the list again

  useEffect(() => {
    let current = true;
    adminPaymentRequests(filter).then(
      (list) => {
        if (!current) return;
        setError(null);
        setLoaded({ filter, list });
      },
      (err: unknown) => {
        if (!current) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load the payments.");
        setLoaded({ filter, list: [] });
      },
    );
    return () => {
      current = false;
    };
  }, [filter, epoch]);

  async function decide(id: number, action: "approve" | "reject") {
    setBusy(id);
    setError(null);
    try {
      if (action === "approve") await adminApprovePayment(id);
      else await adminRejectPayment(id, reason.trim());
      setRejecting(null);
      setReason("");
      setEpoch((e) => e + 1);
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "The decision could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="admin-clients">
      <Segmented<Filter>
        label="Status"
        layoutId="admin-pay-filter"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "pending", label: "To review" },
          { value: "approved", label: "Approved" },
          { value: "rejected", label: "Rejected" },
        ]}
      />
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {requests === null ? <p className="c-2" role="status">Loading…</p> : null}
      {requests?.length === 0 && !error ? (
        <Glass level={1} className="admin-placeholder admin-placeholder--center">
          <p className="c-2">{filter === "pending" ? "No receipt is waiting for review." : "Nothing here."}</p>
        </Glass>
      ) : null}
      {requests && requests.length > 0 ? (
        <Glass level={1} className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Client</th>
                <th scope="col">Plan</th>
                <th scope="col" className="admin-table__num">Amount</th>
                <th scope="col">Reference</th>
                <th scope="col">Sent</th>
                <th scope="col">Status</th>
                <th scope="col">Receipt</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.request_id}>
                  <th scope="row" className="admin-table__name">{r.client_name}</th>
                  <td>{r.plan_name}</td>
                  <td className="admin-table__num tabular">{r.amount} {r.currency}</td>
                  <td className="c-2">{r.reference || "—"}</td>
                  <td className="c-2">{new Date(r.created_at).toLocaleString()}</td>
                  <td>
                    <Chip tint={TINT[r.status]}>{r.status}</Chip>
                    {r.reason ? <div className="c-3 t-body-sm">{r.reason}</div> : null}
                    {r.decided_by ? <div className="c-3 t-body-sm">by {r.decided_by}</div> : null}
                  </td>
                  <td>
                    <a href={receiptUrl(r.request_id)} target="_blank" rel="noopener noreferrer">
                      View receipt<span className="sr-only"> of {r.client_name}</span>
                    </a>
                  </td>
                  <td className="admin-table__action">
                    {r.status === "pending" ? (
                      rejecting === r.request_id ? (
                        <div className="admin-form">
                          <TextInput label="Reason the client will read" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} autoFocus />
                          <Button size="sm" variant="danger" disabled={!reason.trim()} loading={busy === r.request_id} onClick={() => void decide(r.request_id, "reject")}>
                            Confirm rejection
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setRejecting(null)}>Cancel</Button>
                        </div>
                      ) : (
                        <>
                          <Button size="sm" variant="primary" loading={busy === r.request_id} onClick={() => void decide(r.request_id, "approve")}>
                            Approve and activate
                          </Button>{" "}
                          <Button size="sm" variant="ghost" onClick={() => { setRejecting(r.request_id); setReason(""); }}>
                            Reject
                          </Button>
                        </>
                      )
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Glass>
      ) : null}
    </div>
  );
}
