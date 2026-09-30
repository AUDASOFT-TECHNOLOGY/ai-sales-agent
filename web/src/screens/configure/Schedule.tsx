import { useState } from "react";
import { Ban, CalendarClock, Gauge, MessageCircle, Send, X } from "lucide-react";
import { useI18n } from "../../i18n";
import { TENANT } from "../../mock/data";
import { useStore } from "../../mock/store";
import { GuardrailSlider } from "../../agent/Autonomy";
import { Switch, TextInput, ToggleChips } from "../../ui/Controls";
import { Group } from "./Group";

/* ── When REVANSA works, and how it connects: days, hours, the daily limit, the WhatsApp
 *  Business number it writes from, and your daily report. ── */
export function ScheduleSection() {
  const { t } = useI18n();
  const { state, dispatch } = useStore();
  const viewer = state.role === "viewer";
  const [draft, setDraft] = useState("");
  return (
    <>
      <Group title={t("configure.days")} hint={t("schedule.timesInZone", { tz: TENANT.timezone })} icon={CalendarClock}>
        <ToggleChips
          label={t("configure.days")}
          value={state.schedule.days}
          onChange={(v) => dispatch({ type: "setScheduleDays", value: v })}
          options={[
            { value: "mon", label: t("schedule.day.mon") }, { value: "tue", label: t("schedule.day.tue") }, { value: "wed", label: t("schedule.day.wed") }, { value: "thu", label: t("schedule.day.thu") },
            { value: "fri", label: t("schedule.day.fri") }, { value: "sat", label: t("schedule.day.sat") }, { value: "sun", label: t("schedule.day.sun") },
          ]}
        />
        <div className="grid-2">
          <TextInput label={t("field.start")} type="time" value={state.schedule.start} onChange={(e) => dispatch({ type: "setScheduleHours", start: e.target.value, end: state.schedule.end })} disabled={viewer} />
          <TextInput label={t("field.end")} type="time" value={state.schedule.end} onChange={(e) => dispatch({ type: "setScheduleHours", start: state.schedule.start, end: e.target.value })} disabled={viewer} />
        </div>
      </Group>
      <hr className="divider" />
      <Group title={t("configure.dailyLimit")} hint={t("configure.dailyLimit.help")} icon={Gauge}>
        <GuardrailSlider
          label={t("configure.dailyLimit")}
          value={state.dailyLimit}
          min={1}
          cap={40}
          scaleMax={60}
          disabled={viewer}
          onChange={(v) => dispatch({ type: "limit", value: v })}
          lockedText={t("configure.platformLimit", { n: 40 })}
        />
        {viewer ? <p className="c-attention">{t("schedule.viewersLimit")}</p> : null}
      </Group>
      <hr className="divider" />
      <Group title={t("configure.optout")} hint={t("schedule.optoutHint", { brand: t("brand.name") })} icon={Ban}>
        <div className="row row--wrap">
          {state.optOutWords.map((p) => (
            <span key={p} className="phrase">
              {p}
              <button type="button" aria-label={t("schedule.removeWord", { word: p })} onClick={() => dispatch({ type: "setOptOutWords", value: state.optOutWords.filter((x) => x !== p) })} disabled={viewer}>
                <X size={16} aria-hidden />
              </button>
            </span>
          ))}
          <form
            className="phrase-add"
            onSubmit={(e) => {
              e.preventDefault();
              const v = draft.trim().toLowerCase();
              if (v && !state.optOutWords.includes(v)) dispatch({ type: "setOptOutWords", value: [...state.optOutWords, v] });
              setDraft("");
            }}
          >
            <input className="input well" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t("schedule.addWordPlaceholder")} aria-label={t("schedule.addOptOutWord")} disabled={viewer} />
          </form>
        </div>
      </Group>
      <hr className="divider" />
      <Group title={t("schedule.whatsappNumber")} hint={t("schedule.whatsappNumberHint", { brand: t("brand.name") })} icon={MessageCircle}>
        <TextInput
          label={t("field.number")}
          type="tel"
          value={state.agentPhone}
          onChange={(e) => dispatch({ type: "setAgentPhone", value: e.target.value })}
          disabled={viewer}
        />
      </Group>
      <hr className="divider" />
      <Group title={t("schedule.dailyReport")} hint={t("schedule.dailyReportHint")} icon={Send}>
        <div className="row switch-row">
          <Switch label={t("schedule.dailyReportLabel")} checked={state.ceoReport.enabled} onChange={(v) => dispatch({ type: "setCeoReport", patch: { enabled: v } })} disabled={viewer} />
          <span>{t("schedule.sendMeDailySummary")}</span>
        </div>
        {state.ceoReport.enabled ? (
          <div className="grid-2">
            <TextInput label={t("field.time")} type="time" value={state.ceoReport.time} onChange={(e) => dispatch({ type: "setCeoReport", patch: { time: e.target.value } })} disabled={viewer} />
            <TextInput
              label={t("field.yourWhatsappNumber")}
              type="tel"
              value={state.ceoReport.phone}
              onChange={(e) => dispatch({ type: "setCeoReport", patch: { phone: e.target.value } })}
              disabled={viewer}
              helper={t("schedule.differentNumberHelper", { brand: t("brand.name") })}
            />
          </div>
        ) : null}
      </Group>
    </>
  );
}
