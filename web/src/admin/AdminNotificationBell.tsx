/** The Admin's bell: unread count and the open notifications, each with its suggested action. */

import { useCallback, useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { IconButton } from "../ui/Button";
import { fetchNotifications, markAllNotificationsRead, markNotificationRead, type AdminNotificationPage } from "./auditApi";

const REFRESH_MS = 60_000;

export function AdminNotificationBell() {
  const [page, setPage] = useState<AdminNotificationPage | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    fetchNotifications().then(setPage).catch(() => setPage(null));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const unread = page?.unread_count ?? 0;

  return (
    <div className="admin-bell">
      <IconButton label={unread ? `Notifications (${unread} unread)` : "Notifications"} onClick={() => setOpen((v) => !v)}>
        <Bell size={18} aria-hidden />
        {unread > 0 && <span className="admin-bell__count">{unread}</span>}
      </IconButton>
      {open && (
        <div className="admin-bell__panel" role="dialog" aria-label="Notifications">
          {page && page.items.length > 0 ? (
            <>
              <button type="button" className="t-body-sm" onClick={() => void markAllNotificationsRead().then(load)}>
                Mark all as read
              </button>
              <ul>
                {page.items.map((n) => (
                  <li key={n.id} className={n.read_at ? "is-read" : "is-unread"} data-severity={n.severity}>
                    <strong className="t-label">{n.title}</strong>
                    {n.client_id && <span className="c-3 t-body-sm"> · {n.client_id}</span>}
                    <p className="t-body-sm">{n.message}</p>
                    {n.action && <p className="c-3 t-body-sm">{n.action}</p>}
                    {!n.read_at && (
                      <button type="button" className="t-body-sm" onClick={() => void markNotificationRead(n.id).then(load)}>
                        Mark as read
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="c-3 t-body-sm">Nothing needs your attention.</p>
          )}
        </div>
      )}
    </div>
  );
}
