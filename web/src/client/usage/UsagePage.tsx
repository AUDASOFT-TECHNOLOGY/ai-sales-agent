/** "Usage": what the plan allows and what has been consumed. The credits of the month come first (what
 *  is left, the pace, when they would run out), then the commercial actions of today and of the month,
 *  then a day-by-day chart with the detail in a fold. Read-only; every figure comes from
 *  `GET /client/v1/usage`. */

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { useI18n } from "../../i18n";
import { Glass } from "../../ui/Glass";
import { Button, LinkButton } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { clientUsage, type ClientUsage, type UsageDay } from "./usageApi";
import { chartMax, forecast, untilReset } from "./usageModel";

type Level = "ok" | "warning" | "reached";
const levelOf = (percent: number | null): Level => (percent == null ? "ok" : percent >= 100 ? "reached" : percent >= 80 ? "warning" : "ok");

function Bar({ percent, level, label }: { percent: number | null; level: Level; label: string }) {
  return (
    <div className="rv-progress rv-usage__bar-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? 0} data-level={level}>
      <div className="rv-progress__fill" style={{ width: `${Math.min(100, percent ?? 0)}%` }} />
    </div>
  );
}

function Credits({ usage }: { usage: ClientUsage }) {
  const { t, n } = useI18n();
  const { credits_used: used, included_credits: included, credits_percent: percent } = usage;
  const level = levelOf(percent);
  const pace = forecast(used, included, usage.day_utc);
  return (
    <Glass level={2} className="rv-card rv-usage__hero" data-testid="usage-credits" data-level={level}>
      <h2 className="t-label c-3">{t("rv.usage.credits")}</h2>
      {used == null || included == null ? (
        <p className="c-3">{t("rv.usage.unavailable")}</p>
      ) : (
        <>
          <p className="rv-usage__big tabular">
            <strong>{n(usage.credits_remaining ?? 0)}</strong>
            <span className="c-2"> {t("rv.usage.credits.left")}</span>
          </p>
          <Bar percent={percent} level={level} label={t("rv.usage.credits")} />
          <p className="c-2 t-body-sm tabular">{t("rv.usage.credits.of", { used: n(used), total: n(included), percent: n(percent ?? 0) })}</p>
          {pace ? (
            <p className="rv-usage__pace" data-testid="usage-pace" data-run-out={pace.runOutDay != null}>
              {pace.runOutDay != null ? t("rv.usage.pace.runOut", { day: pace.runOutDay }) : t("rv.usage.pace.ok", { n: n(pace.projected) })}
            </p>
          ) : null}
          <p className="c-3 t-body-sm">{t("rv.usage.credits.today", { n: n(usage.credits_today ?? 0) })}</p>
        </>
      )}
    </Glass>
  );
}

function Quota({ title, used, limit, percent, hint, testId }: { title: string; used: number | null; limit: number | null; percent: number | null; hint: string; testId: string }) {
  const { t, n } = useI18n();
  const level = levelOf(percent);
  return (
    <Glass level={1} className="rv-card rv-usage__quota" data-testid={testId} data-level={level}>
      <h2 className="t-label c-3">{title}</h2>
      {used == null ? (
        <p className="c-3">{t("rv.usage.unavailable")}</p>
      ) : (
        <>
          <p className="rv-usage__figure tabular">
            <strong>{n(used)}</strong>
            {limit != null ? <span className="c-2"> / {n(limit)}</span> : null}
          </p>
          {limit != null ? <Bar percent={percent} level={level} label={title} /> : <p className="c-3 t-body-sm">{t("rv.usage.noLimit")}</p>}
        </>
      )}
      <p className="c-3 t-body-sm">{hint}</p>
    </Glass>
  );
}

function DayChart({ days, metric, line, today }: { days: UsageDay[]; metric: "credits" | "actions"; line: number | null; today: string }) {
  const { t, n, d } = useI18n();
  const top = chartMax(days, metric, line);
  const dayName = (day: string) => d(`${day}T12:00:00Z`, { day: "numeric", month: "short", timeZone: "UTC" });
  return (
    <div className="rv-usage__chart" role="img" aria-label={t("rv.usage.chart.label")} data-testid="usage-chart">
      {metric === "actions" && line != null ? (
        <span className="rv-usage__line" style={{ bottom: `${(line / top) * 100}%` }} data-testid="usage-line">
          <em>{t("rv.usage.chart.quota", { n: n(line) })}</em>
        </span>
      ) : null}
      {days.map((row) => {
        const value = row[metric];
        return (
          <span key={row.day} className="rv-usage__col" data-today={row.day === today} title={`${dayName(row.day)} · ${value == null ? "—" : n(value)}`}>
            <i style={{ height: `${((value ?? 0) / top) * 100}%` }} />
          </span>
        );
      })}
    </div>
  );
}

