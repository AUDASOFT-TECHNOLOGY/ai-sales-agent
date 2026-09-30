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
          <h2 className="t-h1">Report history</h2>
          <p className="c-2">Every daily report REVANSA has sent you on WhatsApp, kept for you to look back on.</p>
        </div>
        <Segmented
          layoutId="seg-history-range"
          label="Range"
          value={range}
          onChange={setRange}
          options={[
            { value: "7d", label: "7 days" },
            { value: "30d", label: "30 days" },
            { value: "all", label: "All history" },
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
                Today, {state.ceoReport.time}
              </h3>
              <span className="chip" data-tint="agent">
                Sent on WhatsApp
              </span>
            </div>
            {!state.ceoReport.enabled ? (
              <p className="c-attention">
                <AlertTriangle size={16} aria-hidden /> The daily report is off. Turn it on in Configure, WhatsApp.
              </p>
            ) : null}
            <dl className="report-grid">
              <div>
                <dt className="c-3 t-body-sm">Prospects contacted</dt>
                <dd className="t-numeral-m tabular">{KPIS.today.find((k) => k.id === "treated")?.value ?? 0}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">Replies received</dt>
                <dd className="t-numeral-m tabular">{KPIS.today.find((k) => k.id === "replies")?.value ?? 0}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">Conversations open</dt>
                <dd className="t-numeral-m tabular">{state.conversations.length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">Payments to confirm</dt>
                <dd className="t-numeral-m tabular">{state.needs.filter((x) => x.kind === "payment").length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">Human interventions</dt>
                <dd className="t-numeral-m tabular">{state.needs.length}</dd>
              </div>
              <div>
                <dt className="c-3 t-body-sm">Sign-up requests</dt>
                <dd className="c-3 t-body-sm">{t("kpi.notTracked")}</dd>
              </div>
            </dl>
            <p className="report-highlight">
              <Sparkles size={18} aria-hidden />
              <span>
                <strong>Worth knowing: </strong>
                {CEO_REPORT_HIGHLIGHT}
              </span>
            </p>
            <p className="c-2 t-body-sm">
              <strong>Tomorrow: </strong>
              REVANSA plans to start with {THREAD.filter((e) => e.future).length} scheduled actions, including a new first message.
            </p>
            <p className="c-3 t-body-sm">Sent on WhatsApp to {state.ceoReport.phone}.</p>
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
                {day.contacted} prospects contacted · {day.replies} replies · {day.paymentsToConfirm} payment{day.paymentsToConfirm === 1 ? "" : "s"} to confirm
                {day.interventions === 0 ? " · nothing needed you" : ` · ${day.interventions} intervention${day.interventions === 1 ? "" : "s"}`}.
              </p>
              <p className="c-3 t-body-sm">{day.highlight}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
