import { useEffect, useState } from "react";

/** Runs `load` whenever its identity changes (wrap it in `useCallback`; a new one is a new request,
 *  which is also how a retry is made). `loading` is true until the answer to the *current* `load`
 *  arrived, so a stale answer is never shown for a new request. */
export function useFetched<T>(load: () => Promise<T>): { loading: boolean; value?: T; error?: unknown; failed: boolean } {
  const [done, setDone] = useState<{ source: () => Promise<T>; value?: T; error?: unknown; failed: boolean } | null>(null);
  useEffect(() => {
    let cancelled = false;
    load().then(
      (value) => {
        if (!cancelled) setDone({ source: load, value, failed: false });
      },
      (error: unknown) => {
        if (!cancelled) setDone({ source: load, error, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load]);
  const current = done?.source === load ? done : null;
  return { loading: current === null, value: current?.value, error: current?.error, failed: current?.failed ?? false };
}
