/** Billing > Invoices: the invoices, one per billing cycle (GET /admin/v1/billing/invoices -- real data, no fixtures).
 *  Filter by client, period and status. An owner issues the invoices that are due now (invoices already issued
 *  are never rebuilt) and declares an unpaid invoice PAID or FOR FREE, once. A payment can also be
 *  confirmed electronically (signed webhook, see docs/billing-payments.md): the invoice then shows the
 *  origin and transaction, and "Mark PAID" is no longer offered (the server refuses it with a 409).
 *  FAILED and Overdue come from the server. An invoice shows the plan terms it was built from. */

import { useEffect, useId, useState } from "react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell, TextInput } from "../../ui/Controls";
import { Modal } from "../../ui/Overlays";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminGenerateInvoices,
  adminListClients,
  adminListInvoices,
  adminSettleInvoice,
  type ClientSummary,
  type GenerateInvoicesResult,
  type Invoice,
  type InvoiceFilters,
  type InvoiceStatus,
  type PaymentOrigin,
} from "../api";

// Mirrors require_owner on POST /billing/invoices/generate and PUT /billing/invoices/:id/status.
const CAN_EDIT_INVOICES_ROLES = new Set(["owner"]);

const STATUS_LABEL: Record<InvoiceStatus, string> = { PENDING: "Pending", PAID: "Paid", FOR_FREE: "For free", DISCOUNTED_100: "Discounted 100%", FAILED: "Failed" };
const STATUS_TINT = { PENDING: "attention", PAID: "success", FOR_FREE: "neutral", DISCOUNTED_100: "neutral", FAILED: "danger" } as const;
const ORIGIN_LABEL: Record<PaymentOrigin, string> = { manual: "Manual", electronic: "Electronic" };

/** Unpaid invoices can be settled by hand; one paid electronically cannot (the server says 409). */
const canSettle = (i: Invoice) => i.status === "PENDING" || i.status === "FAILED";

function StatusChips({ invoice }: { invoice: Invoice }) {
  return (
    <>
      <Chip tint={STATUS_TINT[invoice.status]}>{STATUS_LABEL[invoice.status]}</Chip>
      {invoice.overdue ? <Chip tint="danger">Overdue</Chip> : null}
    </>
  );
}

type LoadState = "loading" | "ready" | "error";

function generateNotice(result: GenerateInvoicesResult): string {
  const parts = [`${result.created.length} invoice(s) issued`];
  if (result.already_invoiced > 0) parts.push(`${result.already_invoiced} already invoiced (left as they were)`);
  if (result.skipped.length > 0) {
    parts.push(`not invoiced: ${result.skipped.map((s) => `${s.client_id} (${s.reason})`).join(", ")}`);
  }
  return `${parts.join("; ")}.`;
}

