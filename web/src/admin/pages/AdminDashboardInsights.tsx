/** The six chart families of the Admin dashboard, fed by `GET /admin/v1/dashboard` only:
 *  commercial activity, agent performance, financial evolution (billed / collected / free /
 *  discounted are told apart), subscriptions, technical costs by provider, reliability.
 *  Nothing is simulated: a figure that is not tracked says so and is never drawn as 0. */

import { Glass } from "../../ui/Glass";
import { TimeChart, type ChartSeries } from "../../dashboard/TimeChart";
import { amount, indicatorText, money, moneyMap } from "../../dashboard/format";
import type { AdminDashboard, Availability } from "../../dashboard/types";

const PROVIDER_COLORS = ["var(--dv-a)", "var(--dv-b)", "var(--dv-c)", "var(--dv-d)", "var(--dv-e)", "var(--dv-f)"];
const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI (LLM)",
  meta_whatsapp: "Meta WhatsApp",
  google: "Google",
  ovh: "OVH",
  infra: "Infrastructure",
  other: "Other",
};

function Badge({ availability }: { availability: Availability }) {
  if (availability === "measured") return null;
  return (
    <span className={`dv-badge dv-badge--${availability}`}>{availability === "estimated" ? "Estimated" : "Not tracked"}</span>
  );
}

const count = (v: number) => String(Math.round(v));

function Swatch({ color }: { color: string }) {
  return <span className="dv-swatch" style={{ background: color }} aria-hidden="true" />;
}

