/** Who is in charge of a conversation, and what a person can do about it: the banner (waiting for a
 *  person / you are handling it), taking over and giving back, and the box to write to the prospect
 *  yourself. Everything is decided by the server (`client_conversation_control.py`): this only asks,
 *  and says in plain words what the server answered. Nothing is stored in the browser. */

import { useRef, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import {
  ClientApiError,
  clientReleaseConversation,
  clientSendMessage,
  clientTakeOver,
  type ConversationControl,
} from "../client/api";
import { crmFailureOf } from "../client/crmFailure";
import { Button } from "../ui/Button";
import { Textarea } from "../ui/Controls";

export const MAX_MESSAGE = 4000;

/** Refusals that certainly sent nothing: the next try is a new message and gets a new key. Any other
 *  failure (network, 5xx, unconfirmed) keeps the key, so pressing Send again can never send twice. */
const DEFINITE_REFUSALS = new Set([
  "not_in_human_mode",
  "channel_not_ready",
  "opted_out",
  "no_contact",
  "not_linked",
  "no_channel",
  "not_now",
  "send_failed",
  "dry_run",
  "empty",
  "unknown_prospect",
  "agent_not_loaded",
]);

const SEND_ERRORS = new Set([...DEFINITE_REFUSALS, "unconfirmed"]);

function newKey(): string {
  const uuid = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `msg-${uuid}`;
}

/** The sentence for a failed send, in the interface language. The server's own words are shown only as
 *  a detail for the refusals that come from the channel, whose reason the client may need. */
export function sendErrorOf(error: unknown): { key: MessageKey; detail: string | null; definite: boolean; signedOut: boolean } {
  if (error instanceof ClientApiError) {
    if (error.status === 401) return { key: "convo.ctl.err.generic", detail: null, definite: true, signedOut: true };
    if (error.status === 0) return { key: "convo.ctl.err.network", detail: null, definite: false, signedOut: false };
    const code = error.code;
    if (code && SEND_ERRORS.has(code)) {
      const fromChannel = code === "channel_not_ready" || code === "not_now" || code === "send_failed";
      return { key: `convo.ctl.err.${code}` as MessageKey, detail: fromChannel ? error.message : null, definite: DEFINITE_REFUSALS.has(code), signedOut: false };
    }
    if (error.status === 422) return { key: "convo.ctl.err.generic", detail: null, definite: true, signedOut: false };
  }
  return { key: "convo.ctl.err.generic", detail: null, definite: false, signedOut: false };
}

interface BarProps {
  id: string;
  control: ConversationControl | null;
  /** The prospect's status, when known: a person who opted out cannot be written to. */
  status: string | null;
  /** Called after the server changed who is in charge, so the conversation is read again. */
  onChanged: () => void;
}

export function ControlBar({ id, control, status, onChanged }: BarProps) {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [busy, setBusy] = useState<"take" | "release" | null>(null);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (control === null) return null; // not known: say nothing rather than guess

  const act = async (kind: "take" | "release") => {
    setBusy(kind);
    setProblem(null);
    setNotice(null);
    try {
      if (kind === "take") {
        await clientTakeOver(id);
      } else {
        const released = await clientReleaseConversation(id);
        if (released.status === "opted_out") setNotice(t("convo.ctl.released.optedOut"));
        else if (released.next_follow_up_at) setNotice(t("convo.ctl.released.next", { when: d(released.next_follow_up_at, { dateStyle: "medium", timeStyle: "short" }) }));
        else setNotice(t("convo.ctl.released.none"));
      }
      onChanged();
    } catch (error) {
      const failure = crmFailureOf(error);
      if (failure.signedOut) void logout();
      setProblem(failure.key === "crm.error.notFound" || failure.key === "crm.error.notReady" || failure.key === "crm.error.unavailable" ? failure.key : kind === "take" ? "convo.ctl.error.takeOver" : "convo.ctl.error.release");
    } finally {
      setBusy(null);
    }
  };

  const human = control.mode === "human";
  const waiting = human && control.awaiting_human;
  return (
    <div className="control-bar" data-mode={human ? (waiting ? "waiting" : "human") : "agent"} data-testid="control-bar">
      <div className="control-bar__text">
        <p className={waiting ? "t-body-sm ledger__strong" : "t-body-sm"} role="status" data-testid="control-banner">
          {waiting ? t("convo.ctl.banner.waiting") : human ? t("convo.ctl.banner.human") : t("convo.ctl.banner.agent")}
        </p>
        {human && control.reason ? (
          <p className="c-3 t-body-sm" dir="auto" data-testid="control-reason">
            {t("convo.ctl.banner.reason", { reason: control.reason })}
          </p>
        ) : null}
        {notice ? (
          <p className="c-3 t-body-sm" role="status" data-testid="control-notice">
            {notice}
          </p>
        ) : null}
        {problem ? (
          <p className="admin-alert admin-alert--danger" role="alert" data-testid="control-error">
            {t(problem)}
          </p>
        ) : null}
      </div>
      {human && !waiting ? (
        <Button variant="secondary" size="sm" loading={busy === "release"} disabled={busy !== null} onClick={() => void act("release")}>
          {t("convo.ctl.release")}
        </Button>
      ) : (
        <Button variant={waiting ? "primary" : "secondary"} size="sm" loading={busy === "take"} disabled={busy !== null || status === "closed"} onClick={() => void act("take")}>
          {t("convo.ctl.takeOver")}
        </Button>
      )}
      {waiting ? (
        <Button variant="ghost" size="sm" loading={busy === "release"} disabled={busy !== null} onClick={() => void act("release")}>
          {t("convo.ctl.release")}
        </Button>
      ) : null}
    </div>
  );
}

interface ComposerProps {
  id: string;
  name: string;
  /** The prospect asked to stop: writing is closed, and the reason is said. */
  optedOut: boolean;
  onSent: () => void;
}

export function Composer({ id, name, optedOut, onSent }: ComposerProps) {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<{ key: MessageKey; detail: string | null } | null>(null);
  const [sent, setSent] = useState(false);
  // One key per message being written: it changes only once the message went out, or was refused for sure.
  const key = useRef<string>(newKey());

  if (optedOut) {
    return (
      <p className="admin-alert admin-alert--info" data-testid="composer-closed">
        {t("convo.ctl.optedOut")}
      </p>
    );
  }

  const empty = text.trim().length === 0;
  const send = async () => {
    if (empty || sending) return;
    setSending(true);
    setFailure(null);
    setSent(false);
    try {
      await clientSendMessage(id, text.trim(), key.current);
      key.current = newKey();
      setText("");
      setSent(true);
      onSent();
    } catch (error) {
      const found = sendErrorOf(error);
      if (found.signedOut) void logout();
      if (found.definite) key.current = newKey();
      setFailure({ key: found.key, detail: found.detail });
    } finally {
      setSending(false);
    }
  };

  return (
    <form
      className="composer stack"
      data-testid="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <Textarea
        label={t("convo.ctl.composer.label", { name })}
        helper={t("convo.ctl.composer.help")}
        value={text}
        maxLength={MAX_MESSAGE}
        onChange={(e) => {
          setText(e.target.value);
          setSent(false);
        }}
        dir="auto"
      />
      {failure ? (
        <p className="admin-alert admin-alert--danger" role="alert" data-testid="composer-error">
          {t(failure.key)}
          {failure.detail ? ` (${failure.detail})` : ""}
        </p>
      ) : null}
      {sent ? (
        <p className="admin-alert admin-alert--success" role="status" data-testid="composer-sent">
          {t("convo.ctl.sent")}
        </p>
      ) : null}
      <div>
        <Button type="submit" variant="primary" loading={sending} disabledReason={empty && !sending ? t("convo.ctl.sendEmpty") : undefined}>
          {t("convo.ctl.send")}
        </Button>
      </div>
    </form>
  );
}
