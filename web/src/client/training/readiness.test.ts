import { describe, expect, it } from "vitest";
import type { ProductMaterial } from "../api";
import { readinessOf } from "./readiness";
import { DEFAULT_PLAYBOOK, blankLesson, type Lesson } from "./trainingModel";

const products = [{ id: "a", name: "A" }, { id: "b", name: "B" }];
const material = (id: string, readable: number, testimonials = 0): ProductMaterial => ({ product_id: id, total: readable, readable, readable_chars: readable * 100, testimonials, by_category: {}, unread: [] });
const many = (kind: Lesson["kind"], n: number) => Array.from({ length: n }, (_, i) => blankLesson(kind, { text: `t${i}`, when: kind === "habit" || kind === "never" || kind === "proof" ? "" : `w${i}` }));

const full = (): Lesson[] => [...many("habit", 3), ...many("never", 2), ...many("objection", 8), ...many("faq", 5), ...many("example", 3), ...many("proof", 2)];

describe("readiness", () => {
  it("starts at zero with nothing taught and nothing to read", () => {
    const r = readinessOf({ commercial: {}, playbook: DEFAULT_PLAYBOOK, lessons: [], products, material: [] });
    expect(r.score).toBe(0);
    expect(r.level).toBe("start");
    expect(r.next).toEqual(["objections", "method", "material"]);
  });

  it("reaches 100 when every part is covered", () => {
    const r = readinessOf({ commercial: {}, playbook: { ...DEFAULT_PLAYBOOK, chosen: true, pitch: "We help.", ideal_customer: "Shops" }, lessons: full(), products, material: [material("a", 2), material("b", 1)] });
    expect(r.score).toBe(100);
    expect(r.level).toBe("excellent");
    expect(r.next).toEqual([]);
  });

  it("counts a half-written lesson for nothing", () => {
    const half = [blankLesson("objection", { when: "Too expensive" }), blankLesson("habit", { text: " " })];
    expect(readinessOf({ commercial: {}, playbook: DEFAULT_PLAYBOOK, lessons: half, products, material: [] }).score).toBe(0);
  });

  it("counts material only when the agent can really read it, per product of the catalog", () => {
    const only = (m: ProductMaterial[]) => readinessOf({ commercial: {}, playbook: DEFAULT_PLAYBOOK, lessons: [], products, material: m }).dimensions.find((d) => d.id === "material")!;
    expect(only([material("a", 0)]).have).toBe(0);
    expect(only([material("a", 3)]).ratio).toBe(0.5);
    expect(only([material("a", 3), material("gone", 5)]).ratio).toBe(0.5);
    expect(only([material("a", 3), material("b", 1)]).ratio).toBe(1);
  });

  it("counts a readable testimonial as proof", () => {
    const proof = (m: ProductMaterial[], lessons: Lesson[] = []) => readinessOf({ commercial: {}, playbook: DEFAULT_PLAYBOOK, lessons, products, material: m }).dimensions.find((d) => d.id === "proof")!;
    expect(proof([material("a", 1, 2)]).ratio).toBe(1);
    expect(proof([material("a", 1, 1)], many("proof", 1)).ratio).toBe(1);
    expect(proof([]).ratio).toBe(0);
  });

  it("does not ask for proof from a client who wants none in conversations", () => {
    const r = readinessOf({ commercial: {}, playbook: { ...DEFAULT_PLAYBOOK, social_proof: "never" }, lessons: [], products, material: [] });
    expect(r.dimensions.find((d) => d.id === "proof")!.ratio).toBe(1);
  });

  it("asks for as many answers to doubts as the client configured, at most eight", () => {
    const commercial = { conversation: { objections: Array.from({ length: 12 }, (_, i) => ({ code: `o${i}` })) } };
    const r = readinessOf({ commercial, playbook: DEFAULT_PLAYBOOK, lessons: many("objection", 4), products, material: null });
    expect(r.dimensions.find((d) => d.id === "objections")).toMatchObject({ have: 4, goal: 8, ratio: 0.5 });
  });

  it("points first to what is worth the most", () => {
    const r = readinessOf({ commercial: {}, playbook: { ...DEFAULT_PLAYBOOK, chosen: true }, lessons: [...many("habit", 3), ...many("never", 2)], products, material: [] });
    expect(r.next[0]).toBe("objections");
    expect(r.next).not.toContain("voice");
  });
});
