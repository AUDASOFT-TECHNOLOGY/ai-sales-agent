/** Initial data of the Demo space. EVERYTHING here is fictitious: names, addresses, numbers, prices
 *  and messages. Nothing comes from a real client. `buildDemoData` is pure: calling it again gives
 *  the same initial state, which is how "reset" and "reload" restore the demonstration. */

import type { ConversationMessage, Lane, PaymentCase, Proposal, ProspectRow } from "../client/api";
import type { ClientInvoiceDetail } from "../client/billing/billingApi";
import type { DemoData, DemoNotification, DemoProspect, DemoThread } from "./demoTypes";

type Doc = Record<string, unknown>;

export const DEMO_BUSINESS = "Demo Studio";

/* ── Circuits (lanes) ─────────────────────────────────────────────────────────────────────── */

const CHANNELS = {
  email: { channel: "email", label: "E-mail" },
  telegram: { channel: "telegram", label: "Telegram" },
  whatsapp: { channel: "whatsapp", label: "WhatsApp" },
} as const;

const LANE_DEFS = [
  { id: "prospection", name: "Prospection de nouveaux clients", channel: "email", products: ["site-vitrine", "boutique-pro", "audit-express", "reseaux-sociaux", "formation-vente"] },
  { id: "relance-interesses", name: "Relance des prospects intéressés", channel: "whatsapp", products: ["site-vitrine", "boutique-pro", "reseaux-sociaux", "formation-vente"] },
  { id: "devis-negociation", name: "Suivi des devis et négociations", channel: "telegram", products: ["site-vitrine", "boutique-pro", "reseaux-sociaux", "formation-vente"] },
  { id: "paiements-demandes", name: "Suivi des paiements et demandes clients", channel: "whatsapp", products: ["ALL"] },
] as const;

function buildLanes(): Lane[] {
  return LANE_DEFS.map((l, i) => ({
    id: l.id,
    position: i,
    source: {
      id: `feuille-${l.id}`,
      source: "google_sheets",
      label: `Prospects – ${l.name}`,
      google_sheets: { spreadsheet_id: "tableur-demo-fictif", prospects_tab: l.id },
    },
    channel: { ...CHANNELS[l.channel], authorized: true, problem: null },
    products: [...l.products],
    offers_all_products: (l.products as readonly string[]).includes("ALL"),
  }));
}

/** The circuits' display names (the lane model has no name field: the id stands in for it in the UI). */
export const DEMO_LANE_NAMES: Record<string, string> = Object.fromEntries(LANE_DEFS.map((l) => [l.id, l.name]));

/* ── Catalogue, paiement, réglages ────────────────────────────────────────────────────────── */

function product(
  id: string,
  status: string,
  name: string,
  summary: string,
  description: string,
  pricing: Doc,
  benefits: string[],
  suitedFor: string[],
  notFor: string[],
  objection: [string, string, string],
  extra: Doc = {},
): Doc {
  return {
    id,
    status,
    name: { fr: name },
    summary: { fr: summary },
    description: { fr: description },
    url: `https://demo.example.test/offres/${id}`,
    pricing: { disclose: "after_interest", ...pricing },
    value: { proposition: { fr: summary }, benefits, differentiators: ["Équipe fictive dédiée", "Mise en ligne guidée"] },
    audience: { suited_for: suitedFor, not_for: notFor },
    sales_arguments: [{ id: "delai", text: "Livraison rapide et accompagnement inclus." }],
    objections: [{ code: objection[0], examples: [objection[1]], response: objection[2] }],
    strategy: { cta: { kind: "request_info", label: { fr: "Recevoir une présentation" } } },
    restrictions: { do_not_claim: ["Résultats garantis", "Délais de moins de 5 jours"] },
    ...extra,
  };
}

