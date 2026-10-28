/** "Your agent": how it speaks and when it writes, as a questionnaire like the business one. Every
 *  question has a sensible default, so none is required; each answer is saved as it is given through
 *  the same endpoints as the screens it replaces (settings, daily report, follow-up). */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { TextInput, ToggleChips } from "../../ui/Controls";
import { WEEKDAYS } from "../settingsModel";
import { ClientApiError, clientFollowUpSettings, clientReporting, clientSaveReporting, type FollowUpSettings, type ReportingState } from "../api";
import { FollowUpSettingsCard } from "../FollowUpSettingsCard";
import { ListEditor, failureOf } from "../kit";
import { FORMALITY, LANGUAGES, TIME, businessChanges, draftOf, timeZones, type Draft } from "../offer/BusinessPage";
import { useOffer } from "../offer/OfferLayout";
import { ARGUMENT_MAX_LENGTH, REFUSALS, STYLES, argumentsOf, persuasionOf, principlesOf, refusalOf, setArguments, setEmoji, setPersuasion, setPrinciples, setRefusal, styleChosen, styleOf, withStyle, type Persuasion, type Refusal } from "./agentModel";
import { PhraseList } from "./PhraseList";
import { Choices } from "./Choices";
import { QuestionFlow, type FlowQuestion, type Verdict } from "./QuestionFlow";

