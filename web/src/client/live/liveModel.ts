/** What the "Live" screen says about the agent's steps. Pure: no clock, no network. */

import type { MessageKey } from "../../i18n";

export interface LiveStep {
  id: number;
  kind: string;
  state: "running" | "done" | "failed" | "abandoned";
  started_at: string;
  ended_at: string | null;
  prospect_id: string | null;
  name: string | null;
  detail: string | null;
}

export interface LiveAnswer {
  /** The server's clock when it answered. */
  at: string;
  /** Today's sends against the day's limit; absent or null values are not known. */
  today?: { sent: number | null; limit: number | null } | null;
  now: LiveStep[];
  /** Newest first, running steps included. */
  recent: LiveStep[];
}

const SENTENCES: Record<string, MessageKey> = {
  reading_prospects: "rv.live.k.reading_prospects",
  planning: "rv.live.k.planning",
  checking: "rv.live.k.checking",
  reading_history: "rv.live.k.reading_history",
  thinking: "rv.live.k.thinking",
  answering: "rv.live.k.answering",
  sending: "rv.live.k.sending",
  receiving: "rv.live.k.receiving",
  checking_inbox: "rv.live.k.checking_inbox",
  syncing: "rv.live.k.syncing",
  reporting: "rv.live.k.reporting",
};

/** The sentence for a step; a kind this screen does not know yet still gets a plain one. */
export function stepKey(kind: string): MessageKey {
  return SENTENCES[kind] ?? "rv.live.k.other";
}

/* ── The parcours: where a step sits in the agent's day ── */

export type PhaseId = "file" | "prepare" | "send" | "replies";
export const PHASES: Array<{ id: PhaseId; key: MessageKey; kinds: string[] }> = [
  { id: "file", key: "rv.live.ph.file", kinds: ["reading_prospects", "planning"] },
  { id: "prepare", key: "rv.live.ph.prepare", kinds: ["checking", "reading_history", "thinking", "answering"] },
  { id: "send", key: "rv.live.ph.send", kinds: ["sending"] },
  { id: "replies", key: "rv.live.ph.replies", kinds: ["checking_inbox", "receiving"] },
];

export function phaseOf(kind: string): PhaseId | null {
  return PHASES.find((p) => p.kinds.includes(kind))?.id ?? null;
}

/** The phase being worked on now (the newest running step that has one), else null. */
export function activePhase(answer: LiveAnswer | null): PhaseId | null {
  const steps = [...(answer?.now ?? [])].sort((a, b) => b.id - a.id);
  for (const s of steps) {
    const phase = phaseOf(s.kind);
    if (phase) return phase;
  }
  return null;
}

/** The phase of the latest finished step, shown softly when nothing runs. */
export function lastPhase(answer: LiveAnswer | null): PhaseId | null {
  for (const s of answer?.recent ?? []) {
    if (s.state === "done" || s.state === "failed") {
      const phase = phaseOf(s.kind);
      if (phase) return phase;
    }
  }
  return null;
}

/* ── What the agent does for one person ── */

const WRITING = new Set(["thinking", "answering"]);

/** What the agent is doing for one person right now, if anything. */
export function activeFor(answer: LiveAnswer | null, prospectId: string | null): LiveStep | null {
  if (!answer || !prospectId) return null;
  return answer.now.find((s) => s.prospect_id === prospectId) ?? null;
}

/** The agent is composing a message: the conversation shows a "writing" line. */
export function isWriting(step: LiveStep | null): boolean {
  return step !== null && WRITING.has(step.kind);
}

/** The step the stage tells about: one about a person first, else the newest. */
export function primaryStep(answer: LiveAnswer | null): LiveStep | null {
  const running = [...(answer?.now ?? [])].sort((a, b) => b.id - a.id);
  return running.find((s) => s.prospect_id) ?? running[0] ?? null;
}

