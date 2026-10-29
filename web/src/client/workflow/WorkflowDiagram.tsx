/** The agent's journey on one animated picture, read top to bottom.
 *  1. Inside the agent's zone (always on, on our servers): a light crosses the five steps, each plugged
 *     into the tools it uses; follow-ups are bracketed under the two steps that need them; the CRM and
 *     the KPIs are kept up to date along the whole way.
 *  2. Under the zone: the client. Three arrows say who hands what to whom: the agent hands the
 *     conversation back, the agent reports every day, the client takes over whenever they want.
 *  Pure SVG + CSS; the motion stops when the visitor asks for less of it. */

import { BadgeCheck, ChartNoAxesColumn, Cloud, FileSpreadsheet, HandHelping, Handshake, MessageCircleReply, MessageSquareText, RefreshCw, Sparkles, type LucideIcon } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { MARKS, type Brand } from "../connections/BrandIcons";

const VIEW_W = 1200;
const PIPE_Y = 190;
const X_FIRST = 150;
const X_LAST = 1050;
const COUNT = 5;
const STEP = (X_LAST - X_FIRST) / (COUNT - 1);
const nx = (i: number) => X_FIRST + i * STEP;
const CYCLE = 12; // seconds for the light to cross the pipeline

const RELAY_Y = PIPE_Y + 138; // the bracket that carries the follow-ups
const LANE_Y = PIPE_Y + 188; // the always-on lane: CRM and KPIs
const LANE_H = 44;
const ZONE_BOTTOM = LANE_Y + LANE_H + 26;
const PANEL_Y = ZONE_BOTTOM + 76;
const PANEL_H = 84;
const VIEW_H = PANEL_Y + PANEL_H + 20;

type Tool = { brand: Brand; name: string };
const SOURCES: Tool[] = [
  { brand: "sheets", name: "Google Sheets" },
  { brand: "hubspot", name: "HubSpot" },
];
const CHANNELS: Tool[] = [
  { brand: "whatsapp", name: "WhatsApp" },
  { brand: "gmail", name: "Gmail" },
  { brand: "telegram", name: "Telegram" },
];

/** Each step: an icon, its sentence (one line per `|`) and the tools it works with. */
const STEPS: { icon: LucideIcon; label: MessageKey; tools?: Tool[] }[] = [
  { icon: FileSpreadsheet, label: "rv.wf2.s1", tools: SOURCES },
  { icon: MessageSquareText, label: "rv.wf2.s2", tools: CHANNELS },
  { icon: MessageCircleReply, label: "rv.wf2.s3", tools: CHANNELS },
  { icon: Sparkles, label: "rv.wf2.s4" },
  { icon: Handshake, label: "rv.wf2.s5" },
];

/** The follow-ups belong to the conversation and to the persuasion: steps 3 and 4. */
const RELAY = { from: 2, to: 3 };

const LANE_ITEMS: { icon: LucideIcon; label: MessageKey; x: number }[] = [
  { icon: BadgeCheck, label: "rv.wf2.f2", x: 520 },
  { icon: ChartNoAxesColumn, label: "rv.wf2.f3", x: 700 },
];

const PILLS: { label: MessageKey; w: number }[] = [
  { label: "rv.wf2.p1", w: 132 },
  { label: "rv.wf2.p2", w: 126 },
  { label: "rv.wf2.p3", w: 154 },
];

const TILE = 24;
const TILE_GAP = 8;
const PLUG_H = 38;
const PLUG_Y = 96;

/** The tools of a step: a slim capsule plugged into the top of its disc by a thin line. */
function Plug({ tools, cx }: { tools: Tool[]; cx: number }) {
  const inner = tools.length * TILE + (tools.length - 1) * TILE_GAP;
  const width = inner + 24;
  const left = cx - width / 2;
  return (
    <g>
      <line x1={cx} y1={PLUG_Y + PLUG_H} x2={cx} y2={PIPE_Y - 36} className="rv-wfa__plug-line" />
      <circle cx={cx} cy={PIPE_Y - 36} r={3} className="rv-wfa__plug-dot" />
      <rect x={left} y={PLUG_Y} width={width} height={PLUG_H} rx={PLUG_H / 2} className="rv-wfa__plug" />
      {tools.map((tool, i) => (
        <g key={tool.brand} transform={`translate(${left + 12 + i * (TILE + TILE_GAP)} ${PLUG_Y + (PLUG_H - TILE) / 2})`}>
          <title>{tool.name}</title>
          <rect width={TILE} height={TILE} rx={8} fill="#fff" />
          <svg x={4} y={4} width={TILE - 8} height={TILE - 8} viewBox="0 0 24 24" focusable="false">{MARKS[tool.brand]}</svg>
        </g>
      ))}
    </g>
  );
}

