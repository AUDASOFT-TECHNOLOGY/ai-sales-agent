import type { CSSProperties } from "react";
import { Check, Moon, Pause, Power, TriangleAlert } from "lucide-react";
import type { AgentStateKey } from "./agentState";

const LOOK: Record<string, { tone: "success" | "attention" | "neutral" | "info"; icon: typeof Check }> = {
  working: { tone: "success", icon: Check },
  writing: { tone: "success", icon: Check },
  idle: { tone: "info", icon: Moon },
  waiting: { tone: "attention", icon: TriangleAlert },
  degraded: { tone: "attention", icon: TriangleAlert },
  paused: { tone: "attention", icon: Pause },
  off_hours: { tone: "neutral", icon: Moon },
  observing: { tone: "info", icon: Check },
  not_live: { tone: "neutral", icon: Power },
};

/** The agent's state as a flat, light-theme badge: colour plus a glyph, never colour alone.
 *  Decorative for assistive tech; the text next to it says the state. */
export function StatusOrb({ state, size = 40 }: { state: AgentStateKey; size?: number }) {
  const { tone, icon: Icon } = LOOK[state] ?? LOOK.not_live!;
  return (
    <span className="rv-orb" data-tone={tone} data-live={state === "working" || state === "writing" || undefined} style={{ "--size": `${size}px` } as CSSProperties} aria-hidden>
      <Icon size={Math.round(size * 0.5)} />
    </span>
  );
}
