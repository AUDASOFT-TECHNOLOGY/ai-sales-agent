/** Channel: how the client talks to its prospects. It lists only the channels the Admin allows, lets
 *  the client choose one and word its messages, and says where the channel stands: ready, waiting for
 *  the platform, or an action the client has to take. Nothing technical is asked or shown here: the
 *  connection of a channel (servers, mailbox, bot, webhook) is the platform's work. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Glass } from "../ui/Glass";
import { Button } from "../ui/Button";
import { Segmented, TextInput } from "../ui/Controls";
import {
  ClientApiError,
  clientChannel,
  clientRequestEmailActivation,
  clientSaveChannel,
  clientTelegramInvitations,
  type ChannelState,
  type ChannelUpdate,
  type TelegramInvitations,
} from "./api";
import { ClientGmailCard, googleReturnOf } from "./ClientGmailCard";

interface Failure {
  key: MessageKey;
  /** The server's own words, only where the contract says they are safe (a rule, never a value). */
  detail?: string;
}

function failureOf(err: unknown): Failure {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return { key: "auth.error.server" };
      case 403:
        return { key: "channel.error.forbidden" };
      case 409:
        return { key: "channel.error.conflict", detail: err.message };
      case 422:
        return { key: "channel.error.invalid", detail: err.message };
      case 503:
        return { key: "channel.error.unavailable" };
    }
  }
  return { key: "auth.error.server" };
}

type EmailForm = { from_name: string; subject: string };
type TelegramForm = { contact_prompt: string; contact_button: string; not_found_text: string };

const EMAIL_DEFAULTS: EmailForm = { from_name: "", subject: "" };
const TELEGRAM_DEFAULTS: TelegramForm = { contact_prompt: "", contact_button: "", not_found_text: "" };

/** The mailbox Revansa provides is the default for a client that has not chosen yet: its Sender Name
 *  is proposed (the business name) until the client confirms one. A historical mailbox is shown as
 *  stored, as it always was. */
const isPlatformEmail = (state: ChannelState) => (state.email?.mode ?? "platform") === "platform";
const nameConfirmed = (state: ChannelState) => state.email?.sender_name_confirmed === true;

function emailFormOf(state: ChannelState): EmailForm {
  const stored = state.email?.from_name ?? "";
  const from_name = isPlatformEmail(state) && !nameConfirmed(state) ? (state.sender_name_proposal ?? "") : stored;
  return { from_name, subject: state.email?.subject ?? "" };
}

function telegramFormOf(state: ChannelState): TelegramForm {
  return {
    contact_prompt: state.telegram?.contact_prompt ?? "",
    contact_button: state.telegram?.contact_button ?? "",
    not_found_text: state.telegram?.not_found_text ?? "",
  };
}

/** What is sent: only what was typed. An empty sentence is not one, the server keeps its default. */
function withoutEmpty<T extends Record<string, string>>(form: T): Partial<T> {
  return Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== "")) as Partial<T>;
}

