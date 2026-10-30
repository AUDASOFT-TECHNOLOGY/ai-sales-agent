/** The Demo "server": answers the Client's `/client/v1/*` requests from memory.
 *
 *  Nothing here touches the network, a database or a browser storage. The state is a module variable
 *  built from `demoData.ts`; `resetDemo()` rebuilds it, and a page reload loses it. A request this
 *  file does not know is answered with a 404 locally: it is never forwarded anywhere. */

import type { LearnedNote, LearningAnswer } from "../client/learning/learningModel";
import type { LiveStep } from "../client/live/liveModel";
import type {
  ChannelState,
  ClientProfile,
  ConversationControl,
  ConversationHistory,
  Lane,
  LaneSync,
  PaymentCase,
  ProductResource,
  ProspectRow,
  ReportDetail,
  ReportSummary,
  SourceState,
} from "../client/api";
import type { ClientDashboard, Indicator } from "../dashboard/types";
import { buildDemoData, DEMO_BUSINESS, DEMO_LANE_NAMES, DEMO_PRODUCT_NAMES } from "./demoData";
import type { DemoData, DemoProspect, DemoThread } from "./demoTypes";

type Doc = Record<string, unknown>;
type Query = URLSearchParams;
interface Reply {
  status: number;
  body: unknown;
}

const BASE = "/client/v1";

/* ── State ─────────────────────────────────────────────────────────────────────────────────── */

interface Extras {
  profile: ClientProfile;
  versions: { settings: number; lanes: number; channel: number; source: number; followUp: number };
  channel: { channel: string; email: Doc; telegram: Doc };
  access: { gmail: boolean; sheets: boolean; hubspot: boolean; whatsapp: boolean; telegram: "not_connected" | "connected" | "code" };
  source: { source: string; spreadsheet: string; tab: string };
  laneSync: Record<string, { columns: Record<string, string> }>;
  resources: Record<string, ProductResource[]>;
  sentKeys: Set<string>;
  extraReports: Record<string, ReportDetail>;
  mailboxRevealed: boolean;
  nextId: number;
}

let data: DemoData = buildDemoData();
let extras: Extras = freshExtras();
let learning: LearningAnswer = freshLearning();

function freshExtras(): Extras {
  const now = new Date().toISOString();
  const res = (productId: string, id: string, title: string, category: ProductResource["category"], url: string, status: ProductResource["status"] = "ready", detail: string | null = null): ProductResource => ({
    id,
    product_id: productId,
    kind: "link",
    category,
    title,
    url,
    filename: null,
    mime_type: null,
    size_bytes: null,
    status,
    status_detail: detail,
    text_chars: status === "ready" ? 2400 : 0,
    created_at: now,
  });
  const file = (productId: string, id: string, title: string, category: ProductResource["category"], filename: string, mime: string, bytes: number, status: ProductResource["status"], detail: string | null): ProductResource => ({
    id,
    product_id: productId,
    kind: "file",
    category,
    title,
    url: null,
    filename,
    mime_type: mime,
    size_bytes: bytes,
    status,
    status_detail: detail,
    text_chars: status === "ready" ? 3200 : 0,
    created_at: now,
  });
  return {
    profile: { name: DEMO_BUSINESS, login: "Demo", whatsapp_number: "+21690123456", status: "active", must_change_password: false, whatsapp_reconnect_required: false },
    versions: { settings: 3, lanes: 2, channel: 2, source: 2, followUp: 1 },
    channel: {
      channel: "whatsapp",
      email: { from_name: DEMO_BUSINESS, subject: "Votre formation avec Naga Formation" },
      telegram: {
        contact_prompt: "Pour continuer, partagez-nous votre numéro de téléphone.",
        contact_button: "Partager mon numéro",
        not_found_text: "Nous n'avons pas retrouvé votre dossier (exemple fictif).",
        max_new_conversations_per_day: 5,
      },
    },
    access: { gmail: false, sheets: true, hubspot: true, whatsapp: true, telegram: "not_connected" },
    source: { source: "google_sheets", spreadsheet: "tableur-demo-fictif", tab: "Prospects" },
    laneSync: {},
    resources: {
      "pack-vente-pro": [
        file("pack-vente-pro", "r1", "Affiche du programme", "image", "affiche.jpg", "image/jpeg", 184_320, "stored_only", "image"),
        file("pack-vente-pro", "r2", "Photo de la formation", "image", "formation.jpg", "image/jpeg", 242_000, "stored_only", "image"),
        res("pack-vente-pro", "r3", "Vidéo de présentation", "video", "https://naga.example.test/presentation", "stored_only", "video"),
        file("pack-vente-pro", "r4", "Programme détaillé", "pdf", "Programme-detaille.pdf", "application/pdf", 1_240_000, "ready", null),
        file("pack-vente-pro", "r5", "Tarifs et dates", "spreadsheet", "Tarifs-et-dates.csv", "text/csv", 8_400, "ready", null),
        res("pack-vente-pro", "r6", "Témoignage de Sami, gérant de boutique", "testimonial", "https://naga.example.test/temoignages/sami", "ready", null),
        res("pack-vente-pro", "r7", "Témoignage d’Ines, freelance", "testimonial", "https://naga.example.test/temoignages/ines", "ready", null),
        res("pack-vente-pro", "r8", "Retour d’expérience de Mehdi (vidéo)", "testimonial", "https://naga.example.test/temoignages/mehdi", "stored_only", "video"),
        res("pack-vente-pro", "r9", "Avis de Hatem", "testimonial", "https://naga.example.test/temoignages/hatem", "ready", null),
      ],
      "coaching-individuel": [
        file("coaching-individuel", "r10", "Présentation du coach", "image", "coach.jpg", "image/jpeg", 150_000, "stored_only", "image"),
        file("coaching-individuel", "r11", "Déroulé du coaching", "pdf", "Deroule-coaching.pdf", "application/pdf", 640_000, "ready", null),
      ],
      "masterclass-ete": [file("masterclass-ete", "r12", "Visuel de la masterclass", "image", "masterclass.jpg", "image/jpeg", 120_000, "stored_only", "image")],
    },
    sentKeys: new Set(),
    extraReports: {},
    mailboxRevealed: false,
    nextId: 100,
  };
}

/** Back to the initial demonstration data. */
export function resetDemo(): void {
  data = buildDemoData();
  extras = freshExtras();
  learning = freshLearning();
}

/** What the demo agent "learned": an invented story of fourteen finished conversations. */
function freshLearning(): LearningAnswer {
  const note = (code: string, kind: LearnedNote["kind"], state: LearnedNote["state"], cases: number, evidence: LearnedNote["evidence"]): LearnedNote => ({
    id: code,
    code,
    kind,
    state,
    evidence,
    cases,
    against: 0,
    product: null,
    confirmed: false,
    off_by: null,
    reason: kind === "avoid" ? "loss_cause" : "wins_pattern",
  });
  return {
    counts: { studied: 14, achieved: 6, unfavourable: 8, needed: 5 },
    notes: [
      note("acknowledge_first", "do", "active", 7, "high"),
      note("short_messages", "do", "active", 6, "medium"),
      note("doubt_ignored", "avoid", "active", 6, "medium"),
      note("quick_follow_up", "avoid", "active", 5, "medium"),
      note("ask_before_proposing", "do", "watching", 4, "low"),
      note("generic_message", "avoid", "watching", 3, "low"),
    ],
  };
}

