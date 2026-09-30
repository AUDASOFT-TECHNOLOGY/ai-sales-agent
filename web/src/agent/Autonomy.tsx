import { useId, useRef, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { motion } from "motion/react";
import { Lock } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import type { Mode } from "../mock/types";
import { Button } from "../ui/Button";
import { cx } from "../ui/cx";

const MODES: Mode[] = ["observe", "ask", "auto"];
const ANGLE: Record<Mode, number> = { observe: -120, ask: 0, auto: 120 };
const CX = 120;
const CY = 118;
const R = 88;

const pt = (deg: number, radius = R) => {
  const rad = (deg * Math.PI) / 180;
  return { x: CX + radius * Math.sin(rad), y: CY - radius * Math.cos(rad) };
};
const arc = (from: number, to: number) => {
  const a = pt(from);
  const b = pt(to);
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M ${a.x} ${a.y} A ${R} ${R} 0 ${large} 1 ${b.x} ${b.y}`;
};

interface AutonomyDialProps {
  requested: Mode;
  effective: Mode;
  ceiling: Mode;
  onChange: (mode: Mode) => void;
  onRequest?: () => void;
  disabled?: boolean;
  disabledReason?: string;
}

/** How much the agent may do alone (doc 19.2). The arc shows requested versus effective and the locked zone. */
export function AutonomyDial({ requested, effective, ceiling, onChange, onRequest, disabled, disabledReason }: AutonomyDialProps) {
  const { t } = useI18n();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragging = useRef(false);
  const gid = useId();
  const clamped = requested !== effective;
  const ceilAngle = ANGLE[ceiling];

  const fromPointer = (e: ReactPointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg || disabled) return;
    const box = svg.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * 240 - CX;
    const y = CY - ((e.clientY - box.top) / box.height) * 210;
    const deg = (Math.atan2(x, y) * 180) / Math.PI;
    const clampedDeg = Math.max(-120, Math.min(120, deg));
    const nearest = MODES.reduce((best, m) => (Math.abs(ANGLE[m] - clampedDeg) < Math.abs(ANGLE[best] - clampedDeg) ? m : best), "ask" as Mode);
    if (nearest !== requested) onChange(nearest);
  };

  const knob = (angle: number, ghost: boolean) => (
    <motion.g
      initial={false}
      animate={{ rotate: angle }}
      transition={{ type: "spring", stiffness: 260, damping: 26 }}
      style={{ transformBox: "view-box", transformOrigin: `${CX}px ${CY}px` }}
      opacity={ghost ? 0.55 : 1}
    >
      <circle cx={CX} cy={CY - R} r={17} fill={ghost ? "none" : "url(#" + gid + "-knob)"} stroke={ghost ? "var(--aether-300)" : "url(#" + gid + "-line)"} strokeWidth={ghost ? 1.5 : 2} strokeDasharray={ghost ? "3 3" : undefined} />
      {!ghost ? <circle cx={CX} cy={CY - R} r={5} fill="var(--aether-200)" /> : null}
    </motion.g>
  );

  return (
    <div className="dial" data-disabled={disabled || undefined}>
      <svg
        ref={svgRef}
        className="dial__svg"
        viewBox="0 0 240 210"
        aria-hidden
        onPointerDown={(e) => {
          dragging.current = true;
          (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
          fromPointer(e);
        }}
        onPointerMove={(e) => dragging.current && fromPointer(e)}
        onPointerUp={() => (dragging.current = false)}
        onPointerCancel={() => (dragging.current = false)}
      >
        <defs>
          <linearGradient id={`${gid}-line`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--aether-400)" />
            <stop offset="1" stopColor="var(--ice-400)" />
          </linearGradient>
          <radialGradient id={`${gid}-knob`} cx="34%" cy="28%" r="80%">
            <stop offset="0" stopColor="var(--aether-600)" />
            <stop offset="1" stopColor="var(--abyss-3)" />
          </radialGradient>
          <linearGradient id={`${gid}-reach`} x1="0" y1="1" x2="1" y2="0">
            <stop offset="0" stopColor="var(--ice-600)" />
            <stop offset="1" stopColor="var(--ice-300)" />
          </linearGradient>
          <pattern id={`${gid}-hatch`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--text-3)" strokeWidth="2" strokeOpacity="0.45" />
          </pattern>
        </defs>
        <path d={arc(-120, 120)} fill="none" stroke="rgb(255 255 255 / 0.09)" strokeWidth={12} strokeLinecap="round" />
        <path d={arc(-120, ceilAngle)} fill="none" stroke={`url(#${gid}-reach)`} strokeWidth={12} strokeLinecap="round" opacity={0.9} />
        {ceilAngle < 120 ? <path d={arc(ceilAngle + 6, 120)} fill="none" stroke={`url(#${gid}-hatch)`} strokeWidth={12} strokeLinecap="round" /> : null}
        {MODES.map((m) => {
          const p = pt(ANGLE[m]);
          return <circle key={m} cx={p.x} cy={p.y} r={4.5} fill="var(--abyss-1)" stroke="var(--line-control)" strokeWidth={1.5} />;
        })}
        {ceilAngle < 120 ? (
          <g transform={`translate(${pt(120).x - 8} ${pt(120).y + 22})`} color="var(--text-2)">
            <Lock width={16} height={16} />
          </g>
        ) : null}
        {clamped ? knob(ANGLE[requested], true) : null}
        {knob(ANGLE[effective], false)}
        <text x={CX} y={CY + 6} textAnchor="middle" className="dial__label">
          {t(`mode.${effective}` as MessageKey)}
        </text>
      </svg>

      <div className="dial__side">
        <div role="radiogroup" aria-label={t("configure.autonomy")} className="dial__options">
          {MODES.map((m) => {
            const locked = ANGLE[m] > ceilAngle;
            const selected = m === requested;
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={disabled}
                title={disabled ? disabledReason : undefined}
                className={cx("dial__option")}
                data-selected={selected || undefined}
                data-locked={locked || undefined}
                onClick={() => onChange(m)}
              >
                <span className="dial__radio" aria-hidden />
                <span className="dial__option-text">
                  <span className="dial__option-title">
                    {t(`mode.${m}` as MessageKey)}
                    {locked ? <Lock size={13} aria-hidden /> : null}
                    {m === "ask" ? <span className="dial__rec">{t("mode.recommended")}</span> : null}
                  </span>
                  <span className="c-2 t-body-sm">{locked ? `${t(`mode.${m}.desc` as MessageKey)} ${t("mode.locked")}.` : t(`mode.${m}.desc` as MessageKey)}</span>
                </span>
              </button>
            );
          })}
        </div>
        <div aria-live="polite">
          {clamped ? (
            <p className="dial__clamp t-body-sm">
              {t("mode.clamped", { requested: t(`mode.${requested}` as MessageKey), effective: t(`mode.${effective}` as MessageKey) })}
            </p>
          ) : null}
        </div>
        {clamped && onRequest ? (
          <Button size="sm" variant="secondary" onClick={onRequest} disabled={disabled}>
            {t("mode.request")}
          </Button>
        ) : null}
        <p className="dial__limit-note c-3 t-body-sm">
          <Lock size={14} aria-hidden /> {t("mode.limitNote")}
        </p>
      </div>
    </div>
  );
}

