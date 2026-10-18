import { describe, expect, it } from "vitest";
import { applyMethodForm, blankMethod, forgetMethod, formOfMethod, methodProblems, putMethod, removeMethod } from "./paymentModel";
import type { Doc } from "../settingsModel";

const bank: Doc = {
  id: "virement",
  status: "active",
  kind: "offline",
  label: { fr: "Virement", en: "Transfer" },
  provider: "internal-reference",
  instructions: [
    {
      id: "tn",
      applies_to: { countries: ["Tunisie"], currencies: ["TND"], products: ["p1"] },
      text: { fr: "  Payez sur ce compte  " },
      fields: [{ label: "Banque", value: { fr: "BIAT" } }, { label: "RIB", value: "08 006 0123456789 12" }],
    },
  ],
  confirmation: { mode: "manual", note: "Sous 24 h" },
};

describe("payment method form", () => {
  it("reads a method", () => {
    const form = formOfMethod(bank, "fr");
    expect(form.kind).toBe("offline");
    expect(form.label).toBe("Virement");
    expect(form.instructions[0]).toMatchObject({ countries: "Tunisie", currencies: "TND", text: "  Payez sur ce compte  " });
    expect(form.instructions[0]!.fields).toEqual([{ label: "Banque", value: "BIAT" }, { label: "RIB", value: "08 006 0123456789 12" }]);
    expect(form.note).toBe("Sous 24 h");
  });

  it("keeps the exact payment values character for character", () => {
    const form = formOfMethod(bank, "fr");
    const next = applyMethodForm(bank, form, "fr");
    const instruction = (next.instructions as Doc[])[0]!;
    expect((instruction.text as Doc).fr).toBe("  Payez sur ce compte  ");
    expect((instruction.fields as Doc[])[1]!.value).toBe("08 006 0123456789 12");
  });

  it("keeps what the form does not edit (the other languages, the scope by product, reference data)", () => {
    const form = { ...formOfMethod(bank, "fr"), label: "Virement bancaire" };
    const next = applyMethodForm(bank, form, "fr");
    expect(next.label).toEqual({ fr: "Virement bancaire", en: "Transfer" });
    expect(next.provider).toBe("internal-reference");
    expect(((next.instructions as Doc[])[0]!.applies_to as Doc).products).toEqual(["p1"]);
  });

  it("always confirms by hand, whatever the original said", () => {
    const next = applyMethodForm({ ...bank, confirmation: { mode: "none" } }, formOfMethod(bank, "fr"), "fr");
    expect((next.confirmation as Doc).mode).toBe("manual");
  });

  it("writes a new online method with its link", () => {
    const form = { ...blankMethod("lien", "online"), label: "Carte" };
    form.instructions[0]!.url = "https://pay.example/order";
    const next = applyMethodForm(undefined, { ...form, id: "carte" }, "fr");
    expect(next).toMatchObject({ id: "carte", kind: "online", status: "active", label: "Carte" });
    expect((next.instructions as Doc[])[0]).toMatchObject({ id: "main", url: "https://pay.example/order" });
    expect((next.instructions as Doc[])[0]!.text).toBeUndefined();
  });

  it("drops instructions that say nothing and scopes that are empty", () => {
    const form = formOfMethod(bank, "fr");
    form.instructions[0]!.countries = "";
    form.instructions[0]!.currencies = "";
    form.instructions.push({ id: "x", countries: "", currencies: "", url: "", text: "", fields: [] });
    const next = applyMethodForm(bank, form, "fr");
    expect(next.instructions as Doc[]).toHaveLength(1);
    expect(((next.instructions as Doc[])[0]!.applies_to as Doc)).toEqual({ products: ["p1"] });
  });
});

describe("payment method problems", () => {
  it("needs a name", () => {
    expect(methodProblems({ ...blankMethod("a", "offline"), label: "" })).toContain("label");
  });
  it("needs a https link for an online method", () => {
    const form = { ...blankMethod("a", "online"), label: "Carte" };
    expect(methodProblems(form)).toContain("onlineNeedsLink");
    form.instructions[0]!.url = "http://pay.example";
    expect(methodProblems(form)).toContain("url");
    form.instructions[0]!.url = "https://pay.example";
    expect(methodProblems(form)).toEqual([]);
  });
  it("needs written instructions or a detail for an offline method", () => {
    const form = { ...blankMethod("a", "offline"), label: "Virement" };
    expect(methodProblems(form)).toContain("offlineNeedsDetails");
    form.instructions[0]!.fields = [{ label: "IBAN", value: "TN59…" }];
    expect(methodProblems(form)).toEqual([]);
  });
  it("checks currency codes", () => {
    const form = { ...blankMethod("a", "offline"), label: "V" };
    form.instructions[0]!.text = "Payez";
    form.instructions[0]!.currencies = "TND, EURO";
    expect(methodProblems(form)).toContain("currency");
  });
});

describe("payment methods in the document", () => {
  const commercial: Doc = {
    payment: { methods: [bank] },
    catalog: { products: [{ id: "p1", payment: { online: { enabled: true, methods: ["carte"] }, offline: { enabled: true, methods: ["virement", "cash"] } } }, { id: "p2" }] },
  };
  it("adds, replaces and removes", () => {
    const added = putMethod(commercial, { id: "cash", kind: "offline" });
    expect(((added.payment as Doc).methods as Doc[]).map((m) => m.id)).toEqual(["virement", "cash"]);
    const replaced = putMethod(added, { ...bank, status: "inactive" });
    expect(((replaced.payment as Doc).methods as Doc[])[0]!.status).toBe("inactive");
    expect(((removeMethod(added, "cash").payment as Doc).methods as Doc[]).map((m) => m.id)).toEqual(["virement"]);
  });
  it("forgets a removed method in the products that listed it, and touches no other product", () => {
    const next = forgetMethod(commercial, "virement");
    const [p1, p2] = (next.catalog as Doc).products as Doc[];
    expect(((p1!.payment as Doc).offline as Doc).methods).toEqual(["cash"]);
    expect(p2).toEqual({ id: "p2" });
  });
});
