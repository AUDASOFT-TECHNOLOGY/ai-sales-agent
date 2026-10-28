/** The client's daily report: where it goes, at what time, and what happened to each one.
 *
 *  Everything comes from the server for the logged-in client (`/client/v1/reporting`, `/reports`).
 *  Honesty rules: a report is shown as "sent" only when the server says the channel accepted it;
 *  a report that could not be delivered shows the reason and stays available; the time zone is
 *  typed by the client (an explicit IANA name), never guessed from this browser; WhatsApp is shown
 *  as not supported; purchase intentions, appointments and conversions are never invented. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { RefreshCw, Send } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { Segmented, Switch, TextInput } from "../ui/Controls";
import { useClientAuth } from "./ClientAuthContext";
import {
  ClientApiError,
  clientGenerateReport,
  clientReport,
  clientReporting,
  clientReports,
  clientResendReport,
  clientSaveReporting,
  type ReportDetail,
  type ReportingState,
  type ReportStatus,
  type ReportSummary,
} from "./api";

interface Failure {
  key: MessageKey;
  signedOut?: boolean;
}

const INVALID: Record<string, MessageKey> = {
  unsupported_channel: "reporting.error.unsupported_channel",
  unknown_channel: "reporting.error.unknown_channel",
  invalid_timezone: "reporting.error.invalid_timezone",
  invalid_time: "reporting.error.invalid_time",
  invalid_recipient: "reporting.error.invalid_recipient",
  invalid_language: "reporting.error.invalid_language",
};

function failureOf(err: unknown): Failure {
  if (err instanceof ClientApiError) {
    if (err.status === 401) return { key: "settings.error.session", signedOut: true };
    if (err.status === 403) return { key: "reporting.error.forbidden" };
    if (err.status === 404) return { key: "settings.error.notFound" };
    if (err.status === 409) return { key: "reporting.error.conflict" };
    if (err.status === 422) return { key: INVALID[err.message] ?? "settings.error.invalid" };
    if (err.status === 503) return { key: "reporting.error.unavailable" };
  }
  return { key: "auth.error.server" };
}

const TINT: Record<ReportStatus, "success" | "attention" | "neutral" | "danger"> = {
  sent: "success",
  pending: "neutral",
  sending: "neutral",
  failed: "attention",
  undeliverable: "danger",
};

const PROBLEM_CODES = new Set([
  "report_settings_missing",
  "report_disabled",
  "channel_unsupported",
  "channel_not_authorized",
  "channel_unavailable",
  "channel_not_wired",
  "dry_run",
  "send_unconfirmed",
  "send_failed",
  "send_cap_not_configured",
  "send_cap_reached",
  "send_cap_unavailable",
]);

const ACTION_CODES = new Set([
  "needs_human",
  "send_cap_not_configured",
  "send_cap_reached",
  "channel_degraded",
  "sync_failing",
  "inbox_failing",
]);

function timeZoneNames(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    return intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

export function ClientReportingPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [state, setState] = useState<ReportingState | null>(null);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [busy, setBusy] = useState<null | "load" | "save" | "generate" | string>(null);
  const [preview, setPreview] = useState<ReportDetail | null>(null);

  const [enabled, setEnabled] = useState(true);
  const [channel, setChannel] = useState("email");
  const [recipient, setRecipient] = useState("");
  const [sendTime, setSendTime] = useState("09:00");
  const [timezone, setTimezone] = useState("");
  const [language, setLanguage] = useState("en");

  const apply = useCallback(
    (loaded: PromiseSettledResult<ReportingState>, list: PromiseSettledResult<{ reports: ReportSummary[] }>) => {
      if (loaded.status === "rejected") {
        const problem = failureOf(loaded.reason);
        setLoadFailure(problem);
        if (problem.signedOut) void logout();
        return;
      }
      setLoadFailure(null);
      setState(loaded.value);
      setReports(list.status === "fulfilled" ? list.value.reports : []);
      const s = loaded.value.settings;
      if (s) {
        setEnabled(s.enabled);
        setChannel(s.channel);
        setRecipient(s.recipient);
        setSendTime(s.send_time);
        setTimezone(s.timezone);
        setLanguage(s.language);
      } else {
        const first = loaded.value.channels.find((c) => c.available);
        if (first) setChannel(first.key);
      }
    },
    [logout],
  );

  const load = useCallback(async () => {
    const [loaded, list] = await Promise.allSettled([clientReporting(), clientReports()]);
    apply(loaded, list);
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([clientReporting(), clientReports()]).then(([loaded, list]) => {
      if (!cancelled) apply(loaded!, list!);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  function fail(err: unknown) {
    const problem = failureOf(err);
    setFailure(problem);
    if (problem.signedOut) void logout();
  }

  async function refresh() {
    setFailure(null);
    setNotice(null);
    setBusy("load");
    await load();
    setBusy(null);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setFailure(null);
    setNotice(null);
    setBusy("save");
    try {
      await clientSaveReporting({ enabled, channel, recipient: recipient.trim(), send_time: sendTime, timezone: timezone.trim(), language });
      setNotice("reporting.done.saved");
      await load();
    } catch (err) {
      fail(err);
    }
    setBusy(null);
  }

  async function generate() {
    setFailure(null);
    setNotice(null);
    setBusy("generate");
    try {
      setPreview(await clientGenerateReport());
      setNotice("reporting.done.generated");
      await load();
    } catch (err) {
      fail(err);
    }
    setBusy(null);
  }

  async function resend(day: string) {
    setFailure(null);
    setNotice(null);
    setBusy(`resend:${day}`);
    try {
      setPreview(await clientResendReport(day));
      setNotice("reporting.done.resent");
    } catch (err) {
      fail(err);
    }
    await load(); // what the server now says, not what was expected
    setBusy(null);
  }

  async function show(day: string) {
    setFailure(null);
    setBusy(`show:${day}`);
    try {
      setPreview(await clientReport(day));
    } catch (err) {
      fail(err);
    }
    setBusy(null);
  }

  if (!state) {
    return (
      <div className="rv-page">
        <div className="page-head">
          <h2 className="t-h1">{t("reporting.title")}</h2>
        </div>
        {loadFailure ? (
          <>
            <p className="admin-alert admin-alert--danger" role="alert">
              {t(loadFailure.key)}
            </p>
            <div>
              <Button onClick={() => void refresh()} loading={busy === "load"}>
                {t("lanes.retry")}
              </Button>
            </div>
          </>
        ) : (
          <p className="c-3" role="status">
            {t("reporting.loading")}
          </p>
        )}
      </div>
    );
  }

  const working = busy !== null;
  const open = state.channels.filter((c) => c.available);
  const closed = state.channels.filter((c) => !c.available);
  const zones = timeZoneNames();

  function problemText(r: ReportSummary): string | null {
    if (r.status === "sent" || !r.error_code) return null;
    return PROBLEM_CODES.has(r.error_code) ? t(`reporting.problem.${r.error_code}` as MessageKey) : r.problem;
  }

  return (
    <div className="rv-page">
      <div className="page-head">
        <h2 className="t-h1">{t("reporting.title")}</h2>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void refresh()} loading={busy === "load"} disabled={working && busy !== "load"}>
          {t("lanes.refresh")}
        </Button>
      </div>
      <p className="c-2" style={{ maxWidth: "75ch" }}>{t("reporting.intro")}</p>

      {failure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(failure.key)}
        </p>
      ) : null}
      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {t(notice)}
        </p>
      ) : null}

      <div className="rv-grid-2" style={{ alignItems: "start" }}>
      <Glass level={1} className="client-card" aria-label={t("reporting.settings")}>
        <h3 className="t-h2">{t("reporting.settings")}</h3>
        {!state.settings ? <p className="c-3" data-testid="reporting-unconfigured">{t("reporting.unconfigured")}</p> : null}
        <form className="stack stack--lg" onSubmit={(e) => void save(e)} noValidate>
          <Switch checked={enabled} onChange={setEnabled} label={t("reporting.enabled")} disabled={working} />
          <div className="field">
            <p className="t-label c-2">{t("reporting.channel")}</p>
            {open.length > 0 ? (
              <Segmented
                layoutId="seg-report-channel"
                label={t("reporting.channel")}
                value={channel}
                onChange={setChannel}
                options={open.map((c) => ({ value: c.key, label: t(`reporting.channel.${c.key}` as MessageKey) }))}
              />
            ) : (
              <p className="c-3" data-testid="reporting-no-channel">
                {t("reporting.noChannel")}
              </p>
            )}
            <ul className="stack" data-testid="reporting-closed-channels">
              {closed.map((c) => (
                <li key={c.key} className="c-3 t-body-sm">
                  {t(`reporting.channel.${c.key}` as MessageKey)}: {t(c.reason === "not_supported" ? "reporting.channel.notSupported" : "reporting.channel.notAuthorized")}
                </li>
              ))}
            </ul>
          </div>
          <TextInput
            label={t(channel === "telegram" ? "reporting.recipient.telegram" : "reporting.recipient.email")}
            name="recipient"
            value={recipient}
            onChange={(e) => setRecipient(e.target.value)}
            helper={t(channel === "telegram" ? "reporting.recipient.telegramHelper" : "reporting.recipient.emailHelper")}
            autoComplete="off"
            maxLength={254}
            required
          />
          <div className="grid-2">
            <TextInput label={t("reporting.time")} name="send_time" type="time" value={sendTime} onChange={(e) => setSendTime(e.target.value)} required />
            <TextInput
              label={t("reporting.timezone")}
              name="timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              helper={t("reporting.timezoneHelper")}
              placeholder="Africa/Tunis"
              list="report-timezones"
              autoComplete="off"
              maxLength={64}
              required
            />
            <datalist id="report-timezones">
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
          </div>
          <div className="field">
            <p className="t-label c-2">{t("reporting.language")}</p>
            <Segmented
              layoutId="seg-report-language"
              label={t("reporting.language")}
              value={language}
              onChange={setLanguage}
              options={state.languages.map((l) => ({ value: l, label: l.toUpperCase() }))}
            />
          </div>
          <div className="row row--wrap">
            <Button type="submit" variant="primary" loading={busy === "save"} disabled={(working && busy !== "save") || open.length === 0}>
              {t("reporting.save")}
            </Button>
          </div>
        </form>
      </Glass>

      <Glass level={1} className="client-card" aria-label={t("reporting.history")}>
        <div className="row row--wrap">
          <h3 className="t-h2">{t("reporting.history")}</h3>
          <Button leading={<Send size={16} aria-hidden />} onClick={() => void generate()} loading={busy === "generate"} disabled={working && busy !== "generate"}>
            {t("reporting.generate")}
          </Button>
        </div>
        {reports.length === 0 ? (
          <p className="c-3" data-testid="reporting-empty">
            {t("reporting.empty")}
          </p>
        ) : (
          <ul className="rv-reports" data-testid="reporting-list">
            {reports.map((r) => {
              const problem = problemText(r);
              return (
                <li key={r.day} className="rv-report" data-testid={`report-${r.day}`}>
                  <div className="rv-report__main">
                  <div className="row row--wrap">
                    <strong>{r.day}</strong>
                    <span data-testid={`report-status-${r.day}`}>
                      <Chip tint={TINT[r.status]}>{t(`reporting.status.${r.status}` as MessageKey)}</Chip>
                    </span>
                    {r.channel ? (
                      <span className="c-3 t-body-sm">
                        {t(`reporting.channel.${r.channel}` as MessageKey)} {r.recipient_hint ?? ""}
                      </span>
                    ) : null}
                    {r.sent_at ? <span className="c-3 t-body-sm">{t("reporting.sentAt", { date: d(r.sent_at, { dateStyle: "medium", timeStyle: "short" }) })}</span> : null}
                  </div>
                  {problem ? (
                    <p className="c-attention" data-testid={`report-problem-${r.day}`}>
                      {problem}
                    </p>
                  ) : null}
                  </div>
                  <div className="row row--wrap">
                    <Button size="sm" onClick={() => void show(r.day)} loading={busy === `show:${r.day}`} disabled={working && busy !== `show:${r.day}`}>
                      {t("reporting.preview")}
                    </Button>
                    {r.status === "pending" || r.status === "failed" || r.status === "undeliverable" ? (
                      <Button size="sm" onClick={() => void resend(r.day)} loading={busy === `resend:${r.day}`} disabled={working && busy !== `resend:${r.day}`}>
                        {t("reporting.resend")}
                      </Button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Glass>
      </div>

      {preview ? (
        <Glass level={1} className="client-card" aria-label={t("reporting.preview")}>
          <h3 className="t-h2">{t("reporting.previewOf", { day: preview.day })}</h3>
          <dl className="rv-figures" data-testid="report-figures">
            <div>
              <dt className="t-label c-2">{t("reporting.fig.sent")}</dt>
              <dd>{preview.figures.activity.messages_sent}</dd>
            </div>
            <div>
              <dt className="t-label c-2">{t("reporting.fig.received")}</dt>
              <dd>{preview.figures.activity.replies_received}</dd>
            </div>
            <div>
              <dt className="t-label c-2">{t("reporting.fig.scheduled")}</dt>
              <dd>{preview.figures.follow_ups.scheduled}</dd>
            </div>
          </dl>
          <p className="c-3" data-testid="report-untracked">
            {t("reporting.untracked")}
          </p>
          {preview.figures.actions_required.length > 0 ? (
            <ul className="stack" data-testid="report-actions">
              {preview.figures.actions_required.map((a) => (
                <li key={a.code}>{ACTION_CODES.has(a.code) ? t(`reporting.action.${a.code}` as MessageKey, { count: a.count ?? 0 }) : a.code}</li>
              ))}
            </ul>
          ) : null}
          <pre className="t-body-sm" data-testid="report-text" style={{ whiteSpace: "pre-wrap" }}>
            {preview.text}
          </pre>
        </Glass>
      ) : null}
    </div>
  );
}
