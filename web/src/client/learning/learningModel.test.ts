import { describe, expect, it } from "vitest";
import { emptyReason, groupNotes, sentenceKey, shown, type LearnedNote, type LearningAnswer } from "./learningModel";
import { enLearn } from "../../i18n/client.learn.en";
import { frLearn } from "../../i18n/client.learn.fr";

const note = (code: string, over: Partial<LearnedNote> = {}): LearnedNote => ({
  id: code,
  code,
  kind: "do",
  state: "active",
  evidence: "medium",
  cases: 5,
  against: 0,
  product: null,
  confirmed: false,
  off_by: null,
  reason: "",
  ...over,
});

const answer = (notes: LearnedNote[], studied = 0): LearningAnswer => ({ counts: { studied, achieved: 0, unfavourable: 0, needed: 5 }, notes });

describe("groupNotes", () => {
  it("puts what the agent follows, what it only watches and what was set aside apart", () => {
    const groups = groupNotes([
      note("short_messages", { cases: 6 }),
      note("acknowledge_first", { cases: 9 }),
      note("long_messages", { kind: "avoid" }),
      note("easy_next_step", { state: "watching", cases: 3 }),
      note("doubt_ignored", { kind: "avoid", state: "off", off_by: "client" }),
    ]);
    expect(groups.works.map((n) => n.code)).toEqual(["acknowledge_first", "short_messages"]);
    expect(groups.avoids.map((n) => n.code)).toEqual(["long_messages"]);
    expect(groups.watching.map((n) => n.code)).toEqual(["easy_next_step"]);
    expect(groups.off.map((n) => n.code)).toEqual(["doubt_ignored"]);
  });
});

describe("emptyReason", () => {
  it("says it is too early while few conversations were studied", () => {
    expect(emptyReason(answer([], 2))).toBe("few");
  });
  it("says nothing stands out once enough were studied", () => {
    expect(emptyReason(answer([], 12))).toBe("none");
  });
  it("says nothing when there is something to show", () => {
    expect(emptyReason(answer([note("short_messages")], 12))).toBeNull();
  });
});

describe("points the screen knows", () => {
  it("hides a point it has no sentence for rather than showing a code", () => {
    const list = [note("short_messages"), note("something_new")];
    expect(sentenceKey(list[1]!)).toBeNull();
    expect(shown(list).map((n) => n.code)).toEqual(["short_messages"]);
  });
});

describe("copy", () => {
  it("has a sentence in both languages for every point, and the two languages match", () => {
    const codes = Object.keys(enLearn).filter((k) => k.startsWith("rv.learn.code."));
    expect(codes).toHaveLength(16);
    expect(Object.keys(frLearn).sort()).toEqual(Object.keys(enLearn).sort());
    for (const key of codes) expect(sentenceKey(note(key.replace("rv.learn.code.", "")))).toBe(key);
  });

  it("uses no technical word", () => {
    const text = [...Object.values(enLearn), ...Object.values(frLearn)].join(" ").toLowerCase();
    for (const word of ["threshold", "seuil", "correlation", "corrélation", "model", "modèle", "llm", "token", "algorithm"]) {
      expect(text).not.toContain(word);
    }
  });
});