export function UsagePage() {
  const { t, n, d } = useI18n();
  const [usage, setUsage] = useState<ClientUsage | null>(null);
  const [failed, setFailed] = useState(false);
  const [metric, setMetric] = useState<"credits" | "actions">("credits");
  const load = useCallback(() => {
    setFailed(false);
    clientUsage().then(setUsage, () => setFailed(true));
  }, []);
  useEffect(load, [load]);

  const alert = usage && usage.state !== "ok" ? usage : null;
  const reset = untilReset(new Date());
  return (
    <div className="rv-page rv-usage">
      <header className="rv-head">
        <h1 className="t-h1">{t("rv.usage.title")}</h1>
        <p className="c-2">{t("rv.usage.intro")}</p>
        <div className="rv-usage__top">
          {usage?.plan_name ? <span className="rv-usage__plan">{t("rv.usage.plan", { name: usage.plan_name })}</span> : null}
          <Button variant="ghost" size="sm" onClick={load} leading={<RefreshCw size={14} aria-hidden />}>
            {t("rv.usage.refresh")}
          </Button>
        </div>
      </header>

      {failed ? (
        <p role="alert" className="c-2" data-testid="usage-failed">{t("rv.usage.failed")}</p>
      ) : !usage ? (
        <p className="c-3" role="status">{t("rv.usage.loading")}</p>
      ) : (
        <>
          {alert ? (
            <Glass level={2} className="rv-card rv-usage__notice" data-testid="usage-notice" data-state={alert.state} role="status">
              <TriangleAlert size={20} aria-hidden />
              <div>
                <p><strong>{alert.message}</strong></p>
                {alert.action ? <p className="c-2">{alert.action}</p> : null}
              </div>
              <LinkButton to="/account" variant="secondary" size="sm">{t("rv.usage.changePlan")}</LinkButton>
            </Glass>
          ) : null}

          <Credits usage={usage} />

          <div className="rv-usage__quotas">
            <Quota testId="usage-daily" title={t("rv.usage.daily")} used={usage.daily_used} limit={usage.daily_limit} percent={usage.daily_percent} hint={t("rv.usage.daily.hint", { h: reset.hours, m: reset.minutes })} />
            <Quota testId="usage-month" title={t("rv.usage.month")} used={usage.month_actions_used} limit={usage.month_limit} percent={usage.month_percent} hint={t("rv.usage.month.hint")} />
          </div>

          <Glass level={1} className="rv-card rv-usage__days">
            <div className="rv-card__head">
              <h2 className="t-h3">{t("rv.usage.days")}</h2>
              <Segmented
                size="sm"
                layoutId="usage-metric"
                label={t("rv.usage.days")}
                value={metric}
                onChange={setMetric}
                options={[
                  { value: "credits", label: t("rv.usage.col.credits") },
                  { value: "actions", label: t("rv.usage.col.actions") },
                ]}
              />
            </div>
            <DayChart days={usage.days} metric={metric} line={usage.daily_limit} today={usage.day_utc} />
            <details className="rv-usage__detail">
              <summary>{t("rv.usage.detail")}</summary>
              <table className="rv-usage__table" data-testid="usage-days">
                <thead>
                  <tr>
                    <th scope="col">{t("rv.usage.col.day")}</th>
                    <th scope="col" className="num">{t("rv.usage.col.actions")}</th>
                    <th scope="col" className="num">{t("rv.usage.col.credits")}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...usage.days].reverse().map((row) => (
                    <tr key={row.day}>
                      <th scope="row">{d(`${row.day}T12:00:00Z`, { dateStyle: "medium", timeZone: "UTC" })}</th>
                      <td className="num tabular">{n(row.actions)}</td>
                      <td className="num tabular">{row.credits == null ? "—" : n(row.credits)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </Glass>
        </>
      )}
    </div>
  );
}
