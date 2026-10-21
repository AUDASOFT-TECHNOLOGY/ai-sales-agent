/** The client's independent mailbox (Mode B): its status in words, its address, and the login the
 *  Revansa team chose to communicate. The password is shown only after an explicit click, for a
 *  minute, then hidden and dropped. Never a server, port or security mode.
 *  Rendered by ClientChannelPage when the email mode is the platform's mailbox. */

import { useEffect, useRef, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { clientMailbox, clientRevealMailboxPassword, MailboxApiError, type MailboxInfo } from "./mailbox";

export function ClientMailboxCard({ showNotRequested = false }: { showNotRequested?: boolean } = {}) {
  const { t } = useI18n();
  const [loadFailed, setLoadFailed] = useState(false);
  const [info, setInfo] = useState<MailboxInfo | null>(null);
  const [password, setPassword] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    clientMailbox()
      .then((value) => !cancelled && setInfo(value))
      .catch(() => !cancelled && setLoadFailed(true));
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (!info && loadFailed && showNotRequested) {
    return (
      <Glass as="section" aria-label={t("rv.mailbox.title")}>
        <h2>{t("rv.mailbox.title")}</h2>
        <p role="alert">{t("rv.mailbox.unavailable")}</p>
      </Glass>
    );
  }
  if (!info && showNotRequested) {
    return (
      <Glass as="section" aria-label={t("rv.mailbox.title")}>
        <h2>{t("rv.mailbox.title")}</h2>
        <p role="status">{t("rv.mailbox.loading")}</p>
      </Glass>
    );
  }
  if (!info || (info.status === "not_requested" && !showNotRequested)) return null;

  async function reveal() {
    setBusy(true);
    setError(null);
    try {
      const shown = await clientRevealMailboxPassword();
      setPassword(shown.password);
      timer.current = setTimeout(() => setPassword(null), shown.shown_for_seconds * 1000);
    } catch (err) {
      const status = err instanceof MailboxApiError ? err.status : 0;
      setError(status === 429 ? t("rv.mailbox.limited") : status === 403 ? t("rv.mailbox.refused") : t("rv.mailbox.failed"));
    } finally {
      setBusy(false);
    }
  }

  function hide() {
    if (timer.current) clearTimeout(timer.current);
    setPassword(null);
  }

  return (
    <Glass as="section" aria-label={t("rv.mailbox.title")}>
      <h2>{t("rv.mailbox.title")}</h2>
      <p role={info.status === "error" ? "alert" : "status"}>
        <strong>{t(`rv.mailbox.status.${info.status}` as MessageKey)}</strong> {info.message}
      </p>
      {info.status === "rejected" && info.reason ? (
        <p>
          {t("rv.mailbox.reason")}: {info.reason}
        </p>
      ) : null}
      {info.status === "active" && info.address ? (
        <dl>
          <dt>{t("rv.mailbox.address")}</dt>
          <dd dir="ltr">{info.address}</dd>
          {info.login ? (
            <>
              <dt>{t("rv.mailbox.login")}</dt>
              <dd dir="ltr">{info.login}</dd>
            </>
          ) : null}
          {info.password_revealable ? (
            <>
              <dt>{t("rv.mailbox.password")}</dt>
              <dd dir="ltr">{password ?? t("rv.mailbox.hidden")}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {info.password_revealable ? (
        password ? (
          <Button size="sm" onClick={hide}>
            {t("rv.mailbox.hide")}
          </Button>
        ) : (
          <Button size="sm" loading={busy} onClick={() => void reveal()}>
            {t("rv.mailbox.show")}
          </Button>
        )
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </Glass>
  );
}
