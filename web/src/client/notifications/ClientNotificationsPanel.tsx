/** The client's own notifications (GET /client/v1/notifications). Self-contained: its texts live in
 *  this folder (not in the shared i18n files) and fall back to the server's sentence for a kind it
 *  does not know. The session decides whose notifications these are; nothing here names a client.
 *
 *  Shown twice: in full on `/notifications`, and in a short form (the latest few) in the bell menu. */

import { appFetch } from "../../demo/transport";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, BellOff, Check, Info, XCircle } from "lucide-react";
import { useI18n } from "../../i18n";
import { Button } from "../../ui/Button";
import { Glass } from "../../ui/Glass";
import { notificationText } from "./texts";

interface ClientNotification {
  id: number;
  created_at: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string;
  action: string;
  read_at: string | null;
}

interface Page {
  items: ClientNotification[];
  total: number;
  unread_count: number;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await appFetch(`/client/v1${path}`, { credentials: "include", ...init });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as T;
}

const ICON = { info: Info, warning: AlertTriangle, critical: XCircle } as const;
const TINT = { info: "action", warning: "attention", critical: "danger" } as const;

export function ClientNotificationsPanel({ compact, limit, onChanged }: { compact?: boolean; limit?: number; onChanged?: () => void }) {
  const { locale: lang, d } = useI18n();
  const [page, setPage] = useState<Page | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    call<Page>("/notifications")
      .then((result) => {
        setPage(result);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, []);

  useEffect(load, [load]);

  const t = (key: "title" | "empty" | "markAll" | "markRead" | "error") => notificationText(lang, key);
  const act = (path: string) => void call(path, { method: "POST" }).then(() => {
    load();
    onChanged?.();
  });
  const items = page ? (limit ? page.items.slice(0, limit) : page.items) : [];

  return (
    <section aria-label={t("title")} className="rv-notes">
      {compact ? null : (
        <h2 className="sr-only">
          {t("title")}
          {page && page.unread_count > 0 ? ` (${page.unread_count})` : ""}
        </h2>
      )}
      {failed && (
        <p role="alert" className="admin-alert admin-alert--danger">
          {t("error")}
        </p>
      )}
      {page && page.items.length === 0 && (
        <div className="rv-notes__empty">
          <BellOff size={22} aria-hidden />
          <p className="c-2">{t("empty")}</p>
        </div>
      )}
      {page && page.items.length > 0 && (
        <>
          {page.unread_count > 0 ? (
            <div className="rv-notes__bar">
              <Button size="sm" variant="ghost" leading={<Check size={14} aria-hidden />} onClick={() => act("/notifications/read-all")}>
                {t("markAll")}
              </Button>
            </div>
          ) : null}
          <ul className="rv-notes__list">
            {items.map((n) => {
              const text = notificationText(lang, n.kind, n);
              const Icon = ICON[n.severity] ?? Info;
              return (
                <li key={n.id}>
                  <Glass level={compact ? 1 : 2} tint={TINT[n.severity] ?? "action"} className="rv-note-item" data-severity={n.severity} data-unread={n.read_at ? undefined : true}>
                    <span className="rv-note-item__icon" aria-hidden>
                      <Icon size={18} />
                    </span>
                    <div className="rv-note-item__body">
                      <strong>{text.title}</strong>
                      <p>{text.message}</p>
                      {text.action && <p className="c-3">{text.action}</p>}
                      <span className="c-3 t-body-sm">{d(n.created_at, { dateStyle: "medium", timeStyle: "short" })}</span>
                    </div>
                    {!n.read_at && (
                      <Button size="sm" variant="ghost" onClick={() => act(`/notifications/${n.id}/read`)}>
                        {t("markRead")}
                      </Button>
                    )}
                  </Glass>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