function decideLearning(id: string, body: Doc): Reply {
  const note = learning.notes.find((n) => n.id === id);
  if (!note) return fail(404, "Ce point est inconnu.");
  const action = body.action;
  if (action === "off") Object.assign(note, { state: "off", off_by: "client", confirmed: false });
  else if (action === "confirm") Object.assign(note, { state: "active", off_by: null, confirmed: true });
  else if (action === "on") Object.assign(note, { state: "watching", off_by: null, confirmed: false });
  else return fail(422, "Choix inconnu.");
  return ok(clone(note));
}

export const demoProfile = (): ClientProfile => ({ ...extras.profile });

/* ── Helpers ──────────────────────────────────────────────────────────────────────────────── */

const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, detail: unknown): Reply => ({ status, body: { detail } });
const nowIso = () => new Date().toISOString();
const clone = <T,>(v: T): T => structuredClone(v);
const obj = (v: unknown): Doc => (v && typeof v === "object" && !Array.isArray(v) ? (v as Doc) : {});
const CHANNEL_LABEL: Record<string, string> = { email: "E-mail", telegram: "Telegram", whatsapp: "WhatsApp" };
const CHANNEL_OPTIONS = [
  { key: "email", label: "E-mail" },
  { key: "telegram", label: "Telegram" },
  { key: "whatsapp", label: "WhatsApp" },
];

const prospectOf = (id: string): DemoProspect | undefined => data.prospects.find((p) => p.row.id === id);
const threadOf = (id: string): DemoThread | undefined => data.threads.find((t) => t.prospect_id === id);
const productName = (id: string): string => {
  const list = obj(obj(data.settings.commercial).catalog).products;
  const found = Array.isArray(list) ? list.map(obj).find((p) => p.id === id) : undefined;
  const name = found ? obj(found.name) : {};
  return (typeof found?.name === "string" ? found.name : (name.fr as string | undefined)) ?? DEMO_PRODUCT_NAMES[id] ?? id;
};
const laneName = (id: string): string => DEMO_LANE_NAMES[id] ?? id;

function appendMessage(thread: DemoThread, channel: string, text: string, author: "agent" | "human", delivery: "sent" | null = null): string {
  const id = `${thread.prospect_id}-m${thread.messages.length + 1}-${extras.nextId++}`;
  const at = nowIso();
  thread.messages.push({ id, direction: "outbound", channel, text, timestamp: at, author, delivery });
  const p = prospectOf(thread.prospect_id);
  if (p) {
    p.row.last_contacted_at = at;
    p.row.last_activity_at = at;
  }
  return id;
}

/* ── Prospects and conversations ──────────────────────────────────────────────────────────── */

function matches(p: DemoProspect, q: Query): boolean {
  const r = p.row;
  const text = q.get("q")?.trim().toLowerCase();
  if (text && ![r.display_name, r.email, r.phone, r.stage, p.summary].some((v) => v?.toLowerCase().includes(text))) return false;
  const statuses = q.getAll("status");
  if (statuses.length > 0 && !statuses.includes(r.status)) return false;
  if (q.get("lane") && r.lane_id !== q.get("lane")) return false;
  if (q.get("product") && p.product !== q.get("product")) return false;
  if (q.get("channel") && r.preferred_channel !== q.get("channel")) return false;
  if (q.get("source") && r.source_id !== q.get("source")) return false;
  if (q.get("needs_human") === "true" && !r.needs_human) return false;
  if (q.get("has_error") === "true" && !r.has_error) return false;
  const since = q.get("active_since");
  const until = q.get("active_until");
  const at = r.last_activity_at;
  if (since && (!at || at < since)) return false;
  if (until && (!at || at >= until)) return false;
  return true;
}

function sorted(rows: DemoProspect[], q: Query, key: (p: DemoProspect) => string | null): DemoProspect[] {
  const out = [...rows];
  if (q.get("sort") === "name") return out.sort((a, b) => a.row.display_name.localeCompare(b.row.display_name));
  return out.sort((a, b) => (key(b) ?? "").localeCompare(key(a) ?? ""));
}

function paged<T>(items: T[], q: Query): { total: number; limit: number; offset: number; slice: T[] } {
  const limit = Math.min(Math.max(Number(q.get("limit")) || 50, 1), 100);
  const offset = Math.max(Number(q.get("offset")) || 0, 0);
  return { total: items.length, limit, offset, slice: items.slice(offset, offset + limit) };
}

function listProspects(q: Query): Reply {
  const rows = sorted(data.prospects.filter((p) => matches(p, q)), q, (p) => p.row.last_activity_at ?? null);
  const { total, limit, offset, slice } = paged(rows, q);
  return ok({ total, limit, offset, prospects: slice.map((p) => clone(p.row)) });
}

function listConversations(q: Query): Reply {
  const withThread = data.prospects.filter((p) => (threadOf(p.row.id)?.messages.length ?? 0) > 0 && matches(p, q));
  const rows = sorted(withThread, q, (p) => p.row.last_activity_at ?? null);
  const { total, limit, offset, slice } = paged(rows, q);
  return ok({
    total,
    limit,
    offset,
    conversations: slice.map((p) => {
      const messages = threadOf(p.row.id)!.messages;
      const last = messages[messages.length - 1]!;
      return { prospect: clone(p.row), message_count: messages.length, last_message_at: last.timestamp, last_message_direction: last.direction };
    }),
  });
}

function conversation(id: string, q: Query): Reply {
  const p = prospectOf(id);
  if (!p) return fail(404, "Conversation introuvable.");
  const thread = threadOf(id);
  const all = thread?.messages ?? [];
  const order = q.get("order") === "desc" ? "desc" : "asc";
  const hasPage = q.has("limit");
  const limit = hasPage ? Math.min(Math.max(Number(q.get("limit")) || 200, 1), 500) : 200;
  const offset = hasPage ? Math.max(Number(q.get("offset")) || 0, 0) : 0;
  const ordered = order === "desc" ? [...all].reverse() : all;
  const row = p.row;
  const history: ConversationHistory = {
    prospect_id: id,
    conversation_id: `conv-${id}`,
    origin: thread?.origin ?? "unknown",
    messages: clone(ordered.slice(offset, offset + limit)),
    total_messages: all.length,
    limit,
    offset,
    order,
    prospect: clone(row),
    next_follow_up: row.next_action_type && row.next_action_due_at ? { action_type: row.next_action_type, due_at: row.next_action_due_at } : null,
    errors: row.has_error
      ? [{ action_id: "demo-act-1", action_type: "follow_up", due_at: row.next_action_due_at, attempts: 2, reason: "Message non délivré (exemple fictif)" }]
      : [],
    control: thread ? clone(thread.control) : null,
  };
  return ok(history);
}

function takeOver(id: string, body: Doc): Reply {
  const thread = threadOf(id);
  if (!thread) return fail(404, "Conversation introuvable.");
  const control: ConversationControl = {
    mode: "human",
    awaiting_human: false,
    taken_by: "Demo",
    taken_at: nowIso(),
    reason: typeof body.reason === "string" && body.reason ? body.reason : thread.control.reason,
    released_at: null,
  };
  thread.control = control;
  return ok(control);
}

