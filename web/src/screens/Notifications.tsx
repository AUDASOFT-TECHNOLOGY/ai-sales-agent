import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, HandHelping, Inbox, RotateCcw, ShieldCheck, X } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useStore } from "../mock/store";
import { NEED_KIND } from "../agent/NeedsYou";
import type { NeedResolution } from "../mock/types";
import { Chip, GlyphTile } from "../ui/Chip";
import type { Tint } from "../ui/Glass";
import { Segmented } from "../ui/Controls";

const RESOLUTION: Record<NeedResolution, { tint: Tint; icon: typeof Check; labelKey: MessageKey }> = {
  approved: { tint: "success", icon: Check, labelKey: "notifications.resolution.approved" },
  declined: { tint: "neutral", icon: X, labelKey: "notifications.resolution.declined" },
  handled: { tint: "success", icon: HandHelping, labelKey: "notifications.resolution.handled" },
  confirmed: { tint: "success", icon: ShieldCheck, labelKey: "notifications.resolution.confirmed" },
  dismissed: { tint: "neutral", icon: RotateCcw, labelKey: "notifications.resolution.dismissed" },
};

type Filter = "all" | "pending" | "resolved";

/** The bell's "View all": every decision REVANSA has ever raised, still waiting or already
 *  settled, so nothing a person acted on from the header disappears without a trace. Pending
 *  items reuse the same kind styling as the Dashboard's "Needs you" section (agent/NeedsYou.tsx);
 *  settled ones carry the outcome as a second chip. */
export function Notifications() {
  const { t, n } = useI18n();
  const { state } = useStore();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");

  const pending = state.needs;
  const resolved = state.needHistory;
  const total = pending.length + resolved.length;
  const showPending = filter !== "resolved";
  const showResolved = filter !== "pending";
  const shown = (showPending ? pending.length : 0) + (showResolved ? resolved.length : 0);

  return (
    <div className="page page--focus notifications-page">
      <div className="page-head">
        <div>
          <h2 className="t-h1">{t("notifications.title")}</h2>
          <p className="c-2 tabular">
            {total === 0 ? t("notifications.empty") : total === 1 ? t("notifications.count.one") : t("notifications.count.many", { n: n(total) })}
          </p>
        </div>
        <Segmented
          layoutId="seg-notifications-filter"
          label={t("notifications.filter")}
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: t("notifications.filter.all") },
            { value: "pending", label: t("notifications.filter.pending") },
            { value: "resolved", label: t("notifications.filter.resolved") },
          ]}
        />
      </div>

      {shown === 0 ? (
        <div className="notif-empty notif-empty--page">
          <Inbox size={24} aria-hidden className="c-3" />
          <p className="c-2">{t("notifications.emptyFiltered")}</p>
        </div>
      ) : (
        <ul className="notif-history-list">
          {showPending
            ? pending.map((item) => {
                const prospect = state.prospects.find((p) => p.id === item.prospectId);
                if (!prospect) return null;
                const cfg = NEED_KIND[item.kind];
                const Icon = cfg.icon;
                return (
                  <li key={item.id}>
                    <button type="button" className="notif-history-row" onClick={() => navigate(`/conversations/${item.prospectId}`)}>
                      <GlyphTile tint={cfg.tint} size={40}>
                        <Icon size={18} />
                      </GlyphTile>
                      <span className="notif-history-row__text">
                        <span className="notif-history-row__top">
                          <span className="t-label">{prospect.name}</span>
                          <span className="c-3 t-body-sm">{t(item.waiting)}</span>
                        </span>
                        <span className="row notif-history-row__chips">
                          <Chip tint={cfg.tint}>{t(cfg.labelKey)}</Chip>
                          <Chip tint="attention" outline>
                            {t("notifications.filter.pending")}
                          </Chip>
                        </span>
                        <span className="c-2 t-body-sm">{t(item.reason)}</span>
                      </span>
                    </button>
                  </li>
                );
              })
            : null}
          {showResolved
            ? resolved.map((item) => {
                const prospect = state.prospects.find((p) => p.id === item.prospectId);
                if (!prospect) return null;
                const cfg = NEED_KIND[item.kind];
                const rcfg = RESOLUTION[item.resolution];
                const RIcon = rcfg.icon;
                return (
                  <li key={item.id}>
                    <button type="button" className="notif-history-row" onClick={() => navigate(`/conversations/${item.prospectId}`)}>
                      <GlyphTile tint={rcfg.tint} size={40} ghost>
                        <RIcon size={18} />
                      </GlyphTile>
                      <span className="notif-history-row__text">
                        <span className="notif-history-row__top">
                          <span className="t-label">{prospect.name}</span>
                          <span className="c-3 t-body-sm">{t(item.resolvedAt)}</span>
                        </span>
                        <span className="row notif-history-row__chips">
                          <Chip tint={cfg.tint} outline>
                            {t(cfg.labelKey)}
                          </Chip>
                          <Chip tint={rcfg.tint}>{t(rcfg.labelKey)}</Chip>
                        </span>
                        <span className="c-2 t-body-sm">{t(item.reason)}</span>
                      </span>
                    </button>
                  </li>
                );
              })
            : null}
        </ul>
      )}
    </div>
  );
}
