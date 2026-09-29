/** Prototype state. Simulates what the Admin API would return and accept; it fakes NO backend
 *  behaviour beyond moving in-memory values so the interactions can be judged visually.
 *
 *  Every configuration field below is a real, single source of truth: editing it (Offers, language
 *  and style, schedule, follow-up, the CEO report, payment, the Sheet link, autonomy) updates this
 *  state, every screen that reads it re-renders, and it survives a reload in this browser via
 *  localStorage (see `persist` below). Nothing in Configure is decorative: if a field can be typed
 *  into, it is because there is a real state field behind it. */
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { CONVERSATIONS, NEEDS, OFFERS, PROSPECTS, STAGES } from "./data";
import { deriveReadiness } from "./offers";
import type { AgentStateKey, CadenceStep, Conversation, LostReason, Mode, NeedItem, Offer, OfferAsset, PaymentProfile, Prospect, Role, Tone } from "./types";

export type GateId = "sheet" | "offer" | "payment" | "voice" | "path" | "live";
export const GATES: GateId[] = ["sheet", "offer", "payment", "voice", "path", "live"];

export interface Toast {
  id: number;
  tone: "success" | "info" | "agent" | "attention" | "danger";
  title: string;
  body?: string;
  undo?: () => void;
}

interface State {
  live: boolean;
  activationRequested: boolean;
  gates: Record<GateId, boolean>;
  demoAgentState: AgentStateKey | "auto";
  paused: boolean;
  requestedMode: Mode;
  /** The deployment ceiling: what the platform allows this client. */
  ceiling: Mode;
  role: Role;
  needs: NeedItem[];
  prospects: Prospect[];
  conversations: Conversation[];
  offers: Offer[];
  changes: string[];
  publishedVersion: number;
  dailyLimit: number;
  dailyUsed: number;
  winPulse: number;
  winActive: boolean;
  toasts: Toast[];
  degraded: boolean;

  // ── Real, editable business configuration (section 9/10: every one of these is wired) ──
  sheet: { url: string; verified: boolean };
  languages: string[];
  defaultLanguage: string;
  tone: Tone;
  handoffEnabled: boolean;
  schedule: { days: string[]; start: string; end: string };
  optOutWords: string[];
  followUp: { steps: CadenceStep[]; stopConditions: string[] };
  ceoReport: { enabled: boolean; time: string; phone: string };
  /** The WhatsApp Business number REVENSA writes to prospects from. Editable by the client. */
  agentPhone: string;
  /** Target countries this client sells into, managed by the client (business section). */
  markets: string[];
  /** Payment varies per market: different methods, different bank details, a different link. */
  paymentProfiles: PaymentProfile[];
}

const initial: State = {
  live: true,
  activationRequested: true,
  gates: { sheet: true, offer: true, payment: true, voice: true, path: true, live: true },
  demoAgentState: "auto",
  paused: false,
  requestedMode: "ask",
  ceiling: "ask",
  role: "owner",
  needs: NEEDS,
  prospects: PROSPECTS,
  conversations: CONVERSATIONS,
  offers: OFFERS,
  changes: [],
  publishedVersion: 7,
  dailyLimit: 20,
  dailyUsed: 14,
  winPulse: 0,
  winActive: false,
  toasts: [],
  degraded: false,

  sheet: { url: "https://docs.google.com/spreadsheets/d/SAMPLE-SHEET-ID/edit", verified: true },
  languages: ["fr", "ar"],
  defaultLanguage: "fr",
  tone: "friendly",
  handoffEnabled: true,
  schedule: { days: ["mon", "tue", "wed", "thu", "fri", "sat"], start: "09:00", end: "18:00" },
  optOutWords: ["stop", "unsubscribe"],
  followUp: {
    steps: [
      { days: 2, label: "First reminder" },
      { days: 3, label: "Second reminder" },
      { days: 4, label: "Last follow-up" },
    ],
    stopConditions: ["asked_to_stop", "not_interested"],
  },
  ceoReport: { enabled: true, time: "20:00", phone: "+216 20 000 000" },
  agentPhone: "+216 20 000 042",
  markets: ["Tunisia", "France", "Gulf"],
  paymentProfiles: [
    { market: "Tunisia", methods: ["bank"], instructions: "IBAN TN59 0000 0000 0000 0000 0000, reference: your first name", link: "" },
    { market: "France", methods: [], instructions: "", link: "" },
    { market: "Gulf", methods: [], instructions: "", link: "" },
  ],
};

/** A client that has not been onboarded yet: the Launch Runway is the first thing it sees. */
const fresh: State = {
  ...initial,
  live: false,
  activationRequested: false,
  gates: { sheet: false, offer: false, payment: false, voice: false, path: false, live: false },
};