function release(id: string): Reply {
  const thread = threadOf(id);
  const p = prospectOf(id);
  if (!thread || !p) return fail(404, "Conversation introuvable.");
  if (thread.control.mode !== "human") return ok({ released: false, control: clone(thread.control), status: p.row.status, next_follow_up_at: p.row.next_action_due_at });
  thread.control = { mode: "agent", awaiting_human: false, taken_by: null, taken_at: null, reason: null, released_at: nowIso() };
  p.row.needs_human = false;
  if (p.row.status === "paused" && p.row.stage === "À traiter par un conseiller") {
    p.row.status = "active";
    p.row.stage = "En attente de réponse";
    p.row.status_reason = null;
    p.row.next_action_type = "follow_up";
    p.row.next_action_due_at = new Date(Date.now() + 2 * 86_400_000).toISOString();
  }
  return ok({ released: true, control: clone(thread.control), status: p.row.status, next_follow_up_at: p.row.next_action_due_at });
}

function sendMessage(id: string, body: Doc): Reply {
  const thread = threadOf(id);
  const p = prospectOf(id);
  if (!thread || !p) return fail(404, "Conversation introuvable.");
  const key = String(body.idempotency_key ?? "");
  const text = String(body.text ?? "").trim();
  if (thread.control.mode !== "human") return fail(409, { code: "not_in_human_mode", message: "Prenez d'abord la main sur la conversation." });
  if (p.row.status === "opted_out") return fail(409, { code: "opted_out", message: "Ce contact s'est désinscrit." });
  if (text === "") return fail(422, "Le message est vide.");
  const channel = p.row.preferred_channel ?? "email";
  if (extras.sentKeys.has(key)) return ok({ message_id: `${id}-replayed`, channel, sent_at: nowIso(), replayed: true });
  extras.sentKeys.add(key);
  const messageId = appendMessage(thread, channel, text, "human", "sent");
  return ok({ message_id: messageId, channel, sent_at: nowIso(), replayed: false });
}

/* ── Approvals and payments ───────────────────────────────────────────────────────────────── */

function decideProposal(id: string, decision: string): Reply {
  const at = data.proposals.findIndex((p) => p.id === id);
  if (at < 0) return fail(404, "Proposition introuvable.");
  const [proposal] = data.proposals.splice(at, 1);
  if (decision === "approve") {
    const p = data.prospects.find((x) => x.row.display_name === proposal!.prospect_name);
    const thread = p ? threadOf(p.row.id) : undefined;
    if (p && thread) appendMessage(thread, p.row.preferred_channel ?? "email", proposal!.text, "agent", p.row.preferred_channel === "whatsapp" ? "sent" : null);
  }
  return ok({ id, decision });
}

function confirmPayment(id: string, body: Doc): Reply {
  const c = data.payments.find((x) => x.prospect_id === id);
  const p = prospectOf(id);
  if (!c || !p) return fail(404, "Paiement introuvable.");
  Object.assign(c, { progress: "paid", confirmed_at: nowIso(), confirmed_by: "Demo", amount: String(body.amount ?? ""), currency: String(body.currency ?? ""), reference: String(body.reference ?? "") } satisfies Partial<PaymentCase>);
  p.row.stage = "Vente conclue";
  p.row.status = "closed";
  p.row.next_action_type = null;
  p.row.next_action_due_at = null;
  const thread = threadOf(id);
  if (thread) appendMessage(thread, p.row.preferred_channel ?? "email", "Merci, votre paiement est bien confirmé. Nous vous écrivons très vite pour la suite.", "agent", p.row.preferred_channel === "whatsapp" ? "sent" : null);
  return ok(clone(c));
}

/* ── Channel, access, source ──────────────────────────────────────────────────────────────── */

function channelState(): ChannelState {
  const c = extras.channel;
  return {
    version: extras.versions.channel,
    channel: c.channel,
    authorized: true,
    options: CHANNEL_OPTIONS,
    status: c.channel === "email" ? "waiting_platform" : c.channel === "telegram" ? "action_required" : "ready",
    status_message: c.channel === "email" ? "Revansa prépare votre boîte d’envoi." : c.channel === "telegram" ? "Connectez votre compte Telegram." : null,
    email: {
      mode: extras.access.gmail && c.email.use_gmail ? "gmail" : "platform",
      from_name: c.email.from_name as string,
      sender_name_confirmed: true,
      subject: c.email.subject as string,
      activation: { status: "in_progress", requested_at: nowIso(), updated_at: nowIso(), reason: null },
      sending: { state: "not_set", message: "Votre boîte d’envoi n’est pas encore prête.", daily_limit: null, sent_today: null },
    },
    telegram: { mode: c.telegram.use_account ? "account" : "bot", ...(c.telegram as object) } as ChannelState["telegram"],
    sender_name_proposal: DEMO_BUSINESS,
    updated_at: nowIso(),
    updated_by: "client",
  };
}

function saveChannel(body: Doc): Reply {
  const c = extras.channel;
  if (typeof body.channel === "string") c.channel = body.channel;
  const email = obj(body.email);
  const telegram = obj(body.telegram);
  if (typeof email.from_name === "string") c.email.from_name = email.from_name;
  if (typeof email.subject === "string") c.email.subject = email.subject;
  if (typeof email.use_gmail === "boolean") c.email.use_gmail = email.use_gmail;
  for (const [k, v] of Object.entries(telegram)) c.telegram[k] = v;
  extras.versions.channel += 1;
  return ok(channelState());
}

const gmailStatus = () => ({
  state: extras.access.gmail ? "connected" : "not_connected",
  address: extras.access.gmail ? "naga@example.test" : null,
  send_cap_configured: true,
  send_ready: extras.access.gmail,
  message: extras.access.gmail ? "Gmail est connecté (simulation de démonstration)." : "Gmail n'est pas connecté.",
  mailbox_sync: extras.access.gmail ? "ok" : "not_started",
  last_sync_at: extras.access.gmail ? nowIso() : null,
});
const sheetsAccess = () => ({
  state: extras.access.sheets ? "connected" : "not_connected",
  address: extras.access.sheets ? "naga@example.test" : null,
  spreadsheet_saved: true,
  message: extras.access.sheets ? "Le tableur est autorisé (simulation de démonstration)." : "Autorisez l'accès au tableur.",
});
const hubspotAccess = () => ({
  state: extras.access.hubspot ? "connected" : "not_connected",
  account: extras.access.hubspot ? "Compte HubSpot de démonstration" : null,
  message: extras.access.hubspot ? "HubSpot est connecté (simulation de démonstration)." : "HubSpot n'est pas connecté.",
});
const whatsappStatus = () => ({
  state: extras.access.whatsapp ? "connected" : "not_connected",
  number: extras.access.whatsapp ? "+21690123456" : null,
  verified_name: extras.access.whatsapp ? DEMO_BUSINESS : null,
  status: extras.access.whatsapp ? "ready" : "action_required",
  message: extras.access.whatsapp ? null : "Connectez votre compte WhatsApp Business.",
  can_start_conversations: extras.access.whatsapp,
  delivered_7d: 212,
  failed_7d: 3,
});
const telegramAccount = () => ({
  available: true,
  state: extras.access.telegram === "connected" ? "connected" : "not_connected",
  limited: false,
  login: extras.access.telegram === "code" ? "code" : null,
  new_today: extras.access.telegram === "connected" ? 3 : null,
  daily_limit: 20,
});

function sourceState(): SourceState {
  return {
    version: extras.versions.source,
    source: extras.source.source,
    runnable: true,
    options: [
      { key: "google_sheets", label: "Google Sheets", runnable: true },
      { key: "hubspot", label: "HubSpot", runnable: true },
    ],
    google_sheets: { spreadsheet_id: extras.source.spreadsheet, prospects_tab: extras.source.tab, share_with: "agent@example.test" },
    access: extras.source.source === "hubspot" ? "ready" : null,
    last_test: { ok: true, checked_at: nowIso(), current: true },
    updated_at: nowIso(),
    updated_by: "client",
  };
}

