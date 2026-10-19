/** The client's own WhatsApp Business account: connect through Meta's sign-in window, reconnect,
 *  disconnect. The client types no token and no identifier; it only clicks and chooses its business
 *  and number in Meta's own window. What is shown is the client's own number and the business name
 *  Meta verified for it. */

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import {
  WhatsAppApiError,
  clientWhatsAppComplete,
  clientWhatsAppDisconnect,
  clientWhatsAppStart,
  clientWhatsAppStatus,
  type ClientWhatsAppStatus,
} from "../whatsapp/api";
import { whatsappText, type WhatsAppKey } from "../whatsapp/copy";
import { runEmbeddedSignup, type SignupDeps } from "../whatsapp/embeddedSignup";

const CAUSES = ["invalid", "denied", "number_mismatch", "number_unclear", "in_use", "unavailable"] as const;

function errorKey(err: unknown): WhatsAppKey {
  if (err instanceof Error && err.message === "sdk") return "client.error.sdk";
  if (err instanceof WhatsAppApiError) {
    if ((CAUSES as readonly string[]).includes(err.message)) return `client.error.${err.message}` as WhatsAppKey;
    if (err.status === 503) return "client.error.unavailable";
  }
  return "client.error.generic";
}

interface Props {
  /** Called when the connection changed, so the page reloads where the channel stands. */
  onChanged?: () => void;
  /** Replaceable in tests. */
  signup?: SignupDeps;
}

export function ClientWhatsAppCard({ onChanged, signup }: Props) {
  const { locale } = useI18n();
  const w = useCallback(
    (key: WhatsAppKey, vars?: Record<string, string | number>) => whatsappText(locale, key, vars),
    [locale],
  );
  const [status, setStatus] = useState<ClientWhatsAppStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<WhatsAppKey | null>(null);
  const [notice, setNotice] = useState<WhatsAppKey | null>(null);
  const [confirm, setConfirm] = useState(false);

  useEffect(() => {
    let cancelled = false;
    clientWhatsAppStatus().then(
      (value) => !cancelled && setStatus(value),
      () => !cancelled && setStatus(null),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const connect = async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const start = await clientWhatsAppStart();
      const outcome = await runEmbeddedSignup(start, signup);
      if (outcome.kind === "cancelled") {
        setNotice("client.cancelled");
        return;
      }
      const next = await clientWhatsAppComplete({
        state: start.state,
        code: outcome.value.code,
        waba_id: outcome.value.wabaId,
        phone_number_id: outcome.value.phoneNumberId,
      });
      setStatus(next);
      setNotice("client.done");
      onChanged?.();
    } catch (err) {
      setError(errorKey(err));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      setStatus(await clientWhatsAppDisconnect());
      setConfirm(false);
      onChanged?.();
    } catch (err) {
      setError(errorKey(err));
    } finally {
      setBusy(false);
    }
  };

  const state = status?.state ?? "not_connected";
  const connected = state === "connected" || state === "reauth_required";
  const unavailable = state === "unavailable";
  const noNumber = status !== null && !status.number;

  return (
    <Glass level={1} className="client-card" aria-label={w("client.title")} data-testid="whatsapp-card">
      <h3 className="t-h2">{w("client.title")}</h3>
      <p className="c-3">{w("client.intro")}</p>
      {status?.number ? <p className="c-3">{w("client.number", { number: status.number })}</p> : null}
      <p data-testid="whatsapp-state">
        <strong>{w(`client.state.${state}` as WhatsAppKey)}</strong>
        {status?.verified_name ? <> — {w("client.verified", { name: status.verified_name })}</> : null}
      </p>
      {status?.message && status.status !== "ready" ? (
        <p className="admin-alert admin-alert--info" role="status">
          {status.message}
        </p>
      ) : null}
      {unavailable ? (
        <p className="admin-alert admin-alert--info" role="status">
          {w("client.unavailable")}
        </p>
      ) : null}
      {noNumber ? (
        <p className="admin-alert admin-alert--info" role="status">
          {w("client.noNumber")}
        </p>
      ) : null}
      {state === "connected" ? (
        <>
          <p className={status?.can_start_conversations ? "c-success" : "c-3"}>
            {w(status?.can_start_conversations ? "client.canStart" : "client.cannotStart")}
          </p>
          <p className="c-3 t-body-sm">
            {w("client.delivery", { delivered: status?.delivered_7d ?? 0, failed: status?.failed_7d ?? 0 })}
          </p>
        </>
      ) : null}
      {notice ? (
        <p
          className={notice === "client.done" ? "admin-alert admin-alert--success" : "admin-alert admin-alert--info"}
          role="status"
        >
          {w(notice)}
        </p>
      ) : null}
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {w(error)}
        </p>
      ) : null}

      <div>
        {!unavailable ? (
          <Button
            type="button"
            variant="primary"
            loading={busy}
            disabled={busy || noNumber}
            onClick={() => void connect()}
          >
            {w(connected ? "client.reconnect" : "client.connect")}
          </Button>
        ) : null}{" "}
        {connected ? (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirm(true)}>
            {w("client.disconnect")}
          </Button>
        ) : null}
      </div>
      {confirm ? (
        <div className="admin-alert admin-alert--info" role="group" aria-label={w("client.disconnect")}>
          <p>{w("client.disconnectConfirm")}</p>
          <Button type="button" variant="primary" loading={busy} disabled={busy} onClick={() => void disconnect()}>
            {w("client.disconnectYes")}
          </Button>{" "}
          <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirm(false)}>
            {w("client.cancel")}
          </Button>
        </div>
      ) : null}
    </Glass>
  );
}
