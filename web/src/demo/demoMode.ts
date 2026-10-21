/** Demo mode switch. Lives only in module memory: no storage of any kind, so a page reload always
 *  returns to the signed-out state and the initial demo data. */

import { useSyncExternalStore } from "react";

/** Demo access, entered on the normal login form. Local demonstration only: it unlocks nothing but
 *  the in-memory fictitious data of this folder, and never reaches the real authentication. */
export const DEMO_LOGIN = "Demo";
export const DEMO_PASSWORD = "123456789";

let active = false;
const listeners = new Set<() => void>();

export const isDemo = (): boolean => active;

export function setDemo(value: boolean): void {
  if (active === value) return;
  active = value;
  listeners.forEach((l) => l());
}

export function useIsDemo(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    isDemo,
    () => false,
  );
}

export function isDemoCredentials(login: string, password: string): boolean {
  return login.trim().toLowerCase() === DEMO_LOGIN.toLowerCase() && password === DEMO_PASSWORD;
}

/** The identifier of the demo account, whatever the password: it is never sent to the real backend. */
export function isDemoLogin(login: string): boolean {
  return login.trim().toLowerCase() === DEMO_LOGIN.toLowerCase();
}
