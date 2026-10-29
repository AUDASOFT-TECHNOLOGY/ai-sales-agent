/** What the agent learned from its results, as the screen shows it. Pure: no clock, no network. */

import type { MessageKey } from "../../i18n";

export type NoteState = "active" | "watching" | "off";

export interface LearnedNote {
  id: string;
  /** Names the point; the screen words it in the client's language. */
  code: string;
  kind: "do" | "avoid";
  state: NoteState;
  evidence: "low" | "medium" | "high";
  cases: number;
  against: number;
  product: string | null;
  confirmed: boolean;
  off_by: "client" | "auto" | null;
  reason: string;
}

export interface LearningAnswer {
  counts: { studied: number; achieved: number; unfavourable: number; needed: number };
  notes: LearnedNote[];
}

export type Choice = "off" | "confirm" | "on";

export interface Groups {
  works: LearnedNote[];
  avoids: LearnedNote[];
  watching: LearnedNote[];
  off: LearnedNote[];
}

/** The points by what the client needs to know: what the agent follows (to do, to avoid), what it only
 *  watches, and what was set aside. The best supported come first. */
export function groupNotes(notes: readonly LearnedNote[]): Groups {
  const strongest = (a: LearnedNote, b: LearnedNote) => b.cases - a.cases || a.id.localeCompare(b.id);
  const pick = (keep: (n: LearnedNote) => boolean) => notes.filter(keep).sort(strongest);
  return {
    works: pick((n) => n.state === "active" && n.kind === "do"),
    avoids: pick((n) => n.state === "active" && n.kind === "avoid"),
    watching: pick((n) => n.state === "watching"),
    off: pick((n) => n.state === "off"),
  };
}

/** What to say when there is nothing to list: not enough conversations yet, or nothing clear. */
export function emptyReason(answer: LearningAnswer): "few" | "none" | null {
  if (answer.notes.length > 0) return null;
  return answer.counts.studied < answer.counts.needed ? "few" : "none";
}

const KNOWN = new Set([
  "short_messages",
  "single_question",
  "spaced_follow_ups",
  "acknowledge_first",
  "answer_doubt_first",
  "ask_before_proposing",
  "easy_next_step",
  "long_messages",
  "quick_follow_up",
  "pushy_after_hesitation",
  "doubt_ignored",
  "needless_question",
  "unkept_promise",
  "generic_message",
  "proposal_too_early",
  "repeating_yourself",
]);

/** The sentence of a point; one the screen does not know (a newer server) has none and is not shown. */
export function sentenceKey(note: LearnedNote): MessageKey | null {
  return KNOWN.has(note.code) ? (`rv.learn.code.${note.code}` as MessageKey) : null;
}

export function shown(notes: readonly LearnedNote[]): LearnedNote[] {
  return notes.filter((n) => sentenceKey(n) !== null);
}