type Action =
  | { type: "gate"; id: GateId }
  | { type: "requestActivation" }
  | { type: "goLive" }
  | { type: "skipToLive" }
  | { type: "restart" }
  | { type: "winEnd" }
  | { type: "demoState"; value: AgentStateKey | "auto" }
  | { type: "pause"; value: boolean }
  | { type: "mode"; value: Mode }
  | { type: "role"; value: Role }
  | { type: "resolveNeed"; id: string }
  | { type: "approveDraft"; prospectId: string; text: string }
  | { type: "declineDraft"; prospectId: string }
  | { type: "takeOver"; prospectId: string }
  | { type: "giveBack"; prospectId: string }
  | { type: "sendHuman"; prospectId: string; text: string }
  | { type: "confirmPayment"; prospectId: string }
  | { type: "setProspectStage"; id: string; stage: string }
  | { type: "markProspectLost"; id: string; reason: LostReason }
  | { type: "reopenProspect"; id: string }
  | { type: "change"; label: string }
  | { type: "discard" }
  | { type: "publish" }
  | { type: "limit"; value: number }
  | { type: "degraded"; value: boolean }
  | { type: "toast"; toast: Toast }
  | { type: "dismissToast"; id: number }
  | { type: "addOffer"; offer: Offer }
  | { type: "updateOffer"; id: string; patch: Partial<Offer> }
  | { type: "removeOffer"; id: string }
  | { type: "reorderOffer"; id: string; direction: "up" | "down" }
  | { type: "addOfferAsset"; offerId: string; asset: Omit<OfferAsset, "id"> }
  | { type: "removeOfferAsset"; offerId: string; assetId: string }
  | { type: "addMarket"; name: string }
  | { type: "removeMarket"; name: string }
  | { type: "setPaymentProfile"; market: string; patch: Partial<Omit<PaymentProfile, "market">> }
  | { type: "setSheetUrl"; url: string }
  | { type: "verifySheet" }
  | { type: "setLanguages"; value: string[] }
  | { type: "setDefaultLanguage"; value: string }
  | { type: "setTone"; value: Tone }
  | { type: "setHandoff"; value: boolean }
  | { type: "setScheduleDays"; value: string[] }
  | { type: "setScheduleHours"; start: string; end: string }
  | { type: "setOptOutWords"; value: string[] }
  | { type: "setFollowUpSteps"; value: CadenceStep[] }
  | { type: "setFollowUpStop"; value: string[] }
  | { type: "setCeoReport"; patch: Partial<State["ceoReport"]> }
  | { type: "setAgentPhone"; value: string };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "gate":
      return { ...state, gates: { ...state.gates, [action.id]: true } };
    case "requestActivation":
      return { ...state, activationRequested: true, gates: { ...state.gates, live: true } };
    case "goLive":
      return { ...state, live: true, winPulse: state.winPulse + 1, winActive: true };
    case "skipToLive":
      return { ...state, live: true, activationRequested: true, gates: { sheet: true, offer: true, payment: true, voice: true, path: true, live: true } };
    case "restart":
      return { ...fresh, role: state.role };
    case "winEnd":
      return { ...state, winActive: false };
    case "demoState":
      return { ...state, demoAgentState: action.value };
    case "pause":
      return { ...state, paused: action.value };
    case "mode":
      return { ...state, requestedMode: action.value, changes: addChange(state.changes, "Autonomy: level changed") };
    case "role":
      return { ...state, role: action.value };
    case "resolveNeed":
      return { ...state, needs: state.needs.filter((n) => n.id !== action.id) };
    case "approveDraft":
      return {
        ...state,
        needs: state.needs.filter((n) => !(n.kind === "draft" && n.prospectId === action.prospectId)),
        conversations: state.conversations.map((c) =>
          c.prospectId === action.prospectId
            ? {
                ...c,
                draft: undefined,
                needsYou: false,
                messages: [
                  ...c.messages,
                  { id: `m${c.messages.length + 1}`, author: "agent", time: "Just now", parts: [{ text: action.text }] },
                ],
              }
            : c,
        ),
        dailyUsed: state.dailyUsed + 1,
      };
    case "declineDraft":
      return {
        ...state,
        needs: state.needs.filter((n) => !(n.kind === "draft" && n.prospectId === action.prospectId)),
        conversations: state.conversations.map((c) =>
          c.prospectId === action.prospectId ? { ...c, draft: undefined, needsYou: false } : c,
        ),
      };
    case "takeOver":
      return {
        ...state,
        needs: state.needs.filter((n) => !(n.kind === "handoff" && n.prospectId === action.prospectId)),
        conversations: state.conversations.map((c) =>
          c.prospectId === action.prospectId
            ? { ...c, needsYou: false, notes: { ...c.notes, handoff: undefined } }
            : c,
        ),
        prospects: state.prospects.map((p) =>
          p.id === action.prospectId ? { ...p, next: "You are handling this conversation", attention: "none" } : p,
        ),
      };
    case "giveBack":
      return {
        ...state,
        prospects: state.prospects.map((p) =>
          p.id === action.prospectId ? { ...p, next: "I'll follow up tomorrow, 10:00", attention: "working" } : p,
        ),
      };
    case "sendHuman":
      return {
        ...state,
        conversations: state.conversations.map((c) =>
          c.prospectId === action.prospectId
            ? {
                ...c,
                messages: [
                  ...c.messages,
                  { id: `m${c.messages.length + 1}`, author: "human", who: "Sami K.", time: "Just now", parts: [{ text: action.text }] },
                ],
              }
            : c,
        ),
      };
    case "confirmPayment":
      return {
        ...state,
        winPulse: state.winPulse + 1,
        winActive: true,
        needs: state.needs.filter((n) => !(n.kind === "payment" && n.prospectId === action.prospectId)),
        conversations: state.conversations.map((c) =>
          c.prospectId === action.prospectId
            ? {
                ...c,
                payment: "paid",
                needsYou: false,
                messages: [
                  ...c.messages,
                  { id: `m${c.messages.length + 1}`, author: "system", time: "Just now", parts: [{ text: "Payment confirmed by Sami K." }] },
                ],
              }
            : c,
        ),
        prospects: state.prospects.map((p) =>
          p.id === action.prospectId
            ? {
                ...p,
                stage: "won",
                status: "closed",
                attention: "won",
                step: 4,
                read: "Payment confirmed. Welcome message scheduled.",
                next: "Nothing planned",
                recommendation: undefined,
                facts: [...p.facts, { key: "Payment", value: "Confirmed", status: "confirmed", source: "human", excerpt: "Confirmed by Sami K." }],
              }
            : p,
        ),
      };
    case "setProspectStage": {
      return {
        ...state,
        prospects: state.prospects.map((p) => {
          if (p.id !== action.id) return p;
          const target = STAGES.find((s) => s.id === action.stage);
          if (!target) return p;
          const index = STAGES.findIndex((s) => s.id === target.id);
          const won = target.milestone === "won";
          return {
            ...p,
            stage: target.id,
            step: Math.min(index, 4),
            status: won ? "closed" : p.status === "lost" || p.status === "closed" ? "active" : p.status,
            attention: won ? "won" : p.attention === "won" || p.attention === "lost" ? "none" : p.attention,
            lostReason: undefined,
          };
        }),
      };
    }
    case "markProspectLost": {
      return {
        ...state,
        prospects: state.prospects.map((p) =>
          p.id === action.id ? { ...p, status: "lost", lostReason: action.reason, attention: "lost" } : p,
        ),
      };
    }
    case "reopenProspect": {
      return {
        ...state,
        prospects: state.prospects.map((p) =>
          p.id === action.id ? { ...p, status: "active", lostReason: undefined, attention: "none" } : p,
        ),
      };
    }
    case "change":
      return { ...state, changes: addChange(state.changes, action.label) };
    case "discard":
      return { ...state, changes: [] };
    case "publish":
      return { ...state, changes: [], publishedVersion: state.publishedVersion + 1 };
    case "limit":
      return { ...state, dailyLimit: action.value, changes: addChange(state.changes, "Schedule: daily limit changed") };
    case "degraded":
      return { ...state, degraded: action.value };
    case "toast":
      return { ...state, toasts: [...state.toasts.slice(-2), action.toast] };
    case "dismissToast":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    case "addOffer":
      return {
        ...state,
        offers: [action.offer, ...state.offers],
        changes: addChange(state.changes, `Offers: ${action.offer.name} added`),
        gates: { ...state.gates, offer: true },
      };
    case "updateOffer": {
      let editedName = "an offer";
      const offers = state.offers.map((o) => {
        if (o.id !== action.id) return o;
        editedName = action.patch.name ?? o.name;
        const merged = { ...o, ...action.patch };
        return { ...merged, ...deriveReadiness(merged) };
      });
      return { ...state, offers, changes: addChange(state.changes, `Offers: ${editedName} edited`) };
    }
    case "removeOffer": {
      const removed = state.offers.find((o) => o.id === action.id);
      return {
        ...state,
        offers: state.offers.filter((o) => o.id !== action.id),
        changes: addChange(state.changes, `Offers: ${removed?.name ?? "a product"} removed`),
      };
    }
    case "reorderOffer": {
      const i = state.offers.findIndex((o) => o.id === action.id);
      const j = action.direction === "up" ? i - 1 : i + 1;
      if (i < 0 || j < 0 || j >= state.offers.length) return state;
      const offers = [...state.offers];
      [offers[i], offers[j]] = [offers[j]!, offers[i]!];
      return { ...state, offers };
    }
    case "addOfferAsset": {
      const offers = state.offers.map((o) => {
        if (o.id !== action.offerId) return o;
        const asset: OfferAsset = { id: `${o.id}-asset-${o.assets.length + 1}-${Date.now().toString(36)}`, ...action.asset };
        const merged = { ...o, assets: [...o.assets, asset] };
        return { ...merged, ...deriveReadiness(merged) };
      });
      return { ...state, offers, changes: addChange(state.changes, "Offers: sales material added") };
    }
    case "removeOfferAsset": {
      const offers = state.offers.map((o) => {
        if (o.id !== action.offerId) return o;
        const merged = { ...o, assets: o.assets.filter((a) => a.id !== action.assetId) };
        return { ...merged, ...deriveReadiness(merged) };
      });
      return { ...state, offers, changes: addChange(state.changes, "Offers: sales material removed") };
    }
    case "addMarket": {
      const name = action.name.trim();
      if (!name || state.markets.some((m) => m.toLowerCase() === name.toLowerCase())) return state;
      return {
        ...state,
        markets: [...state.markets, name],
        paymentProfiles: [...state.paymentProfiles, { market: name, methods: [], instructions: "", link: "" }],
        changes: addChange(state.changes, `Business: ${name} added as a target country`),
      };
    }
    case "removeMarket": {
      return {
        ...state,
        markets: state.markets.filter((m) => m !== action.name),
        offers: state.offers.map((o) => {
          if (!o.markets.includes(action.name)) return o;
          const merged = { ...o, markets: o.markets.filter((m) => m !== action.name) };
          return { ...merged, ...deriveReadiness(merged) };
        }),
        paymentProfiles: state.paymentProfiles.filter((p) => p.market !== action.name),
        changes: addChange(state.changes, `Business: ${action.name} removed as a target country`),
      };
    }
    case "setPaymentProfile": {
      const exists = state.paymentProfiles.some((p) => p.market === action.market);
      const paymentProfiles = exists
        ? state.paymentProfiles.map((p) => (p.market === action.market ? { ...p, ...action.patch } : p))
        : [...state.paymentProfiles, { market: action.market, methods: [], instructions: "", link: "", ...action.patch }];
      return { ...state, paymentProfiles, changes: addChange(state.changes, `Payment: ${action.market} details changed`) };
    }
    case "setSheetUrl":
      return { ...state, sheet: { url: action.url, verified: false }, changes: addChange(state.changes, "Business: Google Sheet link changed") };
    case "verifySheet":
      return { ...state, sheet: { ...state.sheet, verified: true }, gates: { ...state.gates, sheet: true } };
    case "setLanguages":
      return { ...state, languages: action.value, changes: addChange(state.changes, "Voice: languages changed") };
    case "setDefaultLanguage":
      return { ...state, defaultLanguage: action.value, changes: addChange(state.changes, "Voice: default language changed") };
    case "setTone":
      return { ...state, tone: action.value, changes: addChange(state.changes, "Voice: starting style changed") };
    case "setHandoff":
      return { ...state, handoffEnabled: action.value, changes: addChange(state.changes, "Sales strategy: handoff changed") };
    case "setScheduleDays":
      return { ...state, schedule: { ...state.schedule, days: action.value }, changes: addChange(state.changes, "Schedule: working days changed") };
    case "setScheduleHours":
      return { ...state, schedule: { ...state.schedule, start: action.start, end: action.end }, changes: addChange(state.changes, "Schedule: hours changed") };
    case "setOptOutWords":
      return { ...state, optOutWords: action.value, changes: addChange(state.changes, "Schedule: opt-out words changed") };
    case "setFollowUpSteps":
      return { ...state, followUp: { ...state.followUp, steps: action.value }, changes: addChange(state.changes, "Follow-up: cadence changed") };
    case "setFollowUpStop":
      return { ...state, followUp: { ...state.followUp, stopConditions: action.value }, changes: addChange(state.changes, "Follow-up: stop rules changed") };
    case "setCeoReport":
      return { ...state, ceoReport: { ...state.ceoReport, ...action.patch }, changes: addChange(state.changes, "WhatsApp: daily report changed") };
    case "setAgentPhone":
      return { ...state, agentPhone: action.value, changes: addChange(state.changes, "Connections: WhatsApp Business number changed") };
  }
}

