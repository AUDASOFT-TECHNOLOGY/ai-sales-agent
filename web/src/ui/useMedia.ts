import { useSyncExternalStore } from "react";

const supported = () => typeof window !== "undefined" && typeof window.matchMedia === "function";

/** Subscribes to a media query (used for inspector versus drawer and other responsive switches).
 *  Where media queries do not exist (a test DOM) the query simply does not match. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (!supported()) return () => undefined;
      const mq = window.matchMedia(query);
      mq.addEventListener("change", notify);
      return () => mq.removeEventListener("change", notify);
    },
    () => (supported() ? window.matchMedia(query).matches : false),
    () => false,
  );
}
