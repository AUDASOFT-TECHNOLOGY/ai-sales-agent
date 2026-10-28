import { describe, expect, it } from "vitest";
import { goalOf, persuasionOf, principlesOf, setGoal, setPersona, setPersuasion, setPrinciples, withLanguages } from "./agentModel";

describe("languages the server requires", () => {
  it("adds the fallback only when none is set", () => {
    expect(withLanguages({}, "fr")).toEqual({ communication: { languages: { supported: ["fr"], default: "fr" } } });
    const set = { communication: { languages: { supported: ["en", "ar"], default: "ar" } } };
    expect(withLanguages(set, "fr")).toBe(set);
  });
});

describe("main goal", () => {
  it("is written as the primary goal and read back", () => {
    const next = setGoal({}, "meeting", "Book a meeting");
    expect(next.objectives).toEqual({ goals: [{ id: "main", label: "Book a meeting", kind: "meeting" }], primary: "main" });
    expect(goalOf(next)).toBe("meeting");
  });
  it("keeps the other goals and drops only its own", () => {
    const other = { id: "x", label: "X", kind: "handoff" };
    const base = { objectives: { goals: [other], primary: "x" } };
    const next = setGoal(setGoal(base, "conversion", "Sell"), "", "");
    expect((next.objectives as { goals: unknown[] }).goals).toEqual([other]);
  });
});

describe("agent settings", () => {
  it("keeps what is already in the tone when the name changes", () => {
    const base = { communication: { tone: { style: "warm" }, languages: { supported: ["fr"], default: "fr" } } };
    const named = setPersona(base, " Sarah ");
    expect((named.communication as { tone: unknown }).tone).toEqual({ style: "warm", persona_name: "Sarah" });
    expect((setPersona(named, "").communication as { tone: unknown }).tone).toEqual({ style: "warm" });
  });
  it("writes the instructions without blanks", () => {
    expect(principlesOf(setPrinciples({}, ["Be brief", "  ", " No promises "]))).toEqual(["Be brief", "No promises"]);
  });
  it("round-trips the persuasion choices", () => {
    const value = { answerFirst: false, nextStep: false, maxQuestions: 2, emoji: false };
    expect(persuasionOf(setPersuasion({}, value))).toEqual(value);
    expect(persuasionOf({})).toEqual({ answerFirst: true, nextStep: true, maxQuestions: 1, emoji: true });
  });
});
