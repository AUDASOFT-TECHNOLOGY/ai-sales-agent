import { motion } from "motion/react";
import { Check } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { GATES, type GateId } from "../mock/store";

/** The runway: gates the agent must pass before takeoff, joined by a line that lights up as they complete (doc 19.7). */
export function RunwayTrack({ done, current, onSelect }: { done: Record<GateId, boolean>; current: GateId; onSelect: (g: GateId) => void }) {
  const { t } = useI18n();
  const completed = GATES.filter((g) => done[g]).length;
  const idx = GATES.indexOf(current);
  const progress = (Math.min(Math.max(completed, idx), GATES.length - 1) / (GATES.length - 1)) * 100;
  return (
    <nav className="runway" aria-label={t("runway.welcome")}>
      <div className="runway__line" aria-hidden>
        <motion.span className="runway__lit" initial={false} animate={{ width: `${progress}%` }} transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }} />
      </div>
      <ol className="runway__gates">
        {GATES.map((g, i) => {
          const isDone = done[g];
          const isCurrent = g === current;
          return (
            <li key={g}>
              <button type="button" className="runway__gate" data-state={isDone ? "done" : isCurrent ? "current" : "todo"} aria-current={isCurrent ? "step" : undefined} aria-label={`${i + 1}. ${t(`runway.gate.${g}` as MessageKey)}${isDone ? ", done" : ""}`} onClick={() => onSelect(g)}>
                <span className="runway__node">
                  {isDone ? (
                    <motion.span initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 400, damping: 20 }}>
                      <Check size={16} strokeWidth={3} aria-hidden />
                    </motion.span>
                  ) : (
                    <span className="tabular">{i + 1}</span>
                  )}
                </span>
                <span className="runway__label t-body-sm" aria-hidden>{t(`runway.gate.${g}` as MessageKey)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export type NodeHealth = "healthy" | "degraded" | "down" | "off";

/** Your Sheet, WhatsApp and the agent as three connected nodes (doc 19.8). Only renders the client's health snapshot. */
export function ConnectionConstellation({ nodes }: { nodes: Array<{ id: string; label: string; health: NodeHealth; detail: string }> }) {
  const xs = nodes.length === 2 ? [160, 360] : [90, 260, 430];
  const tone: Record<NodeHealth, string> = {
    healthy: "var(--tint-success)",
    degraded: "var(--tint-attention)",
    down: "var(--tint-danger)",
    off: "var(--tint-neutral)",
  };
  return (
    <div className="constellation">
      <svg viewBox="0 0 520 120" width="100%" aria-hidden>
        {nodes.slice(0, -1).map((n, i) => {
          const next = nodes[i + 1]!;
          const broken = n.health === "down" || next.health === "down";
          return <line key={n.id} x1={xs[i]! + 30} x2={xs[i + 1]! - 30} y1={50} y2={50} stroke={`rgb(${broken ? tone.down : "255 255 255"} / ${broken ? 0.5 : 0.25})`} strokeWidth={1.5} strokeDasharray={broken ? "4 5" : undefined} />;
        })}
        {nodes.map((n, i) => (
          <g key={n.id}>
            <circle cx={xs[i]} cy={50} r={30} fill={`rgb(${tone[n.health]} / 0.1)`} stroke={`rgb(${tone[n.health]} / 0.55)`} strokeWidth={1.5} strokeDasharray={n.health === "degraded" ? "40 12" : undefined} />
            <circle cx={xs[i]} cy={50} r={5} fill={`rgb(${tone[n.health]})`} />
          </g>
        ))}
      </svg>
      <ul className="constellation__labels" style={{ gridTemplateColumns: `repeat(${nodes.length}, 1fr)` }}>
        {nodes.map((n) => (
          <li key={n.id}>
            <span className="t-label">{n.label}</span>
            <span className="c-3 t-body-sm">{n.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
