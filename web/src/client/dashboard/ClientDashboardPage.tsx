/** The Client's results: the funnel (prospects, contacted, replies, conversions), the activity over time
 *  and the results by product / source / channel. The agent's state and problems live on Home, the quota on
 *  Usage, the invoices on Account and the payments to confirm under Review.
 *
 *  Everything comes from `GET /client/v1/dashboard` for the logged-in client. Honesty rules: a figure
 *  that is not tracked says so and is never shown as 0; nothing technical is shown. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CalendarRange, CreditCard, MessagesSquare, Send, Users } from "lucide-react";
import { Button } from "../../ui/Button";
import { Glass, type Tint } from "../../ui/Glass";
import { useI18n } from "../../i18n";
import { TimeChart } from "../../dashboard/TimeChart";
import { indicatorText, monthDefaults } from "../../dashboard/format";
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
  const { locale, n } = useI18n();
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
    body = <Body data={value} tx={tx} num={num} chartText={chartText} />;
  }

  return (
    <div className="dv-section rv-results" style={{ marginBlockStart: 0 }}>
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
}: {
  data: ClientDashboard;
  tx: DashboardText;
  num: (v: number) => string;
  chartText: { notTracked: string; period: string; legend: string; none: string };
}) {
  const a = data.activity;
  const s = data.series;
  const na = (series: { availability: string; reason?: string | null }) => (series.availability === "not_tracked" ? (series.reason ?? tx.notTracked) : null);
  return (
    <>
      <div className="dv-kpis">
        <Kpi tx={tx} n={num} tint="action" icon={<Users size={18} />} label={tx.prospects} ind={a.prospects_total} />
        <Kpi tx={tx} n={num} tint="action" icon={<Send size={18} />} label={tx.contacted} ind={a.prospects_contacted} />
        <Kpi tx={tx} n={num} tint="agent" icon={<MessagesSquare size={18} />} label={tx.replies} ind={a.replies_received} />
        <Kpi tx={tx} n={num} tint="agent" icon={<MessagesSquare size={18} />} label={tx.replyRate} ind={a.reply_rate_percent} suffix=" %" />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionsDeclared} ind={a.conversions_declared} note={tx.conversionsDeclaredNote} />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionsConfirmed} ind={a.conversions_confirmed} note={tx.conversionsConfirmedNote} />
        <Kpi tx={tx} n={num} tint="success" icon={<CreditCard size={18} />} label={tx.conversionRateConfirmed} ind={a.conversion_rate_confirmed_percent} suffix=" %" />
      </div>

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

      <Section title={tx.results}>
        <div className="dv-grid-3">
          <Breakdown title={tx.byProduct} rows={a.by_product} tx={tx} ent="prd" />
          <Breakdown title={tx.bySource} rows={a.by_source} tx={tx} ent="src" />
          <Breakdown title={tx.byChannel} rows={a.by_channel} tx={tx} ent="chn" />
        </div>
      </Section>

      <div>
        <Link to="/review/payments" className="link-btn">
          {tx.seePayments} <ArrowRight size={14} className="mirror" aria-hidden />
        </Link>
      </div>
    </>
  );
}