function buildSettings(): DemoData["settings"] {
  const products: Doc[] = [
    product("site-vitrine", "active", "Site vitrine Essentiel", "Un site professionnel de 5 pages, prêt en 2 semaines.", "Site vitrine responsive : accueil, offres, à propos, contact et mentions légales. Hébergement la première année inclus.", { model: "fixed", amount: 1200, currency: "TND" }, ["5 pages sur mesure", "Adapté au mobile", "Hébergement 1 an inclus"], ["Artisans et indépendants", "Petites entreprises"], ["Boutiques de plus de 50 produits"], ["prix", "C'est un peu cher pour un site simple.", "Le prix inclut le design, la mise en ligne et un an d'hébergement. Nous pouvons échelonner le paiement en deux fois."]),
    product("boutique-pro", "active", "Boutique en ligne Pro", "Votre boutique en ligne complète, paiement et livraison inclus.", "Boutique e-commerce avec catalogue, panier, paiement en ligne et gestion des commandes. Formation à la prise en main incluse.", { model: "from", amount: 3500, currency: "TND", note: "Selon le nombre de produits" }, ["Catalogue illimité", "Paiement en ligne", "Gestion des commandes"], ["Commerçants qui vendent déjà hors ligne"], ["Projets sans catalogue de produits"], ["delai", "Combien de temps avant la mise en ligne ?", "Comptez 4 à 6 semaines selon la taille du catalogue, avec un calendrier validé ensemble."]),
    product("reseaux-sociaux", "active", "Pack Réseaux sociaux", "Gestion mensuelle de vos réseaux : posts, visuels et suivi.", "12 publications par mois sur deux réseaux, création des visuels et rapport mensuel d'audience.", { model: "fixed", amount: 450, currency: "TND", note: "Par mois, sans engagement de durée" }, ["12 publications par mois", "Visuels inclus", "Rapport mensuel"], ["Commerces locaux", "Restaurants et cabinets"], ["Marques déjà gérées en interne"], ["resultats", "Est-ce que ça va vraiment m'apporter des clients ?", "Nous ne garantissons pas de résultats chiffrés, mais nous suivons les indicateurs avec vous chaque mois."]),
    product("audit-express", "active", "Audit marketing express", "Un diagnostic de votre présence en ligne en 48 heures.", "Analyse de votre site, de vos réseaux et de votre visibilité locale, avec 10 recommandations priorisées.", { model: "fixed", amount: 300, currency: "TND" }, ["Livré en 48 h", "10 recommandations", "Restitution par visio"], ["Toute entreprise qui démarre en ligne"], ["Entreprises déjà accompagnées par une agence"], ["utilite", "Je ne vois pas l'intérêt d'un audit.", "L'audit vous dit précisément quoi corriger en premier, sans engagement de suite."]),
    product("formation-vente", "active", "Formation Vente en ligne", "Apprenez à vendre en ligne en 4 sessions en direct.", "4 sessions de 2 heures en visio, replays, supports et un suivi individuel d'un mois.", { model: "range", amount: 600, max_amount: 900, currency: "TND", note: "Selon la formule choisie" }, ["4 sessions en direct", "Replays illimités", "Suivi individuel"], ["Dirigeants et équipes commerciales"], ["Débutants sans activité commerciale"], ["temps", "Je n'ai pas le temps de suivre une formation.", "Les sessions sont en fin de journée et les replays vous permettent de rattraper à votre rythme."]),
    product("maintenance", "coming_soon", "Maintenance annuelle", "Mises à jour, sauvegardes et support, bientôt disponible.", "Contrat annuel de maintenance pour les sites réalisés par Demo Studio.", { model: "on_request" }, ["Sauvegardes automatiques", "Support prioritaire"], ["Clients ayant un site Demo Studio"], ["Sites réalisés ailleurs"], ["prix", "Combien ça coûte ?", "Le tarif sera annoncé au lancement ; nous pouvons vous prévenir dès qu'il est disponible."], { strategy: { cta: { kind: "notify_me", label: { fr: "Me prévenir" } } } }),
  ];
  return {
    timezone: "Africa/Tunis",
    opt_out_keywords: ["stop", "désinscrire", "unsubscribe"],
    schedule: { daily_at: "09:30", days: ["mon", "tue", "wed", "thu", "fri"] },
    commercial: {
      business: {
        name: DEMO_BUSINESS,
        website: "https://demo.example.test",
        summary: { fr: "Demo Studio est une agence fictive qui aide les petites entreprises à vendre en ligne : sites, boutiques, réseaux sociaux et formation." },
      },
      communication: { languages: { default: "fr", supported: ["fr", "en", "ar"] }, tone: { style: "Chaleureux, professionnel et concis" } },
      catalog: { products },
      payment: {
        methods: [
          { id: "virement", status: "active", kind: "offline", label: { fr: "Virement bancaire" }, instructions: [{ id: "main", text: { fr: "Effectuez le virement puis envoyez-nous le reçu." }, fields: [{ label: { fr: "Banque" }, value: { fr: "Banque Exemple" } }, { label: { fr: "RIB" }, value: { fr: "00 000 0000000000 00 (exemple fictif)" } }] }], confirmation: { mode: "manual", note: { fr: "Nous confirmons le paiement sous 24 h ouvrées." } } },
          { id: "carte", status: "active", kind: "online", label: { fr: "Paiement en ligne" }, instructions: [{ id: "main", url: "https://paiement.example.test/demo-studio", text: { fr: "Payez par carte sur la page sécurisée." } }], confirmation: { mode: "manual" } },
        ],
      },
    },
  };
}

