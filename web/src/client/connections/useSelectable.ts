/** What the client can choose on a circuit today. A channel or a source whose connection Revansa has not
 *  finished setting up is still shown, but greyed: the client sees it exists and when it will be usable.
 *  What is already chosen is never greyed, whatever happens on Revansa's side. */

import { useEffect, useState } from "react";
import { clientConnections, type ConnectionsInfo } from "../api";

export type Selectable = ConnectionsInfo["selectable"];

/** `null` until the server has answered, or when it cannot: then nothing is greyed on a guess. */
export function useSelectable(): Selectable | null {
  const [selectable, setSelectable] = useState<Selectable | null>(null);
  useEffect(() => {
    let cancelled = false;
    clientConnections().then(
      (info) => !cancelled && setSelectable(info.selectable),
      () => !cancelled && setSelectable(null),
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return selectable;
}

export const CHANNEL_KEYS = ["email", "whatsapp", "telegram"] as const;

export interface Choice {
  key: string;
  label: string;
  /** Revansa has not finished (or the Admin has not opened it): shown, not choosable. */
  pending: boolean;
}

/** Every channel Revansa has, in a fixed order: the open ones as the server names them, the others greyed. */
export function channelChoices(options: Array<{ key: string; label: string }>, selectable: Selectable | null, current: string | null, nameOf: (key: string) => string): Choice[] {
  const known = new Set<string>(CHANNEL_KEYS);
  const keys = [...CHANNEL_KEYS, ...options.map((o) => o.key).filter((k) => !known.has(k))];
  return keys.map((key) => {
    const option = options.find((o) => o.key === key);
    const pending = key !== current && (!option || selectable?.channels[key] === false);
    return { key, label: option?.label ?? nameOf(key), pending };
  });
}

/** Whether a source cannot be chosen yet. */
export function sourcePending(selectable: Selectable | null, key: string, current: string | null): boolean {
  return key !== current && selectable?.sources[key] === false;
}