function lanesState() {
  const list = obj(obj(data.settings.commercial).catalog).products;
  const catalog = (Array.isArray(list) ? list.map(obj) : []).filter((p) => p.status !== "inactive").map((p) => ({ id: String(p.id), name: productName(String(p.id)) }));
  return {
    version: extras.versions.lanes,
    editable: true,
    lanes: clone(data.lanes),
    source_options: [
      { key: "google_sheets", label: "Google Sheets" },
      { key: "hubspot", label: "HubSpot" },
    ],
    channel_options: CHANNEL_OPTIONS,
    catalog,
    share_with: "agent@example.test",
  };
}

function createLane(body: Doc): Reply {
  const id = String(body.id ?? "");
  if (id === "" || data.lanes.some((l) => l.id === id)) return fail(409, "Un circuit porte déjà ce nom.");
  const source = obj(body.source);
  const sheets = obj(source.google_sheets);
  const channel = String(body.channel ?? "email");
  const products = Array.isArray(body.products) ? (body.products as string[]) : [];
  const lane: Lane = {
    id,
    position: data.lanes.length,
    source: {
      id: String(source.id ?? `feuille-${id}`),
      source: String(source.provider ?? "google_sheets"),
      label: `Prospects – ${id}`,
      google_sheets: String(source.provider ?? "google_sheets") === "google_sheets" ? { spreadsheet_id: String(sheets.spreadsheet ?? "tableur-demo-fictif"), prospects_tab: String(sheets.prospects_tab ?? "Prospects") } : null,
    },
    channel: { channel, label: CHANNEL_LABEL[channel] ?? channel, authorized: true, problem: null },
    products,
    offers_all_products: products.includes("ALL"),
  };
  data.lanes.push(lane);
  extras.versions.lanes += 1;
  return ok(clone(lane), 201);
}

function updateLane(id: string, body: Doc): Reply {
  const lane = data.lanes.find((l) => l.id === id);
  if (!lane) return fail(404, "Circuit introuvable.");
  if (typeof body.channel === "string") lane.channel = { channel: body.channel, label: CHANNEL_LABEL[body.channel] ?? body.channel, authorized: true, problem: null };
  if (Array.isArray(body.products)) {
    lane.products = body.products as string[];
    lane.offers_all_products = lane.products.includes("ALL");
  }
  const sheets = obj(body.google_sheets);
  if (lane.source.google_sheets && (sheets.spreadsheet || sheets.prospects_tab)) {
    lane.source.google_sheets = {
      spreadsheet_id: typeof sheets.spreadsheet === "string" ? sheets.spreadsheet : lane.source.google_sheets.spreadsheet_id,
      prospects_tab: typeof sheets.prospects_tab === "string" ? sheets.prospects_tab : lane.source.google_sheets.prospects_tab,
    };
  }
  extras.versions.lanes += 1;
  return ok(clone(lane));
}

function deleteLane(id: string): Reply {
  const at = data.lanes.findIndex((l) => l.id === id);
  if (at < 0) return fail(404, "Circuit introuvable.");
  data.lanes.splice(at, 1);
  data.lanes.forEach((l, i) => (l.position = i));
  extras.versions.lanes += 1;
  return ok(lanesState());
}

const SYNC_RESULTS: LaneSync["results"] = [
  { key: "status", label: "Statut" },
  { key: "last_contacted_at", label: "Dernier contact" },
  { key: "last_inbound_at", label: "Dernière réponse" },
  { key: "follow_up_count", label: "Nombre de relances" },
  { key: "next_action_type", label: "Prochaine action" },
  { key: "next_action_due_at", label: "Date de la prochaine action" },
  { key: "summary", label: "Résumé" },
];

function laneSync(id: string): Reply {
  const lane = data.lanes.find((l) => l.id === id);
  if (!lane) return fail(404, "Circuit introuvable.");
  const columns = extras.laneSync[id]?.columns ?? {};
  const sheets = lane.source.source === "google_sheets";
  const sync: LaneSync = {
    version: 1,
    lane: id,
    source: lane.source.source,
    supported: sheets,
    state: Object.keys(columns).length > 0 ? "configured" : "off",
    message: Object.keys(columns).length > 0 ? "Les résultats sont recopiés dans le tableur (simulation)." : "Aucun résultat n'est recopié dans le tableur.",
    results: SYNC_RESULTS,
    google_sheets: sheets ? { columns } : null,
    hubspot: null,
  };
  return ok(sync);
}

/* ── Reporting ────────────────────────────────────────────────────────────────────────────── */

const dayOf = (iso: string) => iso.slice(0, 10);

function figuresForDay(day: string): { sent: number; received: number; contacted: number; replied: number; newContacts: number; followUps: number; answers: number } {
  let sent = 0;
  let received = 0;
  const contacted = new Set<string>();
  const replied = new Set<string>();
  let newContacts = 0;
  let followUps = 0;
  let answers = 0;
  for (const t of data.threads) {
    t.messages.forEach((m, i) => {
      if (dayOf(m.timestamp) !== day) return;
      if (m.direction === "outbound") {
        sent += 1;
        contacted.add(t.prospect_id);
        if (i === 0) newContacts += 1;
        else if (t.messages[i - 1]!.direction === "inbound") answers += 1;
        else followUps += 1;
      } else {
        received += 1;
        replied.add(t.prospect_id);
      }
    });
  }
  return { sent, received, contacted: contacted.size, replied: replied.size, newContacts, followUps, answers };
}

function byStage(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of data.prospects) out[p.row.stage ?? "—"] = (out[p.row.stage ?? "—"] ?? 0) + 1;
  return out;
}

function reportDetail(day: string): ReportDetail {
  const f = figuresForDay(day);
  const pending = data.prospects.filter((p) => p.row.needs_human).length;
  const start = `${day}T00:00:00.000Z`;
  const end = `${day}T23:59:59.999Z`;
  const isToday = day === dayOf(nowIso());
  const generated = extras.extraReports[day];
  if (generated) return clone(generated);
  return {
    day,
    status: "sent",
    channel: "email",
    recipient_hint: "d***@example.test",
    generated_at: `${day}T18:30:00.000Z`,
    sent_at: isToday ? null : `${day}T18:31:00.000Z`,
    attempts: 1,
    next_attempt_at: null,
    error_code: null,
    problem: null,
    period_start: start,
    period_end: end,
    timezone: "Africa/Tunis",
    messages_sent: f.sent,
    replies_received: f.received,
    language: "fr",
    figures: {
      prospects: { total: data.prospects.length, by_status: byStage() },
      activity: { messages_sent: f.sent, prospects_contacted: f.contacted, new_contacts: f.newContacts, follow_ups_sent: f.followUps, answers_sent: f.answers, replies_received: f.received, prospects_replied: f.replied },
      follow_ups: { scheduled: data.prospects.filter((p) => p.row.next_action_type === "follow_up").length, due_next_24h: 3 },
      purchase_intents: { tracked: false },
      appointments: { tracked: false },
      conversions: { tracked: false },
      quota: { status: "ok", cap: 100, used: f.sent, remaining: Math.max(100 - f.sent, 0), day_utc: day },
      actions_required: pending > 0 ? [{ code: "needs_human", count: pending }] : [],
    },
    text: `Rapport de démonstration du ${day} : ${f.sent} message(s) envoyé(s), ${f.received} réponse(s) reçue(s), ${f.contacted} prospect(s) contacté(s). ${pending} conversation(s) attendent un conseiller. Données entièrement fictives.`,
  };
}

