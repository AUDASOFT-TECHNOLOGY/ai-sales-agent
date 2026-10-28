/** How the agent writes on the channel in use: the name and subject of the emails, or the sentences and
 *  limits of Telegram. These are business words, not a connection: the connection itself is made on the
 *  Connections screen. Only the channel in use has wording; WhatsApp has none. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { TextInput } from "../../ui/Controls";
import {
  ClientApiError,
  clientChannel,
  clientSaveChannel,
  clientTelegramInvitations,
  type ChannelState,
  type ChannelUpdate,
  type TelegramInvitations,
} from "../api";
import { Card, Loading, Note } from "../kit";

function failureKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 403) return "channel.error.forbidden";
    if (err.status === 409) return "channel.error.conflict";
    if (err.status === 422) return "channel.error.invalid";
    if (err.status === 503) return "channel.error.unavailable";
  }
  return "auth.error.server";
}

const isPlatformEmail = (state: ChannelState) => (state.email?.mode ?? "platform") === "platform";
const nameConfirmed = (state: ChannelState) => state.email?.sender_name_confirmed === true;

/** What is sent: only what was typed. An empty sentence is not one, the server keeps its default. */
const withoutEmpty = <T extends Record<string, string>>(form: T): Partial<T> => Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== "")) as Partial<T>;

export function ChannelWordingCard() {
  const { t } = useI18n();
  const [state, setState] = useState<ChannelState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [texts, setTexts] = useState({ contact_prompt: "", contact_button: "", not_found_text: "" });
  const [maxNew, setMaxNew] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [saved, setSaved] = useState(false);
  const [invitations, setInvitations] = useState<TelegramInvitations | null>(null);

  const apply = useCallback((next: ChannelState) => {
    setState(next);
    const email = next.email;
    setName(isPlatformEmail(next) && !nameConfirmed(next) ? (next.sender_name_proposal ?? "") : (email?.from_name ?? ""));
    setSubject(email?.subject ?? "");
    setTexts({ contact_prompt: next.telegram?.contact_prompt ?? "", contact_button: next.telegram?.contact_button ?? "", not_found_text: next.telegram?.not_found_text ?? "" });
    setMaxNew(String(next.telegram?.max_new_conversations_per_day ?? ""));
    setConfirming(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    clientChannel().then(
      (next) => {
        if (cancelled) return;
        apply(next);
        setLoaded(true);
      },
      () => !cancelled && setLoaded(true),
    );
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const telegramAccount = state?.channel === "telegram" && state.telegram?.mode === "account";
  const telegramBot = state?.channel === "telegram" && !telegramAccount;
  useEffect(() => {
    let cancelled = false;
    if (telegramBot && state?.status === "ready") clientTelegramInvitations().then((v) => !cancelled && setInvitations(v), () => undefined);
    return () => {
      cancelled = true;
    };
  }, [telegramBot, state?.status]);

  if (!loaded) return <Loading />;
  if (!state || (state.channel !== "email" && state.channel !== "telegram")) return null;
  const current = state;

  const platformName = current.channel === "email" && isPlatformEmail(current);
  // A name that is only proposed, or that was edited, is not saved until the client confirms it.
  const nameToConfirm = platformName && (!nameConfirmed(current) || name.trim() !== (current.email?.from_name ?? ""));
  const dirty =
    current.channel === "email"
      ? nameToConfirm || subject !== (current.email?.subject ?? "")
      : telegramAccount
        ? maxNew !== String(current.telegram?.max_new_conversations_per_day ?? "")
        : JSON.stringify(texts) !== JSON.stringify({ contact_prompt: current.telegram?.contact_prompt ?? "", contact_button: current.telegram?.contact_button ?? "", not_found_text: current.telegram?.not_found_text ?? "" });

  function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const update: ChannelUpdate = { expected_version: current.version, channel: current.channel };
    if (current.channel === "email") {
      update.email = withoutEmpty({ from_name: name, subject });
      // The name is sent only when the client has just confirmed it, so that a change of subject alone
      // never re-confirms or erases it.
      if (platformName && !nameToConfirm) delete update.email.from_name;
    } else if (telegramAccount) {
      update.telegram = { max_new_conversations_per_day: Number(maxNew) };
    } else {
      update.telegram = withoutEmpty(texts);
    }
    clientSaveChannel(update)
      .then((next) => {
        apply(next);
        setSaved(true);
      })
      .catch((err) => setError(failureKey(err)))
      .finally(() => setBusy(false));
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (nameToConfirm && !confirming) {
      if (name.trim() !== "") setConfirming(true);
      return;
    }
    save();
  };

  const text = (key: keyof typeof texts) => (
    <TextInput label={t(`channel.telegram.${key}` as MessageKey)} name={key} value={texts[key]} onChange={(e) => setTexts({ ...texts, [key]: e.target.value })} disabled={busy} maxLength={200} />
  );

  return (
    <Card aria-label={t("rv.wording.title")}>
      <div className="rv-card__head">
        <div>
          <h3>{t("rv.wording.title")}</h3>
          <p className="rv-card__sub">{t(current.channel === "email" ? "rv.wording.email" : "rv.wording.telegram")}</p>
        </div>
      </div>
      <form className="stack" onSubmit={submit}>
        {current.channel === "email" ? (
          <div className="rv-grid-2">
            <TextInput
              label={t("channel.email.from_name")}
              name="from_name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setConfirming(false);
              }}
              helper={platformName ? t("channel.email.nameHelp") : undefined}
              error={platformName && nameToConfirm && name.trim() === "" ? t("channel.email.nameRequired") : undefined}
              disabled={busy}
              maxLength={150}
              autoComplete="off"
            />
            <TextInput label={t("channel.email.subject")} name="subject" value={subject} onChange={(e) => setSubject(e.target.value)} disabled={busy} maxLength={150} autoComplete="off" />
          </div>
        ) : null}
        {telegramBot ? <div className="rv-grid-2">{text("contact_prompt")}{text("contact_button")}{text("not_found_text")}</div> : null}
        {telegramAccount ? (
          <>
            <Note tone="warn" title={t("channel.tgaccount.warning.title")}>
              {t("channel.tgaccount.warning.text")}
            </Note>
            <TextInput label={t("channel.tgaccount.maxNew")} name="max_new" type="number" min={1} max={5} value={maxNew} onChange={(e) => setMaxNew(e.target.value)} helper={t("channel.tgaccount.maxNewHelp")} disabled={busy} />
          </>
        ) : null}

        {confirming && platformName ? (
          <Note tone="info" title={t("channel.email.confirmTitle")}>
            <span data-testid="sender-preview">{t("channel.email.preview", { name: name.trim() })}</span>
            <span className="rv-actions" style={{ marginBlockStart: 8 }}>
              <Button type="button" size="sm" variant="primary" loading={busy} onClick={save}>
                {t("channel.email.confirm")}
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
                {t("channel.email.edit")}
              </Button>
            </span>
          </Note>
        ) : null}

        <div>
          <Button type="submit" variant="primary" loading={busy} disabled={!dirty || confirming || (telegramAccount && !(Number(maxNew) >= 1 && Number(maxNew) <= 5))}>
            {t("channel.save")}
          </Button>
        </div>
        {error ? <Note tone="danger">{t(error)}</Note> : null}
        {saved ? <Note tone="success">{t("channel.saved")}</Note> : null}
      </form>

      {invitations && invitations.invitations.length > 0 ? (
        <div className="stack">
          <h4 className="t-h3">{t("channel.invitations.title")}</h4>
          <p className="c-3 t-body-sm">
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
        </div>
      ) : null}
    </Card>
  );
}
