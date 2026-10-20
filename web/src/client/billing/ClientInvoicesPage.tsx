/** The Client's invoices and subscription (read only).
 *
 *  Everything comes from the server for the logged-in client. Honesty rules: only a PAID invoice is
 *  shown as money received; a free invoice or a 100% discount is shown as what it is and never as a
 *  payment; nothing here can change an invoice. */

import { useCallback, useEffect, useState } from "react";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { Glass, type Tint } from "../../ui/Glass";
import { useI18n } from "../../i18n";
import { ClientApiError } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import {
  clientInvoice,
  clientInvoices,
  clientSubscription,
  type ClientInvoice,
  type ClientInvoiceDetail,
  type InvoiceStatus,
  type Subscription,
} from "./billingApi";
import { billingText, fill } from "./billingText";

const TINT: Record<InvoiceStatus, Tint> = {
  PENDING: "neutral",
  PAID: "success",
  FAILED: "attention",
  FOR_FREE: "seal",
  DISCOUNTED_100: "seal",
};

export function ClientInvoicesPage() {
  const { locale, n, d } = useI18n();
  const { logout } = useClientAuth();
  const tx = billingText(locale);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [invoices, setInvoices] = useState<ClientInvoice[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [selected, setSelected] = useState<ClientInvoiceDetail | null>(null);

  const apply = useCallback(
    (result: PromiseSettledResult<[Subscription, ClientInvoice[]]>) => {
      if (result.status === "fulfilled") {
        setSubscription(result.value[0]);
        setInvoices(result.value[1]);
        setFailure(null);
        return;
      }
      const err = result.reason;
      if (err instanceof ClientApiError && err.status === 401) {
        setFailure(tx.errorSession);
        void logout();
      } else if (err instanceof ClientApiError && err.status === 503) {
        setFailure(tx.errorUnavailable);
      } else {
        setFailure(tx.errorGeneric);
      }
    },
    [logout, tx],
  );

  const fetchAll = (): Promise<[Subscription, ClientInvoice[]]> => Promise.all([clientSubscription(), clientInvoices()]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([fetchAll()]).then(([result]) => {
      if (!cancelled) apply(result!);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  async function reload() {
    const [result] = await Promise.allSettled([fetchAll()]);
    apply(result!);
  }

  const money = (value: string, currency: string) => n(Number(value), { style: "currency", currency });
  const statusLabel = (i: ClientInvoice) => (i.overdue ? tx.s_OVERDUE : tx[`s_${i.status}` as keyof typeof tx]);
  const tint = (i: ClientInvoice): Tint => (i.overdue ? "danger" : TINT[i.status]);

  async function open(id: number) {
    try {
      setSelected(await clientInvoice(id));
    } catch {
      setFailure(tx.errorGeneric);
    }
  }

  if (invoices === null || subscription === null) {
    return (
      <div className="page page--focus">
        <div className="page-head">
          <h2 className="t-h1">{tx.title}</h2>
        </div>
        {failure ? (
          <>
            <p className="admin-alert admin-alert--danger" role="alert">
              {failure}
            </p>
            <div>
              <Button onClick={() => void reload()}>{tx.retry}</Button>
            </div>
          </>
        ) : (
          <p className="c-3" role="status">
            {tx.loading}
          </p>
        )}
      </div>
    );
  }

  const plan = subscription.plan;
  const last = subscription.last_payment;

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{tx.title}</h2>
      </div>
      {failure && (
        <p className="admin-alert admin-alert--danger" role="alert">
          {failure}
        </p>
      )}

      <Glass level={2} as="section" aria-label={tx.plan}>
        <h3 className="t-h2">{tx.plan}</h3>
        {plan === null ? (
          <p className="c-3">{tx.noPlan}</p>
        ) : (
          <dl className="kv">
            <dt>{tx.plan}</dt>
            <dd>{plan.name}</dd>
            <dt>{tx.periodicity}</dt>
            <dd>{plan.billing_period === "annual" ? tx.annual : tx.monthly}</dd>
            <dt>{tx.price}</dt>
            <dd>{money(plan.monthly_price, plan.currency)}</dd>
            <dt>{tx.dailyQuota}</dt>
            <dd>{fill(tx.actionsPerDay, { n: n(plan.daily_quota) })}</dd>
            <dt>{tx.credits}</dt>
            <dd>{n(plan.included_credits)}</dd>
            <dt>{tx.since}</dt>
            <dd>{d(plan.since, { dateStyle: "medium" })}</dd>
            {subscription.next_billing_at && (
              <>
                <dt>{tx.nextBilling}</dt>
                <dd>{d(subscription.next_billing_at, { dateStyle: "medium" })}</dd>
              </>
            )}
          </dl>
        )}
        <p>
          <strong>{tx.unpaid}:</strong>{" "}
          {subscription.unpaid_invoices === 0
            ? tx.none
            : `${subscription.unpaid_invoices} (${subscription.overdue_invoices} ${tx.overdue})`}
        </p>
        {last && (
          <p>
            <strong>{tx.lastPayment}:</strong> {last.invoice_number}{" "}
            <Chip tint={last.collected ? "success" : TINT[last.status]}>{tx[`s_${last.status}` as keyof typeof tx]}</Chip>
          </p>
        )}
      </Glass>

      <section aria-label={tx.invoices}>
        <h3 className="t-h2">{tx.invoices}</h3>
        {invoices.length === 0 ? (
          <p className="c-3">{tx.noInvoices}</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{tx.number}</th>
                <th>{tx.period}</th>
                <th>{tx.amount}</th>
                <th>{tx.afterDiscount}</th>
                <th>{tx.status}</th>
                <th>{tx.due}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {invoices.map((i) => (
                <tr key={i.invoice_id}>
                  <td>{i.number}</td>
                  <td>{i.period}</td>
                  <td>{money(i.amount, i.currency)}</td>
                  <td>{money(i.amount_after_discount ?? i.amount, i.currency)}</td>
                  <td>
                    <Chip tint={tint(i)}>{statusLabel(i)}</Chip>
                  </td>
                  <td>{d(i.due_at, { dateStyle: "medium" })}</td>
                  <td>
                    <Button onClick={() => void open(i.invoice_id)} aria-label={`${tx.open} ${i.number}`}>
                      {tx.open}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="c-3">{tx.readOnly}</p>
      </section>

      {selected && (
        <Glass level={3} as="section" aria-label={fill(tx.detail, { number: selected.number })}>
          <h3 className="t-h2">{fill(tx.detail, { number: selected.number })}</h3>
          <table className="table">
            <thead>
              <tr>
                <th>{tx.plan_line}</th>
                <th>{tx.days}</th>
                <th>{tx.lineAmount}</th>
              </tr>
            </thead>
            <tbody>
              {selected.lines.map((line, index) => (
                <tr key={index}>
                  <td>{line.plan_name}</td>
                  <td>
                    {line.days} / {line.days_in_period}
                  </td>
                  <td>{money(line.amount, line.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected.discount_amount && (
            <p>
              {tx.discount}: {money(selected.discount_amount, selected.currency)}
            </p>
          )}
          {selected.free_amount && (
            <p>
              {tx.free}: {money(selected.free_amount, selected.currency)}
            </p>
          )}
          <p className="c-3">{selected.collected ? tx.collectedNote : selected.status === "FOR_FREE" || selected.status === "DISCOUNTED_100" ? tx.notCollectedNote : ""}</p>
          <Button onClick={() => setSelected(null)}>{tx.close}</Button>
        </Glass>
      )}
    </div>
  );
}
