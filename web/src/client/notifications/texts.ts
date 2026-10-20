/** Texts of the client's notifications, by kind and language (English fallback, then the server's
 *  own sentence). Kept here so the shared catalogs are not touched. */

import type { Locale } from "../../i18n";

interface Words {
  title: string;
  message: string;
  action: string;
}

const UI = {
  en: { title: "Notifications", empty: "Nothing needs your attention.", markAll: "Mark all as read", markRead: "Mark as read", error: "Notifications could not be loaded." },
  fr: { title: "Notifications", empty: "Rien ne demande votre attention.", markAll: "Tout marquer comme lu", markRead: "Marquer comme lu", error: "Les notifications n'ont pas pu être chargées." },
} as const;

const KINDS: Record<string, Record<"en" | "fr", Words>> = {
  quota_reached: {
    en: { title: "A quota was reached", message: "A usage limit of your plan was reached.", action: "Wait for the next period or change your plan." },
    fr: { title: "Un quota est atteint", message: "Une limite de votre offre est atteinte.", action: "Attendez la prochaine période ou changez d'offre." },
  },
  sync_failure: {
    en: { title: "Synchronisation failed", message: "Results could not be written back to your system.", action: "Check the connection to your system; it will be retried." },
    fr: { title: "La synchronisation a échoué", message: "Les résultats n'ont pas pu être renvoyés vers votre système.", action: "Vérifiez la connexion à votre système ; un nouvel essai aura lieu." },
  },
  human_intervention: {
    en: { title: "A conversation needs you", message: "The agent stopped on a conversation that needs a decision.", action: "Open the conversation and answer or approve." },
    fr: { title: "Une conversation a besoin de vous", message: "L'agent s'est arrêté sur une conversation qui demande une décision.", action: "Ouvrez la conversation pour répondre ou valider." },
  },
};

type UiKey = keyof (typeof UI)["en"];

export function notificationText(locale: Locale, key: UiKey): string;
export function notificationText(locale: Locale, key: string, server: Words): Words;
export function notificationText(locale: Locale, key: string, server?: Words): string | Words {
  const lang = locale === "fr" ? "fr" : "en";
  if (!server) return UI[lang][key as UiKey];
  // The server already words authorisation notices per provider; only the generic kinds are translated.
  return KINDS[key]?.[lang] ?? { title: server.title, message: server.message, action: server.action };
}