const reportSummary = (r: ReportDetail): ReportSummary => {
  const { language: _l, figures: _f, text: _t, ...summary } = r;
  void _l;
  void _f;
  void _t;
  return summary;
};

function reportDays(): string[] {
  const extra = Object.keys(extras.extraReports);
  return [...new Set([...extra, ...data.reportDays])].sort().reverse();
}

function reportingState() {
  const days = reportDays();
  return {
    settings: clone(data.reportSettings),
    channels: [
      { key: "email", available: true, reason: null },
      { key: "telegram", available: true, reason: null },
      { key: "whatsapp", available: false, reason: "not_supported" },
    ],
    languages: ["fr", "en", "ar"],
    latest: days.length > 0 ? reportSummary(reportDetail(days[0]!)) : null,
  };
}

/* ── Dashboard and summary ────────────────────────────────────────────────────────────────── */

const ind = (value: number): Indicator => ({ value, availability: "measured" });
const pct = (a: number, b: number) => (b > 0 ? Math.round((a / b) * 1000) / 10 : 0);

function summary() {
  const by_status = { new: 0, active: 0, paused: 0, opted_out: 0, closed: 0 };
  const types: Record<string, number> = {};
  let scheduled = 0;
  let next: string | null = null;
  for (const { row } of data.prospects) {
    by_status[row.status] += 1;
    if ((row.status === "new" || row.status === "active") && row.next_action_type && row.next_action_due_at) {
      scheduled += 1;
      types[row.next_action_type] = (types[row.next_action_type] ?? 0) + 1;
      if (next === null || row.next_action_due_at < next) next = row.next_action_due_at;
    }
  }
  return { total_prospects: data.prospects.length, by_status, upcoming_actions: { scheduled, by_type: types, next_due_at: next } };
}

function dashboard(): ClientDashboard {
  const rows = data.prospects;
  const contacted = rows.filter((p) => p.row.last_contacted_at).length;
  const threads = data.threads.filter((t) => t.messages.length > 0);
  const sent = threads.reduce((n, t) => n + t.messages.filter((m) => m.direction === "outbound").length, 0);
  const received = threads.reduce((n, t) => n + t.messages.filter((m) => m.direction === "inbound").length, 0);
  const replied = threads.filter((t) => t.messages.some((m) => m.direction === "inbound")).length;
  const declared = rows.filter((p) => p.row.stage === "Vente conclue").length;
  const confirmed = data.payments.filter((c) => c.progress === "paid").length;
  const awaiting = data.payments.length - confirmed;
  const needsHuman = rows.filter((p) => p.row.needs_human).length;
  const count = (key: (p: DemoProspect) => string) => {
    const out: Record<string, number> = {};
    for (const p of rows) out[key(p)] = (out[key(p)] ?? 0) + 1;
    return out;
  };
  const byChannel: Record<string, Record<string, number>> = {};
  for (const t of threads) for (const m of t.messages) {
    const label = CHANNEL_LABEL[m.channel] ?? m.channel;
    const slot = (byChannel[label] ??= { sent: 0, received: 0 });
    slot[m.direction === "outbound" ? "sent" : "received"]! += 1;
  }
  const days: string[] = [];
  for (let i = 29; i >= 0; i -= 1) days.push(new Date(Date.now() - i * 86_400_000).toISOString().slice(0, 10));
  const points = days.map((day) => ({ day, f: figuresForDay(day) }));
  const agent = data.agent;
  const month = nowIso().slice(0, 7);
  const invoices = data.invoices;
  return {
    generated_at: nowIso(),
    period: { from: days[0]!, to: days[days.length - 1]!, months: [month], granularity: "day" },
    agent: {
      availability: "measured",
      state: agent.state,
      active: agent.state === "running",
      ready: true,
      integrations: [
        { id: "source_connected", ok: true, message: null },
        { id: "channel_ready", ok: true, message: null },
      ],
    },
    activity: {
      prospects_total: ind(rows.length),
      prospects_contacted: ind(contacted),
      conversations: ind(threads.length),
      messages_sent: ind(sent),
      replies_received: ind(received),
      prospects_replied: ind(replied),
      reply_rate_percent: ind(pct(replied, contacted)),
      needs_human: ind(needsHuman),
      with_errors: ind(rows.filter((p) => p.row.has_error).length),
      conversions_declared: ind(declared),
      conversions_confirmed: ind(confirmed),
      conversion_rate_declared_percent: ind(pct(declared, contacted)),
      conversion_rate_confirmed_percent: ind(pct(confirmed, contacted)),
      payments: { declared: ind(data.payments.length), confirmed: ind(confirmed), awaiting_confirmation: ind(awaiting) },
      by_status: byStage(),
      by_circuit: count((p) => laneName(p.row.lane_id ?? "—")),
      by_product: count((p) => productName(p.product)),
      by_channel: count((p) => CHANNEL_LABEL[p.row.preferred_channel ?? ""] ?? "—"),
      by_source: count((p) => laneName(p.row.lane_id ?? "—")),
      messages_by_channel: byChannel,
    },
    human_requests: ind(needsHuman + data.proposals.length),
    incidents: {
      open: ind(1),
      by_severity: { warning: 1 },
      by_kind: { delivery: 1 },
      sync_failures: ind(0),
      connection_problems: ind(0),
      items: [{ id: 1, created_at: nowIso(), kind: "delivery", severity: "warning", title: "Un message n'a pas pu être remis", message: "Mehdi Exemple n'a pas reçu la dernière relance (exemple fictif).", action: "Vérifiez le numéro du contact." }],
    },
    usage: { availability: "measured", state: "ok", message: "Votre usage est normal.", action: "", plan_name: "Plan Pro", daily_limit: 100, daily_used: points[points.length - 1]!.f.sent, daily_percent: points[points.length - 1]!.f.sent, month_used: sent },
    billing: {
      availability: "measured",
      subscription: { status: "active", plan_name: "Plan Pro", monthly_price: "149.00", currency: "EUR", next_billing_at: new Date(Date.now() + 9 * 86_400_000).toISOString() },
      invoices: {
        issued: ind(invoices.length),
        paid: ind(invoices.filter((i) => i.status === "PAID").length),
        unpaid: ind(invoices.filter((i) => i.status === "PENDING").length),
        overdue: ind(0),
        recent: invoices.map((i) => ({ number: i.number, period: i.period, issued_on: i.issued_on, currency: i.currency, amount: i.amount, amount_after_discount: null, status: i.status, collected: i.collected, overdue: false })),
      },
    },
    series: {
      activity: { availability: "measured", granularity: "day", points: points.map(({ day, f }) => ({ period: day, sent: f.sent, received: f.received })) },
      performance: {
        availability: "measured",
        granularity: "day",
        points: points.map(({ day, f }) => ({
          period: day,
          prospects_contacted: ind(f.contacted),
          prospects_replied: ind(f.replied),
          reply_rate_percent: ind(Math.min(100, pct(f.replied, f.contacted))),
          conversions_declared: ind(0),
          conversions_confirmed: ind(0),
          conversion_rate_declared_percent: ind(0),
          conversion_rate_confirmed_percent: ind(0),
        })),
      },
      incidents: { availability: "measured", granularity: "day", points: points.map(({ day }) => ({ period: day, incidents: day === days[days.length - 3] ? 1 : 0 })) },
    },
  };
}

