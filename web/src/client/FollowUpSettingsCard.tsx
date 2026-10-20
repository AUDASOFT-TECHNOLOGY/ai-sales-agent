/** How the agent follows up with people who stay silent: how many follow-ups, how many days apart, and
 *  optionally the hours of the day in which one may go out (`GET/PUT /client/v1/followup-settings`).
 *  The server is the judge: the bounds shown here are the ones it sends, a refusal of the Brain's own
 *  rules is shown in its words, and a settings conflict reloads what is stored. Nothing is kept here. */

import { useCallback, useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "./ClientAuthContext";
import { ClientApiError, clientFollowUpSettings, clientSaveFollowUpSettings, type FollowUpSettings } from "./api";
import { crmFailureOf } from "./crmFailure";
import { useFetched } from "./useFetched";
import { Button } from "../ui/Button";
import { TextInput } from "../ui/Controls";

const HOUR = /^([01]\d|2[0-3]):[0-5]\d$/;

interface Form {
  max: string;
  gap: string;
  windowOn: boolean;
  start: string;
  end: string;
  zone: string;
}

function formOf(s: FollowUpSettings): Form {
  return {
    max: String(s.max_followups),
    gap: String(s.gap_days),
    windowOn: s.window !== null,
    start: s.window?.start ?? "09:00",
    end: s.window?.end ?? "18:00",
    zone: s.window?.timezone ?? s.timezone ?? "",
  };
}

function whole(value: string): number | null {
  return /^\d{1,3}$/.test(value.trim()) ? Number.parseInt(value, 10) : null;
}

export function FollowUpSettingsCard() {
  const [attempt, setAttempt] = useState(0);
  const load = useCallback(() => clientFollowUpSettings().then((s) => ({ s, attempt })), [attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const failure = failed ? crmFailureOf(error) : null;
  useEffect(() => {
    if (failure?.signedOut) void logout();
  }, [failure?.signedOut, logout]);

  if (failure) {
    return (
      <div className="stack">
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(failure.key)}
        </p>
        <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
      </div>
    );
  }
  if (loading || !value) {
    return (
      <p className="c-3" role="status">
        {t("followup.loading")}
      </p>
    );
  }
  // Keyed by version: what the server stored is what the form starts from, after a save or a reload.
  return <Editor key={`${value.s.version ?? "none"}-${value.attempt}`} initial={value.s} reload={() => setAttempt((a) => a + 1)} />;
}

function Editor({ initial, reload }: { initial: FollowUpSettings; reload: () => void }) {
  const { t, n } = useI18n();
  const [form, setForm] = useState<Form>(() => formOf(initial));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [refusal, setRefusal] = useState<{ key: MessageKey; detail: string | null } | null>(null);
  const [stored, setStored] = useState(initial);
  const [bounds, defaults] = [initial.bounds, initial.defaults];

  const set = <K extends keyof Form>(field: K, next: Form[K]) => {
    setForm((f) => ({ ...f, [field]: next }));
    setSaved(false);
  };

  const max = whole(form.max);
  const gap = whole(form.gap);
  const maxBad = max === null || max < bounds.max_followups[0] || max > bounds.max_followups[1];
  const gapBad = gap === null || gap < bounds.gap_days[0] || gap > bounds.gap_days[1];
  const hoursBad = form.windowOn && (!HOUR.test(form.start) || !HOUR.test(form.end) || form.start >= form.end);
  const zoneBad = form.windowOn && form.zone.trim() === "";
  const invalid = maxBad || gapBad || hoursBad || zoneBad;

  const save = async () => {
    if (invalid || max === null || gap === null) return;
    setSaving(true);
    setRefusal(null);
    try {
      const next = await clientSaveFollowUpSettings(stored.version, {
        max_followups: max,
        gap_days: gap,
        window: form.windowOn ? { start: form.start, end: form.end, timezone: form.zone.trim() || null } : null,
      });
      setStored(next);
      setForm(formOf(next));
      setSaved(true);
    } catch (error) {
      if (error instanceof ClientApiError && error.status === 409 && stored.configured) {
        setRefusal({ key: "followup.error.conflict", detail: null });
        reload();
      } else if (error instanceof ClientApiError && error.status === 409) {
        setRefusal({ key: "followup.notConfigured", detail: null });
      } else if (error instanceof ClientApiError && error.status === 422) {
        setRefusal({ key: "followup.error.invalid", detail: error.message });
      } else {
        setRefusal({ key: crmFailureOf(error).key, detail: null });
      }
    } finally {
      setSaving(false);
    }
  };

  const range = (r: [number, number]) => ({ min: n(r[0]), max: n(r[1]) });
  return (
    <form
      className="followup-card"
      data-testid="followup-card"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <p className="c-3 t-body-sm" data-testid="followup-default">
        {t("followup.default", { max: n(defaults.max_followups), gap: n(defaults.gap_days) })}
      </p>
      {!stored.configured ? (
        <p className="admin-alert admin-alert--info" role="status">
          {t("followup.notConfigured")}
        </p>
      ) : null}
      {stored.custom_schedule ? (
        <p className="admin-alert admin-alert--info" data-testid="followup-custom">
          {t("followup.custom")}
        </p>
      ) : null}
      <div className="followup-card__grid">
        <TextInput
          label={t("followup.max")}
          type="number"
          inputMode="numeric"
          min={bounds.max_followups[0]}
          max={bounds.max_followups[1]}
          value={form.max}
          helper={t("followup.max.help", range(bounds.max_followups))}
          error={maxBad ? t("followup.error.maxRange", range(bounds.max_followups)) : undefined}
          onChange={(e) => set("max", e.target.value)}
        />
        <TextInput
          label={t("followup.gap")}
          type="number"
          inputMode="numeric"
          min={bounds.gap_days[0]}
          max={bounds.gap_days[1]}
          value={form.gap}
          helper={t("followup.gap.help", range(bounds.gap_days))}
          error={gapBad ? t("followup.error.gapRange", range(bounds.gap_days)) : undefined}
          onChange={(e) => set("gap", e.target.value)}
        />
      </div>
      <label className="followup-card__check t-body-sm">
        <input type="checkbox" checked={form.windowOn} onChange={(e) => set("windowOn", e.target.checked)} />
        {t("followup.window.on")}
      </label>
      {form.windowOn ? (
        <div className="followup-card__grid">
          <TextInput label={t("followup.window.start")} type="time" value={form.start} onChange={(e) => set("start", e.target.value)} />
          <TextInput
            label={t("followup.window.end")}
            type="time"
            value={form.end}
            error={hoursBad ? t("followup.error.hours") : undefined}
            onChange={(e) => set("end", e.target.value)}
          />
          <TextInput
            label={t("followup.window.zone")}
            value={form.zone}
            dir="ltr"
            helper={t("followup.window.help")}
            error={zoneBad ? t("followup.error.zone") : undefined}
            onChange={(e) => set("zone", e.target.value)}
          />
        </div>
      ) : null}
      {refusal ? (
        <p className="admin-alert admin-alert--danger" role="alert" data-testid="followup-error">
          {t(refusal.key)}
          {refusal.detail ? `: ${refusal.detail}` : ""}
        </p>
      ) : null}
      {saved ? (
        <p className="admin-alert admin-alert--success" role="status" data-testid="followup-saved">
          {t("followup.saved")}
        </p>
      ) : null}
      <div className="row row--wrap">
        <Button type="submit" variant="primary" loading={saving} disabled={invalid || !stored.configured}>
          {t("followup.save")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={saving}
          onClick={() => setForm((f) => ({ ...f, max: String(defaults.max_followups), gap: String(defaults.gap_days), windowOn: false }))}
        >
          {t("followup.restore")}
        </Button>
      </div>
    </form>
  );
}
