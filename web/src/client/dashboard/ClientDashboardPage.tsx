/** The Client's results: its agent, activity, results by product / source / channel, payments,
 *  problems, usage and a summary of its invoices.
 *
 *  Everything comes from `GET /client/v1/dashboard` for the logged-in client. Honesty rules: a figure
 *  that is not tracked says so and is never shown as 0; nothing technical is shown. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CalendarRange, CreditCard, MessagesSquare, ShieldAlert, Users } from "lucide-react";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Glass, type Tint } from "../../ui/Glass";
import { useI18n } from "../../i18n";
import { StatusOrb } from "../../agent/StatusOrb";
import { TimeChart } from "../../dashboard/TimeChart";
import { indicatorText, money, monthDefaults } from "../../dashboard/format";
import type { ClientDashboard, Indicator } from "../../dashboard/types";
import { ClientApiError } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { useFetched } from "../useFetched";
import { clientDashboard } from "./clientDashboardApi";
import { dashboardText, type DashboardText } from "./dashboardText";

function Kpi({ label, ind, note, suffix, tx, n, tint = "neutral", icon }: { label: string; ind: Indicator; note?: string; suffix?: string; tx: DashboardText; n: (v: number) => string; tint?: Tint; icon?: React.ReactNode }) {
  const na = ind.value === null;
  return (
    <Glass level={1} tint={tint} className="dv-kpi">
      {icon ? (
        <span className="rv-stat__icon" aria-hidden>
          {icon}
        </span>
      ) : null}
      <p className="dv-kpi__label">{label}</p>
      <p className={na ? "dv-kpi__value dv-kpi__value--na" : "dv-kpi__value"}>{indicatorText(ind, n, suffix)}</p>
      <p className="dv-kpi__note">
        {na ? <span className="dv-badge dv-badge--not_tracked">{tx.notTracked}</span> : null}
        {ind.availability === "estimated" ? <span className="dv-badge dv-badge--estimated">{tx.estimated}</span> : null} {note}
      </p>
    </Glass>
  );
}

/** A count per name, as bars in the colour of what is counted (source, channel, product). */
function Breakdown({ title, rows, tx, ent }: { title: string; rows: Record<string, number>; tx: DashboardText; ent?: "src" | "chn" | "prd" }) {
  const entries = Object.entries(rows).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, c]) => c));
  return (
    <Glass level={1} className="dv-card rv-bars rv-ent" data-ent={ent}>
      <p className="dv-chart__title">{title}</p>
      {entries.length === 0 ? (
        <p className="dv-note">{tx.nothing}</p>
      ) : (
        <ul>
          {entries.map(([name, count]) => (
            <li key={name}>
              <span className="rv-bars__name" dir="auto">
                {name}
              </span>
              <span className="rv-bars__track" aria-hidden>
                <i style={{ width: `${(count / max) * 100}%` }} />
              </span>
              <strong className="tabular">{count}</strong>
            </li>
          ))}
        </ul>
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
      <div role="alert" className="admin-alert admin-alert--danger">
        <p>{expired ? tx.errorSession : tx.errorGeneric}</p>
        <Button onClick={() => setAttempt((a) => a + 1)}>{tx.retry}</Button>
      </div>
    );
  } else {
    body = <Body data={value} tx={tx} num={num} chartText={chartText} locale={locale} date={d} />;
  }

  return (
    <div className="dv-section rv-results" style={{ marginBlockStart: 0 }}>
      <h1 className="t-h1">{tx.title}</h1>
      <div className="dv-filters">
        <CalendarRange size={18} aria-hidden className="c-3" />
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rv-page" style={{ gap: 14 }} aria-label={title}>
      <h2 className="rv-section-title">{title}</h2>
      {children}
    </section>
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
  const pulse = data.agent.state === "running" ? (data.agent.active ? "working" : "idle") : data.agent.state === "paused" ? "paused" : "not_live";
  return (
    <>
      <Glass level={2} tint={data.agent.state === "running" ? "success" : "attention"} className="rv-card rv-agentline" aria-label={tx.agent}>
        {data.agent.availability === "not_tracked" ? (
          <p className="dv-note dv-note--na">{tx.notTracked}</p>
        ) : (
          <>
            <StatusOrb state={pulse} size={44} />
            <div className="rv-agentline__text">
              <p className="dv-chart__title">{tx.agent}</p>
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
            </div>
          </>
        )}
      </Glass>

      <div className="dv-kpis">
        <Kpi tx={tx} n={num} tint="action" icon={<Users size={18} />} label={tx.prospects} ind={a.prospects_total} />
        <Kpi tx={tx} n={num} tint="action" icon={<Users size={18} />} label={tx.contacted} ind={a.prospects_contacted} />
        <Kpi tx={tx} n={num} tint="agent" icon={<MessagesSquare size={18} />} label={tx.conversations} ind={a.conversations} />
        <Kpi tx={tx} n={num} tint="agent" icon={<MessagesSquare size={18} />} label={tx.replies} ind={a.replies_received} />
        <Kpi tx={tx} n={num} tint="agent" icon={<MessagesSquare size={18} />} label={tx.replyRate} ind={a.reply_rate_percent} suffix=" %" />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionsDeclared} ind={a.conversions_declared} note={tx.conversionsDeclaredNote} />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionsConfirmed} ind={a.conversions_confirmed} note={tx.conversionsConfirmedNote} />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionRateDeclared} ind={a.conversion_rate_declared_percent} suffix=" %" />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionRateConfirmed} ind={a.conversion_rate_confirmed_percent} suffix=" %" />
        <Kpi tx={tx} n={num} tint="attention" icon={<ShieldAlert size={18} />} label={tx.human} ind={data.human_requests} note={tx.humanNote} />
        <Kpi tx={tx} n={num} tint="danger" icon={<ShieldAlert size={18} />} label={tx.errors} ind={a.with_errors} />
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

      <Section title={tx.results}>
        <div className="dv-grid-3">
          <Breakdown title={tx.byProduct} rows={a.by_product} tx={tx} ent="prd" />
          <Breakdown title={tx.bySource} rows={a.by_source} tx={tx} ent="src" />
          <Breakdown title={tx.byChannel} rows={a.by_channel} tx={tx} ent="chn" />
        </div>
        <div className="dv-grid-2">
          <Breakdown title={tx.byCircuit} rows={a.by_circuit} tx={tx} />
          <Breakdown title={tx.byStatus} rows={a.by_status} tx={tx} />
        </div>
      </Section>

      <Section title={tx.payments}>
        <div className="dv-kpis">
          <Kpi tx={tx} n={num} tint="attention" label={tx.declared} ind={a.payments.declared} />
          <Kpi tx={tx} n={num} tint="success" label={tx.confirmed} ind={a.payments.confirmed} />
          <Kpi tx={tx} n={num} tint="attention" label={tx.awaiting} ind={a.payments.awaiting_confirmation} note={tx.paymentsNote} />
        </div>
      </Section>

      <div className="dv-grid-2">
        <Section title={tx.problems}>
          <Glass level={1} className="rv-card">
            <div className="rv-card__head">
              <span>{tx.openProblems}</span>
              <Chip tint={(data.incidents.open.value ?? 0) > 0 ? "attention" : "success"}>{indicatorText(data.incidents.open, num)}</Chip>
            </div>
            <div className="rv-card__head">
              <span>{tx.connectionProblems}</span>
              <Chip tint={(data.incidents.connection_problems.value ?? 0) > 0 ? "danger" : "success"}>{indicatorText(data.incidents.connection_problems, num)}</Chip>
            </div>
            {data.incidents.items.length === 0 ? (
              <p className="dv-note">{tx.noProblems}</p>
            ) : (
              <ul className="stack">
                {data.incidents.items.map((i) => (
                  <li key={i.id}>
                    <strong>{i.title}</strong> - {i.message} {i.action}
                  </li>
                ))}
              </ul>
            )}
          </Glass>
        </Section>

        <Section title={tx.usage}>
          <Glass level={1} className="rv-card">
            {usage.availability === "not_tracked" ? (
              <p className="dv-note dv-note--na">{tx.notTracked}</p>
            ) : (
              <>
                <div className="rv-meter">
                  <span className="rv-meter__row">
                    <span>{tx.usageToday}</span>
                    <strong className="tabular">{usage.daily_limit != null ? `${num(usage.daily_used ?? 0)} / ${num(usage.daily_limit)}` : num(usage.daily_used ?? 0)}</strong>
                  </span>
                  {usage.daily_limit ? (
                    <div className="rv-progress" aria-hidden>
                      <div className="rv-progress__fill" style={{ width: `${Math.min(100, usage.daily_percent ?? 0)}%` }} />
                    </div>
                  ) : null}
                </div>
                <div className="rv-meter">
                  <span className="rv-meter__row">
                    <span>{tx.usageMonth}</span>
                    <strong className="tabular">{num(usage.month_used ?? 0)}</strong>
                  </span>
                </div>
                {usage.message ? (
                  <p className="c-2" role={usage.state === "reached" ? "alert" : "status"}>
                    {usage.message} {usage.action}
                  </p>
                ) : null}
              </>
            )}
          </Glass>
        </Section>
      </div>

      <Section title={tx.billing}>
        {bill.availability === "not_tracked" || !bill.subscription || !bill.invoices ? (
          <p className="dv-note dv-note--na">{tx.notTracked}</p>
        ) : (
          <Glass level={1} className="rv-card rv-card--flush">
            <div className="rv-card rv-card--bare" style={{ paddingBlockEnd: 12 }}>
              <div className="rv-card__head">
                <div>
                  <p>
                    {tx.subscription}: <strong>{bill.subscription.status === "active" ? `${tx.active} - ${bill.subscription.plan_name}` : tx.noPlan}</strong>
                  </p>
                  {bill.subscription.next_billing_at ? <p className="c-3 t-body-sm">{`${tx.nextInvoice}: ${date(bill.subscription.next_billing_at)}`}</p> : null}
                </div>
                <div className="rv-actions">
                  <Chip>{`${tx.invoicesIssued} · ${indicatorText(bill.invoices.issued, num)}`}</Chip>
                  <Chip tint="success">{`${tx.invoicesPaid} · ${indicatorText(bill.invoices.paid, num)}`}</Chip>
                  <Chip tint="attention">{`${tx.invoicesUnpaid} · ${indicatorText(bill.invoices.unpaid, num)}`}</Chip>
                  <Chip tint={(bill.invoices.overdue.value ?? 0) > 0 ? "danger" : "neutral"}>{`${tx.invoicesOverdue} · ${indicatorText(bill.invoices.overdue, num)}`}</Chip>
                </div>
              </div>
            </div>
            {bill.invoices.recent.length > 0 ? (
              <div className="ledger__scroll">
                <table className="rv-table">
                  <caption className="sr-only">{tx.recent}</caption>
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
                        <td>{i.issued_on ?? i.period}</td>
                        <td>{money(i.amount, i.currency, locale)}</td>
                        <td>{money(i.amount_after_discount ?? i.amount, i.currency, locale)}</td>
                        <td>{(tx as unknown as Record<string, string>)[`s_${i.status}`] ?? i.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            <div style={{ padding: "12px 24px 20px" }}>
              <Link to="/account" className="link-btn">
                {tx.seeInvoices} <ArrowRight size={14} className="mirror" aria-hidden />
              </Link>
            </div>
          </Glass>
        )}
      </Section>
    </>
  );
}
