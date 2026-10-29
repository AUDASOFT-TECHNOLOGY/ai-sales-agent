/** "En direct": the client watches its agent work, second by second, as if sitting at its screen.
 *  Steps come from `GET /client/v1/live` (read every second); the conversation of the person the
 *  agent works on from the conversation route (every two seconds). */

import { useEffect, useState } from "react";
import { BookOpen, Inbox, PenLine, Send, type LucideIcon } from "lucide-react";
import { useI18n } from "../../i18n";
import { StatusOrb } from "../../agent/StatusOrb";
import type { AgentStateKey } from "../../agent/agentState";
import { Glass } from "../../ui/Glass";
import { useMedia } from "../../ui/useMedia";
import { useClientAuth } from "../ClientAuthContext";
import { useClientAgentStatus } from "../useClientAgentStatus";
import { Feed } from "./Feed";
import { Screen } from "./Stage";
import { PHASES, activePhase, clock, followedProspect, idleSeconds, lastPhase, nameOf, primaryStep, type LiveAnswer, type PhaseId } from "./liveModel";
import { useLive } from "./useLive";
import "./live.css";

const PHASE_ICONS: Record<PhaseId, LucideIcon> = { file: BookOpen, prepare: PenLine, send: Send, replies: Inbox };
const SWITCH_MS = 4000;

function Strip({ answer }: { answer: LiveAnswer | null }) {
  const { t } = useI18n();
  const active = activePhase(answer);
  const last = active ? null : lastPhase(answer);
  return (
    <ol className="lv-strip" aria-label={t("rv.live.strip")} data-testid="live-strip">
      {PHASES.map((phase) => {
        const Icon = PHASE_ICONS[phase.id];
        const state = phase.id === active ? "active" : phase.id === last ? "last" : "idle";
        return (
          <li key={phase.id} data-state={state} aria-current={state === "active" ? "step" : undefined}>
            <span className="lv-strip__node" aria-hidden>
              <Icon size={18} />
            </span>
            <span className="lv-strip__label">{t(phase.key)}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function LivePage() {
  const { t, n } = useI18n();
  const { logout } = useClientAuth();
  const { answer, lost, signedOut, serverNow } = useLive();
  const agent = useClientAgentStatus();
  const wide = useMedia("(min-width: 1100px)");
  const [picked, setPicked] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [track, setTrack] = useState<{ id: string | null; switchedAt: number | null }>({ id: null, switchedAt: null });
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);

  const working = (answer?.now.length ?? 0) > 0;
  const followed = followedProspect(answer, picked);
  const name = nameOf(answer, followed);
  const primary = primaryStep(answer);

  // The agent moved on to someone else: say so for a moment, so the window changing is never a surprise.
  if (followed !== track.id) {
    setTrack({ id: followed, switchedAt: track.id !== null && followed !== null && picked === null ? serverNow : null });
  }
  const switchedTo = track.switchedAt !== null && serverNow - track.switchedAt < SWITCH_MS ? name : null;

  const isPaused = paused || agent.agent?.state === "paused";
  const state: AgentStateKey = lost ? "degraded" : isPaused ? "paused" : working ? "working" : "idle";
  const headline = lost ? "rv.live.lost" : isPaused ? "rv.live.paused" : working ? "rv.live.working" : "rv.live.waiting";
  const today = answer?.today;
  const idle = idleSeconds(answer, serverNow);
  const meter = today && today.sent !== null && today.limit ? Math.min(100, Math.round((today.sent / today.limit) * 100)) : null;

  const feed = (
    <Feed recent={answer?.recent ?? []} followed={followed} serverNow={serverNow} onPick={setPicked} />
  );

  return (
    <div className="rv-page lv">
      <header className="lv-presence">
        <StatusOrb state={state} size={52} />
        <div className="lv-presence__text">
          <h1 className="lv-presence__title" data-testid="live-status" role="status" data-status={lost ? "lost" : working ? "working" : "idle"}>
            {t(headline)}
          </h1>
          <p className="c-2">{answer && !working && idle !== null && !lost ? t("rv.live.lastAt", { time: clock(idle) }) : t("rv.live.intro")}</p>
        </div>
        {today && today.sent !== null ? (
          <div className="lv-today" aria-label={t("rv.live.todayLabel")}>
            <span className="lv-today__figure tabular">
              {n(today.sent)}
              {today.limit ? <small> / {n(today.limit)}</small> : null}
            </span>
            <span className="lv-today__label c-3">{t("rv.live.todayLabel")}</span>
            {meter !== null ? (
              <span className="lv-today__meter" aria-hidden>
                <i style={{ inlineSize: `${meter}%` }} />
              </span>
            ) : null}
          </div>
        ) : null}
      </header>

      <Strip answer={answer} />

      {!answer && !lost ? (
        <p className="c-3" role="status">
          {t("rv.live.loading")}
        </p>
      ) : answer ? (
        <div className="lv-layout">
          <Screen
            answer={answer}
            serverNow={serverNow}
            primary={primary}
            followed={followed}
            name={name}
            auto={picked === null}
            onAuto={() => setPicked(null)}
            switchedTo={switchedTo}
            paused={isPaused}
            onPaused={() => setPaused(true)}
          />
          {wide ? (
            <Glass level={1} className="lv-side">
              <h2 className="t-h3">{t("rv.live.feed")}</h2>
              {feed}
            </Glass>
          ) : (
            <details className="lv-fold">
              <summary>{t("rv.live.feed")}</summary>
              {feed}
            </details>
          )}
        </div>
      ) : null}
    </div>
  );
}
