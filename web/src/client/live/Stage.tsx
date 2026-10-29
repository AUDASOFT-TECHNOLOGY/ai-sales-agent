/** The agent's "screen": what it does now in large, the steps it takes for that person, and the
 *  conversation as it unfolds. The client watches; the two controls (take over, pause) each ask first. */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  BookOpen,
  Brain,
  Check,
  Circle,
  CircleAlert,
  FileText,
  Hand,
  History,
  Inbox,
  LoaderCircle,
  MessageSquare,
  Moon,
  Pause,
  PenLine,
  RefreshCw,
  Send,
  ShieldCheck,
  Sheet,
  type LucideIcon,
} from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { clientPauseAgent, clientTakeOver } from "../api";
import { Button } from "../../ui/Button";
import {
  activeFor,
  checklist,
  clock,
  idleSeconds,
  initials,
  isWriting,
  runFor,
  secondsOf,
  stepKey,
  type LiveAnswer,
  type LiveStep,
} from "./liveModel";
import { useFollowedConversation } from "./useLive";

const ICONS: Record<string, LucideIcon> = {
  reading_prospects: Sheet,
  planning: BookOpen,
  checking: ShieldCheck,
  reading_history: History,
  thinking: Brain,
  answering: PenLine,
  sending: Send,
  receiving: MessageSquare,
  checking_inbox: Inbox,
  syncing: RefreshCw,
  reporting: FileText,
};

const CHECKS: Record<string, MessageKey> = {
  checking: "rv.live.c.checking",
  reading_history: "rv.live.c.reading_history",
  thinking: "rv.live.c.thinking",
  answering: "rv.live.c.answering",
  sending: "rv.live.c.sending",
};

export function Sentence({ step }: { step: LiveStep }) {
  const { t } = useI18n();
  return <>{t(stepKey(step.kind), { name: step.name ?? t("rv.live.someone"), detail: step.detail ?? "" })}</>;
}

/* ── Controls: each action asks before it does anything ── */

type Ask = "takeover" | "pause" | null;

