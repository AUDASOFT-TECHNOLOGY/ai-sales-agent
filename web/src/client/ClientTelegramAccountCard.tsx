/** Telegram from the client's own account: choose it (or go back to the Revansa bot), sign in with
 *  Telegram itself (phone number, the code Telegram sends to its app, its two-step password), say who
 *  expects to be contacted, and disconnect. The client types no token, no server and no key: only what
 *  Telegram itself asks of a person signing in. What it types is sent once and never shown again. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { TextInput } from "../ui/Controls";
import {
  ClientApiError,
  clientSaveChannel,
  clientTelegramAccount,
  clientTelegramDisconnect,
  clientTelegramLoginCancel,
  clientTelegramLoginCode,
  clientTelegramLoginPassword,
  clientTelegramLoginStart,
  clientTelegramWaiting,
  type ChannelState,
  type TelegramAccount,
  type TelegramWaiting,
} from "./api";

type Step = "phone" | "code" | "password";

function errorKey(err: unknown, step: Step | "other"): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 429) return "channel.tgaccount.error.wait";
    if (err.status === 502) return "channel.tgaccount.error.unreachable";
    if (err.status === 503) return "channel.tgaccount.error.unavailable";
    if (err.status === 422 && step !== "other") return `channel.tgaccount.error.${step}` as MessageKey;
    if (err.status === 409) {
      if (/already connected/i.test(err.message)) return "channel.tgaccount.error.used";
      return step === "phone" || step === "other"
        ? "channel.tgaccount.error.generic"
        : "channel.tgaccount.error.restart";
    }
  }
  return "channel.tgaccount.error.generic";
}

interface Props {
  channel: ChannelState;
  /** The channel after the client chose its own account, or the bot. */
  onChannel: (next: ChannelState) => void;
  /** Called when the connection changed, so the page reloads where the channel stands. */
  onChanged: () => void;
}

