/** Wording of the WhatsApp connection screens (Client card and Admin card), in the three interface
 *  languages. Kept apart from the shared catalogs so this feature stands on its own. A language
 *  with no entry for a key falls back to English. Nothing here names a token, a webhook or an id to
 *  the Client. */

import type { Locale } from "../i18n";

const en = {
  "client.title": "WhatsApp Business",
  "client.intro":
    "Connect your own WhatsApp Business account in one click. Meta asks you to sign in and to choose your business and your number; Revansa never sees your password.",
  "client.number": "Your number: {number}",
  "client.state.not_connected": "Not connected",
  "client.state.connected": "Connected",
  "client.state.reauth_required": "Reconnection needed",
  "client.state.unavailable": "Not available yet",
  "client.verified": "Verified business name: {name}",
  "client.unavailable": "WhatsApp is not available yet. Revansa has to finish setting it up.",
  "client.noNumber": "Enter your WhatsApp number in your profile first.",
  "client.connect": "Connect WhatsApp",
  "client.reconnect": "Reconnect WhatsApp",
  "client.disconnect": "Disconnect",
  "client.disconnectConfirm":
    "Revansa will stop receiving your WhatsApp messages and your authorisation will be withdrawn. Your agent will stop writing on WhatsApp.",
  "client.disconnectYes": "Yes, disconnect",
  "client.cancel": "Cancel",
  "client.working": "Connecting…",
  "client.canStart": "Your agent can start new conversations.",
  "client.cannotStart":
    "Your agent answers people who write to you. To start new conversations, Revansa still has to get a message model approved by WhatsApp.",
  "client.delivery": "Last 7 days: {delivered} delivered, {failed} not delivered.",
  "client.done": "Your WhatsApp account is connected.",
  "client.cancelled": "The connection was cancelled. Nothing was changed.",
  "client.error.invalid": "The connection expired. Try again.",
  "client.error.denied": "WhatsApp did not give Revansa the access it needs. Try again and accept every permission.",
  "client.error.number_mismatch":
    "The WhatsApp account you chose is not the number in your profile. Choose the account of your number, or correct your number in your profile.",
  "client.error.number_unclear": "Several numbers were found. Choose only one when WhatsApp asks.",
  "client.error.in_use": "This number is already connected to another Revansa account.",
  "client.error.unavailable": "WhatsApp cannot be reached right now. Try again in a moment.",
  "client.error.generic": "The connection could not be finished. Try again, or contact Revansa.",
  "client.error.sdk": "WhatsApp's sign-in window could not be loaded. Check your connection and any content blocker.",

  "delivery.sent": "sent",
  "delivery.delivered": "delivered",
  "delivery.read": "read",
  "delivery.failed": "not delivered",

  "admin.title": "WhatsApp account",
  "admin.state": "State",
  "admin.number": "Number",
  "admin.verified": "Verified name",
  "admin.quality": "Quality",
  "admin.waba": "Business account",
  "admin.phoneId": "Phone number id",
  "admin.connectedAt": "Connected",
  "admin.opening": "Opening template",
  "admin.noOpening": "none: no new conversation can start",
  "admin.deliveries": "Last 7 days",
  "admin.failures": "Recent delivery failures",
  "admin.refresh": "Ask Meta now",
  "admin.disconnect": "Disconnect",
  "admin.releaseNumber": "Also release the number from the Cloud API",
  "admin.disconnectDone": "Disconnected. Steps at Meta:",
  "admin.templates": "Message templates",
  "admin.sync": "Read from Meta",
  "admin.useOpening": "Use to open conversations",
  "admin.delete": "Delete",
  "admin.createTitle": "Submit a template to Meta",
  "admin.name": "Name (a-z, 0-9, _)",
  "admin.language": "Language (fr, en, ar…)",
  "admin.category": "Category",
  "admin.body": "Text. One variable {{1}} that carries the agent's message, inside a sentence.",
  "admin.examples": "Example value for each variable (one per line)",
  "admin.submit": "Submit for review",
  "admin.platform": "Platform setup",
  "admin.platformReady": "Ready: clients can connect their account.",
  "admin.platformMissing": "Still to set in Settings: {list}",
  "admin.webhookUrl": "Webhook URL",
  "admin.deauthUrl": "Deauthorize callback URL",
  "admin.deletionUrl": "Data deletion request URL",
  "admin.error": "The request failed.",
} as const;

export type WhatsAppKey = keyof typeof en;

