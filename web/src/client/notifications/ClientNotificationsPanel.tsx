/** The client's own notifications (GET /client/v1/notifications). Self-contained: its texts live in
 *  this folder (not in the shared i18n files) and fall back to the server's sentence for a kind it
 *  does not know. The session decides whose notifications these are; nothing here names a client. */

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "../../i18n";
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
  const response = await fetch(`/client/v1${path}`, { credentials: "include", ...init });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as T;
}

export function ClientNotificationsPanel() {
  const { locale: lang } = useI18n();
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

  return (
    <section aria-label={t("title")}>
      <h2 className="t-h2">
        {t("title")}
        {page && page.unread_count > 0 ? ` (${page.unread_count})` : ""}
      </h2>
      {failed && <p role="alert">{t("error")}</p>}
      {page && page.items.length === 0 && <p className="c-3">{t("empty")}</p>}
      {page && page.items.length > 0 && (
        <>
          <button type="button" onClick={() => void call("/notifications/read-all", { method: "POST" }).then(load)}>
            {t("markAll")}
          </button>
          <ul>
            {page.items.map((n) => {
              const text = notificationText(lang, n.kind, n);
              return (
                <li key={n.id} data-severity={n.severity} className={n.read_at ? "is-read" : "is-unread"}>
                  <strong>{text.title}</strong>
                  <p>{text.message}</p>
                  {text.action && <p className="c-3">{text.action}</p>}
                  {!n.read_at && (
                    <button type="button" onClick={() => void call(`/notifications/${n.id}/read`, { method: "POST" }).then(load)}>
                      {t("markRead")}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
