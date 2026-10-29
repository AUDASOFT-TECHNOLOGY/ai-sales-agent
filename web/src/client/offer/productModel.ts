/** The product form, as plain data, and how it is written back into the Brain's product document.
 *
 *  A product document holds more than the form shows (discovery questions, follow-up rules, a
 *  workflow...). `applyProductForm` therefore starts from the stored product and changes only what
 *  the form edits, so saving never drops what an administrator set. Optional parts that the client
 *  emptied are removed rather than saved empty, because the Brain refuses empty values. */

import { localizedText, withLocalized, type Doc } from "../settingsModel";

export type ProductStatus = "active" | "coming_soon" | "inactive";
export type PriceModel = "on_request" | "fixed" | "from" | "range" | "free";
export type Disclosure = "always" | "after_interest" | "on_request";
export type CtaKind = "request_info" | "enrol_link" | "request_callback" | "notify_me";

export const PRODUCT_STATUSES: ProductStatus[] = ["active", "coming_soon", "inactive"];
export const PRICE_MODELS: PriceModel[] = ["on_request", "fixed", "from", "range", "free"];
export const DISCLOSURES: Disclosure[] = ["after_interest", "always", "on_request"];
export const CTA_KINDS: CtaKind[] = ["request_info", "enrol_link", "request_callback", "notify_me"];

export interface VariantForm {
  id: string;
  countries: string;
  model: PriceModel;
  amount: string;
  maxAmount: string;
  currency: string;
}

export interface ArgumentForm {
  id: string;
  text: string;
}

export interface ObjectionForm {
  code: string;
  example: string;
  response: string;
}

export interface ProductForm {
  id: string;
  status: ProductStatus;
  name: string;
  summary: string;
  description: string;
  url: string;
  priceModel: PriceModel;
  amount: string;
  maxAmount: string;
  currency: string;
  priceNote: string;
  disclose: Disclosure;
  variants: VariantForm[];
  proposition: string;
  benefits: string[];
  differentiators: string[];
  suitedFor: string[];
  notFor: string[];
  arguments: ArgumentForm[];
  objections: ObjectionForm[];
  ctaKind: CtaKind;
  ctaLabel: string;
  ctaUrl: string;
  /** "all": every active payment method; "custom": the options and methods chosen below. */
  payMode: "all" | "custom";
  payOnline: boolean;
  payOnlineMethods: string[];
  payOffline: boolean;
  payOfflineMethods: string[];
  receiptRequired: boolean;
  doNotClaim: string[];
}

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}
function num(value: unknown): string {
  return typeof value === "number" ? String(value) : "";
}

export function slugify(text: string): string {
  const base = text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return /^[a-z0-9]/.test(base) ? base : "";
}

