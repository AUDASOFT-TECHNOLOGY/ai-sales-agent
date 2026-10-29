/** Business & messages: who the client is, how its agent speaks and when it works. Written to
 *  `PUT /client/v1/settings`; only the parts changed here are sent. */

import { useMemo, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { TextInput, Textarea, ToggleChips } from "../../ui/Controls";
import { Card, FailureNote, Note, type Failure } from "../kit";
import type { ClientSettings } from "../api";
import { WEEKDAYS, defaultLanguage, localizedText, setBusiness, setLanguages, setToneStyle, splitList, type Doc } from "../settingsModel";
import { useOffer } from "./OfferLayout";

export const LANGUAGES = [
  { value: "fr", label: "Français" },
  { value: "en", label: "English" },
  { value: "ar", label: "العربية" },
];
export const FORMALITY = ["formal", "neutral", "informal"] as const;
export const HTTPS = /^https:\/\/[^\s/$.?#][^\s]*$/;
export const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

export function timeZones(current: string): string[] {
  const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
  const all = supported ? supported("timeZone") : ["Africa/Tunis", "Europe/Paris", "UTC"];
  return current && !all.includes(current) ? [current, ...all] : all;
}

export interface Draft {
  name: string;
  website: string;
  summary: string;
  supported: string[];
  defaultLang: string;
  tone: string;
  formality: string;
  timezone: string;
  dailyAt: string;
  days: string[];
  stopWords: string;
}

export function draftOf(commercial: Doc, settings: { timezone?: string; opt_out_keywords?: string[]; schedule?: { daily_at?: string; days?: string[] } }): Draft {
  const lang = defaultLanguage(commercial);
  const business = obj(commercial.business);
  const languages = obj(obj(commercial.communication).languages);
  const tone = obj(obj(commercial.communication).tone);
  return {
    name: typeof business.name === "string" ? business.name : "",
    website: typeof business.website === "string" ? business.website : "",
    summary: localizedText(business.summary, lang),
    supported: Array.isArray(languages.supported) ? (languages.supported as string[]) : [],
    defaultLang: lang,
    tone: typeof tone.style === "string" ? tone.style : "",
    formality: typeof obj(tone.formality).default === "string" ? String(obj(tone.formality).default) : "neutral",
    timezone: settings.timezone ?? "",
    dailyAt: settings.schedule?.daily_at ?? "",
    days: settings.schedule?.days ?? [],
    stopWords: (settings.opt_out_keywords ?? []).join(", "),
  };
}

/** What to write for the business answers, in groups so a questionnaire can save one answer at a time. */
export type BusinessGroup = "company" | "languages" | "tone" | "schedule";

export function businessChanges(commercial: Doc, draft: Draft, groups: BusinessGroup[] = ["company", "languages", "tone", "schedule"]): ClientSettings {
  let next = commercial;
  if (groups.includes("company")) next = setBusiness(next, { name: draft.name.trim(), website: draft.website.trim(), summary: draft.summary.trim() });
  if (groups.includes("languages")) {
    const lang = draft.supported.includes(draft.defaultLang) ? draft.defaultLang : (draft.supported[0] ?? "");
    next = setLanguages(next, { supported: draft.supported, default: lang });
  }
  if (groups.includes("tone")) {
    next = setToneStyle(next, draft.tone);
    const communication = obj(next.communication);
    const toneDoc = obj(communication.tone);
    next = { ...next, communication: { ...communication, tone: { ...toneDoc, formality: { ...obj(toneDoc.formality), default: draft.formality } } } };
  }
  const changes: ClientSettings = { commercial: next };
  if (groups.includes("schedule")) {
    changes.timezone = draft.timezone.trim() || undefined;
    changes.opt_out_keywords = splitList(draft.stopWords);
    changes.schedule = { ...(draft.dailyAt.trim() ? { daily_at: draft.dailyAt.trim() } : {}), days: draft.days };
  }
  return changes;
}

export function BusinessPage() {
  const { t } = useI18n();
  const { state, commercial, save } = useOffer();
  const saved = useMemo(() => draftOf(commercial, state.settings), [commercial, state.settings]);
  const [draft, setDraft] = useState<Draft>(saved);
  const [base, setBase] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [done, setDone] = useState(false);
  const [problems, setProblems] = useState<MessageKey[]>([]);

  // The server's answer replaces the form (also after a conflict reload), unless the client is typing.
  if (base !== saved && JSON.stringify(base) !== JSON.stringify(saved)) {
    setBase(saved);
    setDraft(saved);
  }
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const set = (patch: Partial<Draft>) => {
    setDone(false);
    setDraft({ ...draft, ...patch });
  };
  const zones = useMemo(() => timeZones(draft.timezone), [draft.timezone]);

  function languageChoices(): Array<{ value: string; label: string }> {
    const extra = draft.supported.filter((l) => !LANGUAGES.some((x) => x.value === l)).map((l) => ({ value: l, label: l }));
    return [...LANGUAGES, ...extra];
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found: MessageKey[] = [];
    if (draft.name.trim() === "") found.push("rv.biz.err.name");
    if (draft.website.trim() !== "" && !HTTPS.test(draft.website.trim())) found.push("rv.biz.err.website");
    if (draft.supported.length === 0) found.push("rv.biz.err.language");
    if (draft.dailyAt.trim() !== "" && !TIME.test(draft.dailyAt.trim())) found.push("rv.biz.err.time");
    setProblems(found);
    if (found.length > 0) return;

    setBusy(true);
    setFailure(null);
    setDone(false);
    const problem = await save(businessChanges(commercial, draft));
    setBusy(false);
    if (problem) setFailure(problem);
    else setDone(true);
  }

  const fieldError = (key: MessageKey) => (problems.includes(key) ? t(key) : undefined);

  return (
    <form className="rv-page" onSubmit={(e) => void onSubmit(e)} aria-label={t("rv.offer.tab.business")} noValidate>
      {failure ? <FailureNote failure={failure} /> : null}
      {done ? <Note tone="success">{t("rv.common.saved")}</Note> : null}

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.biz.company")}</h3>
        </div>
        <TextInput label={t("rv.biz.name")} value={draft.name} onChange={(e) => set({ name: e.target.value })} error={fieldError("rv.biz.err.name")} maxLength={300} />
        <TextInput
          label={t("rv.biz.website")}
          helper={t("rv.biz.websiteHelp")}
          optional={t("rv.common.optional")}
          value={draft.website}
          onChange={(e) => set({ website: e.target.value })}
          error={fieldError("rv.biz.err.website")}
          inputMode="url"
          dir="ltr"
          placeholder="https://"
        />
        <Textarea label={t("rv.biz.summary")} helper={t("rv.biz.summaryHelp")} value={draft.summary} onChange={(e) => set({ summary: e.target.value })} rows={4} maxLength={3000} />
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.biz.talk")}</h3>
        </div>
        <div className="stack">
          <span className="t-label c-2">{t("rv.biz.languages")}</span>
          <ToggleChips label={t("rv.biz.languages")} options={languageChoices()} value={draft.supported} onChange={(supported) => set({ supported })} />
          <span className="c-3 t-body-sm">{t("rv.biz.languagesHelp")}</span>
          {fieldError("rv.biz.err.language") ? <span className="c-danger t-body-sm">{fieldError("rv.biz.err.language")}</span> : null}
        </div>
        <div className="rv-grid-2">
          <label className="rv-select-label">
            <span className="t-label c-2">{t("rv.biz.defaultLanguage")}</span>
            <select className="rv-select" value={draft.supported.includes(draft.defaultLang) ? draft.defaultLang : (draft.supported[0] ?? "")} onChange={(e) => set({ defaultLang: e.target.value })} disabled={draft.supported.length === 0}>
              {languageChoices()
                .filter((l) => draft.supported.includes(l.value))
                .map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
            </select>
          </label>
          <label className="rv-select-label">
            <span className="t-label c-2">{t("rv.biz.formality")}</span>
            <select className="rv-select" value={draft.formality} onChange={(e) => set({ formality: e.target.value })}>
              {FORMALITY.map((f) => (
                <option key={f} value={f}>
                  {t(`rv.biz.formality.${f}` as MessageKey)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <TextInput label={t("rv.biz.tone")} helper={t("rv.biz.toneHelp")} optional={t("rv.common.optional")} value={draft.tone} onChange={(e) => set({ tone: e.target.value })} maxLength={300} />
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.biz.when")}</h3>
        </div>
        <div className="rv-grid-2">
          <label className="rv-select-label">
            <span className="t-label c-2">{t("rv.biz.timezone")}</span>
            <select className="rv-select" value={draft.timezone} onChange={(e) => set({ timezone: e.target.value })} dir="ltr">
              <option value="">{t("rv.common.notSet")}</option>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
            <span className="c-3 t-body-sm">{t("rv.biz.timezoneHelp")}</span>
          </label>
          <TextInput label={t("rv.biz.dailyAt")} helper={t("rv.biz.dailyAtHelp")} value={draft.dailyAt} onChange={(e) => set({ dailyAt: e.target.value })} error={fieldError("rv.biz.err.time")} placeholder="09:00" dir="ltr" inputMode="numeric" />
        </div>
        <div className="stack">
          <span className="t-label c-2">{t("rv.biz.days")}</span>
          <ToggleChips label={t("rv.biz.days")} options={WEEKDAYS.map((day) => ({ value: day, label: t(`rv.day.${day}` as MessageKey) }))} value={draft.days} onChange={(days) => set({ days })} />
        </div>
        <TextInput label={t("rv.biz.stopWords")} helper={t("rv.biz.stopWordsHelp")} optional={t("rv.common.optional")} value={draft.stopWords} onChange={(e) => set({ stopWords: e.target.value })} />
      </Card>

      <div className="rv-sticky-save">
        <span className="c-2 t-body-sm">{dirty ? t("rv.common.unsaved") : t("rv.common.upToDate")}</span>
        <div className="rv-actions">
          {dirty ? (
            <Button type="button" variant="ghost" onClick={() => setDraft(saved)} disabled={busy}>
              {t("rv.common.discard")}
            </Button>
          ) : null}
          <Button type="submit" variant="primary" loading={busy} disabled={!dirty}>
            {t("rv.common.save")}
          </Button>
        </div>
      </div>
    </form>
  );
}
