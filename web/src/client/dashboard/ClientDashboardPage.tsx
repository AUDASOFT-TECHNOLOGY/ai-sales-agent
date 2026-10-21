/** The Client's dashboard: its agent, activity, results, payments, problems, usage and invoices.
 *
 *  Everything comes from `GET /client/v1/dashboard` for the logged-in client. Honesty rules: a figure
 *  that is not tracked says so and is never shown as 0; nothing technical is shown. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../ui/Button";
import { Glass } from "../../ui/Glass";
import { useI18n } from "../../i18n";
import { TimeChart } from "../../dashboard/TimeChart";
import { indicatorText, money, monthDefaults } from "../../dashboard/format";
import type { ClientDashboard, Indicator } from "../../dashboard/types";
import { ClientApiError } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { useFetched } from "../useFetched";
import { clientDashboard } from "./clientDashboardApi";
import { dashboardText, type DashboardText } from "./dashboardText";

function Kpi({ label, ind, note, suffix, tx, n }: { label: string; ind: Indicator; note?: string; suffix?: string; tx: DashboardText; n: (v: number) => string }) {
  const na = ind.value === null;
  return (
    <Glass level={1} className="dv-kpi">
      <p className="dv-kpi__label">{label}</p>
      <p className={na ? "dv-kpi__value dv-kpi__value--na" : "dv-kpi__value"}>{indicatorText(ind, n, suffix)}</p>
      <p className="dv-kpi__note">
        {na ? <span className="dv-badge dv-badge--not_tracked">{tx.notTracked}</span> : null}
        {ind.availability === "estimated" ? <span className="dv-badge dv-badge--estimated">{tx.estimated}</span> : null} {note}
      </p>
    </Glass>
  );
}

function Breakdown({ title, rows, tx }: { title: string; rows: Record<string, number>; tx: DashboardText }) {
  const entries = Object.entries(rows);
  return (
    <Glass level={1} className="dv-card dv-scroll">
      <p className="dv-chart__title">{title}</p>
      {entries.length === 0 ? (
        <p className="dv-note">{tx.nothing}</p>
      ) : (
        <table className="dv-data">
          <caption className="dv-sr">{title}</caption>
          <tbody>
            {entries.map(([name, count]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td>{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Glass>
  );
}

export function ClientDashboardPage() {
  const { locale, n, d } = useI18n();
  const { logout } = useClientAuth();
  const tx = dashboardText(locale);
  const defaults = monthDefaults();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);
  const [attempt, setAttempt] = useState(0);
  const load = useCallback(() => {
    void attempt; // a new attempt is a new request
    return clientDashboard({ from, to });
  }, [from, to, attempt]);
  const { loading, value, failed, error } = useFetched<ClientDashboard>(load);
  const expired = failed && error instanceof ClientApiError && error.status === 401;
  useEffect(() => {
    if (expired) void logout();
  }, [expired, logout]);
  const chartText = { notTracked: tx.notTracked, period: tx.period, legend: tx.legend, none: tx.none };
  const num = (v: number) => n(v);

  let body;
  if (loading) {
    body = (
      <p className="dv-note" role="status" aria-live="polite">
        {tx.loading}
      </p>
    );
  } else if (failed || !value) {
    body = (
      <div role="alert">
        <p>{expired ? tx.errorSession : tx.errorGeneric}</p>
        <Button onClick={() => setAttempt((a) => a + 1)}>{tx.retry}</Button>
      </div>
    );
  } else {
    body = <Body data={value} tx={tx} num={num} chartText={chartText} locale={locale} date={d} />;
  }

  return (
    <div className="dv-section" style={{ marginBlockStart: 0 }}>
      <h1 className="t-h1">{tx.title}</h1>
      <div className="dv-filters">
        <label>
          <span className="c-2 t-body-sm">{tx.from}</span>
          <input className="input well" type="month" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} />
        </label>
        <label>
          <span className="c-2 t-body-sm">{tx.to}</span>
          <input className="input well" type="month" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} />
        </label>
      </div>
      {body}
    </div>
  );
}

function Body({
  data,
  tx,
  num,
  chartText,
  locale,
  date,
}: {
  data: ClientDashboard;
  tx: DashboardText;
  num: (v: number) => string;
  chartText: { notTracked: string; period: string; legend: string; none: string };
  locale: string;
  date: (v: string) => string;
}) {
  const a = data.activity;
  const s = data.series;
  const agentState = data.agent.state === "running" ? tx.agentRunning : data.agent.state === "paused" ? tx.agentPaused : tx.agentNotStarted;
  const todo = (data.agent.integrations ?? []).filter((c) => !c.ok && c.message);
  const na = (series: { availability: string; reason?: string | null }) => (series.availability === "not_tracked" ? (series.reason ?? tx.notTracked) : null);
  const bill = data.billing;
  const usage = data.usage;
  return (
    <>
      <Glass level={1} className="dv-card" aria-label={tx.agent}>
        <p className="dv-chart__title">{tx.agent}</p>
        {data.agent.availability === "not_tracked" ? (
          <p className="dv-note dv-note--na">{tx.notTracked}</p>
        ) : (
          <>
            <p>
              <strong>{agentState}</strong>
              {data.agent.state === "running" && !data.agent.active ? ` - ${tx.agentWaiting}` : ""}
            </p>
            {todo.length > 0 ? (
              <>
                <p className="dv-note">{tx.toDo}</p>
                <ul>
                  {todo.map((c) => (
                    <li key={c.id}>{c.message}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </Glass>

      <div className="dv-kpis">
        <Kpi tx={tx} n={num} label={tx.prospects} ind={a.prospects_total} />
        <Kpi tx={tx} n={num} label={tx.contacted} ind={a.prospects_contacted} />
        <Kpi tx={tx} n={num} label={tx.conversations} ind={a.conversations} />
        <Kpi tx={tx} n={num} label={tx.replies} ind={a.replies_received} />
        <Kpi tx={tx} n={num} label={tx.replyRate} ind={a.reply_rate_percent} suffix=" %" />
        <Kpi tx={tx} n={num} label={tx.conversionsDeclared} ind={a.conversions_declared} note={tx.conversionsDeclaredNote} />
        <Kpi tx={tx} n={num} label={tx.conversionsConfirmed} ind={a.conversions_confirmed} note={tx.conversionsConfirmedNote} />
        <Kpi tx={tx} n={num} label={tx.conversionRateDeclared} ind={a.conversion_rate_declared_percent} suffix=" %" />
        <Kpi tx={tx} n={num} label={tx.conversionRateConfirmed} ind={a.conversion_rate_confirmed_percent} suffix=" %" />
        <Kpi tx={tx} n={num} label={tx.human} ind={data.human_requests} note={tx.humanNote} />
        <Kpi tx={tx} n={num} label={tx.errors} ind={a.with_errors} />
      </div>

      <div className="dv-grid-2">
        <Glass level={1} className="dv-card">
          <TimeChart
            title={tx.activityChart}
            kind={s.activity.points.length > 45 ? "line" : "bars"}
            labels={s.activity.points.map((p) => p.period)}
            series={[
              { id: "sent", label: tx.sent, color: "var(--dv-a)", values: s.activity.points.map((p) => p.sent) },
              { id: "received", label: tx.received, color: "var(--dv-b)", values: s.activity.points.map((p) => p.received) },
            ]}
            format={num}
            unavailable={na(s.activity)}
            emptyText={tx.noData}
            tableLabel={tx.tableLabel}
            text={chartText}
          />
        </Glass>
        <Glass level={1} className="dv-card">
          <TimeChart
            title={tx.perfChart}
            description={tx.perfDesc}
            labels={s.performance.points.map((p) => p.period)}
            series={[{ id: "rate", label: tx.perfChart, color: "var(--dv-c)", values: s.performance.points.map((p) => p.reply_rate_percent.value) }]}
            format={(v) => `${num(Math.round(v * 10) / 10)} %`}
            unavailable={na(s.performance)}
            emptyText={tx.perfEmpty}
            tableLabel={tx.tableLabel}
            text={chartText}
          />
        </Glass>
      </div>

      <section className="dv-section" style={{ marginBlockStart: 0 }} aria-label={tx.results}>
        <h2 className="t-h2">{tx.results}</h2>
        <div className="dv-grid-2">
          <Breakdown title={tx.byProduct} rows={a.by_product} tx={tx} />
          <Breakdown title={tx.byCircuit} rows={a.by_circuit} tx={tx} />
          <Breakdown title={tx.bySource} rows={a.by_source} tx={tx} />
          <Breakdown title={tx.byChannel} rows={a.by_channel} tx={tx} />
          <Breakdown title={tx.byStatus} rows={a.by_status} tx={tx} />
        </div>
      </section>

      <section className="dv-section" style={{ marginBlockStart: 0 }} aria-label={tx.payments}>
        <h2 className="t-h2">{tx.payments}</h2>
        <div className="dv-kpis">
          <Kpi tx={tx} n={num} label={tx.declared} ind={a.payments.declared} />
          <Kpi tx={tx} n={num} label={tx.confirmed} ind={a.payments.confirmed} />
          <Kpi tx={tx} n={num} label={tx.awaiting} ind={a.payments.awaiting_confirmation} note={tx.paymentsNote} />
        </div>
      </section>

      <section className="dv-section" style={{ marginBlockStart: 0 }} aria-label={tx.problems}>
        <h2 className="t-h2">{tx.problems}</h2>
        <div className="dv-kpis">
          <Kpi tx={tx} n={num} label={tx.openProblems} ind={data.incidents.open} />
          <Kpi tx={tx} n={num} label={tx.connectionProblems} ind={data.incidents.connection_problems} />
        </div>
        {data.incidents.items.length === 0 ? (
          <p className="dv-note">{tx.noProblems}</p>
        ) : (
          <ul>
            {data.incidents.items.map((i) => (
              <li key={i.id}>
                <strong>{i.title}</strong> - {i.message} {i.action}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="dv-section" style={{ marginBlockStart: 0 }} aria-label={tx.usage}>
        <h2 className="t-h2">{tx.usage}</h2>
        {usage.availability === "not_tracked" ? (
          <p className="dv-note dv-note--na">{tx.notTracked}</p>
        ) : (
          <>
            <div className="dv-kpis">
              <Kpi tx={tx} n={num} label={tx.usageToday} ind={{ value: usage.daily_used ?? null, availability: usage.daily_used == null ? "not_tracked" : "measured" }} />
              <Kpi tx={tx} n={num} label={tx.usageLimit} ind={{ value: usage.daily_limit ?? null, availability: usage.daily_limit == null ? "not_tracked" : "measured" }} />
              <Kpi tx={tx} n={num} label={tx.usageMonth} ind={{ value: usage.month_used ?? null, availability: usage.month_used == null ? "not_tracked" : "measured" }} />
            </div>
            {usage.message ? (
              <p role={usage.state === "reached" ? "alert" : "status"}>
                {usage.message} {usage.action}
              </p>
            ) : null}
          </>
        )}
      </section>

      <section className="dv-section" style={{ marginBlockStart: 0 }} aria-label={tx.billing}>
        <h2 className="t-h2">{tx.billing}</h2>
        {bill.availability === "not_tracked" || !bill.subscription || !bill.invoices ? (
          <p className="dv-note dv-note--na">{tx.notTracked}</p>
        ) : (
          <>
            <p>
              {tx.subscription}: <strong>{bill.subscription.status === "active" ? `${tx.active} - ${bill.subscription.plan_name}` : tx.noPlan}</strong>
              {bill.subscription.next_billing_at ? ` (${tx.nextInvoice}: ${date(bill.subscription.next_billing_at)})` : ""}
            </p>
            <div className="dv-kpis">
              <Kpi tx={tx} n={num} label={tx.invoicesIssued} ind={bill.invoices.issued} />
              <Kpi tx={tx} n={num} label={tx.invoicesPaid} ind={bill.invoices.paid} />
              <Kpi tx={tx} n={num} label={tx.invoicesUnpaid} ind={bill.invoices.unpaid} />
              <Kpi tx={tx} n={num} label={tx.invoicesOverdue} ind={bill.invoices.overdue} />
            </div>
            {bill.invoices.recent.length > 0 ? (
              <Glass level={1} className="dv-card dv-scroll">
                <table className="dv-data">
                  <caption>{tx.recent}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{tx.invoice}</th>
                      <th scope="col">{tx.period}</th>
                      <th scope="col">{tx.billed}</th>
                      <th scope="col">{tx.toPay}</th>
                      <th scope="col">{tx.status}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bill.invoices.recent.map((i) => (
                      <tr key={i.number}>
                        <th scope="row">{i.number}</th>
                        <td>{i.period}</td>
                        <td>{money(i.amount, i.currency, locale)}</td>
                        <td>{money(i.amount_after_discount ?? i.amount, i.currency, locale)}</td>
                        <td>{(tx as unknown as Record<string, string>)[`s_${i.status}`] ?? i.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Glass>
            ) : null}
            <Link to="/billing">{tx.seeInvoices}</Link>
          </>
        )}
      </section>
    </>
  );
}