function addChange(list: string[], label: string): string[] {
  return list.includes(label) ? list : [...list, label];
}

const MODE_RANK: Record<Mode, number> = { observe: 0, ask: 1, auto: 2 };

export function effectiveMode(requested: Mode, ceiling: Mode): Mode {
  return MODE_RANK[requested] <= MODE_RANK[ceiling] ? requested : ceiling;
}

export type Mood = "calm" | "working" | "attention" | "win" | "trouble" | "paused";

interface Derived {
  agentState: AgentStateKey;
  mode: Mode;
  clamped: boolean;
  mood: Mood;
}

function derive(state: State): Derived {
  const mode = effectiveMode(state.requestedMode, state.ceiling);
  const clamped = mode !== state.requestedMode;
  let agentState: AgentStateKey;
  if (state.demoAgentState !== "auto") agentState = state.demoAgentState;
  else if (!state.live) agentState = "not_live";
  else if (state.paused) agentState = "paused";
  else if (state.degraded) agentState = "degraded";
  else if (mode === "observe") agentState = "observing";
  else if (state.needs.length > 0) agentState = "waiting";
  else agentState = "working";

  let mood: Mood = "calm";
  if (agentState === "degraded") mood = "trouble";
  else if (agentState === "paused" || agentState === "not_live" || agentState === "off_hours") mood = "paused";
  else if (agentState === "waiting") mood = "attention";
  else if (agentState === "working" || agentState === "writing") mood = "working";
  return { agentState, mode, clamped, mood };
}