/** A slug not in `taken`, starting from `wanted` (or `fallback` when the text has no usable letter). */
export function uniqueSlug(wanted: string, taken: Iterable<string>, fallback: string): string {
  const used = new Set(taken);
  const base = wanted || fallback;
  if (!used.has(base)) return base;
  for (let i = 2; i < 1000; i += 1) {
    const candidate = `${base}-${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${base}-${Date.now()}`;
}

export function blankProduct(id: string): ProductForm {
  return {
    id,
    status: "active",
    name: "",
    summary: "",
    description: "",
    url: "",
    priceModel: "on_request",
    amount: "",
    maxAmount: "",
    currency: "",
    priceNote: "",
    disclose: "after_interest",
    variants: [],
    proposition: "",
    benefits: [],
    differentiators: [],
    suitedFor: [],
    notFor: [],
    arguments: [],
    objections: [],
    ctaKind: "request_info",
    ctaLabel: "",
    ctaUrl: "",
    payMode: "all",
    payOnline: false,
    payOnlineMethods: [],
    payOffline: false,
    payOfflineMethods: [],
    receiptRequired: false,
    doNotClaim: [],
  };
}

export function findProduct(commercial: Doc | undefined, id: string): Doc | undefined {
  const list = obj(commercial?.catalog).products;
  return Array.isArray(list) ? list.map(obj).find((p) => p.id === id) : undefined;
}

export function productList(commercial: Doc | undefined): Doc[] {
  const list = obj(commercial?.catalog).products;
  return Array.isArray(list) ? list.map(obj) : [];
}

export function formOfProduct(product: Doc, lang: string): ProductForm {
  const pricing = obj(product.pricing);
  const audience = obj(product.audience);
  const value = obj(product.value);
  const cta = obj(obj(product.strategy).cta);
  const payment = obj(product.payment);
  const online = obj(payment.online);
  const offline = obj(payment.offline);
  const status = typeof product.status === "string" ? (product.status as ProductStatus) : "active";
  return {
    id: String(product.id ?? ""),
    status: PRODUCT_STATUSES.includes(status) ? status : "active",
    name: localizedText(product.name, lang),
    summary: localizedText(product.summary, lang),
    description: localizedText(product.description, lang),
    url: typeof product.url === "string" ? product.url : "",
    priceModel: PRICE_MODELS.includes(pricing.model as PriceModel) ? (pricing.model as PriceModel) : "on_request",
    amount: num(pricing.amount),
    maxAmount: num(pricing.max_amount),
    currency: typeof pricing.currency === "string" ? pricing.currency : "",
    priceNote: typeof pricing.note === "string" ? pricing.note : "",
    disclose: DISCLOSURES.includes(pricing.disclose as Disclosure) ? (pricing.disclose as Disclosure) : "after_interest",
    variants: (Array.isArray(pricing.variants) ? pricing.variants : []).map((raw) => {
      const v = obj(raw);
      return {
        id: String(v.id ?? ""),
        countries: strings(obj(v.applies_to).countries).join(", "),
        model: PRICE_MODELS.includes(v.model as PriceModel) ? (v.model as PriceModel) : "fixed",
        amount: num(v.amount),
        maxAmount: num(v.max_amount),
        currency: typeof v.currency === "string" ? v.currency : "",
      };
    }),
    proposition: localizedText(value.proposition, lang),
    benefits: strings(value.benefits),
    differentiators: strings(value.differentiators),
    suitedFor: strings(audience.suited_for),
    notFor: strings(audience.not_for),
    arguments: (Array.isArray(product.sales_arguments) ? product.sales_arguments : []).map((raw) => {
      const a = obj(raw);
      return { id: String(a.id ?? ""), text: typeof a.text === "string" ? a.text : "" };
    }),
    objections: (Array.isArray(product.objections) ? product.objections : []).map((raw) => {
      const o = obj(raw);
      return { code: String(o.code ?? ""), example: strings(o.examples)[0] ?? "", response: typeof o.response === "string" ? o.response : "" };
    }),
    ctaKind: CTA_KINDS.includes(cta.kind as CtaKind) ? (cta.kind as CtaKind) : "request_info",
    ctaLabel: localizedText(cta.label, lang),
    ctaUrl: typeof cta.url === "string" ? cta.url : "",
    payMode: product.payment === undefined ? "all" : "custom",
    payOnline: online.enabled === true,
    payOnlineMethods: strings(online.methods),
    payOffline: offline.enabled === true,
    payOfflineMethods: strings(offline.methods),
    receiptRequired: offline.receipt_required === true,
    doNotClaim: strings(obj(product.restrictions).do_not_claim),
  };
}

const clean = (list: string[]) => list.map((s) => s.trim()).filter((s) => s !== "");

function setOptional(target: Doc, key: string, value: unknown, empty: boolean): void {
  if (empty) delete target[key];
  else target[key] = value;
}

function priceTerms(model: PriceModel, amount: string, maxAmount: string, currency: string): Doc {
  const terms: Doc = { model };
  if (model === "fixed" || model === "from" || model === "range") {
    terms.amount = Number(amount);
    terms.currency = currency.trim().toUpperCase();
    if (model === "range") terms.max_amount = Number(maxAmount);
  }
  return terms;
}

/** What is wrong with the form before it is sent, as message keys the screen words. Everything else
 *  (a rule only the Brain knows) comes back from the server. */
export type ProductProblem =
  | "name"
  | "summary"
  | "price"
  | "variantPrice"
  | "variantCountries"
  | "url"
  | "onlineChoice"
  | "onlineUrl"
  | "ctaUrl"
  | "comingSoonLink"
  | "objection";

const HTTPS = /^https:\/\/[^\s/$.?#][^\s]*$/;

export function productProblems(form: ProductForm): ProductProblem[] {
  const problems: ProductProblem[] = [];
  if (form.name.trim() === "") problems.push("name");
  if (form.summary.trim() === "") problems.push("summary");
  const priced = (m: PriceModel, a: string, max: string, c: string) => {
    if (m !== "fixed" && m !== "from" && m !== "range") return true;
    if (!(Number(a) > 0) || !/^[A-Za-z]{3}$/.test(c.trim())) return false;
    return m !== "range" || Number(max) >= Number(a);
  };
  if (!priced(form.priceModel, form.amount, form.maxAmount, form.currency)) problems.push("price");
  for (const v of form.variants) {
    if (!priced(v.model, v.amount, v.maxAmount, v.currency)) problems.push("variantPrice");
    if (v.countries.trim() === "") problems.push("variantCountries");
  }
  if (form.url.trim() !== "" && !HTTPS.test(form.url.trim())) problems.push("url");
  // Sold online or not must be answered; a product sold online needs the page of its sheet on the client's site.
  if (form.payMode === "all") problems.push("onlineChoice");
  else if (form.payOnline && !HTTPS.test(form.url.trim())) problems.push("onlineUrl");
  const needsLink = form.ctaKind === "enrol_link";
  if (needsLink && !HTTPS.test((form.ctaUrl.trim() || form.url.trim()))) problems.push("ctaUrl");
  if (needsLink && form.status === "coming_soon") problems.push("comingSoonLink");
  if (form.objections.some((o) => (o.example.trim() === "") !== (o.response.trim() === ""))) problems.push("objection");
  return [...new Set(problems)];
}

export function applyProductForm(original: Doc | undefined, form: ProductForm, lang: string): Doc {
  const product: Doc = { ...(original ?? {}) };
  product.id = form.id;
  product.status = form.status;
  product.name = withLocalized(original?.name, lang, form.name.trim());
  product.summary = withLocalized(original?.summary, lang, form.summary.trim());
  setOptional(product, "description", withLocalized(original?.description, lang, form.description.trim()), form.description.trim() === "");
  setOptional(product, "url", form.url.trim(), form.url.trim() === "");

  // Price.
  const origPricing = obj(original?.pricing);
  const pricing: Doc = { ...origPricing };
  delete pricing.amount;
  delete pricing.max_amount;
  delete pricing.currency;
  Object.assign(pricing, priceTerms(form.priceModel, form.amount, form.maxAmount, form.currency));
  setOptional(pricing, "note", form.priceNote.trim(), form.priceNote.trim() === "");
  pricing.disclose = form.disclose;
  const origVariants = new Map((Array.isArray(origPricing.variants) ? origPricing.variants : []).map((v) => [String(obj(v).id), obj(v)]));
  const variants = form.variants.map((v) => {
    const prior = origVariants.get(v.id) ?? {};
    const next: Doc = { ...prior, id: v.id };
    delete next.amount;
    delete next.max_amount;
    delete next.currency;
    Object.assign(next, priceTerms(v.model, v.amount, v.maxAmount, v.currency));
    next.applies_to = { ...obj(prior.applies_to), countries: clean(v.countries.split(",")) };
    return next;
  });
  if (variants.length > 0) pricing.variants = variants;
  else delete pricing.variants;
  const noPrice = form.priceModel === "on_request" && variants.length === 0 && !origPricing.note && form.priceNote.trim() === "" && form.disclose === "after_interest";
  if (noPrice && original?.pricing === undefined) delete product.pricing;
  else product.pricing = pricing;

  // Who it is for.
  const audience: Doc = { ...obj(original?.audience) };
  setOptional(audience, "suited_for", clean(form.suitedFor), clean(form.suitedFor).length === 0);
  setOptional(audience, "not_for", clean(form.notFor), clean(form.notFor).length === 0);
  setOptional(product, "audience", audience, Object.keys(audience).length === 0);

  // What it brings.
  const value: Doc = { ...obj(original?.value) };
  setOptional(value, "proposition", withLocalized(obj(original?.value).proposition, lang, form.proposition.trim()), form.proposition.trim() === "");
  setOptional(value, "benefits", clean(form.benefits), clean(form.benefits).length === 0);
  setOptional(value, "differentiators", clean(form.differentiators), clean(form.differentiators).length === 0);
  setOptional(product, "value", value, Object.keys(value).length === 0);

  // Arguments and objections.
  const priorArgs = new Map((Array.isArray(original?.sales_arguments) ? original.sales_arguments : []).map((a) => [String(obj(a).id), obj(a)]));
  const argIds: string[] = [];
  const argumentsList = form.arguments
    .filter((a) => a.text.trim() !== "")
    .map((a) => {
      const id = a.id || uniqueSlug("", argIds, "argument");
      argIds.push(id);
      return { ...(priorArgs.get(id) ?? {}), id, text: a.text.trim() };
    });
  setOptional(product, "sales_arguments", argumentsList, argumentsList.length === 0);

  const priorObj = new Map((Array.isArray(original?.objections) ? original.objections : []).map((o) => [String(obj(o).code), obj(o)]));
  const objCodes: string[] = [];
  const objections = form.objections
    .filter((o) => o.example.trim() !== "" && o.response.trim() !== "")
    .map((o) => {
      const code = o.code || uniqueSlug(slugify(o.example), objCodes, "objection");
      objCodes.push(code);
      return { ...(priorObj.get(code) ?? {}), code, examples: [o.example.trim()], response: o.response.trim() };
    });
  setOptional(product, "objections", objections, objections.length === 0);

  // Next step offered to the prospect.
  const strategy: Doc = { ...obj(original?.strategy) };
  const cta: Doc = { ...obj(strategy.cta), kind: form.ctaKind };
  setOptional(cta, "label", withLocalized(obj(strategy.cta).label, lang, form.ctaLabel.trim()), form.ctaLabel.trim() === "");
  if (form.ctaKind === "enrol_link") cta.url = (form.ctaUrl.trim() || form.url.trim());
  else delete cta.url;
  const plainCta = form.ctaKind === "request_info" && cta.label === undefined;
  if (plainCta && obj(original?.strategy).cta === undefined) delete strategy.cta;
  else strategy.cta = cta;
  setOptional(product, "strategy", strategy, Object.keys(strategy).length === 0);

  // Payment options.
  if (form.payMode === "all") delete product.payment;
  else {
    const payment: Doc = { ...obj(original?.payment) };
    payment.online = { ...obj(payment.online), enabled: form.payOnline, methods: form.payOnline ? form.payOnlineMethods : [] };
    const offline: Doc = { ...obj(payment.offline), enabled: form.payOffline, methods: form.payOffline ? form.payOfflineMethods : [] };
    offline.receipt_required = form.payOffline && form.receiptRequired;
    payment.offline = offline;
    product.payment = payment;
  }

  // What the agent must not claim.
  const restrictions: Doc = { ...obj(original?.restrictions) };
  setOptional(restrictions, "do_not_claim", clean(form.doNotClaim), clean(form.doNotClaim).length === 0);
  setOptional(product, "restrictions", restrictions, Object.keys(restrictions).length === 0);
  return product;
}

/** Puts the product into the commercial document: replaces the one with the same id, else adds it. */
export function putProduct(commercial: Doc, product: Doc): Doc {
  const catalog = obj(commercial.catalog);
  const list = Array.isArray(catalog.products) ? [...catalog.products] : [];
  const at = list.findIndex((p) => obj(p).id === product.id);
  if (at >= 0) list[at] = product;
  else list.push(product);
  return { ...commercial, catalog: { ...catalog, products: list } };
}

/** The price as a short phrase for lists, e.g. "120 TND", "from 50 EUR", or `null` when there is none. */
export function priceSummary(product: Doc): { model: PriceModel; text: string } {
  const pricing = obj(product.pricing);
  const model = (PRICE_MODELS.includes(pricing.model as PriceModel) ? pricing.model : "on_request") as PriceModel;
  const amount = typeof pricing.amount === "number" ? pricing.amount : null;
  const max = typeof pricing.max_amount === "number" ? pricing.max_amount : null;
  const currency = typeof pricing.currency === "string" ? pricing.currency : "";
  if (model === "fixed" || model === "from") return { model, text: `${amount ?? ""} ${currency}`.trim() };
  if (model === "range") return { model, text: `${amount ?? ""} – ${max ?? ""} ${currency}`.trim() };
  return { model, text: "" };
}
