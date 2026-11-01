/** Illustrations of the public landing: inline SVG and CSS only, no image to load. Everything here is decoration
 *  or a labelled example (the board's prospects are fictional); motion stops with prefers-reduced-motion. */

import { Bot, Camera, Check, Cloud, Hand, HandHelping, Link2, Mail, MessageCircle, Moon, Send, Sun, Tag, Wallet } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { cx } from "../ui/cx";

type Vars = Record<string, string | number>;
const vars = (v: Vars) => v as React.CSSProperties;

/* ── Orbit: one agent in the middle, the three channels turning around it ── */

export function Orbit() {
  const { t } = useI18n();
  const nodes = [
    { Icon: Mail, cls: "email", a: 0 },
    { Icon: Send, cls: "telegram", a: 120 },
    { Icon: MessageCircle, cls: "whatsapp", a: 240 },
  ];
  return (
    <div className="art-orbit" role="img" aria-label={t("lp.orbit.label")}>
      <span className="art-orbit__ping" aria-hidden />
      <span className="art-orbit__ping art-orbit__ping--2" aria-hidden />
      <div className="art-orbit__ring" aria-hidden>
        {nodes.map(({ Icon, cls, a }) => (
          <span key={cls} className="art-orbit__slot" style={vars({ "--a": `${a}deg` })}>
            <span className={`lp-ch lp-ch--${cls} art-orbit__node`}>
              <Icon size={20} />
            </span>
          </span>
        ))}
      </div>
      <span className="art-orbit__core" aria-hidden>
        <Bot size={40} />
      </span>
    </div>
  );
}

/* ── 24-hour ring: a person covers part of the day, the agent all of it ── */

export function DayRing() {
  const { t } = useI18n();
  return (
    <div className="art-day" role="img" aria-label={t("lp.clock.label")}>
      <svg viewBox="0 0 220 220" aria-hidden>
        <circle className="art-day__track" cx="110" cy="110" r="92" />
        <circle className="art-day__agent" cx="110" cy="110" r="92" pathLength="100" />
        <circle className="art-day__track" cx="110" cy="110" r="66" />
        <circle className="art-day__you" cx="110" cy="110" r="66" pathLength="100" />
      </svg>
      <span className="art-day__sun" aria-hidden>
        <Sun size={18} />
      </span>
      <span className="art-day__moon" aria-hidden>
        <Moon size={18} />
      </span>
      <span className="art-day__center" dir="ltr" aria-hidden>
        24h
      </span>
      <ul className="art-day__legend" aria-hidden>
        <li className="art-day__key art-day__key--agent">{t("lp.clock.agent")}</li>
        <li className="art-day__key art-day__key--you">{t("lp.clock.you")}</li>
      </ul>
    </div>
  );
}

/* ── Prospects advancing from column to column ── */

const HUES = [265, 200, 155];

export function Avatar({ name, hue }: { name: string; hue: number }) {
  return (
    <span className="art-avatar" style={vars({ "--hue": hue })} aria-hidden>
      {name.slice(0, 1)}
    </span>
  );
}