export function ClientTelegramAccountCard({ channel, onChannel, onChanged }: Props) {
  const { t } = useI18n();
  const accountMode = channel.telegram?.mode === "account";
  const [account, setAccount] = useState<TelegramAccount | null>(null);
  const [waiting, setWaiting] = useState<TelegramWaiting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [consentField, setConsentField] = useState(channel.telegram?.consent_field ?? "");
  const [maxNew, setMaxNew] = useState(String(channel.telegram?.max_new_conversations_per_day ?? ""));

  useEffect(() => {
    setConsentField(channel.telegram?.consent_field ?? "");
    setMaxNew(String(channel.telegram?.max_new_conversations_per_day ?? ""));
  }, [channel.telegram?.consent_field, channel.telegram?.max_new_conversations_per_day]);

  const refresh = useCallback(() => {
    clientTelegramAccount()
      .then(setAccount)
      .catch(() => setAccount(null));
  }, []);

  useEffect(() => {
    if (!accountMode) return;
    let cancelled = false;
    clientTelegramAccount()
      .then((value) => !cancelled && setAccount(value))
      .catch(() => !cancelled && setAccount(null));
    return () => {
      cancelled = true;
    };
  }, [accountMode, channel.status]);

  const connected = account?.state === "connected";
  useEffect(() => {
    if (!accountMode || !connected) {
      setWaiting(null);
      return;
    }
    let cancelled = false;
    clientTelegramWaiting()
      .then((value) => !cancelled && setWaiting(value))
      .catch(() => !cancelled && setWaiting(null)); // a convenience: never blocks the card
    return () => {
      cancelled = true;
    };
  }, [accountMode, connected]);

  const choose = (useAccount: boolean) => {
    setError(null);
    setNotice(null);
    setBusy(true);
    clientSaveChannel({ expected_version: channel.version, channel: "telegram", telegram: { use_account: useAccount } })
      .then(onChannel)
      .catch((err) => setError(errorKey(err, "other")))
      .finally(() => setBusy(false));
  };

  const run = (step: Step | "other", call: () => Promise<TelegramAccount>, after?: () => void) => {
    setError(null);
    setNotice(null);
    setBusy(true);
    call()
      .then((next) => {
        setAccount(next);
        after?.();
      })
      .catch((err) => {
        setError(errorKey(err, step));
        refresh();
      })
      .finally(() => setBusy(false));
  };

  const step: Step | null =
    account && account.state !== "connected" && account.available
      ? account.login === "password"
        ? "password"
        : account.login === "code"
          ? "code"
          : "phone"
      : null;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (step === "phone") run("phone", () => clientTelegramLoginStart(phone), () => setCode(""));
    if (step === "code")
      run("code", () => clientTelegramLoginCode(code), () => {
        setCode("");
        setPhone("");
        onChanged();
      });
    if (step === "password")
      run("password", () => clientTelegramLoginPassword(password), () => {
        setPassword("");
        setPhone("");
        onChanged();
      });
  };

  const cancel = () => run("other", clientTelegramLoginCancel, () => {
    setCode("");
    setPassword("");
  });

  const disconnect = () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    clientTelegramDisconnect()
      .then((answer) => {
        setConfirmDisconnect(false);
        setNotice(answer.ended_at_telegram ? "channel.tgaccount.disconnected" : "channel.tgaccount.disconnectedLocal");
        refresh();
        onChanged();
      })
      .catch((err) => setError(errorKey(err, "other")))
      .finally(() => setBusy(false));
  };

  const saveSettings = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    clientSaveChannel({
      expected_version: channel.version,
      channel: "telegram",
      telegram: { consent_field: consentField.trim(), max_new_conversations_per_day: Number(maxNew) },
    })
      .then((next) => {
        onChannel(next);
        setNotice("channel.saved");
      })
      .catch((err) => setError(errorKey(err, "other")))
      .finally(() => setBusy(false));
  };

  const settingsDirty =
    consentField.trim() !== (channel.telegram?.consent_field ?? "") ||
    maxNew !== String(channel.telegram?.max_new_conversations_per_day ?? "");
  const state = account?.state ?? "not_connected";

  return (
    <Glass level={1} className="client-card" aria-label={t("channel.tgaccount.title")}>
      <h3 className="t-h2">{t("channel.tgaccount.title")}</h3>
      <p className="c-3">{t("channel.tgaccount.intro")}</p>
      <div role="group" aria-label={t("channel.tgaccount.title")}>
        <Button
          type="button"
          variant={accountMode ? "primary" : "ghost"}
          disabled={busy || accountMode}
          aria-pressed={accountMode}
          onClick={() => choose(true)}
        >
          {t("channel.tgaccount.useAccount")}
        </Button>{" "}
        <Button
          type="button"
          variant={accountMode ? "ghost" : "primary"}
          disabled={busy || !accountMode}
          aria-pressed={!accountMode}
          onClick={() => choose(false)}
        >
          {t("channel.tgaccount.useBot")}
        </Button>
      </div>
      <p className="c-3 t-body-sm">{t("channel.tgaccount.modeNote")}</p>

      {accountMode ? (
        <>
          <p data-testid="tgaccount-state">
            <strong>{t(`channel.tgaccount.state.${state}` as MessageKey)}</strong>
          </p>
          {account && !account.available ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("channel.tgaccount.notAvailable")}
            </p>
          ) : null}
          {account?.limited ? (
            <p className="admin-alert admin-alert--danger" role="status" data-testid="tgaccount-limited">
              {t("channel.tgaccount.limited")}
            </p>
          ) : null}
          {connected && account?.daily_limit != null ? (
            <p className="c-3" data-testid="tgaccount-today">
              {t("channel.tgaccount.today", { n: account.new_today ?? 0, limit: account.daily_limit })}
            </p>
          ) : null}

          {step ? (
            <form className="stack" onSubmit={onSubmit} aria-label={t("channel.tgaccount.signIn")}>
              {state === "reauth_required" ? (
                <p className="admin-alert admin-alert--info" role="status">
                  {t("channel.tgaccount.reauth")}
                </p>
              ) : null}
              {step === "phone" ? (
                <TextInput
                  label={t("channel.tgaccount.phone")}
                  name="telegram-phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  helper={t("channel.tgaccount.phoneHelp")}
                  disabled={busy}
                  maxLength={32}
                  autoComplete="off"
                />
              ) : null}
              {step === "code" ? (
                <TextInput
                  label={t("channel.tgaccount.code")}
                  name="telegram-code"
                  inputMode="numeric"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  helper={t("channel.tgaccount.codeHelp")}
                  disabled={busy}
                  maxLength={16}
                  autoComplete="one-time-code"
                />
              ) : null}
              {step === "password" ? (
                <TextInput
                  label={t("channel.tgaccount.password")}
                  name="telegram-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  helper={t("channel.tgaccount.passwordHelp")}
                  disabled={busy}
                  maxLength={256}
                  autoComplete="off"
                />
              ) : null}
              <div>
                <Button
                  type="submit"
                  variant="primary"
                  loading={busy}
                  disabled={busy || (step === "phone" ? !phone.trim() : step === "code" ? !code.trim() : !password)}
                >
                  {t(`channel.tgaccount.submit.${step}` as MessageKey)}
                </Button>{" "}
                {step !== "phone" ? (
                  <Button type="button" variant="ghost" disabled={busy} onClick={cancel}>
                    {t("channel.tgaccount.cancel")}
                  </Button>
                ) : null}
              </div>
            </form>
          ) : null}

          {state === "connected" || state === "reauth_required" ? (
            <div>
              {confirmDisconnect ? (
                <div className="admin-alert admin-alert--info" role="group">
                  <p>{t("channel.tgaccount.confirmDisconnect")}</p>
                  <Button type="button" variant="primary" loading={busy} disabled={busy} onClick={disconnect}>
                    {t("channel.tgaccount.disconnect")}
                  </Button>{" "}
                  <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                    {t("channel.tgaccount.cancel")}
                  </Button>
                </div>
              ) : (
                <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(true)}>
                  {t("channel.tgaccount.disconnect")}
                </Button>
              )}
            </div>
          ) : null}

          <form className="stack" onSubmit={saveSettings} aria-label={t("channel.tgaccount.settings")}>
            <h4 className="t-h3">{t("channel.tgaccount.settings")}</h4>
            <div className="grid-2">
              <TextInput
                label={t("channel.tgaccount.consentField")}
                name="consent_field"
                value={consentField}
                onChange={(e) => setConsentField(e.target.value)}
                helper={t("channel.tgaccount.consentHelp")}
                disabled={busy}
                maxLength={64}
              />
              <TextInput
                label={t("channel.tgaccount.maxNew")}
                name="max_new"
                type="number"
                min={1}
                max={30}
                value={maxNew}
                onChange={(e) => setMaxNew(e.target.value)}
                helper={t("channel.tgaccount.maxNewHelp")}
                disabled={busy}
              />
            </div>
            <div>
              <Button type="submit" variant="primary" loading={busy} disabled={busy || !settingsDirty || !consentField.trim()}>
                {t("channel.save")}
              </Button>
            </div>
          </form>

          {waiting && waiting.waiting > 0 ? (
            <div data-testid="tgaccount-waiting">
              <h4 className="t-h3">{t("channel.tgaccount.waiting.title")}</h4>
              <p className="c-3 t-body-sm">{t("channel.tgaccount.waiting.intro", { field: waiting.consent_field })}</p>
              <ul className="stack">
                {waiting.people.map((person) => (
                  <li key={person.prospect_id}>
                    {person.display_name}{" "}
                    <span className="c-3">— {t(`channel.tgaccount.waiting.${person.reason}` as MessageKey)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}

      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(error)}
        </p>
      ) : null}
      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {t(notice)}
        </p>
      ) : null}
    </Glass>
  );
}
