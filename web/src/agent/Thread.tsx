import { AnimatePresence, motion } from "motion/react";
import { ArrowDownLeft, CalendarClock, CreditCard, HandHelping, Send, Sparkles, UserRound, Brain } from "lucide-react";
import { useI18n } from "../i18n";
import type { AgentStateKey, ThreadEvent, ThreadKind } from "../mock/types";
import { GlyphTile } from "../ui/Chip";
import type { Tint } from "../ui/Glass";
import { Pulse } from "./Pulse";
import { TENANT } from "../mock/data";
import { CertaintyRing } from "./Certainty";

const KIND: Record<ThreadKind, { tint: Tint; icon: typeof Send }> = {
  sent: { tint: "agent", icon: Send },
  received: { tint: "action", icon: ArrowDownLeft },
  fact: { tint: "agent", icon: Sparkles },
  decision: { tint: "agent", icon: Brain },
  handoff: { tint: "attention", icon: HandHelping },
  payment: { tint: "attention", icon: CreditCard },
  scheduled: { tint: "neutral", icon: CalendarClock },
  human: { tint: "action", icon: UserRound },
};

function ThreadNode({ event, compact }: { event: ThreadEvent; compact?: boolean }) {
  const cfg = KIND[event.kind];
  const Icon = event.future ? CalendarClock : cfg.icon;
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="thread__item"
      data-future={event.future || undefined}
      data-needs={event.needsYou || undefined}
    >
      <time className="thread__time tabular c-3 t-body-sm">{event.time}</time>
      <div className="thread__node">
        <GlyphTile tint={event.needsYou ? "attention" : cfg.tint} size={28} ghost={event.future}>
          {event.kind === "fact" ? <CertaintyRing status="discovered" source="prospect" size={16} /> : <Icon size={15} />}
        </GlyphTile>
      </div>
      <div className="thread__body">
        <p className="voice voice-s">{event.voice}</p>
        {compact ? (
          event.needsYou ? <p className="thread__flag t-body-sm">Needs you</p> : null
        ) : (
          <p className="c-3 t-body-sm">
            {event.detail}
            {event.future ? " · Planned" : null}
            {event.needsYou ? <span className="thread__flag"> · Needs you</span> : null}
          </p>
        )}
      </div>
    </motion.li>
  );
}

/** The Day Thread (doc 17): the agent's journal and plan in one line, with Now in the middle. */
export function DayThread({ events, nowTime, agentState, compact }: { events: ThreadEvent[]; nowTime: string; agentState: AgentStateKey; compact?: boolean }) {
  const { t } = useI18n();
  const past = events.filter((e) => !e.future);
  const future = events.filter((e) => e.future);
  return (
    <ol className="thread" aria-label={t("command.thread")}>
      <AnimatePresence initial={false}>
        {past.map((e) => (
          <ThreadNode key={e.id} event={e} compact={compact} />
        ))}
      </AnimatePresence>
      <li className="thread__now" aria-label={`Now, ${nowTime}`}>
        <span className="thread__now-time tabular t-label">{nowTime}</span>
        <span className="thread__now-pulse">
          <Pulse state={agentState === "not_live" ? "idle" : agentState} size={22} />
        </span>
        <span className="thread__now-line">
          <span className="t-label c-agent">Now</span>
          <span className="c-3 t-body-sm">{TENANT.timezoneLabel}</span>
        </span>
      </li>
      {future.map((e) => (
        <ThreadNode key={e.id} event={e} compact={compact} />
      ))}
    </ol>
  );
}

/** One serif sentence that cross-fades when something meaningful happens: a heartbeat, not a log (doc 19.4). */
export function Ticker({ text }: { text: string }) {
  return (
    <p className="ticker" aria-live="polite" aria-atomic="true">
      <span className="sr-only">{text}</span>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={text}
          aria-hidden
          className="voice voice-s"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.3 }}
        >
          {text}
        </motion.span>
      </AnimatePresence>
    </p>
  );
}