/* ── Resources ────────────────────────────────────────────────────────────────────────────── */

const LIMITS = { max_file_bytes: 10_000_000, max_per_product: 10, accepted_extensions: [".pdf", ".docx", ".txt", ".csv"] };

function addResource(productId: string, resource: Omit<ProductResource, "id" | "product_id" | "created_at">): ProductResource {
  const created: ProductResource = { ...resource, id: `r${extras.nextId++}`, product_id: productId, created_at: nowIso() };
  (extras.resources[productId] ??= []).push(created);
  return created;
}

/* ── Router ───────────────────────────────────────────────────────────────────────────────── */

type Handler = (m: RegExpMatchArray, body: Doc, q: Query, init?: RequestInit) => Reply;
const routes: Array<[string, RegExp, Handler]> = [];
const on = (method: string, path: string, handler: Handler) => routes.push([method, new RegExp(`^${path}$`), handler]);
const id1 = (m: RegExpMatchArray) => decodeURIComponent(m[1]!);

const agentState = () => ({ state: data.agent.state, active: data.agent.state === "running", ready: true, changed_at: data.agent.changed_at, reasons: [] });
const setAgent = (state: "running" | "paused") => {
  data.agent = { state, changed_at: nowIso() };
  return ok(agentState());
};

// Session
on("GET", "/auth/me", () => ok(demoProfile()));
on("POST", "/auth/logout", () => ok({}));
on("POST", "/profile/password", () => fail(409, "Le mot de passe du compte Demo ne peut pas être modifié."));
on("PATCH", "/profile", (_m, b) => {
  const p = extras.profile;
  if (typeof b.name === "string" && b.name.trim()) p.name = b.name.trim();
  if (typeof b.whatsapp_number === "string") p.whatsapp_number = b.whatsapp_number;
  return ok(demoProfile());
});

// Agent, readiness, settings
on("GET", "/agent", () => ok(agentState()));
on("POST", "/agent/start", () => setAgent("running"));
on("POST", "/agent/pause", () => setAgent("paused"));
on("GET", "/readiness", () =>
  ok({
    ready: true,
    checks: ["enabled_by_administrator", "loaded_by_platform", "model_assigned", "brain_assigned", "platform_connections", "source_connected", "channel_ready", "whatsapp_number"].map((id) => ({ id, ok: true, message: null })),
  }),
);
const settingsState = () => ({ version: extras.versions.settings, settings: clone(data.settings), updated_at: nowIso(), updated_by: "client" });
on("GET", "/settings", () => ok(settingsState()));
on("PUT", "/settings", (_m, b) => {
  data.settings = clone(obj(b.settings)) as DemoData["settings"];
  extras.versions.settings += 1;
  return ok(settingsState());
});
on("GET", "/followup-settings", () => ok({ version: extras.versions.followUp, ...clone(data.followUp) }));
on("PUT", "/followup-settings", (_m, b) => {
  data.followUp.max_followups = Number(b.max_followups ?? data.followUp.max_followups);
  data.followUp.gap_days = Number(b.gap_days ?? data.followUp.gap_days);
  const w = obj(b.window);
  data.followUp.window = b.window ? { start: String(w.start), end: String(w.end), timezone: String(w.timezone ?? data.followUp.timezone ?? "Africa/Tunis") } : null;
  extras.versions.followUp += 1;
  return ok({ version: extras.versions.followUp, ...clone(data.followUp) });
});

// Channels and access
on("GET", "/channel", () => ok(channelState()));
// What the client said about the accounts it wants to connect; everything the demo offers is configured.
const demoHints: Record<string, string | null> = { gmail: null, sheets: null, hubspot: null, telegram: null };
const connectionsInfo = () => ({
  hints: { ...demoHints },
  selectable: { channels: { email: true, whatsapp: true, telegram: true }, sources: { google_sheets: true, hubspot: true } },
});
on("GET", "/connections", () => ok(connectionsInfo()));
on("PUT", "/connections/hints/([^/]+)", (m, b) => {
  const value = String((b as { value?: unknown } | null)?.value ?? "").trim();
  if (!(id1(m) in demoHints) || value === "") return { status: 422, body: { detail: "invalid" } };
  demoHints[id1(m)] = value;
  return ok(connectionsInfo());
});
on("DELETE", "/connections/hints/([^/]+)", (m) => {
  if (id1(m) in demoHints) demoHints[id1(m)] = null;
  return ok(connectionsInfo());
});
on("PUT", "/channel", (_m, b) => saveChannel(b));
on("POST", "/channel/email-activation", () => ok(channelState()));
on("GET", "/channel/google", () => ok(gmailStatus()));
on("POST", "/channel/google/start", () => {
  extras.access.gmail = true;
  return ok({ authorize_url: "#/connections/channels" });
});
on("POST", "/channel/google/disconnect", () => {
  extras.access.gmail = false;
  return ok(gmailStatus());
});
on("GET", "/sheets-access", () => ok(sheetsAccess()));
on("POST", "/sheets-access/start", () => {
  extras.access.sheets = true;
  return ok({ authorize_url: "#/connections/sources" });
});
on("POST", "/sheets-access/disconnect", () => {
  extras.access.sheets = false;
  return ok(sheetsAccess());
});
on("GET", "/hubspot-access", () => ok(hubspotAccess()));
on("POST", "/hubspot-access/start", () => {
  extras.access.hubspot = true;
  return ok({ authorize_url: "#/connections/sources" });
});
on("POST", "/hubspot-access/disconnect", () => {
  extras.access.hubspot = false;
  return ok(hubspotAccess());
});
on("GET", "/channel/whatsapp", () => ok(whatsappStatus()));
// The real flow loads Meta's script in the browser; the demo never does: it reconnects in memory and says so.
on("POST", "/channel/whatsapp/start", () => {
  extras.access.whatsapp = true;
  return fail(409, "Mode démonstration : la connexion WhatsApp est simulée. Le compte de démonstration est reconnecté.");
});
on("POST", "/channel/whatsapp/complete", () => ok(whatsappStatus()));
on("POST", "/channel/whatsapp/disconnect", () => {
  extras.access.whatsapp = false;
  return ok(whatsappStatus());
});
on("GET", "/channel/telegram/account", () => ok(telegramAccount()));
on("POST", "/channel/telegram/account/start", () => {
  extras.access.telegram = "code";
  return ok(telegramAccount());
});
on("POST", "/channel/telegram/account/code", () => {
  extras.access.telegram = "connected";
  return ok(telegramAccount());
});
on("POST", "/channel/telegram/account/password", () => {
  extras.access.telegram = "connected";
  return ok(telegramAccount());
});
on("POST", "/channel/telegram/account/cancel", () => {
  extras.access.telegram = "not_connected";
  return ok(telegramAccount());
});
on("DELETE", "/channel/telegram/account", () => {
  extras.access.telegram = "not_connected";
  return ok({ disconnected: true, ended_at_telegram: false });
});
on("GET", "/channel/invitations", () => ok({ bot_username: "naga_formation_bot", bot_link: "#/connections/channels", waiting: 0, invitations: [] }));
on("GET", "/channel/mailbox", () =>
  ok({
    status: "in_progress",
    message: "Votre boîte e-mail est en cours de création par Revansa.",
    reason: null,
    address: null,
    login: null,
    credentials_shared: false,
    password_revealable: false,
    updated_at: nowIso(),
  }),
);
on("POST", "/channel/mailbox/reveal-password", () => ok({ password: "mot-de-passe-fictif", shown_for_seconds: 20 }));

