/** The two connections that ask something of the client's own app: WhatsApp (Meta's sign-in window, for
 *  the number of its company) and Telegram (the code Telegram sends to the client's own app). No token,
 *  no server, no key: the client gives the number it wants to use, then clicks "Connect my ..." and does
 *  what the provider itself asks of a person signing in. */

import { useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { TextInput } from "../../ui/Controls";
import {
  ClientApiError,
  clientSetHint,
  clientTelegramDisconnect,
  clientTelegramLoginCancel,
  clientTelegramLoginCode,
  clientTelegramLoginPassword,
  clientTelegramLoginStart,
  type TelegramAccount,
} from "../api";
import { WhatsAppApiError, clientWhatsAppComplete, clientWhatsAppDisconnect, clientWhatsAppStart, type ClientWhatsAppStatus } from "../../whatsapp/api";
import { whatsappText, type WhatsAppKey } from "../../whatsapp/copy";
import { runEmbeddedSignup, type SignupDeps } from "../../whatsapp/embeddedSignup";
import { Note } from "../kit";
import { BrandIcon } from "./BrandIcons";
import { ConnectForm } from "./ConnectForm";
import { ConnectionRow } from "./ConnectionRow";
import { DisconnectConfirm, RowError, useRowState } from "./connectionKit";
import type { ConnectionView } from "./connectionModel";

/* ── WhatsApp ─────────────────────────────────────────────────────────────────────────────── */

const CAUSES = ["invalid", "denied", "number_mismatch", "number_unclear", "in_use", "unavailable"] as const;

function whatsappErrorKey(err: unknown): WhatsAppKey {
  if (err instanceof Error && err.message === "sdk") return "client.error.sdk";
  if (err instanceof WhatsAppApiError) {
    if ((CAUSES as readonly string[]).includes(err.message)) return `client.error.${err.message}` as WhatsAppKey;
    if (err.status === 503) return "client.error.unavailable";
  }
  return "client.error.generic";
}

export function WhatsAppRow({ view, status, onChanged, signup }: { view: ConnectionView; status: ClientWhatsAppStatus | null; onChanged: () => Promise<void> | void; signup?: SignupDeps }) {
  const { t, locale } = useI18n();
  const s = useRowState();
  const [problem, setProblem] = useState<WhatsAppKey | null>(null);
  const [notice, setNotice] = useState<WhatsAppKey | null>(null);
  const w = (key: WhatsAppKey) => whatsappText(locale, key);

  // Meta's window: the client signs in there and chooses its business and number. The number it gave is
  // the one Meta must confirm.
  async function connect() {
    setProblem(null);
    setNotice(null);
    try {
      const start = await clientWhatsAppStart();
      const outcome = await runEmbeddedSignup(start, signup);
      if (outcome.kind === "cancelled") {
        setNotice("client.cancelled");
        return;
      }
      await clientWhatsAppComplete({ state: start.state, code: outcome.value.code, waba_id: outcome.value.wabaId, phone_number_id: outcome.value.phoneNumberId });
      setNotice("client.done");
      await onChanged();
    } catch (err) {
      setProblem(whatsappErrorKey(err));
    }
  }

  async function disconnect() {
    setProblem(null);
    s.setBusy(true);
    try {
      await clientWhatsAppDisconnect();
      s.setConfirming(false);
      await onChanged();
    } catch (err) {
      setProblem(whatsappErrorKey(err));
    }
    s.setBusy(false);
  }

  const connected = view.standing === "connected";
  return (
    <ConnectionRow
      id="whatsapp"
      icon={<BrandIcon brand="whatsapp" size={28} />}
      name={t("rv.conn.name.whatsapp")}
      standing={view.standing}
      reconnect={view.reconnect}
      detail={t(view.detail, view.vars)}
      action={
        connected || view.reconnect ? (
          <Button variant="ghost" disabled={s.busy} onClick={() => s.setConfirming(true)}>
            {t("rv.conn.act.disconnect")}
          </Button>
        ) : null
      }
    >
      {!connected && view.field ? (
        <ConnectForm id="whatsapp" field={view.field} brand="whatsapp" value={view.value} reconnect={view.reconnect} pending={view.pending} required onChanged={onChanged} onConnect={connect} />
      ) : null}
      {notice ? <Note tone={notice === "client.done" ? "success" : "info"}>{w(notice)}</Note> : null}
      {connected && status && !status.can_start_conversations ? <Note>{w("client.cannotStart")}</Note> : null}
      {s.confirming ? <DisconnectConfirm busy={s.busy} onNo={() => s.setConfirming(false)} onYes={() => void disconnect()} /> : null}
      {problem ? <Note tone="danger">{w(problem)}</Note> : null}
    </ConnectionRow>
  );
}

/* ── Telegram ─────────────────────────────────────────────────────────────────────────────── */

type Step = "code" | "password";

function telegramErrorKey(err: unknown, step: Step | "other"): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 429) return "channel.tgaccount.error.wait";
    if (err.status === 502) return "channel.tgaccount.error.unreachable";
    if (err.status === 503) return "channel.tgaccount.error.unavailable";
    if (err.status === 422 && step !== "other") return `channel.tgaccount.error.${step}` as MessageKey;
    if (err.status === 409) {
      if (/already connected/i.test(err.message)) return "channel.tgaccount.error.used";
      return step === "other" ? "channel.tgaccount.error.generic" : "channel.tgaccount.error.restart";
    }
  }
  return "channel.tgaccount.error.generic";
}