/* ── Prospects ────────────────────────────────────────────────────────────────────────────── */

type Stage =
  | "Nouveau"
  | "Contacté"
  | "En attente de réponse"
  | "Intéressé"
  | "En négociation"
  | "Devis envoyé"
  | "Relance nécessaire"
  | "Vente conclue"
  | "Perdu / non intéressé"
  | "En pause"
  | "Désinscrit"
  | "Conversation clôturée"
  | "À traiter par un conseiller";

const STATUS_OF: Record<Stage, ProspectRow["status"]> = {
  Nouveau: "new",
  Contacté: "active",
  "En attente de réponse": "active",
  Intéressé: "active",
  "En négociation": "active",
  "Devis envoyé": "active",
  "Relance nécessaire": "active",
  "Vente conclue": "closed",
  "Perdu / non intéressé": "closed",
  "En pause": "paused",
  Désinscrit: "opted_out",
  "Conversation clôturée": "closed",
  "À traiter par un conseiller": "paused",
};

/** [n, name, lane, product, stage, followUps, next [type, days ahead] | null, human, locale, summary] */
type Def = [number, string, string, string, Stage, number, [string, number] | null, boolean, string, string];

const D: Def[] = [
  [1, "Amira Exemple", "prospection", "audit-express", "Nouveau", 0, ["first_contact", 0], false, "fr", "Nouveau contact à qui proposer un audit marketing."],
  [2, "Karim Démo", "prospection", "site-vitrine", "Nouveau", 0, ["first_contact", 1], false, "fr", "Artisan sans site web, premier contact prévu demain."],
  [3, "Société Exemple SARL", "prospection", "boutique-pro", "Contacté", 0, ["follow_up", 3], false, "fr", "Première présentation de la boutique en ligne envoyée."],
  [4, "Leïla Test", "prospection", "site-vitrine", "En attente de réponse", 1, ["follow_up", 2], false, "fr", "Une relance envoyée, sans réponse pour le moment."],
  [5, "Boutique Fictive", "prospection", "boutique-pro", "Intéressé", 0, ["reply", 0], false, "fr", "Intéressée par la boutique, demande des précisions sur le délai."],
  [6, "Youssef Démo", "prospection", "reseaux-sociaux", "Relance nécessaire", 2, ["follow_up", -1], false, "fr", "Deux relances sans réponse : relance en retard."],
  [7, "Atelier Imaginaire", "prospection", "audit-express", "Perdu / non intéressé", 0, null, false, "fr", "A déjà un prestataire, ne souhaite pas donner suite."],
  [8, "Nadia Exemple", "prospection", "reseaux-sociaux", "Désinscrit", 0, null, false, "fr", "A demandé à ne plus être contactée."],
  [9, "Studio Factice", "prospection", "formation-vente", "Contacté", 0, ["follow_up", 4], false, "fr", "Présentation de la formation envoyée hier."],
  [10, "Hichem Test", "prospection", "audit-express", "En attente de réponse", 2, ["follow_up", 1], false, "fr", "Deux relances envoyées, silence radio."],
  [11, "Sana Démo", "relance-interesses", "site-vitrine", "Intéressé", 0, ["reply", 0], false, "fr", "Hésite entre site vitrine et boutique, a demandé le prix."],
  [12, "Mehdi Exemple", "relance-interesses", "boutique-pro", "Relance nécessaire", 2, ["follow_up", 0], false, "fr", "A promis de regarder, plus de nouvelles depuis une semaine."],
  [13, "Ines Fictive", "relance-interesses", "reseaux-sociaux", "En attente de réponse", 1, ["follow_up", 2], false, "fr", "Relance envoyée, en attente."],
  [14, "Omar Test", "relance-interesses", "boutique-pro", "En négociation", 1, ["reply", 0], false, "fr", "Trouve le prix élevé ; paiement en deux fois proposé."],
  [15, "Rania Démo", "relance-interesses", "site-vitrine", "Vente conclue", 0, null, false, "fr", "Site vitrine commandé et payé."],
  [16, "Walid Exemple", "relance-interesses", "formation-vente", "En pause", 0, ["reminder", 25], false, "fr", "En voyage, à recontacter dans un mois."],
  [17, "Salma Test", "relance-interesses", "formation-vente", "Intéressé", 0, ["reply", 0], false, "fr", "Demande les prochaines dates de formation."],
  [18, "Fares Imaginaire", "relance-interesses", "reseaux-sociaux", "Perdu / non intéressé", 0, null, false, "fr", "Trouve l'offre trop chère."],
  [19, "Cabinet Exemple", "devis-negociation", "boutique-pro", "Devis envoyé", 0, ["follow_up", 2], false, "fr", "Devis de 200 produits envoyé ; question sur l'intégration ERP."],
  [20, "Maya Démo", "devis-negociation", "site-vitrine", "En négociation", 1, ["reply", 0], false, "fr", "Demande un tarif plus bas pour le site vitrine."],
  [21, "Anis Test", "devis-negociation", "reseaux-sociaux", "Devis envoyé", 0, ["follow_up", 3], false, "fr", "Devis trois mois de gestion envoyé."],
  [22, "Dorra Fictive", "devis-negociation", "boutique-pro", "À traiter par un conseiller", 0, null, true, "fr", "Commande de trois sites, demande 20 % de remise."],
  [23, "Slim Démo", "devis-negociation", "formation-vente", "Vente conclue", 0, null, false, "fr", "Inscrit à la formation, virement fait."],
  [24, "Hela Exemple", "devis-negociation", "site-vitrine", "Relance nécessaire", 2, ["follow_up", -2], false, "fr", "A reçu l'information, ne répond plus."],
  [25, "Tarek Test", "devis-negociation", "boutique-pro", "Perdu / non intéressé", 0, null, false, "fr", "A choisi une autre solution après le devis."],
  [26, "Rim Démo", "paiements-demandes", "boutique-pro", "Vente conclue", 0, ["reminder", 0], false, "fr", "Dit avoir payé : réception à vérifier."],
  [27, "Bilel Exemple", "paiements-demandes", "site-vitrine", "Vente conclue", 0, ["reminder", 0], false, "fr", "A envoyé un reçu de virement, à confirmer."],
  [28, "Emna Test", "paiements-demandes", "reseaux-sociaux", "Vente conclue", 0, null, false, "fr", "Paiement confirmé, pack démarré."],
  [29, "Nizar Fictif", "paiements-demandes", "site-vitrine", "À traiter par un conseiller", 0, null, true, "fr", "Réclamation : erreur sur le site, demande de remboursement."],
  [30, "Olfa Démo", "paiements-demandes", "audit-express", "À traiter par un conseiller", 0, null, true, "fr", "Demande à être rappelée demain matin."],
  [31, "Jawher Exemple", "paiements-demandes", "formation-vente", "À traiter par un conseiller", 0, null, true, "fr", "Souhaite parler à un conseiller pour une formation d'équipe."],
  [32, "Chiraz Test", "paiements-demandes", "audit-express", "Conversation clôturée", 0, null, false, "fr", "Audit livré et validé, conversation close."],
];