/** A vertical arrow between the zone and the client, with its caption beside it. */
function Handoff({ x, from, to, label, side }: { x: number; from: number; to: number; label: string; side: "left" | "right" }) {
  const mid = (from + to) / 2;
  return (
    <g>
      <line x1={x} y1={from} x2={x} y2={to} className="rv-wfa__hand-link" markerEnd="url(#rv-wfa-tip-hand)" />
      <text x={side === "right" ? x + 14 : x - 14} y={mid + 5} textAnchor={side === "right" ? "start" : "end"} className="rv-wfa__report-t">{label}</text>
    </g>
  );
}

export function WorkflowDiagram() {
  const { t } = useI18n();
  let pillX = 372;
  const relayLeft = nx(RELAY.from) - 70;
  const relayRight = nx(RELAY.to) + 70;
  const relayMid = (relayLeft + relayRight) / 2;
  const relayLabel = t("rv.wf2.f1");
  const relayW = 184;
  return (
    <figure className="rv-wfa" data-testid="workflow-diagram">
      <div className="rv-wfa__scroll">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={t("rv.wf2.title")} className="rv-wfa__svg" style={{ ["--cycle" as string]: `${CYCLE}s` }}>
          <defs>
            <linearGradient id="rv-wfa-grad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#7a63f0" />
              <stop offset="0.55" stopColor="#4f6ef0" />
              <stop offset="1" stopColor="#2f9fd8" />
            </linearGradient>
            <radialGradient id="rv-wfa-glow">
              <stop offset="0" stopColor="#9d8cff" stopOpacity="0.9" />
              <stop offset="1" stopColor="#9d8cff" stopOpacity="0" />
            </radialGradient>
            <marker id="rv-wfa-tip-hand" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="9" markerHeight="9" orient="auto">
              <path d="M0 0 L10 5 L0 10 z" className="rv-wfa__tip-hand" />
            </marker>
          </defs>

          {/* The agent's zone: always on, on our servers. */}
          <rect x={24} y={20} width={VIEW_W - 48} height={ZONE_BOTTOM - 20} rx={32} className="rv-wfa__zone" />
          <g transform="translate(52 40)">
            <rect width={420} height={34} rx={17} className="rv-wfa__zone-tag" />
            <Cloud x={14} y={7} width={20} height={20} className="rv-wfa__zone-icon" aria-hidden />
            <text x={44} y={22} className="rv-wfa__zone-t">{t("rv.wf2.zone")}</text>
          </g>

          {/* The track, and the light that travels along it. */}
          <line x1={nx(0)} y1={PIPE_Y} x2={nx(COUNT - 1)} y2={PIPE_Y} className="rv-wfa__track" />
          <line x1={nx(0)} y1={PIPE_Y} x2={nx(COUNT - 1)} y2={PIPE_Y} className="rv-wfa__flow" />
          <g className="rv-wfa__light" style={{ transform: `translate(${nx(0)}px, ${PIPE_Y}px)` }}>
            <circle r={26} fill="url(#rv-wfa-glow)" />
            <circle r={6} className="rv-wfa__spark" />
          </g>

          {STEPS.map((s, i) => {
            const Icon = s.icon;
            const lines = t(s.label).split("|");
            return (
              <g key={s.label} className="rv-wfa__step" style={{ animationDelay: `${(i * 0.85 * CYCLE) / (COUNT - 1)}s` }}>
                <circle cx={nx(i)} cy={PIPE_Y} r={36} className="rv-wfa__halo" />
                <circle cx={nx(i)} cy={PIPE_Y} r={34} className="rv-wfa__disc" />
                <Icon x={nx(i) - 14} y={PIPE_Y - 14} width={28} height={28} className="rv-wfa__icon" aria-hidden />
                <circle cx={nx(i) + 26} cy={PIPE_Y - 26} r={11} fill="url(#rv-wfa-grad)" />
                <text x={nx(i) + 26} y={PIPE_Y - 22} textAnchor="middle" className="rv-wfa__num">{i + 1}</text>
                {s.tools ? <Plug tools={s.tools} cx={nx(i)} /> : null}
                <text textAnchor="middle" className="rv-wfa__label">
                  {lines.map((line, k) => (
                    <tspan key={k} x={nx(i)} y={PIPE_Y + 76 + k * 20}>{line}</tspan>
                  ))}
                </text>
              </g>
            );
          })}

          {/* Follow-ups: one bracket under the conversation and the persuasion. */}
          <path d={`M ${relayLeft} ${RELAY_Y - 10} V ${RELAY_Y} H ${relayRight} V ${RELAY_Y - 10}`} className="rv-wfa__bracket" />
          <rect x={relayMid - relayW / 2} y={RELAY_Y - 16} width={relayW} height={32} rx={16} className="rv-wfa__chip" />
          <RefreshCw x={relayMid - relayW / 2 + 12} y={RELAY_Y - 8} width={16} height={16} className="rv-wfa__icon" aria-hidden />
          <text x={relayMid - relayW / 2 + 36} y={RELAY_Y + 5} className="rv-wfa__chip-t">{relayLabel}</text>

          {/* Always on, from the first step to the last: the CRM and the KPIs follow along. */}
          <rect x={52} y={LANE_Y} width={VIEW_W - 104} height={LANE_H} rx={LANE_H / 2} className="rv-wfa__lane" />
          <text x={80} y={LANE_Y + LANE_H / 2 + 4} className="rv-wfa__follow-t">{t("rv.wf2.follow")}</text>
          <line x1={LANE_ITEMS[0]!.x + 50} y1={LANE_Y + LANE_H / 2} x2={LANE_ITEMS[1]!.x - 50} y2={LANE_Y + LANE_H / 2} className="rv-wfa__flow rv-wfa__flow--short" />
          {LANE_ITEMS.map((it) => {
            const Icon = it.icon;
            return (
              <g key={it.label}>
                <rect x={it.x - 44} y={LANE_Y + 6} width={88} height={LANE_H - 12} rx={(LANE_H - 12) / 2} className="rv-wfa__chip" />
                <Icon x={it.x - 34} y={LANE_Y + LANE_H / 2 - 8} width={16} height={16} className="rv-wfa__icon" aria-hidden />
                <text x={it.x - 12} y={LANE_Y + LANE_H / 2 + 5} className="rv-wfa__chip-t">{t(it.label)}</text>
              </g>
            );
          })}

          {/* Who hands what to whom. */}
          <Handoff x={200} from={ZONE_BOTTOM + 4} to={PANEL_Y - 4} label={t("rv.wf2.a1")} side="right" />
          <Handoff x={560} from={ZONE_BOTTOM + 4} to={PANEL_Y - 4} label={t("rv.wf2.report")} side="right" />
          <Handoff x={1060} from={PANEL_Y - 4} to={ZONE_BOTTOM + 4} label={t("rv.wf2.a2")} side="left" />

          {/* The client. */}
          <rect x={24} y={PANEL_Y} width={VIEW_W - 48} height={PANEL_H} rx={26} className="rv-wfa__panel" />
          <circle cx={84} cy={PANEL_Y + PANEL_H / 2} r={28} className="rv-wfa__you" />
          <HandHelping x={84 - 14} y={PANEL_Y + PANEL_H / 2 - 14} width={28} height={28} className="rv-wfa__you-icon" aria-hidden />
          <text x={130} y={PANEL_Y + PANEL_H / 2 + 6} className="rv-wfa__you-t">{t("rv.wf2.you")}</text>
          {PILLS.map((p) => {
            const x0 = pillX;
            pillX += p.w + 10;
            return (
              <g key={p.label}>
                <rect x={x0} y={PANEL_Y + PANEL_H / 2 - 15} width={p.w} height={30} rx={15} className="rv-wfa__pill" />
                <text x={x0 + p.w / 2} y={PANEL_Y + PANEL_H / 2 + 5} textAnchor="middle" className="rv-wfa__pill-t">{t(p.label)}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </figure>
  );
}