export function TelegramRow({ view, account, onChanged }: { view: ConnectionView; account: TelegramAccount | null; onChanged: () => Promise<void> | void }) {
  const { t } = useI18n();
  const s = useRowState();
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<MessageKey | null>(null);

  const signedIn = account?.state === "connected";
  // Telegram asked for a code or a password: the sign-in is under way, whether or not it was started here.
  const step: Step | null = !account || signedIn ? null : account.login === "password" ? "password" : account.login === "code" ? "code" : null;

  async function call(which: Step | "other", action: () => Promise<unknown>, after: () => void) {
    setProblem(null);
    s.setBusy(true);
    try {
      await action();
      after();
      await onChanged();
    } catch (err) {
      setProblem(telegramErrorKey(err, which));
      await onChanged();
    }
    s.setBusy(false);
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (step === "code")
      void call("code", () => clientTelegramLoginCode(code), () => {
        setCode("");
      });
    if (step === "password")
      void call("password", () => clientTelegramLoginPassword(password), () => {
        setPassword("");
      });
  };

  // The number the client signs in with is the one it wants Revansa to use.
  const sendCode = async (phone: string) => {
    await clientTelegramLoginStart(phone);
    void clientSetHint("telegram", phone).catch(() => undefined);
    await onChanged();
  };

  const field = { disabled: s.busy, autoComplete: "off" } as const;
  return (
    <ConnectionRow
      id="telegram"
      icon={<BrandIcon brand="telegram" size={28} />}
      name={t("rv.conn.name.telegram")}
      standing={view.standing}
      reconnect={view.reconnect}
      detail={t(view.detail, view.vars)}
      action={
        signedIn || view.reconnect ? (
          <Button variant="ghost" disabled={s.busy} onClick={() => s.setConfirming(true)}>
            {t("rv.conn.act.disconnect")}
          </Button>
        ) : null
      }
    >
      <Note tone="warn" title={t("channel.tgaccount.warning.title")}>
        {t("channel.tgaccount.warning.text")}
      </Note>
      {account?.limited ? <Note tone="danger">{t("channel.tgaccount.limited")}</Note> : null}
      {!signedIn && !step && view.field ? (
        <ConnectForm id="telegram" field={view.field} brand="telegram" value={view.value} reconnect={view.reconnect} pending={view.pending} required onChanged={onChanged} onConnect={sendCode} />
      ) : null}
      {step ? (
        <form className="stack" onSubmit={submit} aria-label={t("channel.tgaccount.signIn")}>
          {step === "code" ? <TextInput {...field} label={t("channel.tgaccount.code")} name="telegram-code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} helper={t("channel.tgaccount.codeHelp")} maxLength={16} autoComplete="one-time-code" /> : null}
          {step === "password" ? <TextInput {...field} label={t("channel.tgaccount.password")} name="telegram-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} helper={t("channel.tgaccount.passwordHelp")} maxLength={256} /> : null}
          <div className="rv-actions">
            <Button type="submit" variant="primary" size="lg" loading={s.busy} leading={<BrandIcon brand="telegram" />} disabled={step === "code" ? !code.trim() : !password}>
              {t(`channel.tgaccount.submit.${step}` as MessageKey)}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={s.busy}
              onClick={() =>
                void call("other", clientTelegramLoginCancel, () => {
                  setCode("");
                  setPassword("");
                })
              }
            >
              {t("channel.tgaccount.cancel")}
            </Button>
          </div>
        </form>
      ) : null}
      {s.confirming ? <DisconnectConfirm busy={s.busy} onNo={() => s.setConfirming(false)} onYes={() => void call("other", clientTelegramDisconnect, () => s.setConfirming(false))} /> : null}
      {problem ? <Note tone="danger">{t(problem)}</Note> : null}
      <RowError error={s.error} />
    </ConnectionRow>
  );
}
