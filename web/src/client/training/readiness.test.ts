import { describe, expect, it } from "vitest";
import { readinessOf } from "./readiness";
import { DEFAULT_PLAYBOOK, blankLesson, type Lesson } from "./trainingModel";

const many = (kind: Lesson["kind"], n: number) => Array.from({ length: n }, (_, i) => blankLesson(kind, { text: `t${i}`, when: kind === "habit" || kind === "never" ? "" : `w${i}` }));
const full = (): Lesson[] => [...many("habit", 3), ...many("never", 2), ...many("objection", 8), ...many("faq", 5), ...many("example", 3)];
const run = (over: Partial<Parameters<typeof readinessOf>[0]> = {}) => readinessOf({ commercial: {}, playbook: DEFAULT_PLAYBOOK, lessons: [], ...over });

describe("readiness", () => {
  it("starts at zero with nothing taught", () => {
    const r = run();
    expect(r.score).toBe(0);
    expect(r.level).toBe("start");
    expect(r.next).toEqual(["objections", "method", "pitch"]);
  });

  it("reaches 100 when every part is covered", () => {
    const r = run({ playbook: { ...DEFAULT_PLAYBOOK, chosen: true, pitch: "We help.", ideal_customer: "Shops" }, lessons: full() });
    expect(r.score).toBe(100);
    expect(r.level).toBe("excellent");
    expect(r.next).toEqual([]);
  });

  it("does not ask for what belongs to each product: documents and testimonials", () => {
    expect(run().dimensions.map((d) => d.id)).toEqual(["method", "pitch", "voice", "redlines", "objections", "faq", "examples"]);
  });

  it("counts a half-written lesson for nothing", () => {
    const half = [blankLesson("objection", { when: "Too expensive" }), blankLesson("habit", { text: " " })];
    expect(run({ lessons: half }).score).toBe(0);
  });

  it("asks for as many answers to doubts as the client configured, at most eight", () => {
    const commercial = { conversation: { objections: Array.from({ length: 12 }, (_, i) => ({ code: `o${i}` })) } };
    const r = run({ commercial, lessons: many("objection", 4) });
    expect(r.dimensions.find((d) => d.id === "objections")).toMatchObject({ have: 4, goal: 8, ratio: 0.5 });
  });

  it("points first to what is worth the most", () => {
    const r = run({ playbook: { ...DEFAULT_PLAYBOOK, chosen: true }, lessons: [...many("habit", 3), ...many("never", 2)] });
    expect(r.next[0]).toBe("objections");
    expect(r.next).not.toContain("voice");
  });
});
