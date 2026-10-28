/** What the client sells, in its own word: products, services, courses or subscriptions. The answer only
 *  changes the words the space uses for it (a few titles and buttons); nothing the agent says depends on
 *  it. The server has no field for it, so it is kept in this browser, for this login. */

import { useCallback, useSyncExternalStore } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { useClientAuth } from "../ClientAuthContext";

export type OfferKind = "product" | "service" | "course" | "subscription";
export const OFFER_KINDS: OfferKind[] = ["product", "service", "course", "subscription"];

type Words = Partial<Record<MessageKey, string>>;

/** The few keys whose wording follows the kind. A kind that is not chosen leaves the default wording. */
const WORDS: Record<OfferKind, { fr: Words; en: Words }> = {
  product: {
    fr: { "rv.prep.phase.products": "Vos produits", "rv.prep.nextTitle.products": "Vos produits", "rv.prod.title": "Vos produits", "rv.prod.add": "Ajouter un produit", "rv.prod.new": "Nouveau produit" },
    en: { "rv.prep.phase.products": "Your products", "rv.prep.nextTitle.products": "Your products", "rv.prod.title": "Your products", "rv.prod.add": "Add a product", "rv.prod.new": "New product" },
  },
  service: {
    fr: { "rv.prep.phase.products": "Vos services", "rv.prep.nextTitle.products": "Vos services", "rv.prod.title": "Vos services", "rv.prod.add": "Ajouter un service", "rv.prod.new": "Nouveau service" },
    en: { "rv.prep.phase.products": "Your services", "rv.prep.nextTitle.products": "Your services", "rv.prod.title": "Your services", "rv.prod.add": "Add a service", "rv.prod.new": "New service" },
  },
  course: {
    fr: { "rv.prep.phase.products": "Vos formations", "rv.prep.nextTitle.products": "Vos formations", "rv.prod.title": "Vos formations", "rv.prod.add": "Ajouter une formation", "rv.prod.new": "Nouvelle formation" },
    en: { "rv.prep.phase.products": "Your courses", "rv.prep.nextTitle.products": "Your courses", "rv.prod.title": "Your courses", "rv.prod.add": "Add a course", "rv.prod.new": "New course" },
  },
  subscription: {
    fr: { "rv.prep.phase.products": "Vos abonnements", "rv.prep.nextTitle.products": "Vos abonnements", "rv.prod.title": "Vos abonnements", "rv.prod.add": "Ajouter un abonnement", "rv.prod.new": "Nouvel abonnement" },
    en: { "rv.prep.phase.products": "Your subscriptions", "rv.prep.nextTitle.products": "Your subscriptions", "rv.prod.title": "Your subscriptions", "rv.prod.add": "Add a subscription", "rv.prod.new": "New subscription" },
  },
};

const isKind = (v: unknown): v is OfferKind => OFFER_KINDS.includes(v as OfferKind);
const storageKey = (login: string) => `revansa.offerKind.${login}`;
const listeners = new Set<() => void>();

function read(login: string): OfferKind | "" {
  try {
    const value = window.localStorage.getItem(storageKey(login));
    return isKind(value) ? value : "";
  } catch {
    return "";
  }
}

function write(login: string, kind: OfferKind | ""): void {
  try {
    if (kind === "") window.localStorage.removeItem(storageKey(login));
    else window.localStorage.setItem(storageKey(login), kind);
  } catch {
    /* a browser that refuses storage keeps the default wording */
  }
  listeners.forEach((l) => l());
}

/** The kind the client chose (or "") and how to change it. */
export function useOfferKind(): [OfferKind | "", (kind: OfferKind | "") => void] {
  const { profile } = useClientAuth();
  const login = profile?.login ?? "";
  const kind = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => read(login),
    (): OfferKind | "" => "",
  );
  const set = useCallback((next: OfferKind | "") => write(login, next), [login]);
  return [kind, set];
}

/** `t` that follows the kind chosen: the same call, with a few keys worded for what the client sells. */
export function useTerms(): (key: MessageKey, vars?: Record<string, string | number>) => string {
  const { t, locale } = useI18n();
  const [kind] = useOfferKind();
  return (key, vars) => {
    const own = kind === "" || locale === "ar" ? undefined : WORDS[kind][locale === "fr" ? "fr" : "en"][key];
    return own ?? t(key, vars);
  };
}
