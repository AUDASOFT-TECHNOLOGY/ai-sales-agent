/** SAMPLE DATA ONLY. Every name, number, price and sentence in this file is invented so the
 *  interface can be judged visually. Nothing here is a real client fact and nothing is connected.
 *  In the real product these values come from the Admin API (docs/naga-client-ui-design-system.md C5). */
import type { MessageKey } from "../i18n";
import { deriveReadiness } from "./offers";
import type {
  Conversation,
  DashboardPeriodStat,
  FunnelStep,
  Kpi,
  NeedItem,
  Offer,
  ProductPerformance,
  Prospect,
  ReportDay,
  ResolvedNeed,
  Stage,
  StageSlice,
  ThreadEvent,
} from "./types";

export const TENANT = { name: "Naga", timezone: "Africa/Tunis", timezoneLabel: "Tunis time" };
export const USER = { name: "Sami", initials: "SK", role: "Owner" };

/** The primary flow (doc: pipeline review): the agent picks who to contact from the sheet, so the
 *  pipeline starts at "Contacted" rather than an "Unsorted" backlog. Registration and payment were
 *  merged into one stage because, for this business, a prospect commits and pays in the same move. */
export const STAGES: Stage[] = [
  { id: "contacted", label: "funnel.contacted" },
  { id: "discussing", label: "stage.discussing" },
  { id: "interested", label: "stage.interested" },
  { id: "registration_payment", label: "funnel.converted" },
  { id: "won", label: "stage.paid", milestone: "won" },
];

export const PATH_STEPS: MessageKey[] = ["funnel.contacted", "stage.discussing", "stage.interested", "funnel.converted", "stage.paid"];