const pid = (n: number) => `demo-p${String(n).padStart(2, "0")}`;
const slug = (name: string) =>
  name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ".")
    .replace(/^\.|\.$/g, "");

/* ── Conversations ───────────────────────────────────────────────────────────────────────── */

/** [prospect n, hours ago the first message was written, messages [o = agent, i = prospect, h = person]] */
type Script = [number, number, Array<["o" | "i" | "h", string]>];

const INTRO = (what: string) => `Bonjour, je suis l'assistant commercial de ${DEMO_BUSINESS}. ${what} Souhaitez-vous que je vous en dise plus ?`;

const SCRIPTS: Script[] = [
  [3, 50, [["o", INTRO("Nous aidons les commerçants à lancer leur boutique en ligne avec notre offre Boutique en ligne Pro.")]]],
  [4, 140, [["o", INTRO("Notre Site vitrine Essentiel vous donne un site professionnel en deux semaines.")], ["o", "Je me permets de revenir vers vous au sujet du site vitrine. Avez-vous eu l'occasion d'y réfléchir ?"]]],
  [5, 90, [["o", INTRO("Notre Boutique en ligne Pro permet de vendre en ligne avec paiement inclus.")], ["i", "Bonjour, oui ça m'intéresse. Quel est le délai de mise en ligne ?"], ["o", "Comptez 4 à 6 semaines selon la taille de votre catalogue, avec un calendrier validé ensemble. Combien de produits souhaitez-vous vendre ?"], ["i", "Environ 80 produits. Je regarde et je reviens vers vous."]]],
  [6, 260, [["o", INTRO("Le Pack Réseaux sociaux prend en charge vos publications chaque mois.")], ["o", "Petite relance : le Pack Réseaux sociaux (450 TND par mois, sans engagement de durée) pourrait vous faire gagner du temps."], ["o", "Je reste disponible si vous souhaitez un exemple de planning éditorial."]]],
  [7, 200, [["o", INTRO("L'Audit marketing express analyse votre présence en ligne en 48 heures.")], ["i", "Merci mais nous avons déjà un prestataire, nous ne sommes pas intéressés."], ["o", "Bien compris, merci pour votre réponse. Bonne continuation !"]]],
  [8, 300, [["o", INTRO("Le Pack Réseaux sociaux prend en charge vos publications chaque mois.")], ["i", "STOP, merci de ne plus me contacter."], ["o", "C'est noté, vous ne recevrez plus de messages de notre part."]]],
  [9, 28, [["o", INTRO("Notre Formation Vente en ligne se déroule en 4 sessions en direct.")]]],
  [10, 240, [["o", INTRO("L'Audit marketing express vous donne 10 recommandations en 48 heures.")], ["o", "Je me permets de relancer au sujet de l'audit marketing. Il est facturé 300 TND, sans engagement."], ["o", "Dernière relance de ma part : n'hésitez pas à me répondre si le sujet vous intéresse."]]],
  [11, 70, [["o", INTRO("Notre Site vitrine Essentiel est livré en deux semaines.")], ["i", "Bonjour, c'est quoi la différence entre le site vitrine et la boutique ?"], ["o", "Le site vitrine présente votre activité (5 pages). La boutique ajoute catalogue, panier et paiement en ligne."], ["i", "D'accord, le site vitrine me suffit. Quel est le prix ?"], ["o", "Le Site vitrine Essentiel est à 1 200 TND, hébergement la première année inclus."]]],
  [12, 230, [["o", INTRO("La Boutique en ligne Pro vous permet de vendre 24 h/24.")], ["i", "Je regarde ça cette semaine."], ["o", "Bonjour Mehdi, avez-vous pu regarder la présentation de la boutique ?"], ["o", "Je reste disponible pour répondre à vos questions ou organiser un appel."]]],
  [13, 120, [["o", INTRO("Le Pack Réseaux sociaux (450 TND par mois) gère vos publications.")], ["o", "Une petite relance : souhaitez-vous voir un exemple de calendrier de publications ?"]]],
  [14, 100, [["o", INTRO("La Boutique en ligne Pro démarre à partir de 3 500 TND.")], ["i", "Le prix de 3 500 TND est trop élevé pour moi."], ["o", "Je comprends. Le tarif inclut le paiement en ligne, la gestion des commandes et la formation. Je peux proposer un paiement en deux fois sans frais."], ["i", "Pouvez-vous faire une réduction de 10 % ?"], ["o", "Je ne peux pas modifier le tarif moi-même, mais le paiement en deux fois reste possible. Cela vous convient-il ?"], ["i", "Je vais y réfléchir."]]],
  [15, 300, [["o", INTRO("Notre Site vitrine Essentiel est livré en deux semaines.")], ["i", "Oui, je veux commander le site vitrine."], ["o", "Parfait ! Le montant est de 1 200 TND. Voici les instructions de virement (exemple fictif) : Banque Exemple, RIB 00 000 0000000000 00. Envoyez-moi le reçu une fois fait."], ["i", "Paiement effectué, voici le reçu."], ["o", "Merci Rania ! Votre paiement est confirmé. Nous démarrons la semaine prochaine."]]],
  [16, 400, [["o", INTRO("Notre Formation Vente en ligne commence bientôt.")], ["i", "Je suis en voyage, recontactez-moi dans un mois."], ["o", "Pas de souci, je vous recontacte dans un mois. Bon voyage !"]]],
  [17, 40, [["o", INTRO("Notre Formation Vente en ligne se déroule en 4 sessions en direct.")], ["i", "Est-ce que la formation est en ligne ou en présentiel ?"], ["o", "Elle est 100 % en ligne : 4 sessions de 2 h en direct avec replays."], ["i", "Super, quelles sont les prochaines dates ?"]]],
  [18, 330, [["o", INTRO("Le Pack Réseaux sociaux est à 450 TND par mois.")], ["i", "Non merci, c'est trop cher pour nous."], ["o", "Je comprends. N'hésitez pas à revenir vers nous si votre budget évolue."]]],
  [19, 110, [["o", INTRO("La Boutique en ligne Pro correspond aux commerçants qui vendent déjà hors ligne.")], ["i", "Nous aimerions un devis pour une boutique de 200 produits."], ["o", "Voici votre devis fictif DEV-DEMO-019 : Boutique en ligne Pro, 200 produits, 4 200 TND, livraison en 6 semaines."], ["i", "Bien reçu. Pouvez-vous intégrer la boutique à notre logiciel de gestion (ERP) ?"]]],
  [20, 80, [["o", INTRO("Le Site vitrine Essentiel est livré en deux semaines.")], ["i", "Combien coûte le site vitrine ?"], ["o", "Le Site vitrine Essentiel est à 1 200 TND, hébergement inclus la première année."], ["i", "C'est un peu cher, vous avez moins cher ?"], ["o", "Je peux vous proposer le paiement en deux fois. Souhaitez-vous que je prépare un devis ?"]]],
  [21, 75, [["o", INTRO("Le Pack Réseaux sociaux gère vos publications et vos visuels.")], ["i", "Je voudrais un devis pour 3 mois de gestion de réseaux."], ["o", "Voici votre devis fictif DEV-DEMO-021 : Pack Réseaux sociaux, 3 mois, 1 350 TND au total."]]],
  [22, 60, [["o", INTRO("La Boutique en ligne Pro démarre à partir de 3 500 TND.")], ["i", "Nous commandons 3 boutiques. Pouvez-vous faire une remise exceptionnelle de 20 % ?"], ["o", "Une remise de cette ampleur doit être validée par un conseiller. Je lui transmets votre demande."]]],
  [23, 320, [["o", INTRO("Notre Formation Vente en ligne commence bientôt.")], ["i", "Je m'inscris à la formation."], ["o", "Bienvenue ! La formule complète est à 750 TND. Vous pouvez régler par virement ou en ligne."], ["i", "Virement fait."], ["o", "Paiement confirmé, bienvenue dans la formation !"]]],
  [24, 250, [["o", INTRO("Le Site vitrine Essentiel est livré en deux semaines.")], ["i", "Envoyez-moi plus d'informations."], ["o", "Bien sûr : 5 pages sur mesure, adapté au mobile, hébergement 1 an inclus, 1 200 TND."], ["o", "Avez-vous des questions sur l'offre ? Je peux préparer un devis."]]],
  [25, 280, [["o", INTRO("La Boutique en ligne Pro démarre à partir de 3 500 TND.")], ["i", "Pouvez-vous m'envoyer un devis ?"], ["o", "Voici votre devis fictif DEV-DEMO-025 : Boutique en ligne Pro, 3 800 TND."], ["i", "Après réflexion nous avons choisi une autre solution."], ["o", "Merci de nous avoir consultés, et bonne réussite pour votre projet."]]],
  [26, 14, [["o", "Bonjour Rim, voici les instructions de paiement pour la Boutique en ligne Pro (exemple fictif) : Banque Exemple, RIB 00 000 0000000000 00."], ["i", "J'ai effectué le virement ce matin."], ["o", "Merci ! Un conseiller va vérifier la réception du paiement et vous confirmer."]]],
  [27, 10, [["o", "Bonjour Bilel, voici comment régler le Site vitrine Essentiel (1 200 TND) : virement ou paiement en ligne."], ["i", "Voici mon reçu de virement."], ["o", "Reçu bien reçu. La vérification est en cours."]]],
  [28, 200, [["o", "Bonjour Emna, voici comment régler le Pack Réseaux sociaux (450 TND)."], ["i", "C'est payé, reçu en pièce jointe."], ["o", "Merci Emna, votre paiement est confirmé. Le pack démarre lundi."]]],
  [29, 30, [["i", "Mon site affiche une erreur depuis hier, je veux être remboursé."], ["o", "Je suis désolé pour ce désagrément. Je transmets votre réclamation à un conseiller, qui reviendra vers vous."]]],
  [30, 20, [["i", "Pouvez-vous m'appeler demain matin ?"], ["o", "Bien sûr, un conseiller va vous rappeler. À quelle heure vous convient-il ?"], ["i", "Vers 10 h si possible."]]],
  [31, 26, [["o", INTRO("Notre Formation Vente en ligne peut aussi se faire en équipe.")], ["i", "Je préfère parler à quelqu'un directement pour une formation d'équipe."], ["o", "Je vous mets en relation avec un conseiller qui vous contactera."]]],
  [32, 360, [["o", "Bonjour Chiraz, votre Audit marketing express est prêt, le rapport est disponible."], ["i", "Merci pour l'audit, c'est très clair, tout est bon pour moi."], ["o", "Ravi d'avoir pu vous aider. Je clôture cette conversation, n'hésitez pas à revenir vers nous."]]],
];

