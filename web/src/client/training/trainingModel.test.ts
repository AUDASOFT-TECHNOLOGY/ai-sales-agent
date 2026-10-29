import { describe, expect, it } from "vitest";
import { DEFAULT_PLAYBOOK, LESSON_MAX, TEXT_MAX, blankLesson, configuredObjections, countOf, holdsExactValue, isComplete, isHalf, lessonsOf, orphans, playbookOf, productOptions, setTraining, signature, situationBefore, trainingOf, type Lesson } from "./trainingModel";

const plain = (lessons: Lesson[]) => lessons.map((l) => Object.fromEntries(Object.entries(l).filter(([key]) => key !== "uid")));
const withLessons = (...lessons: Lesson[]) => ({ playbook: DEFAULT_PLAYBOOK, lessons });

describe("lessons", () => {
  it("are written under communication.training and read back", () => {
    const next = setTraining(
      { communication: { principles: ["Be brief"] } },
      withLessons(
        blankLesson("habit", { text: " Write like a chat " }),
        blankLesson("never", { text: "Promise a result" }),
        blankLesson("objection", { when: "Too expensive", text: "I get it.", why: "Price hides doubt", product: "alpha" }),
        blankLesson("faq", { when: "Refund?", text: "Yes, within 14 days." }),
        blankLesson("proof", { text: "Doubled my leads", source: "Leila" }),
      ),
    );
    expect(next.communication).toEqual({
      principles: ["Be brief"],
      training: [
        { kind: "habit", text: "Write like a chat" },
        { kind: "never", text: "Promise a result" },
        { kind: "objection", when: "Too expensive", text: "I get it.", why: "Price hides doubt", product: "alpha" },
        { kind: "faq", when: "Refund?", text: "Yes, within 14 days." },
        { kind: "proof", text: "Doubled my leads", source: "Leila" },
      ],
    });
    expect(plain(lessonsOf(next))).toEqual([
      { kind: "habit", text: "Write like a chat", when: "", product: "", source: "", why: "" },
      { kind: "never", text: "Promise a result", when: "", product: "", source: "", why: "" },
      { kind: "objection", text: "I get it.", when: "Too expensive", product: "alpha", source: "", why: "Price hides doubt" },
      { kind: "faq", text: "Yes, within 14 days.", when: "Refund?", product: "", source: "", why: "" },
      { kind: "proof", text: "Doubled my leads", when: "", product: "", source: "Leila", why: "" },
    ]);
  });

  it("leave out what is empty or half-written", () => {
    const lessons = [blankLesson("habit", { text: "  " }), blankLesson("example", { when: "Price?" }), blankLesson("faq", { text: "Hello" }), blankLesson("objection")];
    expect(setTraining({}, withLessons(...lessons)).communication).toEqual({});
    expect(isHalf(lessons[1]!)).toBe(true);
    expect(isHalf(lessons[3]!)).toBe(false);
    expect(isComplete(blankLesson("example", { when: "a", text: "b" }))).toBe(true);
  });

  it("never carry a product, a source or a reason where the server refuses one", () => {
    const next = setTraining({}, withLessons(blankLesson("habit", { text: "x", product: "alpha", source: "s", why: "w" }), blankLesson("example", { when: "w", text: "t", source: "s" })));
    expect(next.communication).toEqual({ training: [{ kind: "habit", text: "x" }, { kind: "example", when: "w", text: "t" }] });
  });

  it("keep the rest of the document and take the keys out when nothing is left", () => {
    const base = { catalog: { products: [] }, communication: { tone: { persona_name: "Sam" }, training: [{ kind: "habit", text: "x" }], playbook: { approach: "direct" } } };
    const next = setTraining(base, trainingOf({ communication: {} }));
    expect(next).toEqual({ catalog: { products: [] }, communication: { tone: { persona_name: "Sam" } } });
  });

  it("respect the limits of the server", () => {
    const many = Array.from({ length: LESSON_MAX + 5 }, (_, i) => blankLesson("faq", { when: `w${i}`, text: "t".repeat(TEXT_MAX + 50) }));
    const lessons = lessonsOf(setTraining({}, withLessons(...many)));
    expect(lessons).toHaveLength(LESSON_MAX);
    expect(lessons[0]!.text).toHaveLength(TEXT_MAX);
  });

  it("ignore what they do not understand when reading", () => {
    expect(lessonsOf({ communication: { training: [{ kind: "x", text: "a" }, { kind: "example", text: "no situation" }, { kind: "faq", text: "no question" }, 3] } })).toEqual([]);
    const lessons = [blankLesson("habit", { text: "a" }), blankLesson("habit", { text: " " }), blankLesson("example", { when: "w", text: "t" }), blankLesson("example", { when: "w" })];
    expect(countOf(lessons)).toBe(2);
    expect(countOf(lessons, "habit")).toBe(1);
  });

  it("give each lesson its own key for the screen, never saved", () => {
    const [a, b] = [blankLesson("habit"), blankLesson("habit")];
    expect(a.uid).not.toBe(b.uid);
    expect(JSON.stringify(setTraining({}, withLessons({ ...a, text: "x" })))).not.toContain("uid");
  });
});

