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
      <Group title={t("configure.days")} hint={`Times are in ${TENANT.timezone}.`} icon={CalendarClock}>
        <ToggleChips
          label={t("configure.days")}
          value={state.schedule.days}
          onChange={(v) => dispatch({ type: "setScheduleDays", value: v })}
          options={[
            { value: "mon", label: "Mon" }, { value: "tue", label: "Tue" }, { value: "wed", label: "Wed" }, { value: "thu", label: "Thu" },
            { value: "fri", label: "Fri" }, { value: "sat", label: "Sat" }, { value: "sun", label: "Sun" },
          ]}
        />
        <div className="grid-2">
          <TextInput label="Start" type="time" value={state.schedule.start} onChange={(e) => dispatch({ type: "setScheduleHours", start: e.target.value, end: state.schedule.end })} disabled={viewer} />
          <TextInput label="End" type="time" value={state.schedule.end} onChange={(e) => dispatch({ type: "setScheduleHours", start: state.schedule.start, end: e.target.value })} disabled={viewer} />
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
        {viewer ? <p className="c-attention">Only owners can change limits.</p> : null}
      </Group>
      <hr className="divider" />
      <Group title={t("configure.optout")} hint="When a prospect writes one of these, REVANSA stops for good. That rule is built in; only the words are yours." icon={Ban}>
        <div className="row row--wrap">
          {state.optOutWords.map((p) => (
            <span key={p} className="phrase">
              {p}
              <button type="button" aria-label={`Remove ${p}`} onClick={() => dispatch({ type: "setOptOutWords", value: state.optOutWords.filter((x) => x !== p) })} disabled={viewer}>
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
            <input className="input well" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add a word" aria-label="Add an opt-out word" disabled={viewer} />
          </form>
        </div>
      </Group>
      <hr className="divider" />
      <Group title="WhatsApp Business number" hint="The number REVANSA writes to prospects from." icon={MessageCircle}>
        <TextInput
          label="Number"
          type="tel"
          value={state.agentPhone}
          onChange={(e) => dispatch({ type: "setAgentPhone", value: e.target.value })}
          disabled={viewer}
        />
      </Group>
      <hr className="divider" />
      <Group title="Your daily report" hint="A WhatsApp summary sent to you, every working day." icon={Send}>
        <div className="row switch-row">
          <Switch label="Daily report" checked={state.ceoReport.enabled} onChange={(v) => dispatch({ type: "setCeoReport", patch: { enabled: v } })} disabled={viewer} />
          <span>Send me a daily summary on WhatsApp</span>
        </div>
        {state.ceoReport.enabled ? (
          <div className="grid-2">
            <TextInput label="Time" type="time" value={state.ceoReport.time} onChange={(e) => dispatch({ type: "setCeoReport", patch: { time: e.target.value } })} disabled={viewer} />
            <TextInput label="Your WhatsApp number" type="tel" value={state.ceoReport.phone} onChange={(e) => dispatch({ type: "setCeoReport", patch: { phone: e.target.value } })} disabled={viewer} helper="Different from the number REVANSA writes from." />
          </div>
        ) : null}
      </Group>
    </>
  );
}