export const PROSPECTS: Prospect[] = [
  {
    id: "amira",
    name: "Amira Ben Salah",
    initials: "AB",
    place: "Tunis",
    status: "paused",
    stage: "interested",
    attention: "waiting",
    last: "prospect.amira.last",
    read: "prospect.amira.read",
    next: "prospect.next.waitingForYou",
    interest: "Foundation",
    recommendation: "prospect.amira.recommendation",
    step: 1,
    facts: [
      { key: "Interest", value: "Foundation", status: "discovered", source: "conversation", excerpt: "Foundation is made for exactly that.", when: "Yesterday 16:45" },
      { key: "Goal", value: "Start an online business", status: "confirmed", source: "prospect", excerpt: "I want to start something of my own, online.", when: "Yesterday 16:42" },
      { key: "Country", value: "Tunisia", status: "confirmed", source: "client_data", excerpt: "From your lead sheet, column Country." },
      { key: "Experience", value: "Beginner", status: "discovered", source: "conversation", excerpt: "I have never sold anything before.", when: "Yesterday 16:44" },
      { key: "Budget", value: "Under 1 000 TND", status: "hypothesis", source: "brain_inference" },
    ],
  },
  {
    id: "leila",
    name: "Leila Mansouri",
    initials: "LM",
    place: "Sfax",
    status: "paused",
    stage: "registration_payment",
    attention: "waiting",
    last: "prospect.leila.last",
    read: "prospect.leila.read",
    next: "prospect.next.waitingForYou",
    interest: "Growth",
    recommendation: "prospect.leila.recommendation",
    step: 3,
    facts: [
      { key: "Interest", value: "Growth", status: "confirmed", source: "prospect", excerpt: "I already sell, I want more customers." },
      { key: "Goal", value: "Grow an existing shop", status: "confirmed", source: "prospect", excerpt: "I already sell, I want more customers." },
      { key: "Country", value: "Tunisia", status: "confirmed", source: "client_data" },
      { key: "Payment", value: "Bank transfer", status: "discovered", source: "prospect", excerpt: "I made the transfer this morning." },
    ],
  },
  {
    id: "karim",
    name: "Karim Trabelsi",
    initials: "KT",
    place: "Sousse",
    status: "active",
    stage: "discussing",
    attention: "working",
    last: "prospect.karim.last",
    read: "prospect.karim.read",
    next: "prospect.karim.next",
    interest: "Foundation",
    recommendation: "prospect.karim.recommendation",
    step: 0,
    facts: [
      { key: "Interest", value: "Foundation", status: "discovered", source: "conversation" },
      { key: "Goal", value: "Change careers", status: "discovered", source: "prospect", excerpt: "I'm looking for a different path." },
      { key: "Country", value: "Tunisia", status: "confirmed", source: "client_data" },
    ],
  },
  {
    id: "youssef",
    name: "Youssef Gharbi",
    initials: "YG",
    place: "Bizerte",
    status: "active",
    stage: "registration_payment",
    attention: "working",
    last: "prospect.youssef.last",
    read: "prospect.youssef.read",
    next: "prospect.youssef.next",
    interest: "Growth",
    recommendation: "prospect.youssef.recommendation",
    step: 2,
    facts: [
      { key: "Interest", value: "Growth", status: "confirmed", source: "prospect" },
      { key: "Goal", value: "Freelance income", status: "confirmed", source: "prospect" },
      { key: "Country", value: "Tunisia", status: "confirmed", source: "client_data" },
      { key: "Language", value: "French", status: "confirmed", source: "prospect" },
    ],
  },
  {
    id: "nour",
    name: "Nour Jebali",
    initials: "NJ",
    place: "Tunis",
    status: "active",
    stage: "discussing",
    attention: "working",
    last: "prospect.nour.last",
    read: "prospect.nour.read",
    next: "prospect.nour.next",
    interest: "Foundation",
    recommendation: "prospect.nour.recommendation",
    step: 0,
    facts: [
      { key: "Interest", value: "Foundation", status: "hypothesis", source: "brain_inference" },
      { key: "Country", value: "Tunisia", status: "confirmed", source: "client_data" },
    ],
  },
  {
    id: "rami",
    name: "Rami Chaouch",
    initials: "RC",
    place: "Gabès",
    status: "new",
    stage: "contacted",
    attention: "none",
    last: "prospect.notContacted",
    read: "prospect.newInSheet",
    next: "prospect.rami.next",
    interest: "Not known yet",
    recommendation: "prospect.rami.recommendation",
    step: 0,
    facts: [{ key: "Country", value: "Tunisia", status: "confirmed", source: "client_data" }],
  },
  {
    id: "salma",
    name: "Salma Ferchichi",
    initials: "SF",
    place: "Nabeul",
    status: "new",
    stage: "contacted",
    attention: "none",
    last: "prospect.notContacted",
    read: "prospect.newInSheet",
    next: "prospect.salma.next",
    interest: "Not known yet",
    recommendation: "prospect.salma.recommendation",
    step: 0,
    facts: [],
  },
  {
    id: "hedi",
    name: "Hedi Bouzid",
    initials: "HB",
    place: "Tunis",
    status: "active",
    stage: "interested",
    attention: "working",
    last: "prospect.hedi.last",
    read: "prospect.hedi.read",
    next: "prospect.hedi.next",
    interest: "Growth",
    recommendation: "prospect.hedi.recommendation",
    step: 1,
    facts: [
      { key: "Interest", value: "Growth", status: "discovered", source: "conversation" },
      { key: "Goal", value: "Scale a small team", status: "discovered", source: "conversation" },
      { key: "Team size", value: "Around 5 people", status: "hypothesis", source: "brain_inference" },
    ],
  },
  {
    id: "ines",
    name: "Ines Khemiri",
    initials: "IK",
    place: "Monastir",
    status: "active",
    stage: "registration_payment",
    attention: "working",
    last: "prospect.ines.last",
    read: "prospect.ines.read",
    next: "prospect.ines.next",
    interest: "Foundation",
    recommendation: "prospect.ines.recommendation",
    step: 3,
    facts: [
      { key: "Interest", value: "Foundation", status: "confirmed", source: "prospect" },
      { key: "Goal", value: "Start freelancing", status: "confirmed", source: "prospect" },
      { key: "Payment", value: "Online payment link", status: "discovered", source: "prospect" },
    ],
  },
  {
    id: "walid",
    name: "Walid Ayari",
    initials: "WA",
    place: "Kairouan",
    status: "closed",
    stage: "won",
    attention: "won",
    last: "time.yesterday",
    read: "prospect.walid.read",
    next: "prospect.next.nothingPlanned",
    interest: "Foundation",
    step: 4,
    facts: [
      { key: "Interest", value: "Foundation", status: "confirmed", source: "human" },
      { key: "Payment", value: "Confirmed", status: "confirmed", source: "human", excerpt: "Confirmed by Sami K." },
    ],
  },
  {
    id: "mariem",
    name: "Mariem Sassi",
    initials: "MS",
    place: "Tunis",
    status: "closed",
    stage: "won",
    attention: "won",
    last: "time.daysAgo2",
    read: "prospect.mariem.read",
    next: "prospect.next.nothingPlanned",
    interest: "Growth",
    step: 4,
    facts: [
      { key: "Interest", value: "Growth", status: "confirmed", source: "human" },
      { key: "Payment", value: "Confirmed", status: "confirmed", source: "human" },
    ],
  },
  {
    id: "anis",
    name: "Anis Dridi",
    initials: "AD",
    place: "Sfax",
    status: "lost",
    lostReason: "declined",
    stage: "discussing",
    attention: "lost",
    last: "time.daysAgo3",
    read: "prospect.anis.read",
    next: "prospect.next.noneEver",
    interest: "Foundation",
    step: 0,
    facts: [
      { key: "Interest", value: "Foundation", status: "hypothesis", source: "brain_inference" },
      { key: "Asked to stop", value: "Yes", status: "confirmed", source: "prospect", excerpt: "Please stop messaging me." },
    ],
  },
  {
    id: "hazem",
    name: "Hazem Boukadi",
    initials: "HZ",
    place: "Gabes",
    status: "lost",
    lostReason: "no_response",
    stage: "interested",
    attention: "lost",
    last: "time.daysAgo9",
    read: "prospect.hazem.read",
    next: "prospect.next.noneEver",
    interest: "Growth",
    step: 1,
    facts: [{ key: "Interest", value: "Growth", status: "discovered", source: "conversation" }],
  },
];

