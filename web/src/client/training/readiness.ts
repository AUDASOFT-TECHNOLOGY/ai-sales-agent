/** How ready the agent is to sell, from what the client taught it: seven things that make a salesperson
 *  convincing, each worth a share of 100. The score is a guide to what to do next, never a promise of
 *  results: it counts only what exists (a lesson written), not how good it is. What belongs to each
 *  product (its documents, its testimonials, its arguments) is done in the product sheet, not here. */

import type { Doc } from "../settingsModel";
import { configuredObjections, countOf, type Lesson, type Playbook } from "./trainingModel";

export type DimensionId = "method" | "pitch" | "voice" | "redlines" | "objections" | "faq" | "examples";
export type Level = "start" | "progress" | "strong" | "excellent";

export interface Dimension {
  id: DimensionId;
  weight: number;
  /** 0 (nothing) to 1 (enough). */
  ratio: number;
  /** What there is and what is aimed at, for "2 of 5". */
  have: number;
  goal: number;
}

export interface Readiness {
  score: number;
  level: Level;
  dimensions: Dimension[];
  /** The things worth doing next, the most valuable first. */
  next: DimensionId[];
}

const WEIGHTS: Record<DimensionId, number> = { method: 20, pitch: 10, voice: 10, redlines: 10, objections: 30, faq: 10, examples: 10 };

const part = (have: number, goal: number) => (goal <= 0 ? 1 : Math.min(1, have / goal));

export function readinessOf(input: { commercial: Doc; playbook: Playbook; lessons: Lesson[] }): Readiness {
  const { commercial, playbook, lessons } = input;
  const objectionGoal = Math.max(4, Math.min(8, configuredObjections(commercial).length));
  const pitchRatio = (playbook.pitch.trim() ? 0.6 : 0) + (playbook.ideal_customer.trim() ? 0.4 : 0);

  const raw: Array<[DimensionId, number, number]> = [
    ["method", playbook.chosen ? 1 : 0, 1],
    ["pitch", pitchRatio * 2, 2],
    ["voice", countOf(lessons, "habit"), 3],
    ["redlines", countOf(lessons, "never"), 2],
    ["objections", countOf(lessons, "objection"), objectionGoal],
    ["faq", countOf(lessons, "faq"), 5],
    ["examples", countOf(lessons, "example"), 3],
  ];
  const dimensions: Dimension[] = raw.map(([id, have, goal]) => ({ id, weight: WEIGHTS[id], ratio: part(have, goal), have: Math.min(have, goal), goal }));
  const score = Math.round(dimensions.reduce((sum, d) => sum + d.weight * d.ratio, 0));
  const next = dimensions
    .filter((d) => d.ratio < 1)
    .sort((a, b) => b.weight * (1 - b.ratio) - a.weight * (1 - a.ratio))
    .slice(0, 3)
    .map((d) => d.id);
  const level: Level = score < 35 ? "start" : score < 65 ? "progress" : score < 85 ? "strong" : "excellent";
  return { score, level, dimensions, next };
}
