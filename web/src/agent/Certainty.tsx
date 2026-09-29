import { MessageCircle, Quote, Settings2, Sparkles, Table2, User } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import type { Fact, FactSource, FactStatus } from "../mock/types";
import { PopoverCard } from "../ui/Overlays";

const SOURCE_ICON: Record<FactSource, typeof User> = {
  prospect: MessageCircle,
  conversation: Quote,
  client_data: Table2,
  human: User,
  system: Settings2,
  brain_inference: Sparkles,
};

const SOURCE_LABEL: Record<FactSource, string> = {
  prospect: "They said so",
  conversation: "Read from the conversation",
  client_data: "From your data",
  human: "Entered by a person",
  system: "Computed by the platform",
  brain_inference: "My own inference",
};

/** Tint follows the class of the source: prospect and conversation ice, people and data emerald, system neutral, inference aether. */
const SOURCE_TINT: Record<FactSource, string> = {
  prospect: "var(--tint-action)",
  conversation: "var(--tint-action)",
  client_data: "var(--tint-success)",
  human: "var(--tint-success)",
  system: "var(--tint-neutral)",
  brain_inference: "var(--tint-agent)",
};

/** Certainty ring (doc 15.5): solid = confirmed, three-quarter arc = learned, dotted = a guess. Shape, not color alone. */
export function CertaintyRing({ status, source = "prospect", size = 14 }: { status: FactStatus; source?: FactSource; size?: number }) {
  const c = 7;
  const r = 5.2;
  const circ = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} aria-hidden className="certainty-ring" style={{ color: `rgb(${SOURCE_TINT[source]})` }} data-status={status}>
      {status === "confirmed" ? (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.6" />
          <circle cx={c} cy={c} r={2.2} fill="currentColor" />
        </>
      ) : null}
      {status === "discovered" ? (
        <circle
          cx={c}
          cy={c}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeDasharray={`${circ * 0.75} ${circ}`}
          transform={`rotate(-90 ${c} ${c})`}
        />
      ) : null}
      {status === "hypothesis" ? (
        <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeDasharray="0.1 2.6" opacity={0.7} />
      ) : null}
    </svg>
  );
}

export function CertaintyLegend() {
  const { t } = useI18n();
  return (
    <ul className="certainty-legend" aria-label={t("prospects.legend")}>
      {(["confirmed", "discovered", "hypothesis"] as FactStatus[]).map((s) => (
        <li key={s}>
          <CertaintyRing status={s} size={14} />
          <span className="c-3 t-body-sm">{t(`certainty.${s}` as MessageKey)}</span>
        </li>
      ))}
    </ul>
  );
}

/** A fact as a chip: ring, value, source glyph. The whole chip opens Evidence. */
export function FactChip({ fact }: { fact: Fact }) {
  const { t } = useI18n();
  const SourceIcon = SOURCE_ICON[fact.source];
  const hyp = fact.status === "hypothesis";
  return (
    <PopoverCard
      label={`${fact.key}: ${fact.value}`}
      trigger={
        <button type="button" className="fact-chip" data-status={fact.status}>
          <CertaintyRing status={fact.status} source={fact.source} size={16} />
          <span className="fact-chip__key c-3">{fact.key}</span>
          <span className={hyp ? "fact-chip__val fact-chip__val--guess" : "fact-chip__val"}>{fact.value}</span>
          <SourceIcon size={13} aria-hidden className="c-3" />
          <span className="sr-only">{t(`certainty.${fact.status}` as MessageKey)}</span>
        </button>
      }
    >
      <div className="evidence">
        <div className="row">
          <CertaintyRing status={fact.status} source={fact.source} size={18} />
          <p className="t-label">
            {fact.key}: {fact.value}
          </p>
        </div>
        <p className="c-2 t-body-sm">
          {t(`certainty.${fact.status}` as MessageKey)} · {SOURCE_LABEL[fact.source]}
        </p>
        {fact.excerpt ? <blockquote className="evidence__quote">“{fact.excerpt}”</blockquote> : <p className="c-3 t-body-sm">No message is attached to this.</p>}
        {fact.when ? <p className="c-3 t-body-sm">{fact.when}</p> : null}
      </div>
    </PopoverCard>
  );
}

/** Facts grouped by certainty. A guess only ever appears inside its own group (doc 15.5). */
export function CertaintyPanel({ facts, hideTitle }: { facts: Fact[]; hideTitle?: boolean }) {
  const { t } = useI18n();
  const groups: FactStatus[] = ["confirmed", "discovered", "hypothesis"];
  return (
    <div className="certainty-panel">
      <div className="row row--wrap">
        {hideTitle ? null : <h3 className="t-label c-2">{t("prospects.knows")}</h3>}
        <span className="spacer" />
        <CertaintyLegend />
      </div>
      {facts.length === 0 ? <p className="c-3 t-body-sm">Nothing known yet.</p> : null}
      {groups.map((g) => {
        const items = facts.filter((f) => f.status === g);
        if (items.length === 0) return null;
        return (
          <div key={g} className="certainty-group">
            <p className="t-label c-3">{t(`certainty.${g}` as MessageKey)}</p>
            <div className="row row--wrap">
              {items.map((f) => (
                <FactChip key={`${f.key}-${f.value}`} fact={f} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