/** The person whose conversation to show: the one the client picked, else the one the agent is working
 *  on now, else the last one it worked on. */
export function followedProspect(answer: LiveAnswer | null, picked: string | null): string | null {
  if (picked) return picked;
  if (!answer) return null;
  return primaryStep(answer)?.prospect_id ?? answer.recent.find((s) => s.prospect_id)?.prospect_id ?? null;
}

export function nameOf(answer: LiveAnswer | null, prospectId: string | null): string | null {
  if (!answer || !prospectId) return null;
  return answer.recent.find((s) => s.prospect_id === prospectId && s.name)?.name ?? null;
}

export interface ChecklistItem {
  kind: string;
  state: "todo" | "running" | "done" | "failed";
  seconds: number | null;
}

const RUN_START = new Set(["checking", "receiving"]);

/** The latest run for one person, oldest step first: from its last "checking" or "receiving" on. */
export function runFor(answer: LiveAnswer | null, prospectId: string | null): LiveStep[] {
  if (!answer || !prospectId) return [];
  const mine = answer.recent.filter((s) => s.prospect_id === prospectId);
  const start = mine.findIndex((s) => RUN_START.has(s.kind));
  const run = start === -1 ? mine : mine.slice(0, start + 1);
  return [...run].reverse();
}

/** What the agent does for a person, in order: the steps taken, the one running, those still to come. */
export function checklist(run: LiveStep[], nowMs: number): ChecklistItem[] {
  const answering = run.some((s) => s.kind === "answering" || s.kind === "receiving");
  const plan = ["checking", "reading_history", answering ? "answering" : "thinking", "sending"];
  return plan.map((kind) => {
    const step = [...run].reverse().find((s) => s.kind === kind);
    if (!step) return { kind, state: "todo", seconds: null };
    const state = step.state === "running" ? "running" : step.state === "done" ? "done" : "failed";
    return { kind, state, seconds: secondsOf(step, nowMs) };
  });
}

/* ── The activity feed, one line per person ── */

export interface FeedGroup {
  key: string;
  prospectId: string | null;
  name: string | null;
  steps: LiveStep[];
  startedAt: string;
  state: "running" | "failed" | "done";
}

/** Consecutive steps about the same person become one group; a step about nobody stands alone.
 *  `recent` is newest first, so are the groups. */
export function groupFeed(recent: LiveStep[], max = 30): FeedGroup[] {
  const groups: FeedGroup[] = [];
  for (const step of recent) {
    const last = groups[groups.length - 1];
    if (last && step.prospect_id !== null && last.prospectId === step.prospect_id) {
      last.steps.push(step);
      last.startedAt = step.started_at;
      continue;
    }
    groups.push({ key: String(step.id), prospectId: step.prospect_id, name: step.name, steps: [step], startedAt: step.started_at, state: "done" });
  }
  for (const g of groups) {
    g.state = g.steps.some((s) => s.state === "running") ? "running" : g.steps.some((s) => s.state === "failed" || s.state === "abandoned") ? "failed" : "done";
    g.steps.reverse();
  }
  return groups.slice(0, max);
}

/* ── Time ── */

/** Whole seconds a step has lasted, up to `nowMs` (the server's clock) while it runs. */
export function secondsOf(step: LiveStep, nowMs: number): number {
  const end = step.ended_at ? Date.parse(step.ended_at) : nowMs;
  return Math.max(0, Math.round((end - Date.parse(step.started_at)) / 1000));
}

/** "4 s", "1 min 05 s": short enough for a timer that moves every second. */
export function clock(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** How long ago the latest finished step ended, in seconds; null when nothing has finished. */
export function idleSeconds(answer: LiveAnswer | null, nowMs: number): number | null {
  const ended = answer?.recent.find((s) => s.ended_at)?.ended_at;
  return ended ? Math.max(0, Math.round((nowMs - Date.parse(ended)) / 1000)) : null;
}

export function initials(name: string | null): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase();
}