const HANDOFF: Record<number, string> = {
  22: "Demande de remise exceptionnelle",
  29: "Réclamation commerciale",
  30: "Demande de rappel par un conseiller",
  31: "Demande de transfert à un conseiller",
};

const GAPS_H = [1.5, 5, 19, 2, 7, 26, 3, 9];

/* ── Assembly ────────────────────────────────────────────────────────────────────────────── */

export function buildDemoData(now: Date = new Date()): DemoData {
  const t0 = now.getTime();
  const iso = (ms: number) => new Date(ms).toISOString();
  const hoursAgo = (h: number) => iso(t0 - h * 3_600_000);
  const daysFromNow = (d: number) => iso(t0 + d * 86_400_000);

  const laneOf = Object.fromEntries(LANE_DEFS.map((l) => [l.id, l]));

  const threads: DemoThread[] = SCRIPTS.map(([n, startH, msgs]) => {
    const def = D.find((d) => d[0] === n)!;
    const channel = laneOf[def[2]]!.channel;
    let at = t0 - startH * 3_600_000;
    const messages: ConversationMessage[] = msgs.map(([who, text], i) => {
      if (i > 0) at += GAPS_H[i % GAPS_H.length]! * 3_600_000;
      at = Math.min(at, t0 - (msgs.length - i) * 600_000);
      const outbound = who !== "i";
      return {
        id: `${pid(n)}-m${i + 1}`,
        direction: outbound ? "outbound" : "inbound",
        channel,
        text,
        timestamp: iso(at),
        author: who === "i" ? "prospect" : who === "h" ? "human" : "agent",
        delivery: outbound && channel === "whatsapp" ? (i === msgs.length - 1 && n % 5 === 0 ? "delivered" : "read") : null,
      };
    });
    const awaiting = def[7];
    return {
      prospect_id: pid(n),
      origin: msgs[0]![0] === "i" ? "inbound" : "outbound",
      messages,
      handoff_reason: awaiting ? (HANDOFF[n] ?? null) : null,
      control: {
        mode: "agent",
        awaiting_human: awaiting,
        taken_by: null,
        taken_at: null,
        reason: awaiting ? (HANDOFF[n] ?? null) : null,
        released_at: null,
      },
    };
  });

  const prospects: DemoProspect[] = D.map(([n, name, laneId, productId, stage, followUps, next, human, locale, summary]) => {
    const lane = laneOf[laneId]!;
    const thread = threads.find((th) => th.prospect_id === pid(n));
    const sent = thread?.messages.filter((m) => m.direction === "outbound") ?? [];
    const received = thread?.messages.filter((m) => m.direction === "inbound") ?? [];
    const channel = lane.channel;
    const first = name.split(" ")[0]!;
    const row: ProspectRow = {
      id: pid(n),
      display_name: name,
      status: STATUS_OF[stage],
      stage,
      locale,
      lane_id: laneId,
      phone: channel === "email" ? null : `+216000000${String(n).padStart(2, "0")}`,
      email: channel === "telegram" ? null : `${slug(name)}@example.test`,
      preferred_channel: channel,
      next_action_type: next ? next[0] : null,
      next_action_due_at: next ? daysFromNow(next[1]) : null,
      last_contacted_at: sent.length > 0 ? sent[sent.length - 1]!.timestamp : null,
      last_inbound_at: received.length > 0 ? received[received.length - 1]!.timestamp : null,
      follow_up_count: followUps,
      source_id: `feuille-${laneId}`,
      last_activity_at: thread ? thread.messages[thread.messages.length - 1]!.timestamp : null,
      needs_human: human,
      status_reason: human ? (HANDOFF[n] ?? "Attente d'un conseiller") : stage === "En pause" ? `${first} a demandé à être recontacté plus tard` : null,
      has_error: n === 12,
    };
    return { row, product: productId, summary };
  });

  const proposal = (n: number, id: string, text: string, hAgo: number): Proposal => ({
    id,
    prospect_name: D.find((d) => d[0] === n)![1],
    text,
    language: "fr",
    due_at: hoursAgo(hAgo),
  });
  const proposals: Proposal[] = [
    proposal(22, "demo-ap-1", "Bonjour Dorra, merci pour votre commande groupée. Un conseiller vous contacte aujourd'hui pour étudier une remise sur les trois boutiques (offre fictive).", 1),
    proposal(29, "demo-ap-2", "Bonjour Nizar, nous sommes désolés pour l'erreur constatée sur votre site. Notre équipe technique intervient dès aujourd'hui et un conseiller vous recontacte pour la suite à donner à votre demande.", 3),
    proposal(30, "demo-ap-3", "Bonjour Olfa, un conseiller vous rappellera demain à 10 h comme demandé. Merci de confirmer ce créneau.", 2),
    proposal(19, "demo-ap-4", "Bonjour, l'intégration avec votre logiciel de gestion nécessite l'avis d'un expert. Je le consulte et je reviens vers vous avec une réponse précise sous 48 h.", 5),
    proposal(31, "demo-ap-5", "Bonjour Jawher, je vous mets en relation avec un conseiller pour votre formation d'équipe. Il vous contactera dans la journée.", 4),
    proposal(20, "demo-ap-6", "Bonjour Maya, je vous propose le Site vitrine Essentiel à 1 200 TND payable en deux fois sans frais. Souhaitez-vous que je vous envoie le devis ?", 6),
  ];

  const pay = (n: number, progress: PaymentCase["progress"], hAgo: number, extra: Partial<PaymentCase> = {}): PaymentCase => ({
    prospect_id: pid(n),
    prospect_name: D.find((d) => d[0] === n)![1],
    progress,
    signalled_at: hoursAgo(hAgo),
    confirmed_at: null,
    confirmed_by: null,
    amount: null,
    currency: null,
    reference: null,
    ...extra,
  });
  const payments: PaymentCase[] = [
    pay(26, "claimed", 13),
    pay(27, "proof_received", 9),
    pay(28, "paid", 190, { confirmed_at: hoursAgo(180), confirmed_by: "Demo", amount: "450", currency: "TND", reference: "VIR-DEMO-0028" }),
    pay(15, "paid", 290, { confirmed_at: hoursAgo(280), confirmed_by: "Demo", amount: "1200", currency: "TND", reference: "VIR-DEMO-0015" }),
    pay(23, "paid", 310, { confirmed_at: hoursAgo(300), confirmed_by: "Demo", amount: "750", currency: "TND", reference: "VIR-DEMO-0023" }),
  ];

  const notif = (id: number, h: number, kind: string, severity: DemoNotification["severity"], title: string, message: string, action: string, read: boolean): DemoNotification => ({
    id,
    created_at: hoursAgo(h),
    kind,
    severity,
    title,
    message,
    action,
    read_at: read ? hoursAgo(h - 1) : null,
  });
  const notifications: DemoNotification[] = [
    notif(1, 2, "demo_payment_to_confirm", "warning", "Deux paiements à vérifier", "Rim Démo et Bilel Exemple annoncent un paiement.", "Ouvrez « À valider » puis « Paiements ».", false),
    notif(2, 5, "demo_human_requested", "warning", "Quatre conversations attendent un conseiller", "Remise, réclamation, rappel et transfert demandés.", "Ouvrez « Conversations » pour prendre la main.", false),
    notif(3, 20, "demo_daily_report", "info", "Rapport du jour disponible", "Le rapport quotidien de démonstration est prêt.", "Consultez « Rapports ».", true),
    notif(4, 70, "demo_sync_ok", "info", "Tableur synchronisé", "Les prospects du tableur de démonstration sont à jour.", "", true),
  ];

  const invoice = (id: number, period: string, issued: string, amount: string, status: ClientInvoiceDetail["status"]): ClientInvoiceDetail => ({
    invoice_id: id,
    number: `DEMO-${String(id).padStart(3, "0")}`,
    period,
    issued_on: issued,
    currency: "EUR",
    amount,
    amount_after_discount: null,
    discount_amount: null,
    free_amount: null,
    status,
    collected: status === "PAID",
    due_at: issued,
    overdue: false,
    created_at: issued,
    status_changed_at: status === "PAID" ? issued : null,
    lines: [{ plan_name: "Plan Démo", monthly_price: amount, currency: "EUR", amount }],
  });
  const month = (back: number) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
    return d.toISOString().slice(0, 10);
  };
  const invoices = [
    invoice(3, month(0), month(0), "149.00", "PENDING"),
    invoice(2, month(1), month(1), "149.00", "PAID"),
    invoice(1, month(2), month(2), "149.00", "PAID"),
  ];

  const reportDays: string[] = [];
  for (let i = 1; i <= 7; i += 1) reportDays.push(new Date(t0 - i * 86_400_000).toISOString().slice(0, 10));

  return {
    generatedAt: iso(t0),
    agent: { state: "running", changed_at: hoursAgo(30) },
    settings: buildSettings(),
    lanes: buildLanes(),
    prospects,
    threads,
    proposals,
    payments,
    notifications,
    invoices,
    followUp: {
      configured: true,
      max_followups: 3,
      gap_days: 3,
      window: { start: "09:00", end: "18:00", timezone: "Africa/Tunis" },
      timezone: "Africa/Tunis",
      custom_schedule: false,
      defaults: { max_followups: 3, gap_days: 3 },
      bounds: { max_followups: [0, 6], gap_days: [1, 14] },
    },
    reportSettings: {
      enabled: true,
      channel: "email",
      recipient: "direction@example.test",
      send_time: "18:30",
      timezone: "Africa/Tunis",
      language: "fr",
      updated_at: hoursAgo(200),
    },
    reportDays,
  };
}

export const DEMO_PRODUCT_NAMES: Record<string, string> = {
  "site-vitrine": "Site vitrine Essentiel",
  "boutique-pro": "Boutique en ligne Pro",
  "reseaux-sociaux": "Pack Réseaux sociaux",
  "audit-express": "Audit marketing express",
  "formation-vente": "Formation Vente en ligne",
};
