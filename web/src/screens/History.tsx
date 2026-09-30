import { useState } from "react";
import { AlertTriangle, Sparkles } from "lucide-react";
import { useI18n } from "../i18n";
import { CEO_REPORT_HIGHLIGHT, KPIS, REPORT_HISTORY_PAST, THREAD } from "../mock/data";
import { useStore } from "../mock/store";
import { Glass } from "../ui/Glass";
import { Segmented } from "../ui/Controls";

type Range = "7d" | "30d" | "all";

/** Report history: every daily WhatsApp report REVANSA has sent this CEO, kept so a past day can be
 *  reopened rather than lost once the next one arrives. Today's own entry reads the same live state
 *  as Results.tsx did before this section moved here; the days before it are hand-authored sample
 *  data (mock/data.ts REPORT_HISTORY_PAST), like every other figure in this prototype. */
export function History() {
  const { t } = useI18n();
  const { state } = useStore();
  const [range, setRange] = useState<Range>("30d");
  const pastCount = range === "7d" ? 4 : range === "30d" ? REPORT_HISTORY_PAST.length : REPORT_HISTORY_PAST.length;
  const past = REPORT_HISTORY_PAST.slice(0, pastCount);

  return (
    <div className="page page--focus history-page">
      <div className="page-head">
        <div>
          <h2 className="t-h1">{t("history.title")}</h2>
          <p className="c-2">{t("history.subtitle", { brand: t("brand.name") })}</p>
        </div>
        <Segmented
          layoutId="seg-history-range"
          label={t("common.range")}
          value={range}
          onChange={setRange}
          options={[
            { value: "7d", label: t("dashboard.period.7d") },
            { value: "30d", label: t("dashboard.period.30d") },
            { value: "all", label: t("history.range.all") },
          ]}
        />
      </div>

      <div className="report-timeline">
        <div className="report-timeline__row">
          <div className="report-timeline__rail" aria-hidden>
            <span className="report-timeline__dot report-timeline__dot--today" />
            <span className="report-timeline__line" />
          </div>
          <Glass level={2} className="report-panel report-timeline__today">
            <div className="report-timeline__head">
              <h3 className="t-h3" id="report-h">
                {t("history.todayAt", { time: state.ceoReport.time })}
              </h3>
              <span className="chip" data-tint="agent">
                {t("history.sentOnWhatsApp")}
              </span>
            </div>
            {!state.ceoReport.enabled ? (
              <p className="c-attention">
                <AlertTriangle size={16} aria-hidden /> {t("history.reportOff")}
              </p>
            ) : null}
            <dl className="report-grid">
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.contacted")}</dt>
                <dd className="t-numeral-m tabular">{KPIS.today.find((k) => k.id === "treated")?.value ?? 0}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.replies")}</dt>
                <dd className="t-numeral-m tabular">{KPIS.today.find((k) => k.id === "replies")?.value ?? 0}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.conversationsOpen")}</dt>
                <dd className="t-numeral-m tabular">{state.conversations.length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.paymentsToConfirm")}</dt>
                <dd className="t-numeral-m tabular">{state.needs.filter((x) => x.kind === "payment").length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.interventions")}</dt>
                <dd className="t-numeral-m tabular">{state.needs.length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">{t("history.kpi.signups")}</dt>
                <dd className="c-3 t-body-sm">{t("kpi.notTracked")}</dd>
              </div>
            </dl>
            <p className="report-highlight">
              <Sparkles size={18} aria-hidden />
              <span>
                <strong>{t("history.worthKnowing")}</strong>
                {CEO_REPORT_HIGHLIGHT}
              </span>
            </p>
            <p className="c-2 t-body-sm">
              <strong>{t("history.tomorrow")}</strong>
              {t("history.tomorrowPlan", { brand: t("brand.name"), n: THREAD.filter((e) => e.future).length })}
            </p>
            <p className="c-3 t-body-sm">{t("history.sentTo", { phone: state.ceoReport.phone })}</p>
          </Glass>
        </div>

        {past.map((day, i) => (
          <div className="report-timeline__row" key={day.date}>
            <div className="report-timeline__rail" aria-hidden>
              <span className="report-timeline__dot" />
              {i < past.length - 1 ? <span className="report-timeline__line" /> : null}
            </div>
            <div className="report-timeline__past">
              <p className="report-timeline__date">
                {day.date} — {day.time}
              </p>
              <p className="c-2 t-body-sm">
                {t("history.past.contactedReplies", { contacted: day.contacted, replies: day.replies })}
                {day.paymentsToConfirm === 1 ? t("history.past.payments.one") : t("history.past.payments.many", { n: day.paymentsToConfirm })}
                {day.interventions === 0
                  ? t("history.past.noIntervention")
                  : day.interventions === 1
                    ? t("history.past.intervention.one")
                    : t("history.past.intervention.many", { n: day.interventions })}
              </p>
              <p className="c-3 t-body-sm">{day.highlight}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
