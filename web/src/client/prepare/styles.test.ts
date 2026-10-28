import { describe, expect, it } from "vitest";
import { persuasionOf, refusalOf, setRefusal, styleOf, withStyle } from "./agentModel";

const wording = { response: "Stay kind.", examples: ["No thanks"] };

describe("selling style", () => {
  it("is read from the settings and written back as the same mix", () => {
    for (const style of ["advisor", "direct", "listener", "informative"] as const) {
      expect(styleOf(withStyle(persuasionOf({}), style))).toBe(style);
    }
    expect(styleOf(persuasionOf({}))).toBe("advisor");
  });
  it("reads a hand-made mix as custom and keeps it until a style is chosen", () => {
    const custom = persuasionOf({ conversation: { recommendation: { always_end_with: "none" } }, communication: { message: { max_questions: 1 } } });
    expect(styleOf(custom)).toBe("");
  });
});

describe("a prospect who is not interested", () => {
  it("stops by default and writes nothing to get there", () => {
    expect(refusalOf({})).toBe("stop");
    expect(setRefusal({}, "stop", wording)).toEqual({ conversation: { objections: [] } });
  });
  it("tries once or twice, then drops, and no longer closes on the first refusal", () => {
    const once = setRefusal({}, "once", wording);
    expect(refusalOf(once)).toBe("once");
    expect((once.conversation as { objections: unknown[] }).objections).toEqual([{ code: "not_interested", examples: ["No thanks"], response: "Stay kind.", max_rebuttals: 1, then: "drop" }]);
    expect((once.follow_up as { stop_conditions: string[] }).stop_conditions).not.toContain("explicit_no");
    expect(refusalOf(setRefusal({}, "twice", wording))).toBe("twice");
  });
  it("hands over without any attempt", () => {
    const handed = setRefusal({}, "handoff", wording);
    expect(refusalOf(handed)).toBe("handoff");
    expect((handed.conversation as { objections: Array<{ max_rebuttals: number }> }).objections[0]!.max_rebuttals).toBe(0);
  });
  it("puts the closing rule back and keeps the other objections when going back to stop", () => {
    const other = { code: "price", examples: [], response: "x", max_rebuttals: 1, then: "follow_up" };
    const tried = setRefusal({ conversation: { objections: [other] } }, "once", wording);
    const back = setRefusal(tried, "stop", wording);
    expect(refusalOf(back)).toBe("stop");
    expect((back.conversation as { objections: unknown[] }).objections).toEqual([other]);
    expect((back.follow_up as { stop_conditions: string[] }).stop_conditions).toContain("explicit_no");
  });
});
