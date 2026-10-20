/** A small accessible chart (SVG, no library): lines or grouped bars over a shared x axis.
 *
 *  Honesty rules: a `null` value is a gap (never a 0); an unavailable chart says why instead of
 *  drawing; every chart has a legend (identity is never colour alone: markers/dashes differ), a
 *  native tooltip per mark, a text alternative and a data table. */

import { useId } from "react";

export interface ChartSeries {
  id: string;
  label: string;
  /** A CSS colour, normally one of the `--dv-*` tokens. */
  color: string;
  values: (number | null)[];
}

interface Props {
  title: string;
  description?: string;
  labels: string[];
  series: ChartSeries[];
  kind?: "line" | "bars";
  format?: (value: number) => string;
  /** Why the chart cannot be drawn (not tracked): shown instead of the plot. */
  unavailable?: string | null;
  emptyText?: string;
  tableLabel?: string;
  /** Texts of the chart itself (English by default; the Client passes its own language). */
  text?: { notTracked: string; period: string; legend: string; none: string };
}

const W = 640;
const H = 220;
const PAD = { top: 12, right: 12, bottom: 28, left: 48 };
const DASH = ["", "6 4", "2 3", "8 3 2 3"];

function niceMax(max: number): number {
  if (max <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(max));
  const f = max / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

function Marker({ index, x, y, color }: { index: number; x: number; y: number; color: string }) {
  const s = 4;
  const common = { fill: color, stroke: "var(--dv-surface)", strokeWidth: 2 };
  switch (index % 4) {
    case 0:
      return <circle cx={x} cy={y} r={s} {...common} />;
    case 1:
      return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} {...common} />;
    case 2:
      return <polygon points={`${x},${y - s - 1} ${x + s + 1},${y + s} ${x - s - 1},${y + s}`} {...common} />;
    default:
      return <polygon points={`${x},${y - s - 1} ${x + s + 1},${y} ${x},${y + s + 1} ${x - s - 1},${y}`} {...common} />;
  }
}

export function TimeChart({
  title,
  description,
  labels,
  series,
  kind = "line",
  format = (v) => String(v),
  unavailable,
  emptyText = "No data for this period.",
  tableLabel = "Data table",
  text = { notTracked: "Not tracked", period: "Period", legend: "legend", none: "no value" },
}: Props) {
  const id = useId();
  if (unavailable) {
    return (
      <div className="dv-chart" role="group" aria-label={title}>
        <p className="dv-chart__title">{title}</p>
        <p className="dv-note dv-note--na" role="status">
          {text.notTracked}: {unavailable}
        </p>
      </div>
    );
  }
  const known = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  if (labels.length === 0 || known.length === 0) {
    return (
      <div className="dv-chart" role="group" aria-label={title}>
        <p className="dv-chart__title">{title}</p>
        <p className="dv-note" role="status">
          {emptyText}
        </p>
      </div>
    );
  }
  const max = niceMax(Math.max(...known));
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const step = labels.length > 1 ? iw / (labels.length - 1) : 0;
  const band = iw / labels.length;
  const xAt = (i: number) => (kind === "bars" ? PAD.left + band * (i + 0.5) : PAD.left + (labels.length > 1 ? step * i : iw / 2));
  const yAt = (v: number) => PAD.top + ih - (v / max) * ih;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => max * f);
  const every = Math.ceil(labels.length / 8);
  const barW = Math.max(2, Math.min(18, (band * 0.8) / series.length - 2));
  const summary = `${title}. ${description ?? ""} ${series
    .map((s) => {
      const vs = s.values.filter((v): v is number => v !== null);
      return `${s.label}: ${vs.length ? `from ${format(Math.min(...vs))} to ${format(Math.max(...vs))}` : text.none}`;
    })
    .join("; ")}.`;

  return (
    <div className="dv-chart" role="group" aria-label={title}>
      <p className="dv-chart__title">{title}</p>
      {description ? <p className="dv-chart__desc">{description}</p> : null}
      <ul className="dv-legend" aria-label={`${title}: ${text.legend}`}>
        {series.map((s, i) => (
          <li key={s.id}>
            <svg width="22" height="12" aria-hidden="true">
              {kind === "bars" ? (
                <rect x="4" y="1" width="12" height="10" rx="2" fill={s.color} />
              ) : (
                <>
                  <line x1="0" x2="22" y1="6" y2="6" stroke={s.color} strokeWidth="2" strokeDasharray={DASH[i % 4]} />
                  <Marker index={i} x={11} y={6} color={s.color} />
                </>
              )}
            </svg>
            <span>{s.label}</span>
          </li>
        ))}
      </ul>
      <svg className="dv-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} preserveAspectRatio="xMidYMid meet">
        <title id={`${id}-t`}>{title}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line className="dv-grid" x1={PAD.left} x2={W - PAD.right} y1={yAt(t)} y2={yAt(t)} />
            <text className="dv-axis" x={PAD.left - 6} y={yAt(t) + 4} textAnchor="end">
              {format(t)}
            </text>
          </g>
        ))}
        {labels.map((label, i) =>
          i % every === 0 ? (
            <text key={label} className="dv-axis" x={xAt(i)} y={H - 8} textAnchor="middle">
              {label}
            </text>
          ) : null,
        )}
        {series.map((s, si) => {
          if (kind === "bars") {
            return (
              <g key={s.id}>
                {s.values.map((v, i) => {
                  if (v === null) return null;
                  const x = xAt(i) - (barW * series.length + 2 * (series.length - 1)) / 2 + si * (barW + 2);
                  const y = yAt(v);
                  return (
                    <rect key={labels[i]} x={x} y={y} width={barW} height={Math.max(0, PAD.top + ih - y)} rx={2} fill={s.color}>
                      <title>{`${labels[i]} - ${s.label}: ${format(v)}`}</title>
                    </rect>
                  );
                })}
              </g>
            );
          }
          // A line is broken at every gap: an unknown value is never joined through as a zero.
          const segments: { i: number; v: number }[][] = [];
          s.values.forEach((v, i) => {
            if (v === null) {
              segments.push([]);
              return;
            }
            if (segments.length === 0) segments.push([]);
            segments[segments.length - 1]!.push({ i, v });
          });
          return (
            <g key={s.id}>
              {segments
                .filter((seg) => seg.length > 1)
                .map((seg) => (
                  <polyline
                    key={seg[0]!.i}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeDasharray={DASH[si % 4]}
                    strokeLinejoin="round"
                    points={seg.map((p) => `${xAt(p.i)},${yAt(p.v)}`).join(" ")}
                  />
                ))}
              {s.values.map((v, i) =>
                v === null ? null : (
                  <g key={labels[i]}>
                    <Marker index={si} x={xAt(i)} y={yAt(v)} color={s.color} />
                    <title>{`${labels[i]} - ${s.label}: ${format(v)}`}</title>
                  </g>
                ),
              )}
            </g>
          );
        })}
      </svg>
      <details className="dv-table">
        <summary>{tableLabel}</summary>
        <table>
          <caption className="dv-sr">{title}</caption>
          <thead>
            <tr>
              <th scope="col">{text.period}</th>
              {series.map((s) => (
                <th key={s.id} scope="col">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {labels.map((label, i) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                {series.map((s) => (
                  <td key={s.id}>{s.values[i] === null || s.values[i] === undefined ? "-" : format(s.values[i]!)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
