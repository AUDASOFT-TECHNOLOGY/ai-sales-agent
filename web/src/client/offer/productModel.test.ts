import { describe, expect, it } from "vitest";
import {
  applyProductForm,
  blankProduct,
  formOfProduct,
  priceSummary,
  productProblems,
  putProduct,
  slugify,
  uniqueSlug,
  type ProductForm,
} from "./productModel";
import type { Doc } from "../settingsModel";

const stored: Doc = {
  id: "formation-vente",
  status: "active",
  name: { fr: "Formation vente", en: "Sales course" },
  summary: "Apprenez à vendre",
  pricing: { model: "fixed", amount: 120, currency: "TND", disclose: "after_interest", variants: [{ id: "fr", applies_to: { countries: ["France"] }, model: "fixed", amount: 90, currency: "EUR", disclose: "always" }] },
  follow_up: { cadence: "keep-me" },
  workflow: "w1",
  objections: [{ code: "cher", examples: ["C'est cher"], response: "Voici la valeur", max_rebuttals: 2, then: "handoff" }],
  sales_arguments: [{ id: "a1", text: "Résultats rapides", use_when: { goal: ["sell"] } }],
};

describe("slugs", () => {
  it("makes URL-safe ids from names, without accents", () => {
    expect(slugify("Formation Été 2025 !")).toBe("formation-ete-2025");
    expect(slugify("   ")).toBe("");
  });
  it("never repeats an id already taken", () => {
    expect(uniqueSlug("cours", ["cours", "cours-2"], "produit")).toBe("cours-3");
    expect(uniqueSlug("", [], "produit")).toBe("produit");
  });
});

describe("product form", () => {
  it("reads the stored product in the default language", () => {
    const form = formOfProduct(stored, "fr");
    expect(form.name).toBe("Formation vente");
    expect(form.priceModel).toBe("fixed");
    expect(form.amount).toBe("120");
    expect(form.variants).toEqual([{ id: "fr", countries: "France", model: "fixed", amount: "90", maxAmount: "", currency: "EUR" }]);
    expect(form.objections[0]).toMatchObject({ code: "cher", example: "C'est cher" });
    expect(form.payMode).toBe("all");
  });

  it("keeps what the form does not edit, and the other languages", () => {
    const form = { ...formOfProduct(stored, "fr"), name: "Formation vente 2", description: "Détails" };
    const next = applyProductForm(stored, form, "fr");
    expect(next.name).toEqual({ fr: "Formation vente 2", en: "Sales course" });
    expect(next.follow_up).toEqual({ cadence: "keep-me" });
    expect(next.workflow).toBe("w1");
    expect(next.description).toBe("Détails");
  });

  it("keeps the hidden settings of objections, arguments and price variants", () => {
    const next = applyProductForm(stored, formOfProduct(stored, "fr"), "fr");
    expect(next.objections).toEqual([{ code: "cher", examples: ["C'est cher"], response: "Voici la valeur", max_rebuttals: 2, then: "handoff" }]);
    expect(next.sales_arguments).toEqual([{ id: "a1", text: "Résultats rapides", use_when: { goal: ["sell"] } }]);
    const pricing = next.pricing as Doc;
    expect((pricing.variants as Doc[])[0]).toMatchObject({ id: "fr", disclose: "always", amount: 90, currency: "EUR", applies_to: { countries: ["France"] } });
  });

  it("removes optional parts the client emptied instead of saving them empty", () => {
    const form: ProductForm = { ...formOfProduct(stored, "fr"), objections: [], arguments: [], variants: [], url: "", benefits: [] };
    const next = applyProductForm(stored, form, "fr");
    expect(next.objections).toBeUndefined();
    expect(next.sales_arguments).toBeUndefined();
    expect(next.url).toBeUndefined();
    expect((next.pricing as Doc).variants).toBeUndefined();
    expect(next.value).toBeUndefined();
  });

  it("writes a new product with only what was filled in", () => {
    const form: ProductForm = { ...blankProduct("coaching"), name: "Coaching", summary: "Un coaching" };
    const next = applyProductForm(undefined, form, "fr");
    expect(next).toEqual({ id: "coaching", status: "active", name: "Coaching", summary: "Un coaching" });
  });

  it("writes a price the way the Brain expects, with no amount for 'on request'", () => {
    const priced: ProductForm = { ...blankProduct("x"), name: "X", summary: "S", priceModel: "range", amount: "10", maxAmount: "20", currency: "tnd" };
    expect((applyProductForm(undefined, priced, "fr").pricing as Doc)).toMatchObject({ model: "range", amount: 10, max_amount: 20, currency: "TND" });
    const free: ProductForm = { ...priced, priceModel: "on_request" };
    const pricing = applyProductForm(undefined, free, "fr").pricing as Doc | undefined;
    expect(pricing?.amount).toBeUndefined();
    expect(pricing?.currency).toBeUndefined();
  });

  it("sends the order link only for the 'order link' next step, using the product page by default", () => {
    const form: ProductForm = { ...blankProduct("x"), name: "X", summary: "S", url: "https://x.example/buy", ctaKind: "enrol_link" };
    const cta = ((applyProductForm(undefined, form, "fr").strategy as Doc).cta as Doc);
    expect(cta).toEqual({ kind: "enrol_link", url: "https://x.example/buy" });
    const other = applyProductForm(undefined, { ...form, ctaKind: "request_callback" }, "fr");
    expect(((other.strategy as Doc).cta as Doc).url).toBeUndefined();
  });

  it("declares payment options only when the client chose them for this product", () => {
    const inherit = applyProductForm(stored, formOfProduct(stored, "fr"), "fr");
    expect(inherit.payment).toBeUndefined();
    const custom: ProductForm = { ...formOfProduct(stored, "fr"), payMode: "custom", payOnline: true, payOnlineMethods: ["carte"], payOffline: true, receiptRequired: true };
    expect(applyProductForm(stored, custom, "fr").payment).toEqual({
      online: { enabled: true, methods: ["carte"] },
      offline: { enabled: true, methods: [], receipt_required: true },
    });
    const noReceipt: ProductForm = { ...custom, payOffline: false };
    expect((applyProductForm(stored, noReceipt, "fr").payment as Doc).offline).toEqual({ enabled: false, methods: [], receipt_required: false });
  });

  it("adds a product to the catalogue or replaces the one with the same id", () => {
    const commercial: Doc = { business: { name: "B" }, catalog: { presentation: { style: "single_best" }, products: [stored] } };
    const added = putProduct(commercial, { id: "nouveau", name: "N", summary: "S" });
    expect(((added.catalog as Doc).products as Doc[]).map((p) => p.id)).toEqual(["formation-vente", "nouveau"]);
    expect((added.catalog as Doc).presentation).toEqual({ style: "single_best" });
    const replaced = putProduct(added, { ...stored, status: "inactive" });
    expect(((replaced.catalog as Doc).products as Doc[])).toHaveLength(2);
    expect(((replaced.catalog as Doc).products as Doc[])[0]!.status).toBe("inactive");
  });
});