describe("what a lesson may not hold", () => {
  it("spots a price, a link and an e-mail address, as the server does", () => {
    for (const text of ["It costs 49 EUR", "only 20€", "$ 30", "see https://example.org", "www.example.org", "write to a@b.co", "pay to 123456789012"]) expect(holdsExactValue(text), text).toBe(true);
    for (const text of ["Yes, within 14 days.", "We start in 2 weeks", "Trois séances par semaine"]) expect(holdsExactValue(text), text).toBe(false);
  });

  it("is not saved", () => {
    const next = setTraining({}, withLessons(blankLesson("faq", { when: "How much?", text: "It costs 49 EUR" }), blankLesson("habit", { text: "Be kind" })));
    expect(next.communication).toEqual({ training: [{ kind: "habit", text: "Be kind" }] });
  });
});

describe("the sales method", () => {
  it("is read with the defaults for what is missing or unknown", () => {
    expect(playbookOf({})).toEqual(DEFAULT_PLAYBOOK);
    const read = playbookOf({ communication: { playbook: { approach: "direct", closing: "weird", pitch: "We help." } } });
    expect(read).toMatchObject({ approach: "direct", closing: "choice", pitch: "We help.", chosen: true });
  });

  it("is written only once the client made a choice, and not when nothing was said", () => {
    expect(setTraining({}, withLessons()).communication).toEqual({});
    expect(setTraining({}, { playbook: { ...DEFAULT_PLAYBOOK, chosen: true }, lessons: [] }).communication).toEqual({ playbook: { approach: "consultative", closing: "choice", urgency: "none", social_proof: "when_relevant" } });
    expect(setTraining({}, { playbook: { ...DEFAULT_PLAYBOOK, approach: "guide", chosen: true, pitch: " We help. ", ideal_customer: "Shops" }, lessons: [] }).communication).toEqual({
      playbook: { approach: "guide", closing: "choice", urgency: "none", social_proof: "when_relevant", pitch: "We help.", ideal_customer: "Shops" },
    });
  });

  it("makes a document differ when only the method changed", () => {
    const base = {};
    const a = trainingOf(base);
    expect(signature(base, a)).toBe(signature(base, trainingOf(base)));
    expect(signature(base, { ...a, playbook: { ...a.playbook, closing: "soft", chosen: true } })).not.toBe(signature(base, a));
  });
});

describe("the catalog", () => {
  const commercial = {
    catalog: { products: [{ id: "alpha", name: { fr: "Alpha", en: "Alpha EN" }, objections: [{ code: "price" }] }, { id: "old", name: "Old", status: "inactive" }, { id: "beta" }] },
    conversation: { objections: [{ code: "later" }, { code: "price" }] },
  };

  it("lists the products a lesson can be about", () => {
    expect(productOptions(commercial, "fr")).toEqual([{ id: "alpha", name: "Alpha" }, { id: "beta", name: "beta" }]);
  });

  it("finds the lessons about a product that is gone", () => {
    const products = productOptions(commercial, "en");
    const lessons = [blankLesson("faq", { product: "alpha" }), blankLesson("faq", { product: "ghost" }), blankLesson("faq")];
    expect(orphans(lessons, products).map((l) => l.product)).toEqual(["ghost"]);
  });

  it("lists the objections already configured, once each", () => {
    expect(configuredObjections(commercial).sort()).toEqual(["later", "price"]);
  });
});

describe("a test conversation", () => {
  it("finds the situation an agent message answered", () => {
    const thread = [
      { direction: "outbound", text: "Hello" },
      { direction: "inbound", text: " How much? " },
      { direction: "outbound", text: "120 TND" },
    ];
    expect(situationBefore(thread, 2)).toBe("How much?");
    expect(situationBefore(thread, 0)).toBe("");
  });
});
