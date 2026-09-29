import type { CSSProperties } from "react";
import { useI18n, type MessageKey } from "../i18n";
import type { AgentStateKey } from "../mock/types";

interface PulseProps {
  state: AgentStateKey;
  /** Pixel size. 24 mini, 56 nav, 96 compact hero, 160 hero (doc 19.1). */
  size?: number;
  /** Amber count badge, shown for the waiting state. */
  count?: number;
  className?: string;
}

/** The Pulse: a living orb that IS the agent. Decorative for assistive tech; the text label carries the status. */
export function Pulse({ state, size = 56, count, className }: PulseProps) {
  const tier = size <= 32 ? "mini" : size <= 64 ? "nav" : "hero";
  return (
    <span
      className={className ? `pulse ${className}` : "pulse"}
      data-state={state}
      data-tier={tier}
      style={{ "--size": `${size}px` } as CSSProperties}
      aria-hidden
    >
      <span className="pulse__halo" />
      <span className="pulse__core" />
      <span className="pulse__caustic" />
      <span className="pulse__ring" />
      <span className="pulse__arc" />
      <span className="pulse__sat" style={{ "--t": "9s", "--r": "20deg" } as CSSProperties} />
      <span className="pulse__sat" style={{ "--t": "13s", "--r": "140deg" } as CSSProperties} />
      <span className="pulse__sat" style={{ "--t": "17s", "--r": "255deg" } as CSSProperties} />
      {count && count > 0 && tier !== "mini" ? <span className="pulse__count">{count}</span> : null}
    </span>
  );
}

/** Accessible, localized status text for an agent state. */
export function useAgentLabel(state: AgentStateKey, needs = 0): string {
  const { t } = useI18n();
  if (state === "waiting") return needs === 1 ? t("agent.state.waiting.one") : t("agent.state.waiting", { n: needs });
  return t(`agent.state.${state}` as MessageKey);
}
