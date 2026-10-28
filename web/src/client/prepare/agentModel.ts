/** How the questionnaires write into the commercial document, as pure functions: each starts from the
 *  stored document and changes only its own part, so what an administrator set is kept. */

import type { Doc } from "../settingsModel";

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

export type GoalKind = "conversion" | "meeting" | "lead_capture" | "handoff";
export const GOAL_KINDS: GoalKind[] = ["conversion", "meeting", "lead_capture", "handoff"];
const MAIN_GOAL = "main";

/** The server refuses a document without languages: until the client chooses, the interface language stands in. */
export function withLanguages(commercial: Doc, fallback: string): Doc {
  const communication = obj(commercial.communication);
  const languages = obj(communication.languages);
  if (Array.isArray(languages.supported) && languages.supported.length > 0 && typeof languages.default === "string") return commercial;
  return { ...commercial, communication: { ...communication, languages: { ...languages, supported: [fallback], default: fallback } } };
}

export function goalOf(commercial: Doc): GoalKind | "" {
  const objectives = obj(commercial.objectives);
  const goals = Array.isArray(objectives.goals) ? objectives.goals.map(obj) : [];
  const main = goals.find((g) => g.id === (objectives.primary ?? MAIN_GOAL)) ?? goals[0];
  return GOAL_KINDS.includes(main?.kind as GoalKind) ? (main!.kind as GoalKind) : "";
}

/** The main objective of the agent; the label is the wording shown to the client. */
export function setGoal(commercial: Doc, kind: GoalKind | "", label: string): Doc {
  const objectives: Doc = { ...obj(commercial.objectives) };
  const others = (Array.isArray(objectives.goals) ? objectives.goals.map(obj) : []).filter((g) => g.id !== MAIN_GOAL);
  if (kind === "") {
    objectives.goals = others;
    if (others.length === 0) delete objectives.primary;
  } else {
    objectives.goals = [{ id: MAIN_GOAL, label, kind }, ...others];
    objectives.primary = MAIN_GOAL;
  }
  return { ...commercial, objectives };
}

export function personaOf(commercial: Doc): string {
  const name = obj(obj(commercial.communication).tone).persona_name;
  return typeof name === "string" ? name : "";
}

export function setPersona(commercial: Doc, name: string): Doc {
  const communication = obj(commercial.communication);
  const tone: Doc = { ...obj(communication.tone) };
  if (name.trim() === "") delete tone.persona_name;
  else tone.persona_name = name.trim();
  return { ...commercial, communication: { ...communication, tone } };
}

/** The settings keep one list of principles (at most 10). Instructions and persuasion arguments are
 *  both in it; the arguments carry this tag so each question reads and writes only its own lines. */
export const ARGUMENT_TAG = "[persuasion] ";
export const ARGUMENT_MAX_LENGTH = 300 - ARGUMENT_TAG.length;

function principles(commercial: Doc): string[] {
  const list = obj(commercial.communication).principles;
  return Array.isArray(list) ? list.filter((x): x is string => typeof x === "string") : [];
}

function writePrinciples(commercial: Doc, list: string[]): Doc {
  const communication = obj(commercial.communication);
  return { ...commercial, communication: { ...communication, principles: list } };
}

const clean = (list: string[]) => list.map((x) => x.trim()).filter((x) => x !== "");

export function principlesOf(commercial: Doc): string[] {
  return principles(commercial).filter((p) => !p.startsWith(ARGUMENT_TAG));
}

export function setPrinciples(commercial: Doc, list: string[]): Doc {
  return writePrinciples(commercial, [...clean(list), ...principles(commercial).filter((p) => p.startsWith(ARGUMENT_TAG))]);
}

/** The client's own ways to convince, in its words. */
export function argumentsOf(commercial: Doc): string[] {
  return principles(commercial)
    .filter((p) => p.startsWith(ARGUMENT_TAG))
    .map((p) => p.slice(ARGUMENT_TAG.length));
}

export function setArguments(commercial: Doc, list: string[]): Doc {
  return writePrinciples(commercial, [...principles(commercial).filter((p) => !p.startsWith(ARGUMENT_TAG)), ...clean(list).map((x) => ARGUMENT_TAG + x)]);
}

export interface Persuasion {
  /** Answer the prospect's question before anything else. */
  answerFirst: boolean;
  /** End each message with a next step. */
  nextStep: boolean;
  maxQuestions: number;
  emoji: boolean;
}