export function AdminDashboardInsights({ data }: { data: AdminDashboard }) {
  const s = data.series;
  const na = (availability: Availability, reason?: string | null) => (availability === "not_tracked" ? (reason ?? "not available") : null);

  // ---- finance, one chart per currency ----
  const timeline = s.finance.timeline;
  const currencies = [...new Set(timeline.map((p) => p.currency))].sort();
  const months = data.period.months;

  // ---- costs, one chart per currency ----
  const costPoints = s.costs.points;
  const costCurrencies = [...new Set(costPoints.flatMap((p) => Object.keys(p.totals)))].sort();
  const providers = [...new Set(costPoints.flatMap((p) => p.by_provider.map((b) => b.provider)))];

  const perf = s.performance.points;
  const activity = s.activity.points;
  const incidents = s.reliability.points;

  return (
    <>
      <section className="dv-section" aria-labelledby="dv-activity">
        <h2 id="dv-activity" className="t-h2">Commercial activity</h2>
        <div className="dv-grid-2">
          <Glass level={1} className="dv-card">
            <TimeChart
              title="Messages sent and received"
              description={`By ${s.activity.granularity ?? "day"}, all clients in scope.`}
              kind={activity.length > 45 ? "line" : "bars"}
              labels={activity.map((p) => p.period)}
              series={[
                { id: "sent", label: "Sent", color: "var(--dv-a)", values: activity.map((p) => p.sent) },
                { id: "received", label: "Received", color: "var(--dv-b)", values: activity.map((p) => p.received) },
              ]}
              format={count}
              unavailable={na(s.activity.availability, s.activity.reason)}
            />
          </Glass>
          <Glass level={1} className="dv-card">
            <TimeChart
              title="Agent performance: reply rate"
              description="Prospects who replied / prospects contacted, month by month (can exceed 100% when prospects write first)."
              labels={perf.map((p) => p.period)}
              series={[
                {
                  id: "rate",
                  label: "Reply rate (%)",
                  color: "var(--dv-c)",
                  values: perf.map((p) => p.reply_rate_percent.value),
                },
              ]}
              format={(v) => `${v.toFixed(1)}%`}
              unavailable={na(s.performance.availability, s.performance.reason)}
              emptyText="Nobody was contacted in this period, so there is no rate."
            />
          </Glass>
          <Glass level={1} className="dv-card">
            <TimeChart
              title="Agent performance: conversions"
              description="Declared = the prospect says they paid. Confirmed = payment confirmed by the client. A declaration is never counted as confirmed."
              labels={perf.map((p) => p.period)}
              series={[
                {
                  id: "declared",
                  label: "Declared",
                  color: "var(--dv-a)",
                  values: perf.map((p) => p.conversions_declared.value),
                },
                {
                  id: "confirmed",
                  label: "Confirmed",
                  color: "var(--dv-b)",
                  values: perf.map((p) => p.conversions_confirmed.value),
                },
              ]}
              format={(v) => String(Math.round(v))}
              unavailable={na(s.performance.availability, s.performance.reason)}
              emptyText="No conversion in this period."
            />
            <p className="dv-note">
              Conversion rate (of prospects contacted): declared{" "}
              {data.activity.conversion_rate_declared_percent.value ?? "—"}
              {data.activity.conversion_rate_declared_percent.value === null ? "" : "%"}, confirmed{" "}
              {data.activity.conversion_rate_confirmed_percent.value ?? "—"}
              {data.activity.conversion_rate_confirmed_percent.value === null ? "" : "%"}.
            </p>
          </Glass>
        </div>
      </section>

      <section className="dv-section" aria-labelledby="dv-finance">
        <h2 id="dv-finance" className="t-h2">Financial evolution</h2>
        {data.finance.availability === "not_tracked" ? (
          <p className="dv-note dv-note--na" role="status">Not tracked: {data.finance.reason}</p>
        ) : (
          <>
            <div className="dv-grid-2">
              {currencies.length === 0 ? (
                <Glass level={1} className="dv-card">
                  <TimeChart title="Billed vs collected" labels={[]} series={[]} emptyText="No invoice in this period." />
                </Glass>
              ) : (
                currencies.map((cur) => {
                  const rows = months.map((m) => timeline.find((p) => p.period === m && p.currency === cur));
                  return (
                    <Glass level={1} className="dv-card" key={cur}>
                      <TimeChart
                        title={`Billed vs collected (${cur})`}
                        description="Collected = invoices actually paid. A free invoice or a 100% discount is not money received."
                        kind="bars"
                        labels={months}
                        series={[
                          { id: "billed", label: "Billed", color: "var(--dv-billed)", values: rows.map((r) => amount(r?.billed) ?? null) },
                          { id: "collected", label: "Collected", color: "var(--dv-collected)", values: rows.map((r) => amount(r?.collected) ?? null) },
                        ]}
                        format={(v) => money(String(v), cur)}
                      />
                    </Glass>
                  );
                })
              )}
            </div>
            <Glass level={1} className="dv-card dv-scroll">
              <table className="dv-data">
                <caption className="dv-sr">Invoice totals by currency</caption>
                <thead>
                  <tr>
                    <th scope="col">Currency</th>
                    <th scope="col"><Swatch color="var(--dv-billed)" />Billed</th>
                    <th scope="col">After discount</th>
                    <th scope="col"><Swatch color="var(--dv-collected)" />Collected</th>
                    <th scope="col"><Swatch color="var(--dv-free)" />Free</th>
                    <th scope="col"><Swatch color="var(--dv-discounted)" />Discounted</th>
                    <th scope="col">Pending</th>
                    <th scope="col">Overdue</th>
                    <th scope="col">Invoices (issued / paid / free / discounted)</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.finance.totals ?? []).map((t) => (
                    <tr key={t.currency}>
                      <th scope="row">{t.currency}</th>
                      <td>{money(t.billed, t.currency)}</td>
                      <td>{money(t.after_discount, t.currency)}</td>
                      <td>{money(t.collected, t.currency)}</td>
                      <td>{money(t.free, t.currency)}</td>
                      <td>{money(t.discounted, t.currency)}</td>
                      <td>{money(t.pending, t.currency)}</td>
                      <td>{money(t.overdue, t.currency)}</td>
                      <td>
                        {t.invoices_issued} / {t.invoices_paid} / {t.invoices_free} / {t.invoices_discounted}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Glass>
          </>
        )}
      </section>

      <section className="dv-section" aria-labelledby="dv-subs">
        <h2 id="dv-subs" className="t-h2">Subscriptions</h2>
        <Glass level={1} className="dv-card">
          <TimeChart
            title="Active vs inactive subscriptions"
            description="At the end of each month: clients on a plan vs clients that had been on one but were not."
            labels={s.subscriptions.points.map((p) => p.period)}
            series={[
              { id: "active", label: "Active", color: "var(--dv-collected)", values: s.subscriptions.points.map((p) => p.active) },
              { id: "inactive", label: "Inactive", color: "var(--dv-e)", values: s.subscriptions.points.map((p) => p.inactive) },
            ]}
            format={count}
            unavailable={na(s.subscriptions.availability, data.finance.reason)}
          />
        </Glass>
      </section>

      <section className="dv-section" aria-labelledby="dv-costs">
        <h2 id="dv-costs" className="t-h2">Technical costs by provider</h2>
        {data.costs.availability === "not_tracked" && !data.costs.providers ? (
          <p className="dv-note dv-note--na" role="status">Not tracked: {data.costs.reason}</p>
        ) : (
          <>
            <div className="dv-grid-2">
              {costCurrencies.length === 0 ? (
                <Glass level={1} className="dv-card">
                  <TimeChart title="Costs by provider" labels={[]} series={[]} emptyText="No cost with an amount is known for this period." />
                </Glass>
              ) : (
                costCurrencies.map((cur) => {
                  const series: ChartSeries[] = providers.map((name, i) => ({
                    id: name,
                    label: PROVIDER_LABEL[name] ?? name,
                    color: PROVIDER_COLORS[i % PROVIDER_COLORS.length]!,
                    values: costPoints.map((p) => amount(p.by_provider.find((b) => b.provider === name)?.totals[cur]) ?? null),
                  }));
                  return (
                    <Glass level={1} className="dv-card" key={cur}>
                      <TimeChart
                        title={`Costs by provider (${cur})`}
                        description="Costs are counted by whole calendar month. A gap means no known amount."
                        kind="bars"
                        labels={costPoints.map((p) => p.period)}
                        series={series}
                        format={(v) => money(String(v), cur)}
                      />
                    </Glass>
                  );
                })
              )}
            </div>
            <Glass level={1} className="dv-card dv-scroll">
              <table className="dv-data">
                <caption className="dv-sr">Costs by provider with their basis</caption>
                <thead>
                  <tr>
                    <th scope="col">Provider</th>
                    <th scope="col">Basis</th>
                    <th scope="col">Known amount</th>
                    <th scope="col">Complete</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.costs.providers ?? []).map((p, i) => (
                    <tr key={p.provider}>
                      <th scope="row"><Swatch color={PROVIDER_COLORS[i % PROVIDER_COLORS.length]!} />{PROVIDER_LABEL[p.provider] ?? p.provider}</th>
                      <td>
                        {p.basis} <Badge availability={p.availability} />
                      </td>
                      <td>{moneyMap(p.totals)}</td>
                      <td>{p.complete === false ? "No (lower bound)" : "Yes"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Glass>
            <Glass level={1} className="dv-card" aria-label="Profitability">
              <p className="dv-chart__title">
                Profitability{" "}
                <span className="dv-badge">
                  {data.profitability.status === "available" ? "Available" : data.profitability.status === "partial" ? "Partial" : "Not available"}
                </span>
              </p>
              {data.profitability.status === "available" ? (
                <p>
                  Margin (collected minus technical costs): <strong>{moneyMap(data.profitability.margin)}</strong>
                  {data.profitability.basis === "estimated" ? " (estimated: some costs are flat tariffs)" : ""}
                </p>
              ) : (
                <p className="dv-note">
                  {data.profitability.reason} Known so far: revenue {moneyMap(data.profitability.revenue)}, costs{" "}
                  {moneyMap(data.profitability.costs_known)}.
                </p>
              )}
            </Glass>
          </>
        )}
      </section>

      <section className="dv-section" aria-labelledby="dv-rel">
        <h2 id="dv-rel" className="t-h2">Reliability</h2>
        <div className="dv-kpis">
          <Glass level={1} className="dv-kpi">
            <p className="dv-kpi__label">Open incidents</p>
            <p className="dv-kpi__value">{indicatorText(data.reliability.open)}</p>
          </Glass>
          <Glass level={1} className="dv-kpi">
            <p className="dv-kpi__label">Synchronisation failures</p>
            <p className="dv-kpi__value">{indicatorText(data.reliability.sync_failures)}</p>
          </Glass>
          <Glass level={1} className="dv-kpi">
            <p className="dv-kpi__label">Prospects with a failed action</p>
            <p className="dv-kpi__value">{indicatorText(data.reliability.failed_actions)}</p>
          </Glass>
          <Glass level={1} className="dv-kpi">
            <p className="dv-kpi__label">Clients degraded</p>
            <p className="dv-kpi__value">{indicatorText(data.reliability.health.degraded_clients)}</p>
          </Glass>
          <Glass level={1} className="dv-kpi">
            <p className="dv-kpi__label">Integrations ready / not ready</p>
            <p className="dv-kpi__value">
              {indicatorText(data.integrations.ready)} / {indicatorText(data.integrations.not_ready)}
            </p>
          </Glass>
        </div>
        <Glass level={1} className="dv-card">
          <TimeChart
            title="Incidents raised"
            description="Warnings and critical notifications by creation date."
            labels={incidents.map((p) => p.period)}
            series={[{ id: "incidents", label: "Incidents", color: "var(--dv-e)", values: incidents.map((p) => p.incidents) }]}
            format={count}
            kind="bars"
            unavailable={na(s.reliability.availability, s.reliability.reason)}
          />
        </Glass>
        <Glass level={1} className="dv-card dv-scroll">
          <p className="dv-chart__title">Usage against plan quotas</p>
          {data.usage.availability === "not_tracked" ? (
            <p className="dv-note dv-note--na" role="status">Not tracked: {data.usage.reason}</p>
          ) : (
            <table className="dv-data">
              <caption className="dv-sr">Quota usage by client</caption>
              <thead>
                <tr>
                  <th scope="col">Client</th>
                  <th scope="col">Plan</th>
                  <th scope="col">Today</th>
                  <th scope="col">This month</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {data.usage.clients.map((c) => (
                  <tr key={c.client_id}>
                    <th scope="row">{c.display_name}</th>
                    <td>{c.plan_name ?? "—"}</td>
                    <td>
                      {c.daily_used ?? "—"} / {c.daily_limit ?? "—"}
                      {c.daily_percent !== null ? ` (${c.daily_percent}%)` : ""}
                    </td>
                    <td>{c.month_used ?? "—"}</td>
                    <td>{c.state}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Glass>
      </section>
    </>
  );
}