export const NEEDS: NeedItem[] = [
  {
    id: "n1",
    kind: "draft",
    prospectId: "karim",
    reason: "need.n1.reason",
    waiting: "time.waiting12min",
    preview:
      "Happy to explain. Foundation can be paid by bank transfer or online, and I'll send the exact details once you choose.",
    factors: ["need.n1.factor1", "need.n1.factor2", "need.n1.factor3", "need.n1.factor4"],
  },
  {
    id: "n2",
    kind: "payment",
    prospectId: "leila",
    reason: "need.n2.reason",
    waiting: "time.waiting1h",
    factors: ["need.n2.factor1", "need.n2.factor2", "need.n2.factor3", "need.n2.factor4"],
  },
  {
    id: "n3",
    kind: "handoff",
    prospectId: "amira",
    reason: "need.n3.reason",
    waiting: "time.waiting12min",
    factors: ["need.n3.factor1", "need.n3.factor2", "need.n3.factor3"],
  },
];

/** Needs already settled before this session started, so the notification history isn't empty on
 *  first load. New entries are prepended live as the reducer resolves a need (see mock/store.tsx). */
export const NEED_HISTORY: ResolvedNeed[] = [
  {
    id: "n-h1",
    kind: "payment",
    prospectId: "walid",
    reason: "need.nh1.reason",
    waiting: "time.waiting40min",
    resolution: "confirmed",
    resolvedAt: "time.yesterday",
  },
  {
    id: "n-h2",
    kind: "draft",
    prospectId: "youssef",
    reason: "need.nh2.reason",
    waiting: "time.waiting20min",
    preview: "Quick reminder: your registration for Growth is almost done, just the last step left.",
    resolution: "approved",
    resolvedAt: "time.yesterday",
  },
  {
    id: "n-h3",
    kind: "handoff",
    prospectId: "hedi",
    reason: "need.nh3.reason",
    waiting: "time.waiting15min",
    resolution: "handled",
    resolvedAt: "time.daysAgo2",
  },
  {
    id: "n-h4",
    kind: "payment",
    prospectId: "mariem",
    reason: "need.nh4.reason",
    waiting: "time.waiting1h",
    resolution: "confirmed",
    resolvedAt: "time.daysAgo2",
  },
  {
    id: "n-h5",
    kind: "draft",
    prospectId: "anis",
    reason: "need.nh5.reason",
    waiting: "time.waiting2h",
    preview: "Understood, I won't reach out again. Wishing you the best.",
    resolution: "declined",
    resolvedAt: "time.daysAgo3",
  },
];