function Controls({ prospectId, name, paused, onPaused }: { prospectId: string | null; name: string | null; paused: boolean; onPaused: () => void }) {
  const { t } = useI18n();
  // What was asked and told belongs to the person it was about: following someone else clears it.
  const [asked, setAsk] = useState<{ what: Ask; about: string | null }>({ what: null, about: null });
  const [busy, setBusy] = useState(false);
  const [told, setNote] = useState<{ what: "takeover" | "pause" | "failed"; about: string | null } | null>(null);
  const ask = asked.about === prospectId ? asked.what : null;
  const note = told && told.about === prospectId ? told.what : null;
  const who = name ?? t("rv.live.someone");

  const confirm = async () => {
    const what = ask;
    setBusy(true);
    try {
      if (what === "takeover" && prospectId) await clientTakeOver(prospectId);
      if (what === "pause") {
        await clientPauseAgent();
        onPaused();
      }
      if (what) setNote({ what, about: prospectId });
    } catch {
      setNote({ what: "failed", about: prospectId });
    } finally {
      setBusy(false);
      setAsk({ what: null, about: prospectId });
    }
  };

  if (ask) {
    return (
      <div className="lv-ask" role="alertdialog" aria-label={t(ask === "takeover" ? "rv.live.takeover" : "rv.live.pause")}>
        <p>{ask === "takeover" ? t("rv.live.takeover.ask", { name: who }) : t("rv.live.pause.ask")}</p>
        <div className="lv-ask__actions">
          <Button variant="primary" onClick={() => void confirm()} disabled={busy}>
            {t(ask === "takeover" ? "rv.live.takeover.yes" : "rv.live.pause.yes")}
          </Button>
          <Button onClick={() => setAsk({ what: null, about: prospectId })} disabled={busy}>
            {t("rv.live.cancel")}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="lv-controls">
      {prospectId ? (
        <Button size="sm" leading={<Hand size={15} aria-hidden />} onClick={() => setAsk({ what: "takeover", about: prospectId })}>
          {t("rv.live.takeover")}
        </Button>
      ) : null}
      {!paused ? (
        <Button size="sm" leading={<Pause size={15} aria-hidden />} onClick={() => setAsk({ what: "pause", about: prospectId })}>
          {t("rv.live.pause")}
        </Button>
      ) : null}
      {note ? (
        <p className="lv-note" role="status" data-tone={note === "failed" ? "warn" : "ok"}>
          {note === "takeover" ? t("rv.live.takeover.done", { name: who }) : note === "pause" ? t("rv.live.pause.done") : t("rv.live.control.failed")}
        </p>
      ) : null}
    </div>
  );
}

/* ── The stage ── */

function Avatar({ step }: { step: LiveStep }) {
  const Icon = ICONS[step.kind] ?? CircleAlert;
  return step.prospect_id ? (
    <span className="lv-avatar" aria-hidden>
      {initials(step.name)}
      <span className="lv-avatar__badge">
        <Icon size={14} />
      </span>
    </span>
  ) : (
    <span className="lv-tile" aria-hidden>
      <Icon size={30} />
    </span>
  );
}

function Checklist({ answer, prospectId, name, serverNow }: { answer: LiveAnswer; prospectId: string; name: string | null; serverNow: number }) {
  const { t } = useI18n();
  const items = checklist(runFor(answer, prospectId), serverNow);
  return (
    <div className="lv-check">
      <p className="lv-eyebrow">{t("rv.live.cl.title", { name: name ?? t("rv.live.someone") })}</p>
      <ol>
        {items.map((item) => {
          const Icon = item.state === "done" ? Check : item.state === "running" ? LoaderCircle : item.state === "failed" ? CircleAlert : Circle;
          return (
            <li key={item.kind} data-state={item.state}>
              <span className="lv-check__mark" aria-hidden>
                <Icon size={14} />
              </span>
              <span className="lv-check__text">{t(CHECKS[item.kind] ?? "rv.live.k.other")}</span>
              <span className="lv-check__time tabular">{item.seconds !== null ? clock(item.seconds) : ""}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Stage({ answer, primary, serverNow }: { answer: LiveAnswer; primary: LiveStep | null; serverNow: number }) {
  const { t, n } = useI18n();
  if (!primary) {
    const idle = idleSeconds(answer, serverNow);
    const today = answer.today;
    return (
      <div className="lv-stage lv-stage--idle" data-testid="live-idle">
        <span className="lv-tile lv-tile--calm" aria-hidden>
          <Moon size={30} />
        </span>
        <div className="lv-stage__body">
          <p className="lv-eyebrow">{t("rv.live.now")}</p>
          <h2 className="lv-sentence">{t("rv.live.stage.idle")}</h2>
          <p className="c-2">{t("rv.live.stage.idle.text")}</p>
          <ul className="lv-facts">
            {idle !== null ? <li>{t("rv.live.lastAt", { time: clock(idle) })}</li> : null}
            {today && today.sent !== null ? (
              <li>{today.limit !== null ? t("rv.live.today", { sent: n(today.sent), limit: n(today.limit) }) : t("rv.live.today.nolimit", { sent: n(today.sent) })}</li>
            ) : null}
          </ul>
        </div>
      </div>
    );
  }
  const others = answer.now.filter((s) => s.id !== primary.id);
  return (
    <div className="lv-stage" data-kind={primary.kind} data-testid="live-now">
      <Avatar step={primary} />
      <div className="lv-stage__body">
        <p className="lv-eyebrow">{t("rv.live.now")}</p>
        <h2 className="lv-sentence" key={primary.id}>
          <Sentence step={primary} />
        </h2>
        <div className="lv-timer">
          <span className="lv-timer__value tabular" aria-label={t("rv.live.since", { time: clock(secondsOf(primary, serverNow)) })}>
            {clock(secondsOf(primary, serverNow))}
          </span>
          <span className="lv-bar-run" aria-hidden />
        </div>
        {others.length > 0 ? (
          <p className="lv-parallel c-2">
            {t("rv.live.parallel")} : {others.map((s, i) => (
              <span key={s.id}>
                {i > 0 ? " · " : ""}
                <Sentence step={s} />
              </span>
            ))}
          </p>
        ) : null}
      </div>
      {primary.prospect_id ? <Checklist answer={answer} prospectId={primary.prospect_id} name={primary.name} serverNow={serverNow} /> : null}
    </div>
  );
}

/* ── The conversation window ── */

function Window({ prospectId, name, working, auto, onAuto }: { prospectId: string; name: string | null; working: LiveStep | null; auto: boolean; onAuto: () => void }) {
  const { t, d } = useI18n();
  const { messages, ready, failed } = useFollowedConversation(prospectId);
  const end = useRef<HTMLLIElement>(null);
  const last = messages[messages.length - 1]?.id;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [last, working?.id]);
  const who = name ?? t("rv.live.someone");
  return (
    <div className="lv-window" data-testid="live-thread">
      <header className="lv-window__head">
        <span className="lv-avatar lv-avatar--sm" aria-hidden>
          {initials(name)}
        </span>
        <h3 className="lv-window__title">{t("rv.live.window", { name: who })}</h3>
        <span className="lv-window__tools">
          {auto ? <span className="lv-chip">{t("rv.live.autoOn")}</span> : <Button onClick={onAuto}>{t("rv.live.auto")}</Button>}
          <Link to={`/conversations/${encodeURIComponent(prospectId)}`} className="lv-link">
            {t("rv.live.open")}
          </Link>
        </span>
      </header>
      {failed ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t("rv.live.threadLost")}
        </p>
      ) : null}
      {!ready ? (
        <p className="c-3" role="status">
          {t("convo.live.loading")}
        </p>
      ) : messages.length === 0 && !working ? (
        <p className="c-3">{t("rv.live.thread.empty")}</p>
      ) : (
        <ol className="msgs lv-msgs" aria-label={t("convo.messagesLabel")} aria-live="polite">
          {messages.map((m) => {
            const mine = m.direction === "outbound";
            const author = m.author === "human" ? "human" : mine ? "agent" : "prospect";
            return (
              <li key={m.id} className="msg" data-author={author} data-mine={mine || undefined}>
                <div className="msg__col">
                  <p className="msg__who t-label c-3">{author === "human" ? t("convo.ctl.byYou") : t(mine ? "convo.live.sent" : "convo.live.received")}</p>
                  <div className="msg__bubble" dir="auto">
                    {m.text}
                  </div>
                  <p className="c-3 t-body-sm">
                    {d(m.timestamp, { timeStyle: "medium" })} · {m.channel}
                  </p>
                </div>
              </li>
            );
          })}
          {working ? (
            <li className="msg" data-author="agent" data-mine data-testid="live-typing">
              <div className="msg__col">
                <p className="msg__who t-label c-3">{t("convo.live.sent")}</p>
                <div className="msg__bubble lv-typing" role="status">
                  {t(isWriting(working) ? "rv.live.typing" : stepKey(working.kind), { name: working.name ?? "", detail: working.detail ?? "" })}
                  <span className="lv-dots" aria-hidden>
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              </div>
            </li>
          ) : null}
          <li ref={end} aria-hidden />
        </ol>
      )}
    </div>
  );
}

export interface ScreenProps {
  answer: LiveAnswer;
  serverNow: number;
  primary: LiveStep | null;
  followed: string | null;
  name: string | null;
  auto: boolean;
  onAuto: () => void;
  switchedTo: string | null;
  paused: boolean;
  onPaused: () => void;
}

export function Screen({ answer, serverNow, primary, followed, name, auto, onAuto, switchedTo, paused, onPaused }: ScreenProps) {
  const { t } = useI18n();
  const working = answer.now.length > 0;
  return (
    <section className="lv-screen" aria-label={t("rv.live.screen")} data-on={working || undefined}>
      <header className="lv-bar">
        <span className="lv-bar__dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        <span className="lv-bar__title">{t("rv.live.screen")}</span>
        <span className="lv-onair" data-on={working || undefined}>
          <span className="lv-onair__dot" aria-hidden />
          {t(working ? "rv.live.onair" : "rv.live.standby")}
        </span>
      </header>
      <div className="lv-screen__body">
        <Stage answer={answer} primary={primary} serverNow={serverNow} />
        <Controls prospectId={followed} name={name} paused={paused} onPaused={onPaused} />
        {switchedTo ? (
          <p className="lv-switch" role="status" data-testid="live-switch">
            {t("rv.live.switched", { name: switchedTo })}
          </p>
        ) : null}
        {followed ? (
          <Window key={followed} prospectId={followed} name={name} working={activeFor(answer, followed)} auto={auto} onAuto={onAuto} />
        ) : (
          <p className="c-3 lv-empty" data-testid="live-nothread">
            {t("rv.live.nothread")}
          </p>
        )}
      </div>
    </section>
  );
}
