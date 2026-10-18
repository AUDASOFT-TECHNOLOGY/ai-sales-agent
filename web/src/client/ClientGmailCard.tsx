/** The client's own Gmail as its email: choose between Gmail and a Revansa mailbox (they exclude each
 *  other), connect through Google's own consent page, reconnect, disconnect. The client types no
 *  password, no server and no token: it only clicks. The address shown is the client's own. */

import { useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import {
  ClientApiError,
  clientGmailDisconnect,
  clientGmailStart,
  clientGmailStatus,
  clientSaveChannel,
  type ChannelState,
  type GmailStatus,
} from "./api";

const RETURNS = ["connected", "denied", "scope", "invalid", "in_use", "unavailable", "error"] as const;
type GoogleReturn = (typeof RETURNS)[number];

/** `?google=...` as Google's return leaves it. Anything else is ignored. */
export function googleReturnOf(search: string): GoogleReturn | null {
  const value = new URLSearchParams(search).get("google");
  return (RETURNS as readonly string[]).includes(value ?? "") ? (value as GoogleReturn) : null;
}

function errorKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 503) return "channel.gmail.error.unavailable";
    if (err.status === 409 || err.status === 403) return "channel.gmail.error.conflict";
  }
  return "channel.gmail.error.generic";
}

interface Props {
  channel: ChannelState;
  /** The channel after the client switched mailbox. */
  onChannel: (next: ChannelState) => void;
  /** Called when the Gmail connection changed, so the page can reload its status. */
  onChanged: () => void;
  /** The `?google=` outcome of a return from Google, already read by the page. */
  returned: GoogleReturn | null;
  /** Sends the browser to Google (replaceable in tests). */
  go?: (url: string) => void;
}

export function ClientGmailCard({ channel, onChannel, onChanged, returned, go }: Props) {
  const { t, locale } = useI18n();
  const gmailMode = channel.email?.mode === "gmail";
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  useEffect(() => {
    if (!gmailMode) return;
    let cancelled = false;
    clientGmailStatus()
      .then((value) => !cancelled && setStatus(value))
      .catch(() => !cancelled && setStatus(null));
    return () => {
      cancelled = true;
    };
  }, [gmailMode, channel.status]);

  const choose = (useGmail: boolean) => {
    setError(null);
    setBusy(true);
    clientSaveChannel({ expected_version: channel.version, channel: "email", email: { use_gmail: useGmail } })
      .then(onChannel)
      .catch((err) => setError(errorKey(err)))
      .finally(() => setBusy(false));
  };

  const connect = () => {
    setError(null);
    setBusy(true);
    clientGmailStart()
      .then(({ authorize_url }) => (go ?? ((url) => window.location.assign(url)))(authorize_url))
      .catch((err) => {
        setError(errorKey(err));
        setBusy(false);
      });
  };

  const disconnect = () => {
    setError(null);
    setBusy(true);
    clientGmailDisconnect()
      .then((next) => {
        setStatus(next);
        setConfirmDisconnect(false);
        onChanged();
      })
      .catch((err) => setError(errorKey(err)))
      .finally(() => setBusy(false));
  };

  const state = status?.state ?? "not_connected";
  const unavailable = state === "unavailable";
  const connected = state === "connected" || state === "reauth_required";
  const syncDate = status?.last_sync_at ? ` (${new Date(status.last_sync_at).toLocaleString(locale)})` : "";

  return (
    <Glass level={1} className="client-card" aria-label={t("channel.gmail.title")}>
      <h3 className="t-h2">{t("channel.gmail.title")}</h3>
      <p className="c-3">{t("channel.gmail.mailbox")}</p>
      <div role="group" aria-label={t("channel.gmail.mailbox")}>
        <Button
          type="button"
          variant={gmailMode ? "primary" : "ghost"}
          disabled={busy || gmailMode}
          aria-pressed={gmailMode}
          onClick={() => choose(true)}
        >
          {t("channel.gmail.useGmail")}
        </Button>{" "}
        <Button
          type="button"
          variant={gmailMode ? "ghost" : "primary"}
          disabled={busy || !gmailMode || connected}
          aria-pressed={!gmailMode}
          onClick={() => choose(false)}
        >
          {t("channel.gmail.useDedicated")}
        </Button>
      </div>
      <p className="c-3 t-body-sm">{t("channel.gmail.gmailExclusive")}</p>

      {returned ? (
        <p
          data-testid="gmail-return"
          className={returned === "connected" ? "admin-alert admin-alert--success" : "admin-alert admin-alert--danger"}
          role="status"
        >
          {t(`channel.gmail.return.${returned}` as MessageKey)}
        </p>
      ) : null}

      {gmailMode ? (
        <>
          <p data-testid="gmail-state">
            <strong>{t(`channel.gmail.state.${state}` as MessageKey)}</strong>
            {status?.address ? <> — {t("channel.gmail.connected", { address: status.address })}</> : null}
          </p>
          {status?.state === "reauth_required" || status?.state === "not_connected" ? (
            <p className="admin-alert admin-alert--info" role="status">
              {status.message}
            </p>
          ) : null}
          {unavailable ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("channel.gmail.error.unavailable")}
            </p>
          ) : null}
          {state === "not_connected" ? <p className="c-3">{t("channel.gmail.intro")}</p> : null}
          {status && status.state !== "unavailable" ? (
            <p className="c-3 t-body-sm" data-testid="gmail-sync">
              {t(`channel.gmail.sync.${status.mailbox_sync}` as MessageKey, { date: syncDate })}
            </p>
          ) : null}
          {connected && status && !status.send_cap_configured ? (
            <p className="admin-alert admin-alert--info" role="status" data-testid="gmail-cap-warning">
              {t("channel.gmail.capWarning")}
            </p>
          ) : null}
          {status?.send_ready ? <p className="c-success">{t("channel.gmail.ready")}</p> : null}
          <p className="c-3 t-body-sm">{t("channel.gmail.restricted")}</p>

          <div>
            {!unavailable ? (
              <Button type="button" variant="primary" loading={busy} disabled={busy} onClick={connect}>
                {t(connected ? "channel.gmail.reconnect" : "channel.gmail.connect")}
              </Button>
            ) : null}{" "}
            {connected ? (
              <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(true)}>
                {t("channel.gmail.disconnect")}
              </Button>
            ) : null}
          </div>
          {confirmDisconnect ? (
            <div className="admin-alert admin-alert--info" role="group" aria-label={t("channel.gmail.disconnect")}>
              <p>{t("channel.gmail.disconnectConfirm")}</p>
              <Button type="button" variant="primary" loading={busy} disabled={busy} onClick={disconnect}>
                {t("channel.gmail.disconnectYes")}
              </Button>{" "}
              <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                {t("channel.email.cancel")}
              </Button>
            </div>
          ) : null}
        </>
      ) : null}

      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(error)}
        </p>
      ) : null}
    </Glass>
  );
}
