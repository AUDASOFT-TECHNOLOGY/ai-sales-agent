/** The client's independent mailbox (Mode B): its status in words, its address, and the login the
 *  Revansa team chose to communicate. The password is shown only after an explicit click, for a
 *  minute, then hidden and dropped. Never a server, port or security mode.
 *
 *  Strings live here (not in client.en/fr.ts, which another change is editing); move them to the
 *  dictionaries when integrating. Integration: render `<ClientMailboxCard />` in ClientChannelPage
 *  under the email settings when `channel.email?.mode === "platform"`. */

import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { clientMailbox, clientRevealMailboxPassword, MailboxApiError, type MailboxInfo, type MailboxStatus } from "./mailbox";

const TEXT = {
  en: {
    title: "Your mailbox",
    status: {
      not_requested: "Not requested",
      pending: "Request pending",
      in_progress: "Being set up",
      active: "Active",
      error: "Needs attention",
      rejected: "Not accepted",
    } as Record<MailboxStatus, string>,
    address: "Address",
    login: "Login",
    password: "Password",
    show: "Show the password",
    hide: "Hide",
    hidden: "Hidden until you ask for it.",
    limited: "You asked for the password several times already. Try again later.",
    refused: "This is not available to you.",
    failed: "Could not show the password. Try again in a moment.",
    reason: "Reason",
  },
  fr: {
    title: "Votre boîte email",
    status: {
      not_requested: "Non demandée",
      pending: "Demande en attente",
      in_progress: "En cours de traitement",
      active: "Active",
      error: "À vérifier",
      rejected: "Non acceptée",
    } as Record<MailboxStatus, string>,
    address: "Adresse",
    login: "Identifiant",
    password: "Mot de passe",
    show: "Afficher le mot de passe",
    hide: "Masquer",
    hidden: "Masqué tant que vous ne le demandez pas.",
    limited: "Vous avez déjà demandé le mot de passe plusieurs fois. Réessayez plus tard.",
    refused: "Ceci n'est pas disponible pour vous.",
    failed: "Impossible d'afficher le mot de passe. Réessayez dans un instant.",
    reason: "Motif",
  },
};

export function ClientMailboxCard() {
  const { locale } = useI18n();
  const text = TEXT[locale === "fr" ? "fr" : "en"];
  const [info, setInfo] = useState<MailboxInfo | null>(null);
  const [password, setPassword] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    clientMailbox()
      .then((value) => !cancelled && setInfo(value))
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (!info || info.status === "not_requested") return null;

  async function reveal() {
    setBusy(true);
    setError(null);
    try {
      const shown = await clientRevealMailboxPassword();
      setPassword(shown.password);
      timer.current = setTimeout(() => setPassword(null), shown.shown_for_seconds * 1000);
    } catch (err) {
      const status = err instanceof MailboxApiError ? err.status : 0;
      setError(status === 429 ? text.limited : status === 403 ? text.refused : text.failed);
    } finally {
      setBusy(false);
    }
  }

  function hide() {
    if (timer.current) clearTimeout(timer.current);
    setPassword(null);
  }

  return (
    <Glass as="section" aria-label={text.title}>
      <h2>{text.title}</h2>
      <p role={info.status === "error" ? "alert" : "status"}>
        <strong>{text.status[info.status]}</strong> {info.message}
      </p>
      {info.status === "rejected" && info.reason ? (
        <p>
          {text.reason}: {info.reason}
        </p>
      ) : null}
      {info.status === "active" && info.address ? (
        <dl>
          <dt>{text.address}</dt>
          <dd dir="ltr">{info.address}</dd>
          {info.login ? (
            <>
              <dt>{text.login}</dt>
              <dd dir="ltr">{info.login}</dd>
            </>
          ) : null}
          {info.password_revealable ? (
            <>
              <dt>{text.password}</dt>
              <dd dir="ltr">{password ?? text.hidden}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {info.password_revealable ? (
        password ? (
          <Button size="sm" onClick={hide}>
            {text.hide}
          </Button>
        ) : (
          <Button size="sm" loading={busy} onClick={() => void reveal()}>
            {text.show}
          </Button>
        )
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </Glass>
  );
}