const fr: Partial<Record<WhatsAppKey, string>> = {
  "client.title": "WhatsApp Business",
  "client.intro":
    "Connectez votre propre compte WhatsApp Business en un clic. Meta vous demande de vous identifier et de choisir votre entreprise et votre numéro ; Revansa ne voit jamais votre mot de passe.",
  "client.number": "Votre numéro : {number}",
  "client.state.not_connected": "Non connecté",
  "client.state.connected": "Connecté",
  "client.state.reauth_required": "Reconnexion nécessaire",
  "client.state.unavailable": "Pas encore disponible",
  "client.verified": "Nom d'entreprise vérifié : {name}",
  "client.unavailable": "WhatsApp n'est pas encore disponible. Revansa doit terminer sa mise en place.",
  "client.noNumber": "Renseignez d'abord votre numéro WhatsApp dans votre profil.",
  "client.connect": "Connecter WhatsApp",
  "client.reconnect": "Reconnecter WhatsApp",
  "client.disconnect": "Déconnecter",
  "client.disconnectConfirm":
    "Revansa ne recevra plus vos messages WhatsApp et votre autorisation sera retirée. Votre agent cessera d'écrire sur WhatsApp.",
  "client.disconnectYes": "Oui, déconnecter",
  "client.cancel": "Annuler",
  "client.working": "Connexion en cours…",
  "client.canStart": "Votre agent peut démarrer de nouvelles conversations.",
  "client.cannotStart":
    "Votre agent répond aux personnes qui vous écrivent. Pour démarrer de nouvelles conversations, Revansa doit encore faire approuver un modèle de message par WhatsApp.",
  "client.delivery": "7 derniers jours : {delivered} délivrés, {failed} non délivrés.",
  "client.done": "Votre compte WhatsApp est connecté.",
  "client.cancelled": "La connexion a été annulée. Rien n'a été modifié.",
  "client.error.invalid": "La connexion a expiré. Recommencez.",
  "client.error.denied":
    "WhatsApp n'a pas donné à Revansa l'accès nécessaire. Recommencez en acceptant toutes les autorisations.",
  "client.error.number_mismatch":
    "Le compte WhatsApp choisi n'est pas celui du numéro de votre profil. Choisissez le compte de votre numéro, ou corrigez votre numéro dans votre profil.",
  "client.error.number_unclear": "Plusieurs numéros ont été trouvés. N'en choisissez qu'un seul quand WhatsApp le demande.",
  "client.error.in_use": "Ce numéro est déjà connecté à un autre compte Revansa.",
  "client.error.unavailable": "WhatsApp est injoignable pour le moment. Réessayez dans un instant.",
  "client.error.generic": "La connexion n'a pas pu être terminée. Réessayez, ou contactez Revansa.",
  "client.error.sdk":
    "La fenêtre de connexion de WhatsApp n'a pas pu être chargée. Vérifiez votre connexion et votre bloqueur de contenu.",

  "delivery.sent": "envoyé",
  "delivery.delivered": "délivré",
  "delivery.read": "lu",
  "delivery.failed": "non délivré",

  "admin.title": "Compte WhatsApp",
  "admin.state": "État",
  "admin.number": "Numéro",
  "admin.verified": "Nom vérifié",
  "admin.quality": "Qualité",
  "admin.waba": "Compte WhatsApp Business",
  "admin.phoneId": "Identifiant du numéro",
  "admin.connectedAt": "Connecté",
  "admin.opening": "Modèle d'ouverture",
  "admin.noOpening": "aucun : aucune nouvelle conversation ne peut démarrer",
  "admin.deliveries": "7 derniers jours",
  "admin.failures": "Échecs de livraison récents",
  "admin.refresh": "Interroger Meta maintenant",
  "admin.disconnect": "Déconnecter",
  "admin.releaseNumber": "Libérer aussi le numéro de la Cloud API",
  "admin.disconnectDone": "Déconnecté. Étapes chez Meta :",
  "admin.templates": "Modèles de message",
  "admin.sync": "Lire depuis Meta",
  "admin.useOpening": "Utiliser pour ouvrir les conversations",
  "admin.delete": "Supprimer",
  "admin.createTitle": "Soumettre un modèle à Meta",
  "admin.name": "Nom (a-z, 0-9, _)",
  "admin.language": "Langue (fr, en, ar…)",
  "admin.category": "Catégorie",
  "admin.body": "Texte. Une variable {{1}} qui porte le message de l'agent, au milieu d'une phrase.",
  "admin.examples": "Valeur d'exemple de chaque variable (une par ligne)",
  "admin.submit": "Soumettre à la validation",
  "admin.platform": "Mise en place de la plateforme",
  "admin.platformReady": "Prête : les clients peuvent connecter leur compte.",
  "admin.platformMissing": "Reste à renseigner dans Paramètres : {list}",
  "admin.webhookUrl": "URL du webhook",
  "admin.deauthUrl": "URL de rappel de désautorisation",
  "admin.deletionUrl": "URL de demande de suppression des données",
  "admin.error": "La requête a échoué.",
};

const catalogs: Partial<Record<Locale, Partial<Record<WhatsAppKey, string>>>> = { fr };

export function whatsappText(
  locale: Locale,
  key: WhatsAppKey,
  vars: Record<string, string | number> = {},
): string {
  const template = catalogs[locale]?.[key] ?? en[key];
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}
