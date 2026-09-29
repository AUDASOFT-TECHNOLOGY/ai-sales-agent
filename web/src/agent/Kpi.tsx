import { useEffect, useId, useRef, useState } from "react";
import { animate, motion } from "motion/react";
import { useI18n, type MessageKey } from "../i18n";
import type { FunnelStep } from "../mock/types";

/* ── Number that tweens between values instead of flashing (doc 14.1) ── */
export function AnimatedNumber({ value }: { value: number }) {
  const { n } = useI18n();
  const ref = useRef<HTMLSpanElement>(null);
  const previous = useRef(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      node.textContent = n(value);
      previous.current = value;
      return;
    }
    const controls = animate(previous.current, value, {
      duration: 0.9,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        node.textContent = n(Math.round(v));
      },
    });
    previous.current = value;
    return () => controls.stop();
  }, [value, n]);
  return <span ref={ref}>{n(value)}</span>;
}

/* ── Shared smoothing for the funnel ribbon below ── */
function smooth(points: Array<[number, number]>): string {
  if (points.length < 2) return "";
  let d = `M ${points[0]![0]} ${points[0]![1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i]!;
    const p1 = points[i]!;
    const p2 = points[i + 1]!;
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

/* ── Funnel ribbon (doc 14.4): one continuous ribbon narrowing to outcomes; ghost segments for what is not tracked ── */
const W = 1000;
const H = 200;
const MID = H / 2;

function ribbonPath(halves: number[], xs: number[]): string {
  const top = halves.map((h, i): [number, number] => [xs[i]!, MID - h]);
  const bottom = halves.map((h, i): [number, number] => [xs[i]!, MID + h]).reverse();
  const t = smooth(top);
  const b = smooth(bottom).replace(/^M [\d.-]+ [\d.-]+/, `L ${bottom[0]![0]} ${bottom[0]![1]}`);
  return `${t} ${b} Z`;
}

export function FunnelRibbon({ steps }: { steps: FunnelStep[] }) {
  const { t, n } = useI18n();
  const gid = useId();
  const [hover, setHover] = useState<number | null>(null);
  const col = W / steps.length;
  const known = steps.filter((s) => s.count !== null);
  const first = known[0]?.count ?? 1;
  const xs = steps.map((_, i) => col * i + col / 2);
  const halves = known.map((s) => Math.max(9, ((s.count ?? 0) / first) * 84));
  const knownXs = xs.slice(0, known.length);
  // Start and end caps so the ribbon has a body at both ends.
  const capXs = [0, ...knownXs];
  const capHalves = [halves[0] ?? 84, ...halves];
  const ghostXs = xs.slice(known.length - 1);
  const lastHalf = halves[halves.length - 1] ?? 9;
  const ghostHalves = ghostXs.map((_, i) => Math.max(6, lastHalf * (1 - i * 0.28)));

  return (
    <div className="ribbon" onPointerLeave={() => setHover(null)}>
      <motion.svg
        className="ribbon__svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-hidden
        initial={{ clipPath: "inset(0 100% 0 0)" }}
        animate={{ clipPath: "inset(0 0% 0 0)" }}
        transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
      >
        <defs>
          <linearGradient id={`${gid}-g`} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2={knownXs[knownXs.length - 1] ?? W} y2="0">
            <stop offset="0" stopColor="var(--ice-400)" stopOpacity="0.9" />
            <stop offset="0.55" stopColor="var(--aether-500)" stopOpacity="0.85" />
            <stop offset="1" stopColor="var(--emerald-500)" stopOpacity="0.85" />
          </linearGradient>
          <filter id={`${gid}-b`} x="-10%" y="-30%" width="120%" height="160%">
            <feGaussianBlur stdDeviation="14" />
          </filter>
        </defs>
        {hover !== null ? <rect x={hover * col} y="0" width={col} height={H} fill="rgb(255 255 255 / 0.05)" /> : null}
        <path d={ribbonPath(capHalves, capXs)} fill={`url(#${gid}-g)`} filter={`url(#${gid}-b)`} opacity={0.45} />
        <path d={ribbonPath(capHalves, capXs)} fill={`url(#${gid}-g)`} opacity={0.92} />
        {ghostXs.length > 1 ? (
          <path
            d={ribbonPath(ghostHalves, ghostXs)}
            fill="rgb(var(--tint-success) / 0.04)"
            stroke="rgb(var(--tint-success) / 0.5)"
            strokeWidth="1.5"
            strokeDasharray="6 6"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </motion.svg>
      <ol className="ribbon__labels">
        {steps.map((s, i) => (
          <li key={s.id} data-dim={hover !== null && hover !== i ? true : undefined} onPointerEnter={() => setHover(i)}>
            <span className="t-label c-2">{t(`funnel.${s.id}` as MessageKey)}</span>
            {s.count === null ? (
              <span className="c-3 t-body-sm">{t("kpi.notTracked")}</span>
            ) : (
              <span className="t-numeral-m tabular">
                <AnimatedNumber value={s.count} />
              </span>
            )}
            {s.rate ? <span className="c-3 t-body-sm tabular">{n(parseInt(s.rate, 10))}%</span> : <span className="t-body-sm">&nbsp;</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