export function persuasionOf(commercial: Doc): Persuasion {
  const conversation = obj(commercial.conversation);
  const message = obj(obj(commercial.communication).message);
  return {
    answerFirst: conversation.answer_first !== false,
    nextStep: obj(conversation.recommendation).always_end_with !== "none",
    maxQuestions: typeof message.max_questions === "number" ? message.max_questions : 1,
    emoji: message.emoji !== "none",
  };
}

export function setPersuasion(commercial: Doc, value: Persuasion): Doc {
  const conversation = obj(commercial.conversation);
  const communication = obj(commercial.communication);
  return {
    ...commercial,
    conversation: { ...conversation, answer_first: value.answerFirst, recommendation: { ...obj(conversation.recommendation), always_end_with: value.nextStep ? "next_step" : "none" } },
    communication: { ...communication, message: { ...obj(communication.message), max_questions: value.maxQuestions, emoji: value.emoji ? "light" : "none" } },
  };
}

/** How the agent sells, as four styles instead of four switches. Each is a fixed mix of the settings. */
export type Style = "advisor" | "direct" | "listener" | "informative";
export const STYLES: Style[] = ["advisor", "direct", "listener", "informative"];
const STYLE_MIX: Record<Style, Pick<Persuasion, "nextStep" | "maxQuestions">> = {
  advisor: { nextStep: true, maxQuestions: 1 },
  direct: { nextStep: true, maxQuestions: 0 },
  listener: { nextStep: false, maxQuestions: 2 },
  informative: { nextStep: false, maxQuestions: 0 },
};

/** The style the settings amount to, or "" for a mix set by hand (it is kept until another style is chosen). */
export function styleOf(p: Persuasion): Style | "" {
  return STYLES.find((s) => STYLE_MIX[s].nextStep === p.nextStep && STYLE_MIX[s].maxQuestions === p.maxQuestions) ?? "";
}

export function withStyle(p: Persuasion, style: Style): Persuasion {
  return { ...p, answerFirst: true, ...STYLE_MIX[style] };
}

/** What the agent does when a prospect says it is not interested. */
export type Refusal = "stop" | "once" | "twice" | "handoff";
export const REFUSALS: Refusal[] = ["stop", "once", "twice", "handoff"];
const REFUSAL_CODE = "not_interested";
const DEFAULT_STOPS = ["rejected_all_interests", "explicit_no", "handoff_active"];

function objectionsOf(commercial: Doc): Doc[] {
  const list = obj(commercial.conversation).objections;
  return Array.isArray(list) ? list.map(obj) : [];
}

export function refusalOf(commercial: Doc): Refusal {
  const rule = objectionsOf(commercial).find((o) => o.code === REFUSAL_CODE);
  if (!rule) return "stop";
  if (rule.then === "handoff") return "handoff";
  return typeof rule.max_rebuttals === "number" && rule.max_rebuttals >= 2 ? "twice" : "once";
}

/** "stop": the Brain's own rule closes politely. The others write an objection "not interested" that
 *  lets the agent try again (once or twice) before it drops the prospect, or hands over at once; the
 *  rule that closes on a first refusal is then taken out, or it would act first. */
export function setRefusal(commercial: Doc, choice: Refusal, wording: { response: string; examples: string[] }): Doc {
  const conversation = obj(commercial.conversation);
  const others = objectionsOf(commercial).filter((o) => o.code !== REFUSAL_CODE);
  const followUp = obj(commercial.follow_up);
  const stored = Array.isArray(followUp.stop_conditions) ? (followUp.stop_conditions as string[]) : null;

  let objections = others;
  let stops = stored;
  if (choice === "stop") {
    if (stored && !stored.includes("explicit_no")) stops = [...stored, "explicit_no"];
  } else {
    objections = [...others, { code: REFUSAL_CODE, examples: wording.examples, response: wording.response, max_rebuttals: choice === "handoff" ? 0 : choice === "twice" ? 2 : 1, then: choice === "handoff" ? "handoff" : "drop" }];
    stops = (stored ?? DEFAULT_STOPS).filter((c) => c !== "explicit_no");
  }
  const next: Doc = { ...commercial, conversation: { ...conversation, objections } };
  if (stops !== stored) next.follow_up = { ...followUp, stop_conditions: stops };
  return next;
}

/** Whether the client has ever set how the agent sells: without it the agent keeps its usual way. */
export function styleChosen(commercial: Doc): boolean {
  const conversation = obj(commercial.conversation);
  return obj(conversation.recommendation).always_end_with !== undefined || obj(obj(commercial.communication).message).max_questions !== undefined;
}

export function setEmoji(commercial: Doc, on: boolean): Doc {
  const communication = obj(commercial.communication);
  return { ...commercial, communication: { ...communication, message: { ...obj(communication.message), emoji: on ? "light" : "none" } } };
}