export const THREAD: ThreadEvent[] = [
  { id: "t1", time: "09:02", kind: "sent", who: "Amira B.", voice: "thread.t1.voice", detail: "thread.t1.detail" },
  { id: "t2", time: "09:14", kind: "received", who: "Amira B.", voice: "thread.t2.voice", detail: "thread.t2.detail" },
  { id: "t3", time: "09:14", kind: "fact", who: "Amira B.", voice: "thread.t3.voice", detail: "thread.t3.detail" },
  { id: "t4", time: "09:31", kind: "sent", who: "Youssef G.", voice: "thread.t4.voice", detail: "thread.t4.detail" },
  { id: "t5", time: "09:48", kind: "decision", who: "Hedi B.", voice: "thread.t5.voice", detail: "thread.t5.detail" },
  { id: "t6", time: "10:05", kind: "payment", who: "Leila M.", voice: "thread.t6.voice", detail: "thread.t6.detail", needsYou: true },
  { id: "t7", time: "10:20", kind: "handoff", who: "Amira B.", voice: "thread.t7.voice", detail: "thread.t7.detail", needsYou: true },
  { id: "t8", time: "11:00", kind: "scheduled", who: "Karim T.", voice: "thread.t8.voice", detail: "thread.planned", future: true },
  { id: "t9", time: "11:30", kind: "scheduled", who: "Rami C.", voice: "thread.t9.voice", detail: "thread.planned", future: true },
  { id: "t10", time: "14:00", kind: "human", who: "Youssef G.", voice: "thread.t10.voice", detail: "thread.planned", future: true },
];

/** What today's 14 commercial actions actually are (section "What REVANSA is doing today"): most of
 *  the daily quota is follow-ups and reminders on prospects already in conversation, not only new
 *  contacts. Hand-authored sample data, exactly like the KPI numbers below. */
export const ACTIONS_TODAY = {
  total: 14,
  newProspects: 4,
  followUps: 6,
  reminders: 3,
  registrationChecks: 1,
};

/** The one qualitative line of today's CEO report (section 11): authored the same way KPI
 *  `caption`s already are elsewhere in this file, never a fabricated statistic. */
export const CEO_REPORT_HIGHLIGHT: MessageKey = "report.today.highlight";

const s = (n: number, base: number, amp: number, phase = 0) =>
  Array.from({ length: n }, (_, i) => Math.round(base + Math.sin(i * 0.9 + phase) * amp + i * (amp / 6)));

/** Scales a shape of relative weights to whole numbers that sum to exactly `total` (largest-remainder
 *  rounding), so a trend chart's bars always add up to the headline figure shown above it. */
function distribute(total: number, shape: number[]): number[] {
  const sum = shape.reduce((a, b) => a + b, 0) || 1;
  const raw = shape.map((w) => (w / sum) * total);
  const result = raw.map(Math.floor);
  const remainder = total - result.reduce((a, b) => a + b, 0);
  const order = raw.map((v, i) => [v - Math.floor(v), i] as const).sort((a, b) => b[0] - a[0]);
  for (let k = 0; k < remainder; k++) result[order[k]![1]] += 1;
  return result;
}