// Sources and circuits
on("GET", "/source", () => ok(sourceState()));
on("PUT", "/source", (_m, b) => {
  if (typeof b.source === "string") extras.source.source = b.source;
  const s = obj(b.google_sheets);
  if (typeof s.spreadsheet === "string" && s.spreadsheet) extras.source.spreadsheet = s.spreadsheet;
  if (typeof s.prospects_tab === "string" && s.prospects_tab) extras.source.tab = s.prospects_tab;
  extras.versions.source += 1;
  return ok(sourceState());
});
on("POST", "/source/test", () =>
  ok({
    source: extras.source.source,
    ok: true,
    message: "Connexion réussie (test simulé en mode démonstration).",
    details: { onglet: extras.source.tab, lignes: String(data.prospects.length) },
    checked_at: nowIso(),
    lanes: data.lanes.map((l) => ({ lane: l.id, ok: true, message: "Onglet lu (simulation)." })),
  }),
);
on("GET", "/lanes", () => ok(lanesState()));
on("POST", "/lanes", (_m, b) => createLane(b));
on("PUT", "/lanes/([^/]+)", (m, b) => updateLane(id1(m), b));
on("DELETE", "/lanes/([^/]+)", (m) => deleteLane(id1(m)));
on("GET", "/lanes/([^/]+)/sync", (m) => laneSync(id1(m)));
on("PUT", "/lanes/([^/]+)/sync", (m, b) => {
  const sheets = obj(b.google_sheets);
  extras.laneSync[id1(m)] = { columns: { ...(obj(sheets.columns) as Record<string, string>) } };
  return laneSync(id1(m));
});

// What the agent is doing: a short script that repeats, so the Live screen always has something to show.
const LIVE_CYCLE_MS = 40000;
const LIVE_SCRIPT: Array<{ kind: string; from: number; to: number; who: 0 | 1 | null }> = [
  { kind: "reading_prospects", from: 0, to: 4, who: null },
  { kind: "planning", from: 4, to: 6, who: null },
  { kind: "checking", from: 6, to: 7, who: 0 },
  { kind: "reading_history", from: 7, to: 9, who: 0 },
  { kind: "thinking", from: 9, to: 17, who: 0 },
  { kind: "sending", from: 17, to: 19, who: 0 },
  { kind: "checking_inbox", from: 19, to: 23, who: null },
  { kind: "receiving", from: 23, to: 25, who: 1 },
  { kind: "checking", from: 25, to: 26, who: 1 },
  { kind: "reading_history", from: 26, to: 28, who: 1 },
  { kind: "answering", from: 28, to: 38, who: 1 },
  { kind: "sending", from: 38, to: 40, who: 1 },
];
function live(): Reply {
  const now = Date.now();
  const people = data.prospects.map((p) => p.row);
  const steps: LiveStep[] = [];
  for (let cycle = Math.floor(now / LIVE_CYCLE_MS); cycle > Math.floor(now / LIVE_CYCLE_MS) - 4; cycle--) {
    LIVE_SCRIPT.forEach((s, i) => {
      const started = cycle * LIVE_CYCLE_MS + s.from * 1000;
      if (started > now) return;
      const ends = cycle * LIVE_CYCLE_MS + s.to * 1000;
      const who = s.who === null || people.length === 0 ? null : people[(cycle * 2 + s.who) % people.length]!;
      steps.push({
        id: cycle * 100 + i,
        kind: s.kind,
        state: ends > now ? "running" : "done",
        started_at: new Date(started).toISOString(),
        ended_at: ends > now ? null : new Date(ends).toISOString(),
        prospect_id: who?.id ?? null,
        name: who?.display_name ?? null,
        detail: null,
      });
    });
  }
  steps.sort((a, b) => b.id - a.id);
  return ok({ at: new Date(now).toISOString(), today: { sent: 5 + (Math.floor(now / LIVE_CYCLE_MS) % 4), limit: 20 }, now: steps.filter((x) => x.state === "running"), recent: steps });
}
on("GET", "/live", () => live());
on("GET", "/learning", () => ok(clone(learning)));
on("POST", "/learning/([^/]+)/decision", (m, b) => decideLearning(id1(m), obj(b)));

// Prospects and conversations
on("GET", "/summary", () => ok(summary()));
on("GET", "/prospects", (_m, _b, q) => listProspects(q));
on("GET", "/conversations", (_m, _b, q) => listConversations(q));
on("GET", "/conversations/([^/]+)", (m, _b, q) => conversation(id1(m), q));
on("POST", "/conversations/([^/]+)/take-over", (m, b) => takeOver(id1(m), b));
on("POST", "/conversations/([^/]+)/release", (m) => release(id1(m)));
on("POST", "/conversations/([^/]+)/messages", (m, b) => sendMessage(id1(m), b));

// To review
on("GET", "/approvals", () => ok({ proposals: clone(data.proposals) }));
on("POST", "/approvals/([^/]+)/(approve|reject)", (m) => decideProposal(id1(m), m[2]!));
on("GET", "/payments", () => ok({ cases: clone(data.payments) }));
on("POST", "/payments/([^/]+)/confirm", (m, b) => confirmPayment(id1(m), b));

// Reports
on("GET", "/reporting", () => ok(reportingState()));
on("PUT", "/reporting/settings", (_m, b) => {
  data.reportSettings = { ...data.reportSettings, ...(b as object), updated_at: nowIso() } as DemoData["reportSettings"];
  return ok(clone(data.reportSettings));
});
on("GET", "/reports", (_m, _b, q) => ok({ reports: reportDays().slice(0, Number(q.get("limit")) || 30).map((d) => reportSummary(reportDetail(d))) }));
on("POST", "/reports/generate", () => {
  const day = dayOf(nowIso());
  const base = reportDetail(day);
  extras.extraReports[day] = { ...base, status: "pending", sent_at: null, attempts: 0, next_attempt_at: new Date(Date.now() + 3_600_000).toISOString() };
  return ok(clone(extras.extraReports[day]));
});
on("GET", "/reports/([^/]+)", (m) => (/^\d{4}-\d{2}-\d{2}$/.test(id1(m)) ? ok(reportDetail(id1(m))) : fail(404, "Rapport introuvable.")));
on("POST", "/reports/([^/]+)/resend", (m) => {
  const day = id1(m);
  extras.extraReports[day] = { ...reportDetail(day), status: "sent", sent_at: nowIso(), attempts: 2 };
  return ok(clone(extras.extraReports[day]));
});

