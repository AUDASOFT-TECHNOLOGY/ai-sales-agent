/** Meta's Embedded Signup, from the browser: loads Meta's own JavaScript SDK, opens Meta's sign-in
 *  window (`FB.login` with the platform's configuration), and returns the one-time code Meta gives
 *  plus the ids its window reports. The code is then sent to Revansa's server, which exchanges it:
 *  no token is ever held here. The ids are only hints; the server asks Meta what the code grants. */

import type { WhatsAppStart } from "./api";

const SDK_URL = "https://connect.facebook.net/en_US/sdk.js";
const SESSION_INFO_GRACE_MS = 1500;

interface FacebookSdk {
  init(options: { appId: string; version: string; cookie: boolean; xfbml: boolean }): void;
  login(
    callback: (response: { authResponse?: { code?: string } | null }) => void,
    options: Record<string, unknown>,
  ): void;
}

declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

export interface SignupCode {
  code: string;
  wabaId?: string;
  phoneNumberId?: string;
}

export type SignupOutcome = { kind: "code"; value: SignupCode } | { kind: "cancelled" };

export interface SignupDeps {
  loadSdk: () => Promise<FacebookSdk>;
  addMessageListener: (listener: (event: MessageEvent) => void) => () => void;
}

export function loadFacebookSdk(): Promise<FacebookSdk> {
  if (window.FB) return Promise.resolve(window.FB);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    script.onload = () => (window.FB ? resolve(window.FB) : reject(new Error("sdk")));
    script.onerror = () => reject(new Error("sdk"));
    document.head.appendChild(script);
  });
}

const defaults: SignupDeps = {
  loadSdk: loadFacebookSdk,
  addMessageListener: (listener) => {
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  },
};

interface SessionInfo {
  event?: string;
  wabaId?: string;
  phoneNumberId?: string;
}

/** What Meta's window posts to the page: only from facebook.com, only the WhatsApp signup event. */
export function sessionInfoOf(event: MessageEvent): SessionInfo | null {
  let origin: URL;
  try {
    origin = new URL(event.origin);
  } catch {
    return null;
  }
  if (origin.protocol !== "https:" || !(origin.hostname === "facebook.com" || origin.hostname.endsWith(".facebook.com"))) {
    return null;
  }
  try {
    const data: unknown = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
    if (!data || typeof data !== "object" || (data as { type?: unknown }).type !== "WA_EMBEDDED_SIGNUP") return null;
    const { event: name, data: details } = data as { event?: unknown; data?: Record<string, unknown> };
    const id = (value: unknown) => (typeof value === "string" && /^\d{5,32}$/.test(value) ? value : undefined);
    return {
      event: typeof name === "string" ? name : undefined,
      wabaId: id(details?.waba_id),
      phoneNumberId: id(details?.phone_number_id),
    };
  } catch {
    return null;
  }
}

export async function runEmbeddedSignup(start: WhatsAppStart, deps: SignupDeps = defaults): Promise<SignupOutcome> {
  const sdk = await deps.loadSdk();
  let info: SessionInfo = {};
  let announce: () => void = () => undefined;
  const infoArrived = new Promise<void>((resolve) => (announce = resolve));
  const stop = deps.addMessageListener((event) => {
    const found = sessionInfoOf(event);
    if (!found) return;
    info = { ...info, ...found };
    if (found.event?.startsWith("FINISH")) announce();
  });
  try {
    sdk.init({ appId: start.app_id, version: start.api_version, cookie: false, xfbml: false });
    const code = await new Promise<string | null>((resolve) => {
      sdk.login((response) => resolve(response.authResponse?.code ?? null), {
        config_id: start.config_id,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
      });
    });
    if (!code || info.event === "CANCEL") return { kind: "cancelled" };
    // The ids usually arrive just before the code; wait briefly, never long: the code lives 30 s.
    if (!info.wabaId && !info.phoneNumberId) {
      await Promise.race([infoArrived, new Promise((resolve) => setTimeout(resolve, SESSION_INFO_GRACE_MS))]);
    }
    return { kind: "code", value: { code, wabaId: info.wabaId, phoneNumberId: info.phoneNumberId } };
  } finally {
    stop();
  }
}
