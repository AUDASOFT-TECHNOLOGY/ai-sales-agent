/** What the client teaches the agent, as pure functions over the commercial document.
 *
 *  Two parts, both under `communication`: the **sales method** (`playbook`: a few choices that hold for
 *  every product) and the **lessons** (`training`): habits, red lines, answers to situations, to
 *  objections and to frequent questions. A lesson may be tied to one product.
 *  Everything else in the document is kept as it is. */

import type { Doc } from "../settingsModel";
import { localizedText } from "../settingsModel";

/** The limits of the server (`TrainingLesson`, `Playbook`). */
export const TEXT_MAX = 600;
export const SHORT_MAX = 300;
export const LESSON_MAX = 60;

export type LessonKind = "habit" | "never" | "example" | "objection" | "faq";
export const KINDS: LessonKind[] = ["habit", "never", "example", "objection", "faq"];
/** The kinds that answer a situation: they need their `when`. */
export const SITUATIONAL: LessonKind[] = ["example", "objection", "faq"];
/** The kinds that hold for every product. */
export const GLOBAL: LessonKind[] = ["habit", "never"];

export interface Lesson {
  /** Only for the screen (a stable key while it is edited); never saved. */
  uid: number;
  kind: LessonKind;
  text: string;
  /** What the prospect says or asks (situational kinds). */
  when: string;
  /** The product it is about; "" for every product. */
  product: string;
  /** The reasoning behind the answer. */
  why: string;
}

let sequence = 0;
export const blankLesson = (kind: LessonKind, patch: Partial<Omit<Lesson, "uid">> = {}): Lesson => ({ uid: ++sequence, kind, text: "", when: "", product: "", why: "", ...patch });

export type Approach = "consultative" | "challenger" | "guide" | "direct";
export type Closing = "soft" | "choice" | "assumptive" | "direct";
export const APPROACHES: Approach[] = ["consultative", "challenger", "guide", "direct"];
export const CLOSINGS: Closing[] = ["soft", "choice", "assumptive", "direct"];

export interface Playbook {
  approach: Approach;
  closing: Closing;
  urgency: "none" | "real_reasons_only";
  discounts: "never_offer" | "ask_a_person";
  competitors: "stay_positive" | "avoid";
  pitch: string;
  ideal_customer: string;
  /** The client made the choices (even to keep the defaults): the method is then written down. */
  chosen: boolean;
}

export const DEFAULT_PLAYBOOK: Playbook = { chosen: false, approach: "consultative", closing: "choice", urgency: "none", discounts: "never_offer", competitors: "stay_positive", pitch: "", ideal_customer: "" };

export interface Training {
  playbook: Playbook;
  lessons: Lesson[];
}

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

const str = (value: unknown) => (typeof value === "string" ? value : "");
const clip = (text: string, max: number) => text.trim().slice(0, max);

export function playbookOf(commercial: Doc): Playbook {
  const raw = obj(obj(commercial.communication).playbook);
  const pick = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T => (allowed.includes(value as T) ? (value as T) : fallback);
  return {
    approach: pick(raw.approach, APPROACHES, DEFAULT_PLAYBOOK.approach),
    closing: pick(raw.closing, CLOSINGS, DEFAULT_PLAYBOOK.closing),
    urgency: pick(raw.urgency, ["none", "real_reasons_only"], DEFAULT_PLAYBOOK.urgency),
    discounts: pick(raw.discounts, ["never_offer", "ask_a_person"], DEFAULT_PLAYBOOK.discounts),
    competitors: pick(raw.competitors, ["stay_positive", "avoid"], DEFAULT_PLAYBOOK.competitors),
    pitch: str(raw.pitch),
    ideal_customer: str(raw.ideal_customer),
    chosen: Object.keys(raw).length > 0,
  };
}

/** The lessons the document holds, in the client's order; what cannot be understood is left out. */
export function lessonsOf(commercial: Doc): Lesson[] {
  const list = obj(commercial.communication).training;
  const out: Lesson[] = [];
  for (const raw of Array.isArray(list) ? list : []) {
    const l = obj(raw);
    if (!KINDS.includes(l.kind as LessonKind) || typeof l.text !== "string") continue;
    const lesson = blankLesson(l.kind as LessonKind, { text: l.text, when: str(l.when), product: str(l.product), why: str(l.why) });
    if (SITUATIONAL.includes(lesson.kind) && lesson.when === "") continue;
    out.push(lesson);
  }
  return out;
}

/** A lesson is saved only when it says everything it needs: a situational one has its situation. */
export function isComplete(lesson: Lesson): boolean {
  if (clip(lesson.text, TEXT_MAX) === "") return false;
  return !SITUATIONAL.includes(lesson.kind) || clip(lesson.when, SHORT_MAX) !== "";
}

