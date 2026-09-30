import * as Popover from "@radix-ui/react-popover";
import { useNavigate } from "react-router-dom";
import { Bell, Inbox } from "lucide-react";
import { useI18n } from "../i18n";
import { useStore } from "../mock/store";
import { NEED_KIND } from "../agent/NeedsYou";
import { Chip, GlyphTile } from "../ui/Chip";
import { IconButton } from "../ui/Button";

/** Bell in the header (doc 21): a popover onto the same decisions the Dashboard's "Needs you"
 *  section lists, so a person can act on one from anywhere without leaving the page they're on. */
export function NotificationsBell() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { state } = useStore();
  const needs = state.needs;
  const count = state.live ? needs.length : 0;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <IconButton label={`${t("bar.needsYou")}: ${count}`} className="bar__bell">
          <Bell size={20} />
          {count > 0 ? <span className="bar__bell-dot tabular">{count}</span> : null}
        </IconButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="popover glass notif-panel" data-level="2" side="bottom" align="end" sideOffset={8} collisionPadding={12} aria-label={t("notifications.title")}>
          <div className="notif-panel__head">
            <p className="t-label">{t("notifications.title")}</p>
            {count > 0 ? <span className="c-3 t-body-sm tabular">{count}</span> : null}
          </div>
          {needs.length === 0 ? (
            <div className="notif-empty">
              <Inbox size={20} aria-hidden className="c-3" />
              <p className="c-3 t-body-sm">{t("notifications.empty")}</p>
            </div>
          ) : (
            <ul className="notif-list">
              {needs.map((item) => {
                const prospect = state.prospects.find((p) => p.id === item.prospectId);
                if (!prospect) return null;
                const cfg = NEED_KIND[item.kind];
                const Icon = cfg.icon;
                return (
                  <li key={item.id}>
                    <Popover.Close asChild>
                      <button type="button" className="notif-row" onClick={() => navigate(`/conversations/${item.prospectId}`)}>
                        <GlyphTile tint={cfg.tint} size={32}>
                          <Icon size={16} />
                        </GlyphTile>
                        <span className="notif-row__text">
                          <span className="notif-row__top">
                            <span className="t-body-sm notif-row__name">{prospect.name}</span>
                            <span className="c-3 t-body-sm notif-row__time">{t(item.waiting)}</span>
                          </span>
                          <Chip tint={cfg.tint}>{t(cfg.labelKey)}</Chip>
                          <span className="c-2 t-body-sm notif-row__reason">{t(item.reason)}</span>
                        </span>
                      </button>
                    </Popover.Close>
                  </li>
                );
              })}
            </ul>
          )}
          <Popover.Close asChild>
            <button type="button" className="notif-panel__foot link-btn" onClick={() => navigate("/notifications")}>
              {t("notifications.viewAll")}
            </button>
          </Popover.Close>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
