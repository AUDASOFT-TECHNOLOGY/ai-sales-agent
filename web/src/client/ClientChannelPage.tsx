/** Channel: how the client talks to its prospects. It lists only the channels the Admin allows,
 *  lets the client choose Email or Telegram, and takes the settings and the secret that channel
 *  needs. A secret is write-only: the server says whether one is stored, never what it is, and this
 *  page empties the field the moment it is sent. The test is a real call to the real service (the
 *  server never simulates one): what is shown is exactly what it answered, and nothing is shown as
 *  a success unless it was. Settings must be saved before they can be tested. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Glass } from "../ui/Glass";
import { Button } from "../ui/Button";
import { Segmented, TextInput } from "../ui/Controls";
import {
  ClientApiError,
  clientChannel,
  clientConnectTelegram,
  clientRemoveCredential,
  clientSaveChannel,
  clientSaveCredential,
  clientTelegramInvitations,
  clientTestChannel,
  type ChannelState,
  type ChannelTestResult,
  type ChannelUpdate,
  type TelegramInvitations,
} from "./api";

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
      case 429:
        return { key: "channel.error.tooMany" };
      case 502:
        return { key: "channel.error.upstream" };
      case 503:
        return { key: "channel.error.unavailable" };
    }
  }
  return { key: "auth.error.server" };
}

type EmailForm = {
  from_address: string;
  from_name: string;
  username: string;
  smtp_host: string;
  smtp_port: string;
  smtp_security: string;
  imap_host: string;
  imap_port: string;
  imap_security: string;
  mailbox: string;
  subject: string;
};
type TelegramForm = { contact_prompt: string; contact_button: string; not_found_text: string };

const EMAIL_DEFAULTS: EmailForm = {
  from_address: "",
  from_name: "",
  username: "",
  smtp_host: "",
  smtp_port: "587",
  smtp_security: "starttls",
  imap_host: "",
  imap_port: "993",
  imap_security: "ssl",
  mailbox: "INBOX",
  subject: "",
};

function emailFormOf(state: ChannelState): EmailForm {
  const form = { ...EMAIL_DEFAULTS };
  if (state.email) {
    for (const key of Object.keys(form) as Array<keyof EmailForm>) {
      const value = state.email[key];
      if (value !== null && value !== undefined) form[key] = String(value);
    }
  }
  return form;
}

function telegramFormOf(state: ChannelState): TelegramForm {
  return {
    contact_prompt: state.telegram?.contact_prompt ?? "",
    contact_button: state.telegram?.contact_button ?? "",
    not_found_text: state.telegram?.not_found_text ?? "",
  };
}

/** What is sent for the mailbox: what was typed, ports as numbers, empty fields left out. */
function emailChanges(form: EmailForm): NonNullable<ChannelUpdate["email"]> {
  const out: NonNullable<ChannelUpdate["email"]> = {};
  for (const key of Object.keys(form) as Array<keyof EmailForm>) {
    const value = form[key].trim();
    if (!value) continue;
    out[key] = key === "smtp_port" || key === "imap_port" ? Number(value) : value;
  }
  return out;
}

