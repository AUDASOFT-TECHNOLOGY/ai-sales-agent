import { ArrowDown, ArrowUp, Lock, Minus, Plus, Trash2 } from "lucide-react";
import { motion } from "motion/react";
import { useI18n, type MessageKey } from "../i18n";
import type { CadenceStep, Offer } from "../mock/types";

export type { CadenceStep };
import { Button, IconButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { VerbatimBadge } from "../ui/Controls";

/** An offer as a poster-like card with a completeness ring, not a table row (doc 13.5). The
 *  selectable content is its own button; delete/reorder live in a sibling row so no interactive
 *  control is ever nested inside another (invalid HTML, breaks keyboard/AT navigation). */
export function OfferCard({
  offer,
  selected,
  onSelect,
  index,
  onDelete,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
}: {
  offer: Offer;
  selected: boolean;
  onSelect: () => void;
  index: number;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  const { t } = useI18n();
  const C = 2 * Math.PI * 22;
  const statusTint = offer.status === "active" ? "success" : offer.status === "coming_soon" ? "attention" : "neutral";
  const statusLabel = offer.status === "active" ? t("offers.status.active") : offer.status === "coming_soon" ? t("offers.status.comingSoon") : t("offers.status.inactive");
  const priced = offer.price !== "Not set" && offer.price !== "On request";
  return (
    <motion.li layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, delay: index * 0.05, ease: [0.16, 1, 0.3, 1] }}>
      <Glass level={selected ? 3 : 1} tint="action" className="offer" data-selected={selected || undefined}>
        <div className="offer__actions">
          <IconButton label={t("offers.move.earlier")} onClick={onMoveUp} disabled={!canMoveUp}>
            <ArrowUp size={16} />
          </IconButton>
          <IconButton label={t("offers.move.later")} onClick={onMoveDown} disabled={!canMoveDown}>
            <ArrowDown size={16} />
          </IconButton>
          <IconButton label={t("offers.delete", { name: offer.name })} onClick={onDelete}>
            <Trash2 size={16} />
          </IconButton>
        </div>
        <button type="button" className="offer-btn offer-btn--inline" onClick={onSelect} aria-pressed={selected}>
          <div className="offer__top">
            <span className="offer__ring" aria-label={t("offers.completeness", { pct: Math.round(offer.completeness * 100) })} role="img">
              <svg viewBox="0 0 52 52" width={52} height={52} aria-hidden>
                <circle cx="26" cy="26" r="22" fill="none" stroke="rgb(255 255 255 / 0.1)" strokeWidth="3" />
                <motion.circle
                  cx="26"
                  cy="26"
                  r="22"
                  fill="none"
                  stroke={offer.completeness >= 1 ? "var(--emerald-400)" : "var(--ice-400)"}
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeDasharray={C}
                  initial={{ strokeDashoffset: C }}
                  animate={{ strokeDashoffset: C * (1 - offer.completeness) }}
                  transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
                  transform="rotate(-90 26 26)"
                />
              </svg>
              <span className="offer__initial">{offer.name.slice(0, 1)}</span>
            </span>
            <Chip tint={statusTint}>{statusLabel}</Chip>
          </div>
          <h3 className="t-h3">{offer.name}</h3>
          <p className="c-2 t-body-sm">{offer.summary}</p>
          <div className="offer__price">
            {priced ? (
              <>
                <span className="t-mono">{offer.price}</span>
                <VerbatimBadge />
              </>
            ) : (
              <span className="c-3 t-body-sm">{offer.price}</span>
            )}
          </div>
          {offer.markets.length > 0 ? (
            <div className="row row--wrap">
              {offer.markets.map((m) => (
                <Chip key={m} tint="action" outline>
                  {m}
                </Chip>
              ))}
            </div>
          ) : null}
          {offer.missing.length > 0 ? (
            <p className="c-attention t-body-sm">
              {t("offers.missing", {
                list: offer.missing.slice(0, 2).map((k) => t(k, { brand: t("brand.name") })).join(", ") + (offer.missing.length > 2 ? "…" : ""),
              })}
            </p>
          ) : null}
        </button>
      </Glass>
    </motion.li>
  );
}

const MAX_REMINDERS = 4;

/** Follow-up cadence as a horizontal timeline; the platform's minimum gap and reminder limit are drawn as locked zones (doc 17.2). */
export function CadenceEditor({ steps, onChange }: { steps: CadenceStep[]; onChange: (steps: CadenceStep[]) => void }) {
  const { t } = useI18n();
  const SCALE = 16;
  const atCap = steps.length >= MAX_REMINDERS;
  const cumulative = steps.reduce<number[]>((acc, s) => [...acc, (acc[acc.length - 1] ?? 0) + s.days], []);
  const pos = (d: number) => `${(d / SCALE) * 100}%`;
  return (
    <div className="cadence">
      <div className="cadence__rail" aria-hidden>
        <div className="cadence__gap" title={t("offers.cadence.gapHint")}>
          <Lock size={12} />
        </div>
        <span className="cadence__line" />
        <div className="cadence__cap" style={{ insetInlineStart: pos(SCALE * 0.82) }}>
          <Lock size={12} />
        </div>
        <span className="cadence__node cadence__node--start" style={{ insetInlineStart: 0 }} />
        {cumulative.map((d, i) => (
          <motion.span key={i} className="cadence__node" layout animate={{ insetInlineStart: pos(d) }} transition={{ type: "spring", stiffness: 260, damping: 26 }}>
            <span className="cadence__day tabular">{d}d</span>
          </motion.span>
        ))}
      </div>
      <ul className="cadence__steps">
        <li className="cadence__step">
          <span className="t-label c-2">{t("offers.cadence.firstMessage")}</span>
          <span className="c-3 t-body-sm">{t("offers.cadence.day0")}</span>
        </li>
        {steps.map((s, i) => {
          const stepLabel = t(s.label as MessageKey);
          return (
          <li key={i} className="cadence__step">
            <span className="t-label c-2">{stepLabel}</span>
            <span className="row">
              <button
                type="button"
                className="icon-btn cadence__btn"
                aria-label={t("offers.cadence.fewerDays", { label: stepLabel })}
                disabled={s.days <= 1}
                onClick={() => onChange(steps.map((x, j) => (j === i ? { ...x, days: x.days - 1 } : x)))}
              >
                <Minus size={16} />
              </button>
              <span className="tabular t-body-sm cadence__val">{t("offers.cadence.daysAfter", { n: s.days })}</span>
              <button
                type="button"
                className="icon-btn cadence__btn"
                aria-label={t("offers.cadence.moreDays", { label: stepLabel })}
                disabled={cumulative[i]! >= SCALE * 0.78}
                onClick={() => onChange(steps.map((x, j) => (j === i ? { ...x, days: x.days + 1 } : x)))}
              >
                <Plus size={16} />
              </button>
            </span>
          </li>
          );
        })}
      </ul>
      <div className="row row--wrap">
        <Button
          size="sm"
          variant="secondary"
          leading={<Plus size={16} aria-hidden />}
          disabledReason={atCap ? t("offers.cadence.capReason", { n: MAX_REMINDERS }) : undefined}
          onClick={() => onChange([...steps, { days: 3, label: t("offers.cadence.reminderLabel", { n: steps.length + 1 }) }])}
        >
          {t("offers.cadence.add")}
        </Button>
        <span className="c-3 t-body-sm">
          {t("offers.cadence.summary", { followup: t("configure.tab.followup"), n: MAX_REMINDERS })}
        </span>
      </div>
    </div>
  );
}