/** A half-written lesson: something typed, but not enough to be kept. */
export function isHalf(lesson: Lesson): boolean {
  return !isComplete(lesson) && (lesson.text.trim() !== "" || lesson.when.trim() !== "");
}

/** Whether the text holds something the server refuses in a lesson: a price, a link, an e-mail address. */
export function holdsExactValue(text: string): boolean {
  return /https?:\/\/|www\.|\b[\w.+-]+@[\w-]+\.[\w.-]+|\d\s?(?:€|\$|£)|\d\s?(?:eur|usd|tnd|dt|dh|mad|dzd)\b|(?:€|\$|£)\s?\d|\d{9,}/i.test(text);
}

export function lessonProblem(lesson: Lesson): "exact" | null {
  return [lesson.text, lesson.when, lesson.why].some(holdsExactValue) ? "exact" : null;
}

function documentOf(lesson: Lesson): Doc {
  const out: Doc = { kind: lesson.kind, text: clip(lesson.text, TEXT_MAX) };
  if (SITUATIONAL.includes(lesson.kind)) out.when = clip(lesson.when, SHORT_MAX);
  if (!GLOBAL.includes(lesson.kind) && lesson.product !== "") out.product = lesson.product;
  if (!GLOBAL.includes(lesson.kind) && clip(lesson.why, SHORT_MAX) !== "") out.why = clip(lesson.why, SHORT_MAX);
  return out;
}

/** Writes the lessons and the method; the key is taken out when there is nothing left to say. */
export function setTraining(commercial: Doc, training: Training): Doc {
  const communication: Doc = { ...obj(commercial.communication) };
  const lessons = training.lessons.filter((l) => isComplete(l) && lessonProblem(l) === null).slice(0, LESSON_MAX).map(documentOf);
  if (lessons.length === 0) delete communication.training;
  else communication.training = lessons;

  const p = training.playbook;
  const pitch = clip(p.pitch, SHORT_MAX);
  const ideal = clip(p.ideal_customer, SHORT_MAX);
  const unchanged = p.approach === DEFAULT_PLAYBOOK.approach && p.closing === DEFAULT_PLAYBOOK.closing && p.urgency === DEFAULT_PLAYBOOK.urgency && p.discounts === DEFAULT_PLAYBOOK.discounts && p.competitors === DEFAULT_PLAYBOOK.competitors;
  if (unchanged && !p.chosen && pitch === "" && ideal === "") delete communication.playbook;
  else communication.playbook = { approach: p.approach, closing: p.closing, urgency: p.urgency, discounts: p.discounts, competitors: p.competitors, ...(pitch ? { pitch } : {}), ...(ideal ? { ideal_customer: ideal } : {}) };
  return { ...commercial, communication };
}

export function trainingOf(commercial: Doc): Training {
  return { playbook: playbookOf(commercial), lessons: lessonsOf(commercial) };
}

/** What would be written, for telling whether the screen differs from the server. */
export function signature(commercial: Doc, training: Training): string {
  return JSON.stringify(obj(setTraining(commercial, training).communication));
}

export const countOf = (lessons: Lesson[], kind?: LessonKind) => lessons.filter((l) => (kind === undefined || l.kind === kind) && isComplete(l)).length;

export interface ProductOption {
  id: string;
  name: string;
}

export function productOptions(commercial: Doc, lang: string): ProductOption[] {
  const list = obj(commercial.catalog).products;
  return (Array.isArray(list) ? list : []).map(obj).filter((p) => typeof p.id === "string" && p.status !== "inactive").map((p) => ({ id: p.id as string, name: localizedText(p.name, lang) || (p.id as string) }));
}

/** Lessons tied to a product the catalog no longer holds: they would never be shown. */
export function orphans(lessons: Lesson[], products: ProductOption[]): Lesson[] {
  const known = new Set(products.map((p) => p.id));
  return lessons.filter((l) => l.product !== "" && !known.has(l.product));
}

/** The objections the client already configured on its products and conversation (their codes). */
export function configuredObjections(commercial: Doc): string[] {
  const codes = new Set<string>();
  const add = (list: unknown) => {
    for (const o of Array.isArray(list) ? list : []) if (typeof obj(o).code === "string") codes.add(obj(o).code as string);
  };
  add(obj(commercial.conversation).objections);
  const products = obj(commercial.catalog).products;
  for (const p of Array.isArray(products) ? products : []) add(obj(p).objections);
  return [...codes];
}

export interface ThreadMessage {
  direction: string;
  text: string;
}

/** The situation a message of the agent answered: the last thing the prospect wrote before it, or "" for an opening message. */
export function situationBefore(messages: ThreadMessage[], index: number): string {
  for (let i = index - 1; i >= 0; i--) if (messages[i]!.direction !== "outbound") return messages[i]!.text.trim().slice(0, SHORT_MAX);
  return "";
}