interface Store extends Derived {
  state: State;
  dispatch: (action: Action) => void;
  toast: (toast: Omit<Toast, "id">) => void;
  canConfirmPayments: boolean;
}

const Ctx = createContext<Store | null>(null);

/* ── Persistence: this prototype's only "backend" is this browser's localStorage. Bumping
 *  STORAGE_VERSION invalidates older shapes instead of crashing on them. Toasts and the win
 *  animation are transient and never persisted. ── */
const STORAGE_KEY = "revensa.mock.state";
const STORAGE_VERSION = 6;

function loadPersisted(): State {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return initial;
    const parsed = JSON.parse(raw) as { v: number; state: Partial<State> };
    if (!parsed || parsed.v !== STORAGE_VERSION || !parsed.state) return initial;
    return { ...initial, ...parsed.state, toasts: [], winPulse: 0, winActive: false };
  } catch {
    return initial;
  }
}

function clearPersisted(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* a private window or blocked storage still runs the prototype, just without persistence */
  }
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, rawDispatch] = useReducer(reducer, undefined, loadPersisted);
  const toastId = useRef(0);

  const derived = useMemo(() => derive(state), [state]);

  const dispatch = useCallback((action: Action) => {
    if (action.type === "restart") clearPersisted();
    rawDispatch(action);
  }, []);
  const toast = useCallback((t: Omit<Toast, "id">) => {
    toastId.current += 1;
    rawDispatch({ type: "toast", toast: { ...t, id: toastId.current } });
  }, []);

  // Every change is saved, so Configure's promise ("changes stay a draft until you publish
  // them") is honest about what persists: everything, immediately, in this browser.
  useEffect(() => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars -- destructured only to exclude these from what is saved
      const { toasts, winPulse, winActive, ...persisted } = state;
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, state: persisted }));
    } catch {
      /* not fatal: the session keeps working, it just won't survive a reload */
    }
  }, [state]);

  // A win tints the aurora emerald for six seconds (doc 3.2).
  useEffect(() => {
    if (state.winPulse === 0) return;
    const id = window.setTimeout(() => rawDispatch({ type: "winEnd" }), 6000);
    return () => window.clearTimeout(id);
  }, [state.winPulse]);

  useEffect(() => {
    document.documentElement.dataset.mood = state.winActive ? "win" : derived.mood;
  }, [derived.mood, state.winActive]);

  useEffect(() => {
    const onVisibility = () => {
      document.documentElement.dataset.hidden = String(document.hidden);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const value = useMemo<Store>(
    () => ({ ...derived, state, dispatch, toast, canConfirmPayments: state.role === "owner" }),
    [derived, state, dispatch, toast],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useStore must be used inside StoreProvider");
  return ctx;
}

export function useProspect(id: string | undefined): Prospect | undefined {
  const { state } = useStore();
  return state.prospects.find((p) => p.id === id);
}
