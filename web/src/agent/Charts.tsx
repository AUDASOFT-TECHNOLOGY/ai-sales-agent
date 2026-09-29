import { motion } from "motion/react";
import type { StageSlice } from "../mock/types";

/* ── Shared smoothing, matches agent/Kpi.tsx so every chart in the product has the same hand ── */
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

/** A small trend shape embedded directly inside a KPI card — no axis, no per-point labels. The
 *  card's own headline number and delta text carry the precise figures; this only needs to answer
 *  "is it going up" at a glance, the way a real product's scorecards (Stripe, Linear) do it. */
export function MiniTrend({ series, label, height = 32 }: { series: number[]; label: string; height?: number }) {
  const W = 120;
  const H = height;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = Math.max(max - min, 1);
  const pts: Array<[number, number]> = series.map((v, i) => [(i / Math.max(series.length - 1, 1)) * W, H - 3 - ((v - min) / span) * (H - 6)]);
  const line = smooth(pts);
  return (
    <svg className="mini-trend" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} role="img" aria-label={label}>
      <path d={line} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Prospects by stage as one proportional bar, ordered by pipeline progression (new → in
 *  conversation → ready to decide → won), with a two-column legend underneath. Replaced an earlier
 *  donut (four thin arcs were hard to compare) and, before that, a row of separate bar-meters (too
 *  tall next to the funnel it sits beside) — a single bar is the fastest shape to read a mix from. */
export function StageSegmentedBar({ slices }: { slices: StageSlice[] }) {
  const total = slices.reduce((sum, s) => sum + s.count, 0) || 1;
  return (
    <div className="stage-seg">
      <div className="stage-seg__bar" role="img" aria-label={`${total} prospects: ${slices.map((s) => `${s.label} ${s.count}`).join(", ")}`}>
        {slices.map((s) => (
          <span key={s.id} className="stage-seg__slice" style={{ width: `${(s.count / total) * 100}%`, background: `var(--${s.tint})` }} />
        ))}
      </div>
      <ul className="stage-seg__legend">
        {slices.map((s) => (
          <li key={s.id}>
            <span className="stage-seg__dot" style={{ background: `var(--${s.tint})` }} aria-hidden />
            <span className="stage-seg__label">{s.label}</span>
            <span className="tabular stage-seg__count">
              {s.count} · {Math.round((s.count / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One labelled, animated horizontal bar — used for revenue and conversion by product, and for the
 *  prospects-by-stage breakdown above. */
export function BarMeter({ label, valueLabel, fraction, tint = "agent", dashed }: { label: string; valueLabel: string; fraction: number; tint?: "agent" | "action" | "seal" | "attention" | "success"; dashed?: boolean }) {
  return (
    <div className="bar-meter">
      <div className="bar-meter__head">
        <span className="bar-meter__label">{label}</span>
        <span className="tabular bar-meter__value">{valueLabel}</span>
      </div>
      <div className="bar-meter__track" data-dashed={dashed || undefined}>
        {!dashed ? (
          <motion.div
            className="bar-meter__fill"
            style={{ background: `var(--${tint})` }}
            initial={{ width: 0 }}
            animate={{ width: `${Math.max(0, Math.min(100, fraction * 100))}%` }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          />
        ) : null}
      </div>
    </div>
  );
}