describe("product problems", () => {
  const ok: ProductForm = { ...blankProduct("x"), name: "X", summary: "S" };
  it("accepts a minimal product", () => {
    expect(productProblems(ok)).toEqual([]);
  });
  it("asks for a name and a summary", () => {
    expect(productProblems({ ...ok, name: " ", summary: "" })).toEqual(["name", "summary"]);
  });
  it("refuses a priced offer without a positive amount and a 3-letter currency", () => {
    expect(productProblems({ ...ok, priceModel: "fixed", amount: "0", currency: "TND" })).toContain("price");
    expect(productProblems({ ...ok, priceModel: "fixed", amount: "10", currency: "DINAR" })).toContain("price");
    expect(productProblems({ ...ok, priceModel: "range", amount: "20", maxAmount: "10", currency: "TND" })).toContain("price");
    expect(productProblems({ ...ok, priceModel: "fixed", amount: "10", currency: "TND" })).toEqual([]);
  });
  it("refuses addresses that are not https and an order link on a coming-soon product", () => {
    expect(productProblems({ ...ok, url: "http://x.example" })).toContain("url");
    expect(productProblems({ ...ok, ctaKind: "enrol_link", ctaUrl: "" })).toContain("ctaUrl");
    expect(productProblems({ ...ok, ctaKind: "enrol_link", url: "https://x.example", status: "coming_soon" })).toContain("comingSoonLink");
  });
  it("refuses an objection with only one half", () => {
    expect(productProblems({ ...ok, objections: [{ code: "", example: "trop cher", response: "" }] })).toContain("objection");
  });
  it("needs a country for each country price", () => {
    expect(productProblems({ ...ok, variants: [{ id: "p", countries: "", model: "fixed", amount: "5", maxAmount: "", currency: "EUR" }] })).toContain("variantCountries");
  });
});

describe("price summary", () => {
  it("words the price for the list", () => {
    expect(priceSummary(stored)).toEqual({ model: "fixed", text: "120 TND" });
    expect(priceSummary({ pricing: { model: "range", amount: 5, max_amount: 9, currency: "EUR" } })).toEqual({ model: "range", text: "5 – 9 EUR" });
    expect(priceSummary({})).toEqual({ model: "on_request", text: "" });
  });
});