// Products' resources
on("GET", "/products/([^/]+)/resources", (m) => ok({ resources: clone(extras.resources[id1(m)] ?? []), limits: LIMITS }));
on("POST", "/products/([^/]+)/resources/link", (m, b) =>
  ok(addResource(id1(m), { kind: "link", category: (b.category as ProductResource["category"]) ?? "other", title: String(b.title ?? ""), url: String(b.url ?? ""), filename: null, mime_type: null, size_bytes: null, status: "ready", status_detail: null, text_chars: 1800 }), 201),
);
on("POST", "/products/([^/]+)/resources/file", (m, _b, _q, init) => {
  const headers = new Headers(init?.headers);
  const file = init?.body instanceof File ? init.body : null;
  const name = decodeURIComponent(headers.get("X-File-Name") ?? file?.name ?? "document");
  const title = decodeURIComponent(headers.get("X-Resource-Title") ?? name);
  return ok(addResource(id1(m), { kind: "file", category: "document", title, url: null, filename: name, mime_type: file?.type || "application/octet-stream", size_bytes: file?.size ?? 0, status: "stored_only", status_detail: "demo", text_chars: 0 }), 201);
});
on("DELETE", "/products/([^/]+)/resources/([^/]+)", (m) => {
  const list = extras.resources[id1(m)] ?? [];
  extras.resources[id1(m)] = list.filter((r) => r.id !== decodeURIComponent(m[2]!));
  return ok({ deleted: decodeURIComponent(m[2]!) });
});
on("POST", "/products/([^/]+)/resources/([^/]+)/retry", (m) => {
  const found = (extras.resources[id1(m)] ?? []).find((r) => r.id === decodeURIComponent(m[2]!));
  if (!found) return fail(404, "Ressource introuvable.");
  found.status = "ready";
  found.status_detail = null;
  found.text_chars = 1200;
  return ok(clone(found));
});

// Billing and dashboard
on("GET", "/invoices", () => ok({ invoices: clone(data.invoices).map(({ lines: _lines, ...inv }) => (void _lines, inv)) }));
on("GET", "/invoices/(\\d+)", (m) => {
  const found = data.invoices.find((i) => i.invoice_id === Number(m[1]));
  return found ? ok(clone(found)) : fail(404, "Facture introuvable.");
});
on("GET", "/subscription", () => {
  const pending = data.invoices.filter((i) => i.status === "PENDING").length;
  const paid = data.invoices.find((i) => i.status === "PAID");
  return ok({
    status: "active",
    plan: { plan_id: "plan-pro", name: "Plan Pro", monthly_price: "149.00", currency: "EUR", billing_period: "monthly", included_credits: 5000, daily_quota: 100, since: data.invoices[data.invoices.length - 1]!.issued_on },
    next_billing_at: new Date(Date.now() + 9 * 86_400_000).toISOString(),
    unpaid_invoices: pending,
    overdue_invoices: 0,
    last_payment: paid ? { invoice_number: paid.number, period: paid.period, status: paid.status, collected: true, settled_at: paid.issued_on } : null,
  });
});
on("GET", "/account/access", () => ok({ tier: "paid" }));
on("GET", "/dashboard", () => ok(dashboard()));
on("GET", "/usage", () => {
  const today = new Date();
  const days = Array.from({ length: today.getUTCDate() }, (_, i) => {
    const day = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), i + 1)).toISOString().slice(0, 10);
    return { day, actions: (i * 7) % 23, credits: 60 + ((i * 37) % 90) };
  });
  const credits = days.reduce((sum, row) => sum + (row.credits ?? 0), 0);
  const actions = days.reduce((sum, row) => sum + row.actions, 0);
  const last = days[days.length - 1]!;
  const daysInMonth = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 0)).getUTCDate();
  return ok({
    plan_name: "Plan Pro", day_utc: last.day, month: last.day.slice(0, 7),
    daily_limit: 20, daily_used: last.actions, daily_remaining: 20 - last.actions, daily_percent: Math.round((last.actions * 100) / 20),
    month_limit: 20 * daysInMonth, month_actions_used: actions, month_percent: Math.round((actions * 100) / (20 * daysInMonth)),
    included_credits: 5000, credits_used: credits, credits_today: last.credits, credits_remaining: 5000 - credits,
    credits_percent: Math.round((credits * 100) / 5000), credits_basis: "measured", calls: days.length * 14, calls_without_figures: 0,
    state: "ok", message: "", action: "", days,
  });
});

// Notifications
const unread = () => data.notifications.filter((n) => !n.read_at).length;
const notificationsPage = () => ({ items: clone(data.notifications), total: data.notifications.length, unread_count: unread() });
on("GET", "/notifications", () => ok(notificationsPage()));
on("POST", "/notifications/read-all", () => {
  data.notifications.forEach((n) => (n.read_at ??= nowIso()));
  return ok({});
});
on("POST", "/notifications/(\\d+)/read", (m) => {
  const n = data.notifications.find((x) => x.id === Number(m[1]));
  if (n) n.read_at ??= nowIso();
  return ok({});
});

function answer(reply: Reply): Response {
  return new Response(reply.status === 204 ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
}

const demoTests: { id: string; name: string; contact: string; channel: string; circuit: string | null; at: string }[] = [];
on("POST", "/agent-test", (_m, b) => {
  const contact = String(b.contact ?? "").trim();
  if (contact.length < 3) return fail(422, { code: "invalid_contact", message: "Ce contact n'est pas valide pour ce canal." });
  if (demoTests.some((t) => t.contact === contact)) return fail(409, { code: "contact_in_use", message: "Ce contact est déjà l'un de vos prospects." });
  const lane = data.lanes.find((l) => l.id === b.circuit_id) ?? data.lanes[0];
  const id = `inbound-test-${contact.toLowerCase()}`;
  demoTests.push({ id, name: String(b.name ?? "").trim() || contact, contact, channel: lane?.channel.channel ?? "email", circuit: lane?.id ?? null, at: nowIso() });
  return ok({ prospect_id: id, state: "sent", detail: null }, 201);
});
const demoTestOf = (t: (typeof demoTests)[number]) => ({
  prospect_id: t.id,
  name: t.name,
  contact: t.contact,
  channel: t.channel,
  circuit_id: t.circuit,
  status: "active",
  messages: 1,
  replies: 0,
  last_message_at: t.at,
});
on("GET", "/agent-test", () => ok({ tests: [...demoTests].reverse().map(demoTestOf) }));
on("POST", "/agent-test/([^/]+)/rehearse", (_m, b) => {
  const said = String(b.text ?? "").toLowerCase();
  const price = /cher|prix|tarif|expensive|price/.test(said);
  return ok({ decision: "send_message", reply: price ? "Je comprends. Qu’est-ce qui vous inquiète le plus : le prix lui-même, ou d’être sûr que cela vous rapportera ?" : "Avec plaisir. Pouvez-vous me dire ce que vous cherchez en priorité ?" });
});
on("GET", "/agent-test/([^/]+)", (m) => {
  const found = demoTests.find((t) => t.id === id1(m));
  if (!found) return fail(404, "no such test");
  return ok({
    test: demoTestOf(found),
    messages: [{ direction: "outbound", text: `Bonjour ${found.name}, je suis l'assistant de votre entreprise (message d'essai du mode démonstration).`, sent_at: found.at }],
  });
});

function parseBody(init?: RequestInit): Doc {
  if (typeof init?.body !== "string") return {};
  try {
    return obj(JSON.parse(init.body));
  } catch {
    return {};
  }
}

/** Answers a request in memory. The `await` keeps it asynchronous like a real call. */
export async function demoFetch(input: string, init?: RequestInit): Promise<Response> {
  await Promise.resolve();
  const url = new URL(input, "http://demo.invalid");
  const method = (init?.method ?? "GET").toUpperCase();
  if (!url.pathname.startsWith(BASE)) return answer(fail(404, "Hors du mode démonstration."));
  const path = url.pathname.slice(BASE.length);
  for (const [m, pattern, handler] of routes) {
    if (m !== method) continue;
    const match = path.match(pattern);
    if (match) {
      try {
        return answer(handler(match, parseBody(init), url.searchParams, init));
      } catch {
        return answer(fail(500, "Erreur du mode démonstration."));
      }
    }
  }
  return answer(fail(404, "Cette action n'existe pas dans le mode démonstration."));
}

export type { ProspectRow };
