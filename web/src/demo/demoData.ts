/** Initial data of the Demo space. EVERYTHING here is fictitious: names, addresses, numbers, prices
 *  and messages. Nothing comes from a real client. `buildDemoData` is pure: calling it again gives
 *  the same initial state, which is how "reset" and "reload" restore the demonstration.
 *
 *  The story: a small online-training business with two circuits, one sending by WhatsApp from a
 *  spreadsheet, one waiting for its e-mail box (HubSpot leads, nothing sent yet). */

import type { ConversationMessage, Lane, PaymentCase, Proposal, ProspectRow } from "../client/api";
import type { ClientInvoiceDetail } from "../client/billing/billingApi";
import type { DemoData, DemoNotification, DemoProspect, DemoThread } from "./demoTypes";

type Doc = Record<string, unknown>;

export const DEMO_BUSINESS = "Naga Formation";

/* ── Circuits (lanes) ─────────────────────────────────────────────────────────────────────── */

const CHANNELS = {
  email: { channel: "email", label: "E-mail" },
  telegram: { channel: "telegram", label: "Telegram" },
  whatsapp: { channel: "whatsapp", label: "WhatsApp" },
} as const;

interface LaneDef {
  id: string;
  label: string;
  provider: "google_sheets" | "hubspot";
  channel: keyof typeof CHANNELS;
  /** false: the channel is not open yet (Revansa is still preparing it). */
  ready: boolean;
  problem: string | null;
  products: string[];
}

const LANE_DEFS: LaneDef[] = [
  { id: "naga-tunisie", label: "Prospects Tunisie", provider: "google_sheets", channel: "whatsapp", ready: true, problem: null, products: ["pack-vente-pro", "coaching-individuel"] },
  { id: "leads-site-web", label: "Leads site web", provider: "hubspot", channel: "email", ready: false, problem: "Revansa prépare votre boîte d’envoi : rien ne part avant qu’elle soit prête.", products: ["ALL"] },
];

function buildLanes(): Lane[] {
  return LANE_DEFS.map((l, i) => ({
    id: l.id,
    position: i,
    source: {
      id: `feuille-${l.id}`,
      source: l.provider,
      label: l.label,
      google_sheets: l.provider === "google_sheets" ? { spreadsheet_id: "tableur-demo-fictif", prospects_tab: "Prospects" } : null,
    },
    channel: { ...CHANNELS[l.channel], authorized: l.ready, problem: l.problem },
    products: [...l.products],
    offers_all_products: l.products.includes("ALL"),
  }));
}

/** The circuits' display names (the lane model has no name field: the id stands in for it in the UI). */
export const DEMO_LANE_NAMES: Record<string, string> = Object.fromEntries(LANE_DEFS.map((l) => [l.id, l.label]));

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
    url: `https://naga.example.test/offres/${id}`,
    pricing: { disclose: "after_interest", ...pricing },
    value: { proposition: { fr: summary }, benefits, differentiators: ["Formateurs en activité", "Accès à vie aux supports"] },
    audience: { suited_for: suitedFor, not_for: notFor },
    sales_arguments: [{ id: "resultats", text: "Des cas concrets applicables dès le premier jour." }],
    objections: [{ code: objection[0], examples: [objection[1]], response: objection[2] }],
    strategy: { cta: { kind: "request_info", label: { fr: "Recevoir le programme" } } },
    restrictions: { do_not_claim: ["Résultats garantis", "Revenus chiffrés"] },
    ...extra,
  };
}

const PAY_OFFLINE = { enabled: true, methods: ["virement"], receipt_required: true };