export function Board() {
  const { t } = useI18n();
  const stages: MessageKey[] = ["lp.board.s1", "lp.board.s2", "lp.board.s3", "lp.board.s4"];
  const people: Array<{ key: MessageKey; ch: "email" | "telegram" | "whatsapp" }> = [
    { key: "lp.board.p1", ch: "email" },
    { key: "lp.board.p2", ch: "telegram" },
    { key: "lp.board.p3", ch: "whatsapp" },
  ];
  return (
    <div className="lp-glass art-board" role="img" aria-label={t("lp.board.label")}>
      <ul className="art-board__heads" aria-hidden>
        {stages.map((s, i) => (
          <li key={s} className={cx(i === 3 && "is-last")}>
            {t(s)}
          </li>
        ))}
      </ul>
      <div className="art-board__lanes" aria-hidden>
        {people.map((p, i) => (
          <div key={p.ch} className="art-board__lane">
            <div className="art-board__card" style={vars({ "--d": `${-i * 3.2}s` })}>
              <span className="art-board__inner">
                <Avatar name={t(p.key)} hue={HUES[i]!} />
                <b>{t(p.key)}</b>
                <i className={`art-board__dot art-board__dot--${p.ch}`} />
                <Check className="art-board__ok" size={15} />
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Small scenes for the four benefits and the three steps ── */

export function SlidersArt() {
  return (
    <div className="art-scene art-sliders" aria-hidden>
      {[0, 1, 2].map((i) => (
        <span key={i} className="art-sliders__row">
          <i style={vars({ "--k": i })} />
        </span>
      ))}
    </div>
  );
}

export function CardsArt() {
  return (
    <div className="art-scene art-cards" aria-hidden>
      <span className="art-cards__c art-cards__c--1">
        <Camera size={22} />
      </span>
      <span className="art-cards__c art-cards__c--2">
        <Tag size={22} />
      </span>
      <span className="art-cards__c art-cards__c--3">
        <Link2 size={22} />
      </span>
    </div>
  );
}

export function ClockArt() {
  return (
    <div className="art-scene art-clock" aria-hidden>
      <svg viewBox="0 0 80 80">
        <circle cx="40" cy="40" r="34" className="art-clock__face" />
        {Array.from({ length: 12 }, (_, i) => (
          <line key={i} x1="40" y1="10" x2="40" y2={i % 3 === 0 ? 17 : 14} transform={`rotate(${i * 30} 40 40)`} className="art-clock__tick" />
        ))}
        <line x1="40" y1="40" x2="40" y2="20" className="art-clock__hand art-clock__hand--m" />
        <line x1="40" y1="40" x2="40" y2="27" className="art-clock__hand art-clock__hand--h" />
        <circle cx="40" cy="40" r="3" className="art-clock__pin" />
      </svg>
    </div>
  );
}

export function CloudArt() {
  return (
    <div className="art-scene art-cloud" aria-hidden>
      <Cloud size={64} strokeWidth={1.5} />
      <span className="art-cloud__ok">
        <Check size={14} />
      </span>
      <span className="art-cloud__bars">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}

export function CoinsArt() {
  return (
    <div className="art-scene art-coins" aria-hidden>
      <i />
      <i />
      <i />
      <Wallet size={44} strokeWidth={1.6} />
    </div>
  );
}

export function ReportArt() {
  return (
    <div className="art-scene art-report" aria-hidden>
      <span className="art-report__bars">
        <i style={vars({ "--k": 0 })} />
        <i style={vars({ "--k": 1 })} />
        <i style={vars({ "--k": 2 })} />
        <i style={vars({ "--k": 3 })} />
      </span>
      <span className="art-report__sent">
        <Mail size={16} />
      </span>
    </div>
  );
}

export function TalkArt() {
  return (
    <div className="art-scene art-talk" aria-hidden>
      <span className="art-talk__b art-talk__b--1" />
      <span className="art-talk__b art-talk__b--2" />
      <span className="art-talk__b art-talk__b--3" />
    </div>
  );
}

/* ── You / agent switch ── */

export function ControlArt() {
  const { t } = useI18n();
  return (
    <div className="lp-glass art-control" aria-hidden>
      <div className="art-toggle">
        <span className="art-toggle__thumb" />
        <span className="art-toggle__opt art-toggle__opt--agent">
          <Bot size={16} /> {t("lp.control.agent")}
        </span>
        <span className="art-toggle__opt art-toggle__opt--you">
          <Hand size={16} /> {t("lp.control.you")}
        </span>
      </div>
      <div className="art-control__chat">
        <p className="lp-msg-static art-control__b art-control__b--agent">{t("lp.demo.a")}</p>
        <p className="lp-msg-static art-control__b art-control__b--you">
          <HandHelping size={15} /> {t("lp.control.you")}
        </p>
      </div>
    </div>
  );
}