export function ClientChannelPage() {
  const { t, locale } = useI18n();
  const [state, setState] = useState<ChannelState | null>(null);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [selected, setSelected] = useState("");
  const [email, setEmail] = useState<EmailForm>(EMAIL_DEFAULTS);
  const [telegram, setTelegram] = useState<TelegramForm>(TELEGRAM_DEFAULTS);
  const [busy, setBusy] = useState(false);
  // The Sender Name of the platform's mailbox is shown back with a preview before it is saved.
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [invitations, setInvitations] = useState<TelegramInvitations | null>(null);
  // Google sends the browser back here with `?google=connected|denied|...`: read once, then cleaned.
  const [googleReturn] = useState(() => googleReturnOf(window.location.search));
  useEffect(() => {
    if (googleReturn) window.history.replaceState(null, "", window.location.pathname);
  }, [googleReturn]);

  const apply = useCallback((next: ChannelState) => {
    setState(next);
    setSelected(next.channel);
    setEmail(emailFormOf(next));
    setTelegram(telegramFormOf(next));
    setConfirming(false);
  }, []);

  const load = useCallback(async () => {
    try {
      apply(await clientChannel());
      setLoadFailure(null);
    } catch (err) {
      setLoadFailure(failureOf(err));
    }
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    clientChannel()
      .then((value) => !cancelled && apply(value))
      .catch((err) => !cancelled && setLoadFailure(failureOf(err)));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const listInvitations = state?.channel === "telegram" && state.status === "ready";
  useEffect(() => {
    if (!listInvitations) return;
    let cancelled = false;
    clientTelegramInvitations()
      .then((value) => !cancelled && setInvitations(value))
      .catch(() => !cancelled && setInvitations(null)); // the list is a convenience: never blocks the page
    return () => {
      cancelled = true;
    };
  }, [listInvitations]);

  if (loadFailure) {
    return (
      <div className="page page--focus">
        <div className="page-head">
          <h2 className="t-h1">{t("channel.title")}</h2>
        </div>
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(loadFailure.key)}
        </p>
        <div>
          <Button onClick={() => void load()}>{t("channel.retry")}</Button>
        </div>
      </div>
    );
  }
  if (!state) {
    return (
      <div className="page page--focus">
        <p className="c-3" role="status">
          {t("channel.loading")}
        </p>
      </div>
    );
  }

  const current = state;
  const noChannel = current.options.length === 0;
  const optionLabel = (key: string) => current.options.find((o) => o.key === key)?.label ?? key;
  const platformName = selected === "email" && isPlatformEmail(current);
  // A name that is only proposed, or that was edited, is not saved until the client confirms it.
  const nameToConfirm =
    platformName && (!nameConfirmed(current) || email.from_name.trim() !== (current.email?.from_name ?? ""));
  const nameMissing = nameToConfirm && email.from_name.trim() === "";
  const dirty =
    selected !== current.channel ||
    nameToConfirm ||
    (selected === "email" && JSON.stringify(email) !== JSON.stringify(emailFormOf(current))) ||
    (selected === "telegram" && JSON.stringify(telegram) !== JSON.stringify(telegramFormOf(current)));
  const statusKey = `channel.status.${current.status}` as MessageKey;
  const activation = current.email?.activation ?? null;

  const requestActivation = () => {
    setFailure(null);
    setNotice(null);
    setBusy(true);
    clientRequestEmailActivation()
      .then((next) => {
        apply(next);
        setNotice("channel.activation.requested");
      })
      .catch((err) => setFailure(failureOf(err)))
      .finally(() => setBusy(false));
  };

  const onSave = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (nameToConfirm && !confirming) {
      if (!nameMissing) setConfirming(true);
      return;
    }
    save();
  };

  const save = () => {
    setFailure(null);
    setNotice(null);
    setBusy(true);
    const update: ChannelUpdate = { expected_version: current.version, channel: selected };
    if (selected === "email") {
      update.email = withoutEmpty(email);
      // Platform mailbox: the name is sent only when the client has just confirmed it, so that a
      // change of subject alone never re-confirms or erases it.
      if (platformName && !nameToConfirm) delete update.email.from_name;
    }
    if (selected === "telegram") update.telegram = withoutEmpty(telegram);
    clientSaveChannel(update)
      .then((next) => {
        apply(next);
        setNotice(selected !== current.channel ? "channel.saved.switched" : "channel.saved");
      })
      .catch((err) => setFailure(failureOf(err)))
      .finally(() => setBusy(false));
  };

  const emailField = (key: keyof EmailForm) => (
    <TextInput
      label={t(`channel.email.${key}` as MessageKey)}
      name={key}
      value={email[key]}
      onChange={(e) => {
        setEmail({ ...email, [key]: e.target.value });
        if (key === "from_name") setConfirming(false);
      }}
      helper={key === "from_name" && platformName ? t("channel.email.nameHelp") : undefined}
      error={key === "from_name" && platformName && nameMissing && confirming === false && email.from_name === "" ? t("channel.email.nameRequired") : undefined}
      disabled={busy}
      maxLength={150}
      autoComplete="off"
    />
  );
  const telegramField = (key: keyof TelegramForm) => (
    <TextInput
      label={t(`channel.telegram.${key}` as MessageKey)}
      name={key}
      value={telegram[key]}
      onChange={(e) => setTelegram({ ...telegram, [key]: e.target.value })}
      disabled={busy}
      maxLength={200}
    />
  );

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{t("channel.title")}</h2>
      </div>

      <div className="stack stack--lg">
        <Glass level={1} className="client-card">
          <h3 className="t-h2">{t("channel.current")}</h3>
          {noChannel ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("channel.none")}
            </p>
          ) : (
            <>
              <p data-testid="channel-status" className={current.status === "ready" ? "c-success" : "c-3"}>
                <strong>{optionLabel(current.channel)}</strong> — {t(statusKey)}
              </p>
              {current.status_message ? (
                <p className="admin-alert admin-alert--info" role="status">
                  {current.status_message}
                </p>
              ) : null}
              {current.updated_at ? (
                <p className="c-3 t-body-sm">
                  {t(current.updated_by === "administrator" ? "channel.updatedByAdmin" : "channel.updatedByYou", {
                    date: new Date(current.updated_at).toLocaleString(locale),
                  })}
                </p>
              ) : null}
            </>
          )}
        </Glass>

        {noChannel ? null : (
          <Glass level={1} as="form" className="client-card" onSubmit={onSave} aria-label={t("channel.settings")}>
            <h3 className="t-h2">{t("channel.settings")}</h3>
            <Segmented
              layoutId="seg-channel"
              label={t("channel.choose")}
              value={selected}
              onChange={(value) => {
                setFailure(null);
                setNotice(null);
                setSelected(value);
              }}
              options={current.options.map((o) => ({ value: o.key, label: o.label }))}
            />
            {selected !== current.channel ? (
              <p className="admin-alert admin-alert--info" role="status">
                {t("channel.switch.warning")}
              </p>
            ) : null}

            {selected === "email" ? <div className="grid-2">{emailField("from_name")}{emailField("subject")}</div> : null}
            {selected === "telegram" ? (
              <div className="grid-2">
                {telegramField("contact_prompt")}
                {telegramField("contact_button")}
                {telegramField("not_found_text")}
              </div>
            ) : null}

            {confirming && platformName ? (
              <div className="admin-alert admin-alert--info" role="group" aria-label={t("channel.email.confirmTitle")}>
                <p>
                  <strong>{t("channel.email.confirmTitle")}</strong>
                </p>
                <p data-testid="sender-preview">{t("channel.email.preview", { name: email.from_name.trim() })}</p>
                <div>
                  <Button type="button" variant="primary" loading={busy} disabled={busy} onClick={save}>
                    {t("channel.email.confirm")}
                  </Button>{" "}
                  <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
                    {t("channel.email.edit")}
                  </Button>{" "}
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      // Cancelling keeps what is active: the stored name, or the proposal if none was confirmed.
                      setEmail(emailFormOf(current));
                      setConfirming(false);
                    }}
                  >
                    {t("channel.email.cancel")}
                  </Button>
                </div>
              </div>
            ) : null}

            <div>
              <Button type="submit" variant="primary" loading={busy} disabled={!dirty || busy || confirming}>
                {t("channel.save")}
              </Button>
            </div>
          </Glass>
        )}

        {current.channel === "email" && current.email?.mode !== "custom" ? (
          <ClientGmailCard
            channel={current}
            onChannel={apply}
            onChanged={() => void load()}
            returned={googleReturn}
          />
        ) : null}

        {current.channel === "email" && current.email?.sending ? (
          <Glass level={1} className="client-card" aria-label={t("channel.sending.title")}>
            <h3 className="t-h2">{t("channel.sending.title")}</h3>
            <p
              data-testid="sending-state"
              className={current.email.sending.state === "ok" ? "c-success" : "admin-alert admin-alert--info"}
              role="status"
            >
              {t(`channel.sending.${current.email.sending.state}` as MessageKey, {
                sent: current.email.sending.sent_today ?? 0,
                limit: current.email.sending.daily_limit ?? 0,
              })}
            </p>
          </Glass>
        ) : null}

        {current.channel === "email" && isPlatformEmail(current) ? (
          <Glass level={1} className="client-card" aria-label={t("channel.activation.title")}>
            <h3 className="t-h2">{t("channel.activation.title")}</h3>
            {activation ? (
              <>
                <p data-testid="activation-status">
                  <span className="c-3">{t("channel.activation.status")}: </span>
                  <strong>{t(`channel.activation.${activation.status}` as MessageKey)}</strong>
                </p>
                <p className="c-3 t-body-sm">
                  {t("channel.activation.requestedOn", { date: new Date(activation.requested_at).toLocaleString(locale) })}
                  {" · "}
                  {t("channel.activation.updatedOn", { date: new Date(activation.updated_at).toLocaleString(locale) })}
                </p>
                {activation.status === "rejected" && activation.reason ? (
                  <p className="admin-alert admin-alert--danger" role="status">
                    {t("channel.activation.reason", { reason: activation.reason })}
                  </p>
                ) : null}
                {activation.status !== "active" && activation.status !== "rejected" ? (
                  <p className="c-3 t-body-sm">{t("channel.activation.blocked")}</p>
                ) : null}
              </>
            ) : (
              <p className="c-3">{t("channel.activation.intro")}</p>
            )}
            {!activation || activation.status === "rejected" ? (
              <>
                {nameConfirmed(current) ? null : <p className="c-3 t-body-sm">{t("channel.activation.needName")}</p>}
                <div>
                  <Button
                    type="button"
                    variant="primary"
                    loading={busy}
                    disabled={busy || !nameConfirmed(current) || nameToConfirm}
                    onClick={requestActivation}
                  >
                    {t(activation ? "channel.activation.requestAgain" : "channel.activation.request")}
                  </Button>
                </div>
              </>
            ) : null}
          </Glass>
        ) : null}

        {listInvitations && invitations ? (
          <Glass level={1} className="client-card">
            <h3 className="t-h2">{t("channel.invitations.title")}</h3>
            <p className="c-3">
              {t("channel.invitations.waiting", { n: invitations.waiting })}{" "}
              <a href={invitations.bot_link} target="_blank" rel="noreferrer">
                @{invitations.bot_username}
              </a>
            </p>
            <ul className="stack">
              {invitations.invitations.map((item) => (
                <li key={item.prospect_id}>
                  {item.display_name}{" "}
                  {item.link ? (
                    <a href={item.link} target="_blank" rel="noreferrer">
                      {t("channel.invitations.link")}
                    </a>
                  ) : (
                    <span className="c-3">{item.phone ?? item.email ?? ""}</span>
                  )}
                </li>
              ))}
            </ul>
          </Glass>
        ) : null}

        {failure ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
            {failure.detail && failure.key !== "channel.error.forbidden" ? ` (${failure.detail})` : ""}
          </p>
        ) : null}
        {notice ? (
          <p className="admin-alert admin-alert--success" role="status">
            {t(notice)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