function buildSettings(): DemoData["settings"] {
  const products: Doc[] = [
    product(
      "pack-vente-pro",
      "active",
      "Pack Vente Pro",
      "Programme complet pour apprendre à vendre en ligne.",
      "Programme en 8 modules : trouver ses clients, présenter son offre, conclure par message. Accès à vie aux supports, coaching de groupe inclus.",
      { model: "fixed", amount: 490, currency: "TND" },
      ["Accès à vie", "Coaching de groupe inclus", "Supports téléchargeables"],
      ["Commerçants", "Indépendants et freelances"],
      ["Personnes sans projet d’activité"],
      ["prix", "C’est trop cher pour moi.", "Le paiement peut se faire en deux fois, et vous avez 14 jours pour changer d’avis."],
      { payment: { online: { enabled: true, methods: ["pay-pack-vente-pro"] }, offline: PAY_OFFLINE } },
    ),
    product(
      "coaching-individuel",
      "active",
      "Coaching individuel",
      "Accompagnement personnalisé sur 3 mois.",
      "Un rendez-vous par semaine avec un formateur pour construire votre offre et vos premières ventes, avec suivi entre les séances.",
      { model: "fixed", amount: 1200, currency: "TND", note: "Paiement en 1 ou 2 fois" },
      ["Un rendez-vous par semaine", "Suivi entre les séances", "Plan d’action écrit"],
      ["Porteurs de projet", "Petites équipes"],
      ["Personnes qui cherchent une formation en autonomie"],
      ["temps", "Je n’ai pas le temps.", "Les rendez-vous sont fixés selon vos disponibilités, 45 minutes par semaine."],
      { payment: { online: { enabled: false, methods: [] }, offline: PAY_OFFLINE } },
    ),
    product(
      "masterclass-ete",
      "coming_soon",
      "Masterclass été",
      "Session intensive de deux jours.",
      "Deux jours en direct pour construire son offre et ses messages de vente. Dates annoncées prochainement.",
      { model: "fixed", amount: 90, currency: "TND" },
      ["Deux jours en direct", "Replay inclus"],
      ["Tous profils"],
      ["Personnes indisponibles en été"],
      ["dates", "Quelles sont les dates ?", "Les dates seront annoncées bientôt ; nous pouvons vous prévenir dès qu’elles le sont."],
      { strategy: { cta: { kind: "notify_me", label: { fr: "Me prévenir" } } } },
    ),
    product(
      "ancien-programme",
      "inactive",
      "Ancien programme",
      "Programme remplacé par le Pack Vente Pro.",
      "Ancienne version du programme, conservée pour l’historique.",
      { model: "fixed", amount: 200, currency: "TND" },
      ["Supports d’origine"],
      ["Anciens clients"],
      ["Nouveaux clients"],
      ["prix", "Combien ?", "Cette offre n’est plus proposée."],
    ),
  ];
  return {
    timezone: "Africa/Tunis",
    opt_out_keywords: ["stop", "désinscrire", "unsubscribe"],
    schedule: { daily_at: "09:30", days: ["mon", "tue", "wed", "thu", "fri"] },
    commercial: {
      business: {
        name: DEMO_BUSINESS,
        website: "https://naga.example.test",
        summary: { fr: "Naga Formation est un organisme fictif de formation en ligne : il apprend aux commerçants et aux indépendants à vendre leurs services." },
      },
      communication: { languages: { default: "fr", supported: ["fr", "en", "ar"] }, tone: { style: "Chaleureux, professionnel et concis" } },
      catalog: { products },
      payment: {
        methods: [
          {
            id: "virement",
            status: "active",
            kind: "offline",
            label: { fr: "Virement bancaire" },
            instructions: [{ id: "main", text: { fr: "Effectuez le virement puis envoyez-nous le reçu." }, fields: [{ label: { fr: "Banque" }, value: { fr: "Banque Exemple" } }, { label: { fr: "RIB" }, value: { fr: "00 000 0000000000 00 (exemple fictif)" } }] }],
            confirmation: { mode: "manual", note: { fr: "Nous confirmons le paiement sous 24 h ouvrées." } },
          },
          {
            id: "pay-pack-vente-pro",
            status: "active",
            kind: "online",
            label: { fr: "Paiement en ligne · Pack Vente Pro" },
            instructions: [{ id: "main", url: "https://paiement.example.test/naga/pack-vente-pro" }],
            confirmation: { mode: "manual" },
          },
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

const T = "naga-tunisie";
const L = "leads-site-web";
const PACK = "pack-vente-pro";
const COACH = "coaching-individuel";
const MASTER = "masterclass-ete";

const D: Def[] = [
  [1, "Amira Ben Salah", T, PACK, "Intéressé", 0, ["reply", 0], false, "fr", "Intéressée par le Pack Vente Pro, demande le programme détaillé."],
  [2, "Karim Trabelsi", T, COACH, "Vente conclue", 0, ["reminder", 0], false, "fr", "Dit avoir payé le coaching par virement : reçu à vérifier."],
  [3, "Leila Mansour", T, PACK, "À traiter par un conseiller", 1, null, true, "fr", "Demande un geste sur le prix : à reprendre par une personne."],
  [4, "Youssef Gharbi", T, PACK, "En attente de réponse", 2, ["follow_up", 1], false, "fr", "Deux relances envoyées, sans réponse pour le moment."],
  [5, "Sonia Jlassi", T, COACH, "En pause", 0, ["reminder", 25], false, "fr", "En déplacement, à recontacter dans un mois."],
  [6, "Hatem Bouaziz", T, PACK, "Vente conclue", 0, null, false, "fr", "Pack Vente Pro commandé et payé."],
  [7, "Walid Dridi", T, PACK, "Désinscrit", 0, null, false, "fr", "A demandé à ne plus être contacté."],
  [8, "Rim Haddad", T, PACK, "Contacté", 0, ["follow_up", 2], false, "fr", "Première présentation du Pack Vente Pro envoyée."],
  [9, "Emna Zouari", T, PACK, "Vente conclue", 0, ["reminder", 0], false, "fr", "Dit avoir payé le Pack Vente Pro : réception à vérifier."],
  [10, "Slim Ayari", T, COACH, "Vente conclue", 0, null, false, "fr", "Coaching payé et confirmé."],
  [11, "Ines Kallel", T, COACH, "En négociation", 1, ["reply", 0], false, "fr", "Hésite sur le prix, paiement en deux fois proposé."],
  [12, "Mehdi Rezgui", T, PACK, "Relance nécessaire", 2, ["follow_up", -1], false, "fr", "Deux relances sans réponse : une relance est en retard."],
  [13, "Sami Ferchichi", T, COACH, "Intéressé", 0, ["reply", 0], false, "fr", "Demande les prochaines dates de coaching."],
  [14, "Rania Cherif", T, PACK, "Perdu / non intéressé", 0, null, false, "fr", "Trouve l’offre trop chère."],
  [15, "Anis Hamdi", T, PACK, "En attente de réponse", 1, ["follow_up", 2], false, "fr", "Une relance envoyée, en attente."],
  [16, "Dorra Msakni", T, COACH, "À traiter par un conseiller", 0, null, true, "fr", "Demande une remise pour un coaching d’équipe."],
  [17, "Nour Chaabane", L, MASTER, "Nouveau", 0, ["first_contact", 1], false, "fr", "Nouveau contact du site web, pas encore contacté."],
  [18, "Bilel Sassi", L, MASTER, "Nouveau", 0, ["first_contact", 1], false, "fr", "Nouveau contact du site web, pas encore contacté."],
  [19, "Chiraz Jebali", L, MASTER, "Nouveau", 0, ["first_contact", 2], false, "fr", "Nouveau contact du site web, pas encore contacté."],
  [20, "Omar Khelifi", L, PACK, "Nouveau", 0, ["first_contact", 2], false, "fr", "Nouveau contact du site web, pas encore contacté."],
  [21, "Olfa Mejri", L, COACH, "Nouveau", 0, ["first_contact", 3], false, "fr", "Nouveau contact du site web, pas encore contacté."],
  [22, "Jawher Tlili", L, MASTER, "Nouveau", 0, ["first_contact", 3], false, "fr", "Nouveau contact du site web, pas encore contacté."],
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
const PACK_INTRO = INTRO("Vous avez montré de l'intérêt pour nos formations : le Pack Vente Pro apprend à vendre en ligne.");
const COACH_INTRO = INTRO("Notre Coaching individuel vous accompagne pendant 3 mois sur votre projet.");

const SCRIPTS: Script[] = [
  [1, 30, [["o", PACK_INTRO], ["i", "Oui, combien coûte le Pack Vente Pro ?"], ["o", "Le Pack Vente Pro est à 490 TND, avec accès à vie et coaching de groupe. Voulez-vous le détail du programme ?"], ["i", "Oui, envoyez-moi le programme s'il vous plaît."], ["o", "Voici le programme détaillé (PDF). Je reviens vers vous demain pour répondre à vos questions."]]],
  [2, 36, [["o", COACH_INTRO], ["i", "Ça m'intéresse, comment je règle ?"], ["o", "Le Coaching individuel est à 1 200 TND. Vous pouvez payer par virement : Banque Exemple, RIB 00 000 0000000000 00 (exemple fictif). Envoyez-moi le reçu une fois fait."], ["i", "J'ai fait le virement ce matin, voici le reçu."], ["o", "Merci ! Un conseiller vérifie la réception du paiement et vous confirme."]]],
  [3, 60, [["o", PACK_INTRO], ["i", "Pouvez-vous faire un geste sur le prix ? Le budget est serré."], ["o", "Je ne peux pas modifier le tarif moi-même. Je transmets votre demande à un conseiller qui vous répondra."]]],
  [4, 150, [["o", PACK_INTRO], ["o", "Je reviens vers vous au sujet du Pack Vente Pro. Avez-vous pu consulter le programme ?"], ["o", "Petite relance (2 sur 3) : je reste disponible pour répondre à vos questions."]]],
  [5, 400, [["o", COACH_INTRO], ["i", "Je suis en déplacement, recontactez-moi dans un mois."], ["o", "Pas de souci, je vous recontacte dans un mois. Bon voyage !"]]],
  [6, 320, [["o", PACK_INTRO], ["i", "Je veux m'inscrire."], ["o", "Parfait ! Voici le lien de paiement : https://paiement.example.test/naga/pack-vente-pro (490 TND)."], ["i", "Paiement effectué."], ["o", "Merci Hatem, votre paiement est confirmé. Bienvenue dans le Pack Vente Pro !"]]],
  [7, 300, [["o", PACK_INTRO], ["i", "STOP, merci de ne plus me contacter."], ["o", "C'est noté, vous ne recevrez plus de messages de notre part."]]],
  [8, 28, [["o", PACK_INTRO]]],
  [9, 12, [["o", "Bonjour Emna, voici comment régler le Pack Vente Pro (490 TND) : https://paiement.example.test/naga/pack-vente-pro"], ["i", "C'est payé, capture d'écran en pièce jointe."], ["o", "Merci ! Un conseiller vérifie la réception du paiement et vous confirme."]]],
  [10, 280, [["o", COACH_INTRO], ["i", "Je m'inscris au coaching."], ["o", "Bienvenue ! Le Coaching individuel est à 1 200 TND, payable par virement."], ["i", "Virement fait."], ["o", "Paiement confirmé, bienvenue dans le coaching !"]]],
  [11, 90, [["o", INTRO("Le Coaching individuel est à 1 200 TND.")], ["i", "C'est un peu cher."], ["o", "Je comprends. Le paiement en deux fois est possible. Cela vous convient-il ?"]]],
  [12, 230, [["o", PACK_INTRO], ["i", "Je regarde ça cette semaine."], ["o", "Bonjour Mehdi, avez-vous pu regarder le programme ?"], ["o", "Dernière relance : je reste disponible pour un appel."]]],
  [13, 40, [["o", COACH_INTRO], ["i", "Quelles sont les prochaines dates ?"]]],
  [14, 330, [["o", PACK_INTRO], ["i", "Non merci, c'est trop cher pour moi."], ["o", "Je comprends. N'hésitez pas à revenir vers nous si votre budget évolue."]]],
  [15, 80, [["o", PACK_INTRO], ["o", "Petite relance : souhaitez-vous recevoir le programme détaillé ?"]]],
  [16, 50, [["o", COACH_INTRO], ["i", "Nous sommes cinq, pouvez-vous faire une remise pour une formation d'équipe ?"], ["o", "Une remise doit être validée par un conseiller. Je lui transmets votre demande."]]],
];

const HANDOFF: Record<number, string> = {
  3: "Demande de geste sur le prix",
  16: "Demande de remise pour une équipe",
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
      phone: channel === "email" ? null : `+216 90 000 0${String(n).padStart(2, "0")}`,
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
      status_reason: human ? (HANDOFF[n] ?? "Attente d'une personne") : stage === "En pause" ? `${first} a demandé à être recontactée plus tard` : null,
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
    proposal(1, "demo-ap-1", "Bonjour Amira, je reviens vers vous au sujet du Pack Vente Pro. Souhaitez-vous que je vous réserve une place pour la prochaine session ?", 2),
    proposal(4, "demo-ap-2", "Bonjour Youssef, c'est ma dernière relance : le Pack Vente Pro reste disponible à 490 TND. Voulez-vous que je vous envoie le lien de paiement ?", 3),
    proposal(15, "demo-ap-3", "Bonjour Anis, avez-vous eu le temps de regarder le programme ? Je peux répondre à vos questions par message ou par appel.", 5),
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
    pay(2, "proof_received", 20),
    pay(9, "claimed", 12),
    pay(10, "paid", 270, { confirmed_at: hoursAgo(260), confirmed_by: "Naga Formation", amount: "1200", currency: "TND", reference: "VIR-DEMO-0010" }),
    pay(6, "paid", 310, { confirmed_at: hoursAgo(300), confirmed_by: "Naga Formation", amount: "490", currency: "TND", reference: "LIEN-DEMO-0006" }),
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
    notif(1, 2, "demo_payment_to_confirm", "warning", "Deux paiements à vérifier", "Karim Trabelsi et Emna Zouari annoncent un paiement.", "Ouvrez « À valider » puis « Paiements ».", false),
    notif(2, 5, "demo_human_requested", "warning", "Deux conversations attendent une personne", "Un geste sur le prix et une remise pour une équipe sont demandés.", "Ouvrez « Conversations » pour prendre la main.", false),
    notif(3, 20, "demo_mailbox_pending", "info", "Votre boîte e-mail est en cours de création", "Revansa prépare l'envoi par e-mail du circuit « Leads site web ».", "", true),
    notif(4, 70, "demo_sync_ok", "info", "Tableur synchronisé", "Les prospects de « Prospects Tunisie » sont à jour.", "", true),
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
    lines: [{ plan_name: "Plan Pro", monthly_price: amount, currency: "EUR", amount }],
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
      gap_days: 2,
      window: { start: "09:00", end: "18:00", timezone: "Africa/Tunis" },
      timezone: "Africa/Tunis",
      custom_schedule: false,
      defaults: { max_followups: 3, gap_days: 2 },
      bounds: { max_followups: [0, 6], gap_days: [1, 14] },
    },
    reportSettings: {
      enabled: true,
      channel: "email",
      recipient: "naga@example.test",
      send_time: "18:00",
      timezone: "Africa/Tunis",
      language: "fr",
      updated_at: hoursAgo(200),
    },
    reportDays,
  };
}

export const DEMO_PRODUCT_NAMES: Record<string, string> = {
  "pack-vente-pro": "Pack Vente Pro",
  "coaching-individuel": "Coaching individuel",
  "masterclass-ete": "Masterclass été",
  "ancien-programme": "Ancien programme",
};