export function AdminInvoicesPanel() {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_INVOICES_ROLES.has(admin.role);
  const clientSelectId = useId();
  const statusSelectId = useId();
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [filters, setFilters] = useState<InvoiceFilters>({});
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [viewing, setViewing] = useState<Invoice | null>(null);
  const [settling, setSettling] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminListClients()
      .then((data) => {
        if (!cancelled) setClients(data);
      })
      .catch(() => undefined); // the client filter is then just empty
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    adminListInvoices(filters)
      .then((data) => {
        if (cancelled) return;
        setInvoices(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load the invoices.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reload]);

  function setFilter(change: Partial<InvoiceFilters>) {
    setFilters((current) => {
      const next = { ...current, ...change };
      for (const key of Object.keys(next) as (keyof InvoiceFilters)[]) {
        if (!next[key]) delete next[key];
      }
      return next;
    });
  }

  async function onGenerate() {
    setGenerating(true);
    setNotice(null);
    try {
      const result = await adminGenerateInvoices();
      setNotice({ tone: "success", text: generateNotice(result) });
      setReload((n) => n + 1);
    } catch (err) {
      setNotice({ tone: "danger", text: err instanceof AdminApiError ? err.message : "Could not issue the invoices." });
    } finally {
      setGenerating(false);
    }
  }

  async function onSettle(invoice: Invoice, status: "PAID" | "FOR_FREE" | "DISCOUNTED_100") {
    setSettling(invoice.invoice_id);
    setNotice(null);
    try {
      const settled = await adminSettleInvoice(invoice.invoice_id, status);
      setInvoices((current) => current.map((i) => (i.invoice_id === settled.invoice_id ? settled : i)));
      setViewing((current) => (current && current.invoice_id === settled.invoice_id ? settled : current));
    } catch (err) {
      setNotice({ tone: "danger", text: err instanceof AdminApiError ? err.message : "Could not update the invoice." });
    } finally {
      setSettling(null);
    }
  }

  const clientName = (id: string) => clients.find((c) => c.client_id === id)?.display_name ?? id;

  return (
    <div className="admin-clients">
      <div className="admin-clients__header">
        <div className="admin-clients__title">
          <h2 className="t-h2">Invoices</h2>
          {state === "ready" ? <span className="admin-count tabular">{invoices.length}</span> : null}
        </div>
      </div>

      {canEdit ? (
        <Glass level={1} className="admin-detail-card">
          <h3 className="t-label c-2">Issue due invoices</h3>
          <div className="admin-form">
            <p className="c-2 t-body-sm">
              Invoices are issued automatically when a plan is assigned, then every month on the same day, at the plan price.
              Use this to issue any that are due now; invoices already issued are left untouched.
            </p>
            <Button variant="primary" size="sm" loading={generating} onClick={() => void onGenerate()}>
              Issue due invoices
            </Button>
          </div>
        </Glass>
      ) : null}

      {notice ? (
        <p className={`admin-alert admin-alert--${notice.tone}`} role={notice.tone === "danger" ? "alert" : "status"}>
          {notice.text}
        </p>
      ) : null}

      <Glass level={1} className="admin-detail-card">
        <h3 className="t-label c-2">Filters</h3>
        <div className="admin-form">
          <FieldShell id={clientSelectId} label="Client">
            <select id={clientSelectId} className="input well" value={filters.client_id ?? ""} onChange={(e) => setFilter({ client_id: e.target.value })}>
              <option value="">All clients</option>
              {clients.map((c) => (
                <option key={c.client_id} value={c.client_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </FieldShell>
          <TextInput label="From month" type="month" value={filters.period_from ?? ""} onChange={(e) => setFilter({ period_from: e.target.value })} />
          <TextInput label="To month" type="month" value={filters.period_to ?? ""} onChange={(e) => setFilter({ period_to: e.target.value })} />
          <FieldShell id={statusSelectId} label="Status">
            <select
              id={statusSelectId}
              className="input well"
              value={filters.status ?? ""}
              onChange={(e) => setFilter({ status: e.target.value as InvoiceStatus })}
            >
              <option value="">All statuses</option>
              {(Object.keys(STATUS_LABEL) as InvoiceStatus[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </FieldShell>
        </div>
      </Glass>

      {state === "loading" ? (
        <p className="admin-alert admin-alert--info" role="status" aria-live="polite">
          Loading invoices…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && invoices.length === 0 ? (
        <Glass level={1} className="admin-placeholder admin-placeholder--center">
          <p className="c-2">No invoices match.</p>
        </Glass>
      ) : null}

      {state === "ready" && invoices.length > 0 ? (
        <Glass level={1} className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Invoice</th>
                <th scope="col">Client</th>
                <th scope="col">Invoice date</th>
                <th scope="col" className="admin-table__num">
                  Amount
                </th>
                <th scope="col">Status</th>
                <th scope="col">Payment</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((i) => (
                <tr key={i.invoice_id}>
                  <th scope="row" className="admin-table__name">
                    {i.number}
                  </th>
                  <td className="c-2">{clientName(i.client_id)}</td>
                  <td className="tabular">{i.issued_on ?? i.period}</td>
                  <td className="admin-table__num tabular">
                    {i.amount} {i.currency}
                  </td>
                  <td>
                    <StatusChips invoice={i} />
                  </td>
                  <td className="c-2">{i.status === "PAID" && i.payment_origin ? ORIGIN_LABEL[i.payment_origin] : "—"}</td>
                  <td className="admin-table__action">
                    <Button variant="ghost" size="sm" onClick={() => setViewing(i)} aria-label={`Details of ${i.number}`}>
                      Details
                    </Button>
                    {canEdit && canSettle(i) ? (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={settling === i.invoice_id}
                          onClick={() => void onSettle(i, "PAID")}
                          aria-label={`Mark ${i.number} paid`}
                        >
                          Mark PAID
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={settling === i.invoice_id}
                          onClick={() => void onSettle(i, "FOR_FREE")}
                          aria-label={`Mark ${i.number} for free`}
                        >
                          Mark FOR FREE
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={settling === i.invoice_id}
                          onClick={() => void onSettle(i, "DISCOUNTED_100")}
                          aria-label={`Apply a 100% discount to ${i.number}`}
                        >
                          100% discount
                        </Button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Glass>
      ) : null}

      {viewing ? <InvoiceModal invoice={viewing} clientName={clientName(viewing.client_id)} onClose={() => setViewing(null)} /> : null}
    </div>
  );
}

function InvoiceModal({ invoice, clientName, onClose }: { invoice: Invoice; clientName: string; onClose: () => void }) {
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={invoice.number}
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <p className="c-2">
        {clientName} · {invoice.issued_on ?? invoice.period} · <StatusChips invoice={invoice} />
      </p>
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Plan</th>
            <th scope="col" className="admin-table__num">
              Plan price
            </th>
            <th scope="col" className="admin-table__num">
              Credits
            </th>
            <th scope="col" className="admin-table__num">
              Daily quota
            </th>
            <th scope="col" className="admin-table__num">
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {invoice.lines.map((l, index) => (
            <tr key={index}>
              <th scope="row" className="admin-table__name">
                {l.plan_name}
              </th>
              <td className="admin-table__num tabular">
                {l.monthly_price} {l.currency}
              </td>
              <td className="admin-table__num tabular">{l.included_credits}</td>
              <td className="admin-table__num tabular">{l.daily_quota}</td>
              <td className="admin-table__num tabular">{l.amount}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="t-body-sm">
        Total: <strong className="tabular">{invoice.amount} {invoice.currency}</strong>
      </p>
      <p className="c-3 t-body-sm">
        Issued {new Date(invoice.created_at).toLocaleDateString()} by {invoice.created_by}.
        {invoice.status_changed_at && invoice.status_changed_by
          ? ` ${STATUS_LABEL[invoice.status]} on ${new Date(invoice.status_changed_at).toLocaleDateString()} by ${invoice.status_changed_by}.`
          : ""}
      </p>
      {invoice.status === "DISCOUNTED_100" ? (
        <div className="c-3 t-body-sm" data-testid="invoice-discount">
          <p>
            100% discount (not a payment): billed {invoice.amount} {invoice.currency}, after discount {invoice.amount_after_discount ?? "0.00"}{" "}
            {invoice.currency}, discounted {invoice.discount_amount ?? invoice.amount} {invoice.currency}, free {invoice.free_amount ?? invoice.amount}{" "}
            {invoice.currency}.
          </p>
          {invoice.discount_reason ? <p>Reason: {invoice.discount_reason}</p> : null}
        </div>
      ) : null}
      {invoice.status === "FOR_FREE" ? (
        <p className="c-3 t-body-sm">Given for free (not a payment): {invoice.free_amount ?? invoice.amount} {invoice.currency}.</p>
      ) : null}
      {invoice.status === "PAID" && invoice.payment_origin ? (
        <p className="c-3 t-body-sm">
          Payment origin: {ORIGIN_LABEL[invoice.payment_origin]}
          {invoice.payment_origin === "electronic" && invoice.payment_transaction_id
            ? ` (transaction ${invoice.payment_transaction_id}, event ${invoice.payment_event_id ?? "?"})`
            : ""}
          .
        </p>
      ) : null}
      {invoice.due_at && (invoice.status === "PENDING" || invoice.status === "FAILED") ? (
        <p className="c-3 t-body-sm">Due {new Date(invoice.due_at).toLocaleDateString()}.</p>
      ) : null}
    </Modal>
  );
}