export function ClientChannelPage() {
  const { t, locale } = useI18n();
  const [state, setState] = useState<ChannelState | null>(null);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [selected, setSelected] = useState("");
  const [email, setEmail] = useState<EmailForm>(EMAIL_DEFAULTS);
  const [telegram, setTelegram] = useState<TelegramForm>({ contact_prompt: "", contact_button: "", not_found_text: "" });
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<null | "save" | "secret" | "remove" | "test" | "connect">(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [result, setResult] = useState<ChannelTestResult | null>(null);
  const [invitations, setInvitations] = useState<TelegramInvitations | null>(null);

  const apply = useCallback((next: ChannelState, resetForms: boolean) => {
    setState(next);
    if (resetForms) {
      setSelected(next.channel);
      setEmail(emailFormOf(next));
      setTelegram(telegramFormOf(next));
    }
  }, []);

  const load = useCallback(async () => {
    try {
      apply(await clientChannel(), true);
      setLoadFailure(null);
    } catch (err) {
      setLoadFailure(failureOf(err));
    }
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    clientChannel()
      .then((value) => !cancelled && apply(value, true))
      .catch((err) => !cancelled && setLoadFailure(failureOf(err)));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const connected = state?.channel === "telegram" && state.telegram?.connected === true;
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;
    clientTelegramInvitations()
      .then((value) => !cancelled && setInvitations(value))
      .catch(() => !cancelled && setInvitations(null)); // the list is a convenience: never blocks the page
    return () => {
      cancelled = true;
    };
  }, [connected]);

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
  const onSavedChannel = selected === current.channel && current.authorized;
  const dirty =
    selected !== current.channel ||
    (selected === "email" && JSON.stringify(email) !== JSON.stringify(emailFormOf(current))) ||
    (selected === "telegram" && JSON.stringify(telegram) !== JSON.stringify(telegramFormOf(current)));
  const secretField = selected === "telegram" ? "bot_token" : "password";
  const secretStored = onSavedChannel && current.credential?.configured === true;

  function reset() {
    setFailure(null);
    setNotice(null);
    setResult(null);
  }

  async function run(kind: NonNullable<typeof busy>, action: () => Promise<void>) {
    reset();
    setBusy(kind);
    try {
      await action();
    } catch (err) {
      setFailure(failureOf(err));
    } finally {
      setBusy(null);
    }
  }

  const onSave = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run("save", async () => {
      const update: ChannelUpdate = { expected_version: current.version, channel: selected };
      if (selected === "email") update.email = emailChanges(email);
      if (selected === "telegram") {
        // An empty sentence is not one: the server keeps its default for what is not sent.
        update.telegram = Object.fromEntries(Object.entries(telegram).filter(([, v]) => v.trim() !== ""));
      }
      apply(await clientSaveChannel(update), true);
      setNotice(selected !== current.channel ? "channel.saved.switched" : "channel.saved");
    });
  };

  const onSaveSecret = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = secret;
    setSecret(""); // gone from the page whatever the answer is
    void run("secret", async () => {
      await clientSaveCredential(secretField, value);
      apply(await clientChannel(), false);
      setNotice("channel.secret.saved");
    });
  };

  const onRemoveSecret = () =>
    void run("remove", async () => {
      await clientRemoveCredential();
      apply(await clientChannel(), false);
      setNotice("channel.secret.removed");
    });

  const onTest = () =>
    void run("test", async () => {
      setResult(await clientTestChannel());
    });

  const onConnect = () =>
    void run("connect", async () => {
      setResult(await clientConnectTelegram());
      apply(await clientChannel(), false);
    });

  const emailField = (key: keyof EmailForm, extra: { type?: string; helper?: string; required?: boolean } = {}) => (
    <TextInput
      label={t(`channel.email.${key}` as MessageKey)}
      name={key}
      value={email[key]}
      onChange={(e) => setEmail({ ...email, [key]: e.target.value })}
      disabled={busy !== null}
      helper={extra.helper}
      type={extra.type}
      required={extra.required}
      autoComplete="off"
    />
  );
  const security = (key: "smtp_security" | "imap_security") => (
    <div className="field">
      <p className="t-label c-2">{t(`channel.email.${key}` as MessageKey)}</p>
      <Segmented
        layoutId={`seg-${key}`}
        label={t(`channel.email.${key}` as MessageKey)}
        value={email[key]}
        onChange={(value) => setEmail({ ...email, [key]: value })}
        options={[
          { value: "starttls", label: "STARTTLS" },
          { value: "ssl", label: "SSL/TLS" },
        ]}
      />
    </div>
  );
  const telegramField = (key: keyof TelegramForm) => (
    <TextInput
      label={t(`channel.telegram.${key}` as MessageKey)}
      name={key}
      value={telegram[key]}
      onChange={(e) => setTelegram({ ...telegram, [key]: e.target.value })}
      disabled={busy !== null}
      maxLength={200}
    />
  );

  const status = !current.authorized
    ? "channel.status.notAuthorized"
    : current.problem
      ? "channel.status.notConfigured"
      : "channel.status.ready";

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
              <p data-testid="channel-status" className={status === "channel.status.ready" ? "c-success" : "c-3"}>
                <strong>{optionLabel(current.channel)}</strong> — {t(status)}
              </p>
              {current.problem ? (
                <p className="admin-alert admin-alert--info" role="status">
                  {current.problem}
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
                reset();
                setSelected(value);
              }}
              options={current.options.map((o) => ({ value: o.key, label: o.label }))}
            />
            {selected !== current.channel ? (
              <p className="admin-alert admin-alert--info" role="status">
                {t("channel.switch.warning")}
              </p>
            ) : null}

            {selected === "email" ? (
              <div className="grid-2">
                {emailField("from_address", { type: "email", required: true })}
                {emailField("from_name")}
                {emailField("username", { required: true })}
                {emailField("subject")}
                {emailField("smtp_host", { required: true })}
                {emailField("smtp_port", { type: "number" })}
                {security("smtp_security")}
                {emailField("imap_host", { required: true })}
                {emailField("imap_port", { type: "number" })}
                {security("imap_security")}
                {emailField("mailbox")}
              </div>
            ) : null}
            {selected === "telegram" ? <div className="grid-2">{telegramField("contact_prompt")}{telegramField("contact_button")}{telegramField("not_found_text")}</div> : null}

            <div>
              <Button type="submit" variant="primary" loading={busy === "save"} disabled={!dirty || (busy !== null && busy !== "save")}>
                {t("channel.save")}
              </Button>
            </div>
          </Glass>
        )}

        {noChannel || !(selected === "email" || selected === "telegram") ? null : (
          <Glass level={1} as="form" className="client-card" onSubmit={onSaveSecret} aria-label={t("channel.secret.title")}>
            <h3 className="t-h2">{t(selected === "telegram" ? "channel.secret.token" : "channel.secret.password")}</h3>
            {!onSavedChannel ? (
              <p className="c-3" role="status">
                {t("channel.secret.saveFirst")}
              </p>
            ) : (
              <>
                <p data-testid="secret-status" className={secretStored ? "c-success" : "c-3"}>
                  {secretStored ? t("channel.secret.stored") : t("channel.secret.missing")}
                </p>
                <TextInput
                  label={t(selected === "telegram" ? "channel.secret.token" : "channel.secret.password")}
                  name={secretField}
                  type="password"
                  autoComplete="new-password"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  disabled={busy !== null}
                  helper={t(secretStored ? "channel.secret.replaceHelper" : "channel.secret.helper")}
                />
                <div className="row">
                  <Button type="submit" variant="primary" loading={busy === "secret"} disabled={secret.trim() === "" || (busy !== null && busy !== "secret")}>
                    {t(secretStored ? "channel.secret.replace" : "channel.secret.save")}
                  </Button>
                  {secretStored ? (
                    <Button variant="danger" onClick={onRemoveSecret} loading={busy === "remove"} disabled={busy !== null && busy !== "remove"}>
                      {t("channel.secret.remove")}
                    </Button>
                  ) : null}
                </div>
              </>
            )}
          </Glass>
        )}

        {noChannel || !(selected === "email" || selected === "telegram") ? null : (
          <Glass level={1} className="client-card">
            <h3 className="t-h2">{t("channel.test.title")}</h3>
            <p className="c-3">{t(selected === "telegram" ? "channel.test.telegram" : "channel.test.email")}</p>
            {onSavedChannel && dirty ? (
              <p className="c-3" role="status">
                {t("channel.test.unsaved")}
              </p>
            ) : null}
            <div className="row">
              {selected === "telegram" ? (
                <Button
                  onClick={onConnect}
                  loading={busy === "connect"}
                  disabled={busy !== null && busy !== "connect"}
                  disabledReason={!onSavedChannel || dirty || !secretStored ? t("channel.test.needsSaved") : undefined}
                >
                  {t(current.telegram?.connected ? "channel.connect.again" : "channel.connect")}
                </Button>
              ) : null}
              <Button
                variant="primary"
                onClick={onTest}
                loading={busy === "test"}
                disabled={busy !== null && busy !== "test"}
                disabledReason={!onSavedChannel || dirty || !secretStored ? t("channel.test.needsSaved") : undefined}
              >
                {t("channel.test.run")}
              </Button>
            </div>
            {result ? (
              <div className={`admin-alert admin-alert--${result.ok ? "success" : "danger"}`} role={result.ok ? "status" : "alert"} data-testid="test-result">
                <p>
                  <strong>{t(result.ok ? "channel.test.ok" : "channel.test.failed")}</strong> {result.message}
                </p>
                {Object.entries(result.details).map(([name, value]) => (
                  <p key={name} className="t-body-sm">
                    {name}: {value}
                  </p>
                ))}
                <p className="t-body-sm">{new Date(result.checked_at).toLocaleString(locale)}</p>
              </div>
            ) : null}
          </Glass>
        )}

        {connected && invitations ? (
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
