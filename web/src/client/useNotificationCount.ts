import { appFetch } from "../demo/transport";
import { useEffect, useState } from "react";

/** How many notifications are unread (GET /client/v1/notifications), for the menu badge. Re-read when
 *  `refreshKey` changes (a page change). 0 when it cannot be read: a badge is never invented. */
export function useNotificationCount(refreshKey: string): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    appFetch("/client/v1/notifications", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(
        (page: { unread_count?: number }) => !cancelled && setCount(Number(page.unread_count ?? 0)),
        () => undefined,
      );
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);
  return count;
}