export const KPIS: Record<"today" | "7d" | "30d", Kpi[]> = {
  today: [
    { id: "treated", value: 14, series: s(9, 6, 3), ghost: s(9, 5, 3, 1), delta: 8, direction: "good" },
    { id: "replies", value: 6, series: s(9, 3, 2, 1), ghost: s(9, 3, 2), delta: 20, direction: "good", caption: "Most replies come within the first hour." },
    { id: "appointments", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
    { id: "conversions", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
  ],
  "7d": [
    { id: "treated", value: 214, series: s(14, 24, 8), ghost: s(14, 20, 7, 2), delta: 12, direction: "good" },
    { id: "replies", value: 96, series: s(14, 10, 5, 2), ghost: s(14, 9, 4), delta: 9, direction: "good", caption: "Most replies come within the first hour." },
    { id: "appointments", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
    { id: "conversions", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
  ],
  "30d": [
    { id: "treated", value: 812, series: s(20, 34, 10, 1), ghost: s(20, 30, 9), delta: 5, direction: "good" },
    { id: "replies", value: 371, series: s(20, 14, 6), ghost: s(20, 13, 6, 3), delta: -3, direction: "neutral", caption: "Replies dip on Fridays." },
    { id: "appointments", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
    { id: "conversions", value: null, series: [], ghost: [], delta: null, direction: "neutral" },
  ],
};

export const FUNNEL: Record<"today" | "7d" | "30d", FunnelStep[]> = {
  today: [
    { id: "contacted", count: 14, rate: null },
    { id: "replied", count: 6, rate: "43%" },
    { id: "qualified", count: 3, rate: "50%" },
    { id: "appointment", count: null, rate: null },
    { id: "converted", count: null, rate: null },
  ],
  "7d": [
    { id: "contacted", count: 214, rate: null },
    { id: "replied", count: 96, rate: "45%" },
    { id: "qualified", count: 41, rate: "43%" },
    { id: "appointment", count: null, rate: null },
    { id: "converted", count: null, rate: null },
  ],
  "30d": [
    { id: "contacted", count: 812, rate: null },
    { id: "replied", count: 371, rate: "46%" },
    { id: "qualified", count: 158, rate: "43%" },
    { id: "appointment", count: null, rate: null },
    { id: "converted", count: null, rate: null },
  ],
};

const seedOffer = (o: Omit<Offer, "completeness" | "missing">): Offer => ({ ...o, ...deriveReadiness(o as Offer) });

export const OFFERS: Offer[] = [
  seedOffer({
    id: "foundation",
    name: "Foundation",
    summary: "The first step for someone starting from zero.",
    status: "active",
    price: "1 500 TND",
    markets: ["Tunisia"],
    assets: [
      { id: "foundation-doc-1", kind: "doc", label: "Foundation one-pager (PDF)", url: "https://example.com/foundation.pdf" },
      { id: "foundation-test-1", kind: "testimonial", label: "Amira's story", url: "https://example.com/testimonials/amira" },
    ],
  }),
  seedOffer({
    id: "growth",
    name: "Growth",
    summary: "For people who already sell and want more customers.",
    status: "active",
    price: "On request",
    markets: ["Tunisia", "France"],
    assets: [],
  }),
  seedOffer({
    id: "masterclass",
    name: "Masterclass",
    summary: "A live session, opening later this year.",
    status: "coming_soon",
    price: "Not set",
    markets: [],
    assets: [],
  }),
];

export const CONVERSATIONS: Conversation[] = [
  {
    id: "amira",
    prospectId: "amira",
    needsYou: true,
    preview: "Is there any discount for two people?",
    payment: "none",
    notes: {
      learned: PROSPECTS[0]!.facts.slice(0, 3),
      decided: ["convo.amira.decided1", "convo.amira.decided2"],
      handoff: "convo.amira.handoff",
    },
    reasoning: {
      factors: ["convo.amira.factor1", "convo.amira.factor2", "convo.amira.factor3"],
      confidence: "medium",
    },
    messages: [
      { id: "a1", author: "agent", time: "Yesterday 16:40", parts: [{ text: "Hello Amira, I'm the assistant of Naga. You asked about starting online. What are you hoping to build?" }] },
      { id: "a2", author: "prospect", time: "Yesterday 16:42", parts: [{ text: "I want to start something of my own, online. I have never sold anything before." }] },
      { id: "a3", author: "agent", time: "Yesterday 16:45", parts: [{ text: "That's a great place to start. Foundation is made for exactly that. It costs " }, { text: "1 500 TND", verbatim: true }, { text: ". Would you like to know how it works?" }] },
      { id: "a4", author: "prospect", time: "Today 10:18", parts: [{ text: "Yes please. And is there any discount if two of us join together?" }] },
      { id: "a5", author: "system", time: "10:20", parts: [{ text: "Handed to a person" }] },
    ],
  },
  {
    id: "leila",
    prospectId: "leila",
    needsYou: true,
    preview: "I made the transfer this morning.",
    payment: "proof_received",
    notes: {
      learned: PROSPECTS[1]!.facts,
      decided: ["convo.leila.decided1"],
    },
    reasoning: {
      factors: ["convo.leila.factor1", "convo.leila.factor2", "convo.leila.factor3", "convo.leila.factor4"],
      confidence: "high",
    },
    messages: [
      { id: "l1", author: "agent", time: "Yesterday 11:02", parts: [{ text: "Here are the details for a bank transfer:" }] },
      { id: "l2", author: "agent", time: "Yesterday 11:02", parts: [{ text: "IBAN " }, { text: "TN59 0000 0000 0000 0000 0000", verbatim: true }, { text: ", reference " }, { text: "GROWTH-LM", verbatim: true }] },
      { id: "l3", author: "prospect", time: "Today 09:40", parts: [{ text: "I made the transfer this morning." }] },
      { id: "l4", author: "prospect", time: "Today 09:41", parts: [{ text: "Here is the receipt." }], media: { name: "transfer-receipt.jpg", size: "412 KB", possibleProof: true } },
      { id: "l5", author: "system", time: "09:42", parts: [{ text: "Payment claimed, possible proof received" }] },
    ],
  },
  {
    id: "karim",
    prospectId: "karim",
    needsYou: true,
    preview: "How can I pay for Foundation?",
    payment: "none",
    draft: [{ text: "Happy to explain. Foundation can be paid by bank transfer or online, and I'll send the exact details once you choose." }],
    notes: {
      learned: PROSPECTS[2]!.facts,
      decided: ["convo.karim.decided1"],
    },
    reasoning: {
      factors: ["convo.karim.factor1", "convo.karim.factor2", "convo.karim.factor3", "convo.karim.factor4"],
      confidence: "high",
    },
    messages: [
      { id: "k1", author: "agent", time: "Yesterday 15:10", parts: [{ text: "Hi Karim, thanks for your interest. What made you look at a change of path?" }] },
      { id: "k2", author: "prospect", time: "Yesterday 15:31", parts: [{ text: "I'm looking for a different path, something with more freedom." }] },
      { id: "k3", author: "human", who: "Sami K.", time: "Today 08:50", parts: [{ text: "Hi Karim, Sami here from the team. Feel free to ask me anything directly." }] },
      { id: "k4", author: "prospect", time: "Today 10:02", parts: [{ text: "How can I pay for Foundation?" }] },
    ],
  },
  {
    id: "youssef",
    prospectId: "youssef",
    needsYou: false,
    preview: "I'll finish it after lunch.",
    payment: "none",
    notes: {
      learned: PROSPECTS[3]!.facts,
      decided: ["convo.youssef.decided1"],
    },
    reasoning: {
      factors: ["convo.youssef.factor1", "convo.youssef.factor2", "convo.youssef.factor3"],
      confidence: "high",
    },
    messages: [
      { id: "y1", author: "agent", time: "09:31", parts: [{ text: "Bonjour Youssef, il ne manque que deux champs dans le formulaire d'inscription." }], lang: "fr" },
      { id: "y2", author: "prospect", time: "09:40", parts: [{ text: "Je termine après le déjeuner." }], lang: "fr" },
    ],
  },
];

/** Since REVANSA went live with this client: the Dashboard's headline numbers. Hand-authored sample
 *  data, exactly like ACTIONS_TODAY above — this prototype only has 12 sample prospects to derive
 *  live figures from, so a six-month cumulative total has to be authored, not computed. */
export const SINCE_START = {
  startDate: "2026-03-03",
  months: 6,
  prospectsTotal: 1284,
  conversationsTotal: 2010,
  revenueTotal: "186 400 TND",
  conversionRate: 16,
};

/** Dashboard period filter: four hand-authored buckets. `contacted` counts new prospects only (see
 *  the doc comment on DashboardPeriodStat) — it is never the same number as ACTIONS_TODAY.total,
 *  which also includes follow-ups and reminders on prospects already in conversation. Each trend is
 *  bucketed at a grain that fits the window (days for a week, weeks for 30 days/3 months, months for
 *  six months) and is scaled with `distribute()` to sum to exactly `contacted`, so the chart can
 *  never show a different total than the KPI card above it. */
export const DASHBOARD_PERIODS: Record<"7d" | "30d" | "3m" | "all", DashboardPeriodStat> = {
  "7d": {
    label: "dashboard.period.7d",
    contacted: 58,
    responseRate: 71,
    conversionRate: 12,
    revenue: "8 400 TND",
    trendUnit: "day",
    trendLabels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    trend: distribute(58, [6, 7, 6, 8, 9, 11, 13]),
  },
  "30d": {
    label: "dashboard.period.30d",
    contacted: 214,
    responseRate: 68,
    conversionRate: 14,
    revenue: "31 200 TND",
    trendUnit: "week",
    trendLabels: ["Week 1", "Week 2", "Week 3", "Week 4", "Week 5"],
    trend: distribute(214, [7, 8, 9, 10, 12]),
  },
  "3m": {
    label: "dashboard.period.3m",
    contacted: 560,
    responseRate: 65,
    conversionRate: 15,
    revenue: "78 900 TND",
    trendUnit: "week",
    trendLabels: Array.from({ length: 13 }, (_, i) => `W${i + 1}`),
    trend: distribute(560, s(13, 34, 14, 1)),
  },
  all: {
    label: "dashboard.period.all",
    contacted: 1284,
    responseRate: 68,
    conversionRate: 16,
    revenue: "186 400 TND",
    trendUnit: "month",
    trendLabels: ["Mar", "Apr", "May", "Jun", "Jul", "Aug"],
    trend: distribute(1284, s(6, 34, 14, 1)),
  },
};

/** Prospects by stage, for the Dashboard's "active pipeline right now" panel. A live snapshot of
 *  prospects currently open (not the full six-month total, and not affected by the period filter —
 *  it always reflects the present moment), hand-authored the same way as the rest of this file. */
export const STAGE_BREAKDOWN: StageSlice[] = [
  { id: "new", label: "status.new", count: 9, tint: "action" },
  { id: "active", label: "status.active", count: 8, tint: "agent" },
  { id: "waiting", label: "stageBreakdown.readyToDecide", count: 4, tint: "attention" },
  { id: "won", label: "stageBreakdown.won", count: 3, tint: "success" },
];

/** Since-start performance per catalogue product, keyed to OFFERS above. Masterclass is coming
 *  soon, so it honestly has nothing to report yet — REVANSA never invents a number here. */
export const PRODUCT_PERFORMANCE: ProductPerformance[] = [
  { offerId: "foundation", prospectsOriented: 742, conversionRate: 11, revenueValue: 97500, revenueLabel: "97 500 TND" },
  { offerId: "growth", prospectsOriented: 498, conversionRate: 22, revenueValue: 88900, revenueLabel: "88 900 TND" },
  { offerId: "masterclass", prospectsOriented: 0, conversionRate: null, revenueValue: null, revenueLabel: null },
];

/** Report history (new "Report history" menu): every daily WhatsApp report REVANSA has sent this
 *  CEO, kept so a past day can be reopened. Today's own entry is computed live in History.tsx from
 *  the same state Results.tsx already reads; only the days before it need authored sample data. */
export const REPORT_HISTORY_PAST: ReportDay[] = [
  { date: "time.yesterday", time: "20:00", contacted: 14, replies: 9, conversationsOpen: 4, paymentsToConfirm: 2, interventions: 2, highlight: "report.history1.highlight" },
  { date: "time.daysAgo2", time: "20:00", contacted: 11, replies: 7, conversationsOpen: 5, paymentsToConfirm: 0, interventions: 0, highlight: "report.history2.highlight" },
  { date: "time.daysAgo3", time: "20:00", contacted: 9, replies: 6, conversationsOpen: 4, paymentsToConfirm: 1, interventions: 1, highlight: "report.history3.highlight" },
  { date: "time.daysAgo4", time: "20:00", contacted: 8, replies: 5, conversationsOpen: 3, paymentsToConfirm: 1, interventions: 1, highlight: "report.history4.highlight" },
];