/* ── Guardrail slider: the reachable range is bright, the platform's limit is drawn as a hatched wall (doc 19.3) ── */
interface GuardrailSliderProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  cap: number;
  scaleMax: number;
  lockedText: string;
  disabled?: boolean;
}

export function GuardrailSlider({ label, value, onChange, min, cap, scaleMax, lockedText, disabled }: GuardrailSliderProps) {
  const id = useId();
  const pct = ((value - min) / (cap - min)) * 100;
  return (
    <div className="guardrail" style={{ "--reach": cap - min, "--locked": scaleMax - cap, "--pct": `${pct}%` } as CSSProperties}>
      <div className="guardrail__head">
        <label htmlFor={id} className="t-label c-2">
          {label}
        </label>
        <output htmlFor={id} className="t-numeral-m tabular">
          {value}
        </output>
      </div>
      <div className="guardrail__bar">
        <div className="guardrail__reach">
          <span className="guardrail__fill" />
          <input id={id} type="range" min={min} max={cap} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} aria-valuetext={`${value}`} />
        </div>
        <div className="guardrail__locked" aria-hidden>
          <Lock size={14} />
        </div>
      </div>
      <p className="c-3 t-body-sm">{lockedText}</p>
    </div>
  );
}

/* ── Daily budget arc ── */
export function DailyBudget({ used, limit, label, caption }: { used: number; limit: number; label: string; caption?: string }) {
  const ratio = Math.min(used / limit, 1);
  const C = 2 * Math.PI * 40;
  const tint = ratio >= 1 ? "attention" : ratio >= 0.9 ? "attention" : "action";
  return (
    <div className="budget" data-tint={tint}>
      <svg viewBox="0 0 100 100" width={88} height={88} aria-hidden>
        <circle cx="50" cy="50" r="40" fill="none" stroke="rgb(255 255 255 / 0.09)" strokeWidth="8" />
        <motion.circle
          cx="50"
          cy="50"
          r="40"
          fill="none"
          stroke="rgb(var(--tint))"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: C * (1 - ratio) }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          transform="rotate(-90 50 50)"
        />
      </svg>
      <div className="budget__num tabular">
        <span className="t-numeral-m">{used}</span>
        <span className="c-3 t-body-sm">/{limit}</span>
      </div>
      <div className="budget__text">
        <p className="t-label c-2">{label}</p>
        {caption ? <p className="c-3 t-body-sm">{caption}</p> : null}
      </div>
    </div>
  );
}
