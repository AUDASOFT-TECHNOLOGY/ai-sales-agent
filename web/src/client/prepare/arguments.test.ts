import { describe, expect, it } from "vitest";
import { ARGUMENT_TAG, argumentsOf, principlesOf, setArguments, setEmoji, setPrinciples, styleChosen } from "./agentModel";

describe("ways to convince", () => {
  it("are written as tagged principles and read back without the tag", () => {
    const next = setArguments({}, ["14-day guarantee", " ", "Free first lesson"]);
    expect((next.communication as { principles: string[] }).principles).toEqual([ARGUMENT_TAG + "14-day guarantee", ARGUMENT_TAG + "Free first lesson"]);
    expect(argumentsOf(next)).toEqual(["14-day guarantee", "Free first lesson"]);
  });
  it("never drop the instructions, and the instructions never drop them", () => {
    const both = setPrinciples(setArguments({}, ["Guarantee"]), ["Be brief"]);
    expect(principlesOf(both)).toEqual(["Be brief"]);
    expect(argumentsOf(both)).toEqual(["Guarantee"]);
    const again = setArguments(both, []);
    expect(principlesOf(again)).toEqual(["Be brief"]);
    expect(argumentsOf(again)).toEqual([]);
  });
});

describe("no style imposed", () => {
  it("is what a document that never set the style says", () => {
    expect(styleChosen({})).toBe(false);
    expect(styleChosen({ communication: { message: { max_questions: 0 } } })).toBe(true);
  });
  it("lets the emoji be changed alone, leaving the style settings untouched", () => {
    const next = setEmoji({}, false);
    expect(next).toEqual({ communication: { message: { emoji: "none" } } });
    expect(styleChosen(next)).toBe(false);
  });
});