interface Report {
  channel: string;
  recipient: string;
  sendTime: string;
  timezone: string;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function AgentQuestions() {
  const { t, locale } = useI18n();
  const { state, commercial, save } = useOffer();

  const saved = useMemo(() => {
    const core = draftOf(commercial, state.settings);
    const languages = (commercial.communication as { languages?: { mirror_prospect_language?: boolean } } | undefined)?.languages;
    return { core, mirror: languages?.mirror_prospect_language !== false, principles: principlesOf(commercial), persuasion: persuasionOf(commercial), styleSet: styleChosen(commercial), args: argumentsOf(commercial), stops: state.settings.opt_out_keywords ?? [], refusal: refusalOf(commercial) };
  }, [commercial, state.settings]);
  const [core, setCore] = useState<Draft>(saved.core);
  const [mirror, setMirror] = useState(saved.mirror);
  const [principles, setPrinciplesDraft] = useState(saved.principles);
  const [persuasion, setPersuasionDraft] = useState<Persuasion>(saved.persuasion);
  // No style picked: the agent keeps its usual way, and nothing about the style is written.
  const [style, setStyle] = useState<ReturnType<typeof styleOf>>(saved.styleSet ? styleOf(saved.persuasion) : "");
  const [args, setArgs] = useState(saved.args);
  const [stops, setStops] = useState(saved.stops);
  const [refusal, setRefusalDraft] = useState<Refusal>(saved.refusal);
  const [base, setBase] = useState(saved);
  if (base !== saved && JSON.stringify(base) !== JSON.stringify(saved)) {
    setBase(saved);
    setCore(saved.core);
    setMirror(saved.mirror);
    setPrinciplesDraft(saved.principles);
    setPersuasionDraft(saved.persuasion);
    setStyle(saved.styleSet ? styleOf(saved.persuasion) : "");
    setArgs(saved.args);
    setStops(saved.stops);
    setRefusalDraft(saved.refusal);
  }

  // The daily report and the follow-up have their own endpoints.
  const [reporting, setReporting] = useState<ReportingState | null>(null);
  const [follow, setFollow] = useState<FollowUpSettings | null>(null);
  const [report, setReport] = useState<Report>({ channel: "", recipient: "", sendTime: "18:00", timezone: "" });
  const loadReporting = useCallback(async () => {
    try {
      const s = await clientReporting();
      setReporting(s);
      setReport((r) => ({
        channel: s.settings?.channel ?? r.channel ?? s.channels.find((c) => c.available)?.key ?? "",
        recipient: s.settings?.recipient ?? r.recipient,
        sendTime: s.settings?.send_time ?? r.sendTime,
        timezone: s.settings?.timezone ?? r.timezone,
      }));
    } catch {
      setReporting(null);
    }
  }, []);
  const loadFollow = useCallback(async () => {
    try {
      setFollow(await clientFollowUpSettings());
    } catch {
      setFollow(null);
    }
  }, []);
  useEffect(() => {
    void loadReporting();
    void loadFollow();
  }, [loadReporting, loadFollow]);

  const languageChoices = [...LANGUAGES, ...core.supported.filter((l) => !LANGUAGES.some((x) => x.value === l)).map((l) => ({ value: l, label: l }))];
  const zones = useMemo(() => timeZones(core.timezone), [core.timezone]);
  const setC = (patch: Partial<Draft>) => setCore({ ...core, ...patch });
  const openChannels = (reporting?.channels ?? []).filter((c) => c.available);
  const channel = report.channel || openChannels[0]?.key || "";
  const zone = report.timezone || core.timezone;

  function check(id: string): MessageKey | null {
    if (id === "languages" && core.supported.length === 0) return "rv.biz.err.language";
    if (id === "hours" && core.dailyAt.trim() !== "" && !TIME.test(core.dailyAt.trim())) return "rv.biz.err.time";
    if (id === "reportTime" && (!TIME.test(report.sendTime) || zone.trim() === "")) return "rv.prep.err.reportTime";
    if (id === "reportChannel") {
      const ok = channel === "email" ? EMAIL.test(report.recipient.trim()) : /^\d{4,20}$/.test(report.recipient.trim());
      if (!ok) return channel === "email" ? "rv.prep.err.recipientEmail" : "rv.prep.err.recipientTelegram";
    }
    return null;
  }

  async function saveReport(): Promise<Verdict> {
    try {
      await clientSaveReporting({ enabled: true, channel, recipient: report.recipient.trim(), send_time: report.sendTime, timezone: zone.trim(), language: locale });
      await loadReporting();
      return null;
    } catch (err) {
      return { failure: err instanceof ClientApiError || err instanceof Error ? failureOf(err) : { key: "rv.err.generic" } };
    }
  }

  async function onAnswer(index: number, skip: boolean): Promise<Verdict> {
    const id = questions[index]!.id;
    if (skip) return null;
    const wrong = check(id);
    if (wrong) return { problem: wrong };
    let failed = null;
    if (id === "languages") {
      const changes = businessChanges(commercial, core, ["languages"]);
      const comm = (changes.commercial as { communication: Record<string, unknown> }).communication;
      const langs = { ...(comm.languages as Record<string, unknown>), mirror_prospect_language: mirror };
      failed = await save({ commercial: { ...changes.commercial, communication: { ...comm, languages: langs } } });
    } else if (id === "tone") failed = await save(businessChanges(commercial, core, ["tone"]));
    else if (id === "hours") {
      // The stop words are their own question: only the hours and the zone are written here.
      const { timezone, schedule } = businessChanges(commercial, core, ["schedule"]);
      failed = await save({ timezone, schedule });
    } else if (id === "stop") failed = await save({ opt_out_keywords: stops });
    else if (id === "refusal") failed = await save({ commercial: setRefusal(commercial, refusal, { response: t("rv.prep.a.refusal.response"), examples: [t("rv.prep.a.refusal.ex1"), t("rv.prep.a.refusal.ex2"), t("rv.prep.a.refusal.ex3")] }) });
    else if (id === "reportTime") {
      // A report cannot be saved without its recipient: while none exists yet, the time waits for the channel.
      return reporting?.settings ? await saveReport() : null;
    } else if (id === "reportChannel") return await saveReport();
    else if (id === "followup") await loadFollow();
    else if (id === "rules") failed = await save({ commercial: setPrinciples(commercial, principles) });
    else if (id === "persuade") {
      let next = style === "" ? commercial : setPersuasion(commercial, withStyle(persuasion, style));
      // The emoji choice is written when the client changed it, or together with a style.
      if (style === "" && persuasion.emoji !== saved.persuasion.emoji) next = setEmoji(next, persuasion.emoji);
      failed = await save({ commercial: setArguments(next, args) });
    }
    return failed ? { failure: failed } : null;
  }

  const days = core.days.map((d) => t(`rv.day.${d}` as MessageKey)).join(", ");
  const label = (k: string) => t(`rv.prep.a.${k}.label` as MessageKey);
  const title = (k: string) => t(`rv.prep.a.${k}` as MessageKey);
  const toggle = (on: boolean, key: MessageKey, change: (v: boolean) => void) => (
    <label className="rv-checkbox">
      <input type="checkbox" checked={on} onChange={(e) => change(e.target.checked)} />
      <span className="rv-checkbox__text">
        <strong>{t(key)}</strong>
      </span>
    </label>
  );

  const questions: FlowQuestion[] = [
    {
      id: "languages",
      title: title("languages"),
      label: label("languages"),
      summary: core.supported.map((l) => languageChoices.find((x) => x.value === l)?.label ?? l).join(", "),
      body: (problem) => (
        <>
          <ToggleChips label={label("languages")} options={languageChoices} value={core.supported} onChange={(supported) => setC({ supported })} />
          {problem ? <span className="c-danger t-body-sm">{t(problem)}</span> : null}
          {core.supported.length > 1 ? (
            <label className="rv-select-label">
              <span className="t-label c-2">{t("rv.biz.defaultLanguage")}</span>
              <select className="rv-select" value={core.supported.includes(core.defaultLang) ? core.defaultLang : (core.supported[0] ?? "")} onChange={(e) => setC({ defaultLang: e.target.value })}>
                {languageChoices
                  .filter((l) => core.supported.includes(l.value))
                  .map((l) => (
                    <option key={l.value} value={l.value}>
                      {l.label}
                    </option>
                  ))}
              </select>
            </label>
          ) : null}
          {toggle(mirror, "rv.prep.a.languages.mirror", setMirror)}
        </>
      ),
    },
    {
      id: "tone",
      title: title("tone"),
      label: label("tone"),
      optional: true,
      summary: [t(`rv.biz.formality.${core.formality}` as MessageKey), core.tone].filter((x) => x.trim() !== "").join(" · "),
      body: () => (
        <>
          <Choices label={label("tone")} value={core.formality} onChange={(formality) => setC({ formality })} options={FORMALITY.map((f) => ({ value: f, label: t(`rv.biz.formality.${f}` as MessageKey) }))} />
          <TextInput label={t("rv.biz.tone")} helper={t("rv.biz.toneHelp")} optional={t("rv.common.optional")} value={core.tone} onChange={(e) => setC({ tone: e.target.value })} maxLength={300} />
        </>
      ),
    },
    {
      id: "hours",
      title: title("hours"),
      label: label("hours"),
      optional: true,
      summary: [days, core.dailyAt, core.timezone].filter((x) => x !== "").join(" · "),
      body: (problem) => (
        <>
          <ToggleChips label={t("rv.biz.days")} options={WEEKDAYS.map((day) => ({ value: day, label: t(`rv.day.${day}` as MessageKey) }))} value={core.days} onChange={(d) => setC({ days: d })} />
          <div className="rv-grid-2">
            <label className="rv-select-label">
              <span className="t-label c-2">{t("rv.biz.timezone")}</span>
              <select className="rv-select" value={core.timezone} onChange={(e) => setC({ timezone: e.target.value })} dir="ltr">
                <option value="">{t("rv.common.notSet")}</option>
                {zones.map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
              </select>
            </label>
            <TextInput label={t("rv.biz.dailyAt")} value={core.dailyAt} onChange={(e) => setC({ dailyAt: e.target.value })} error={problem ? t(problem) : undefined} placeholder="09:00" dir="ltr" inputMode="numeric" />
          </div>
        </>
      ),
    },
    {
      id: "reportTime",
      title: title("reportTime"),
      label: label("reportTime"),
      optional: true,
      summary: reporting?.settings ? `${reporting.settings.send_time} · ${reporting.settings.timezone}` : "",
      body: (problem) => (
        <div className="rv-grid-2">
          <TextInput label={t("reporting.time")} type="time" value={report.sendTime} onChange={(e) => setReport({ ...report, sendTime: e.target.value })} error={problem ? t(problem) : undefined} />
          <label className="rv-select-label">
            <span className="t-label c-2">{t("reporting.timezone")}</span>
            <select className="rv-select" value={zone} onChange={(e) => setReport({ ...report, timezone: e.target.value })} dir="ltr">
              <option value="">{t("rv.common.notSet")}</option>
              {timeZones(zone).map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          </label>
        </div>
      ),
    },
    {
      id: "reportChannel",
      title: title("reportChannel"),
      label: label("reportChannel"),
      optional: true,
      summary: reporting?.settings ? `${t(`reporting.channel.${reporting.settings.channel}` as MessageKey)} · ${reporting.settings.recipient}` : "",
      body: (problem) =>
        openChannels.length === 0 ? (
          <p className="c-2">{t("rv.prep.a.reportChannel.none")}</p>
        ) : (
          <>
            <Choices label={label("reportChannel")} value={channel} onChange={(c) => setReport({ ...report, channel: c })} options={openChannels.map((c) => ({ value: c.key, label: t(`reporting.channel.${c.key}` as MessageKey) }))} />
            <TextInput
              label={t(channel === "telegram" ? "reporting.recipient.telegram" : "reporting.recipient.email")}
              helper={t(channel === "telegram" ? "reporting.recipient.telegramHelper" : "reporting.recipient.emailHelper")}
              value={report.recipient}
              onChange={(e) => setReport({ ...report, recipient: e.target.value })}
              error={problem ? t(problem) : undefined}
              dir="ltr"
            />
          </>
        ),
    },
    {
      id: "followup",
      title: title("followup"),
      label: label("followup"),
      optional: true,
      summary: follow?.configured ? t("rv.prep.a.followup.summary", { max: follow.max_followups, gap: follow.gap_days }) : "",
      body: () => <FollowUpSettingsCard />,
    },
    {
      id: "rules",
      title: title("rules"),
      label: label("rules"),
      optional: true,
      summary: principles.filter((p) => p.trim() !== "").join(" · "),
      body: () => <ListEditor label={label("rules")} helper={t("rv.prep.a.rules.hint")} values={principles} onChange={setPrinciplesDraft} addLabel={t("rv.prep.a.rules.add")} maxItems={5} />,
    },
    {
      id: "stop",
      title: title("stop"),
      label: label("stop"),
      optional: true,
      summary: stops.join(" · "),
      body: () => (
        <>
          <PhraseList
            label={t("rv.prep.a.stop.field")}
            helper={t("rv.prep.a.stop.help")}
            placeholder={t("rv.prep.a.stop.placeholder")}
            addLabel={t("rv.prep.a.stop.add")}
            values={stops}
            suggestions={[t("rv.prep.a.stop.s1"), t("rv.prep.a.stop.s2"), t("rv.prep.a.stop.s3"), "stop"]}
            onChange={setStops}
          />
          <p className="c-3 t-body-sm">{t("rv.prep.a.stop.note")}</p>
        </>
      ),
    },
    {
      id: "refusal",
      title: title("refusal"),
      label: label("refusal"),
      optional: true,
      summary: t(`rv.prep.a.refusal.${refusal}` as MessageKey),
      body: () => (
        <>
          <p className="c-3 t-body-sm">{t("rv.prep.a.refusal.hint")}</p>
          <Choices
            label={label("refusal")}
            value={refusal}
            onChange={(r) => setRefusalDraft(r as Refusal)}
            options={REFUSALS.map((r) => ({ value: r, label: t(`rv.prep.a.refusal.${r}` as MessageKey), hint: t(`rv.prep.a.refusal.${r}.hint` as MessageKey) }))}
          />
        </>
      ),
    },
    {
      id: "persuade",
      title: title("persuade"),
      label: label("persuade"),
      optional: true,
      summary: [
        style === "" ? (saved.styleSet && styleOf(saved.persuasion) === "" ? t("rv.prep.a.style.custom") : t("rv.prep.a.style.none")) : t(`rv.prep.a.style.${style}` as MessageKey),
        args.filter((x) => x.trim() !== "").length > 0 ? t("rv.prep.a.persuade.args.count", { n: args.filter((x) => x.trim() !== "").length }) : "",
      ]
        .filter((x) => x !== "")
        .join(" · "),
      body: () => (
        <>
          <p className="c-3 t-body-sm">{t("rv.prep.a.persuade.hint")}</p>
          <Choices
            clearable
            label={label("persuade")}
            value={style}
            onChange={(v) => {
              setStyle(v as typeof style);
              if (v !== "") setPersuasionDraft(withStyle(persuasion, v as (typeof STYLES)[number]));
            }}
            options={STYLES.map((x) => ({ value: x, label: t(`rv.prep.a.style.${x}` as MessageKey), hint: t(`rv.prep.a.style.${x}.hint` as MessageKey), example: t(`rv.prep.a.style.${x}.example` as MessageKey) }))}
          />
          <p className="c-3 t-body-sm">{style === "" ? (saved.styleSet && styleOf(saved.persuasion) === "" ? t("rv.prep.a.style.customNote") : t("rv.prep.a.style.noneNote")) : t("rv.prep.a.style.clearNote")}</p>
          <ListEditor label={t("rv.prep.a.persuade.args")} helper={t("rv.prep.a.persuade.args.hint")} placeholder={t("rv.prep.a.persuade.args.placeholder")} values={args} onChange={setArgs} addLabel={t("rv.prep.a.persuade.args.add")} maxItems={5} maxLength={ARGUMENT_MAX_LENGTH} />
          {toggle(persuasion.emoji, "rv.prep.a.persuade.emoji", (emoji) => setPersuasionDraft({ ...persuasion, emoji }))}
        </>
      ),
    },
  ];

  const reset = () => {
    setCore(saved.core);
    setMirror(saved.mirror);
    setPrinciplesDraft(saved.principles);
    setPersuasionDraft(saved.persuasion);
    setStyle(saved.styleSet ? styleOf(saved.persuasion) : "");
    setArgs(saved.args);
    setStops(saved.stops);
    setRefusalDraft(saved.refusal);
  };
  return <QuestionFlow questions={questions} initial={saved.core.days.length === 0 && saved.core.dailyAt === "" && saved.principles.length === 0 ? 0 : null} onAnswer={onAnswer} onDiscard={reset} recapTitle={t("rv.prep.recap.title")} testId="agent" />;
}
