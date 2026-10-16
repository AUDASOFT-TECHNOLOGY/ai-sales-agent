/** Pure helpers behind the settings screen. `commercial` is the Brain's own document and a write
 *  replaces it as a whole, so every edit here copies the document and changes only the part the
 *  screen knows: whatever else it holds (strategy, payment, follow-up...) is sent back untouched. */

import type { ClientSettings } from "./api";

export type Doc = Record<string, unknown>;

export interface Product {
  id: string;
  status: string;
  name: string;
  summary: string;
}

export const PRODUCT_STATUSES = ["active", "coming_soon", "inactive"] as const;
export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

/** Plain text, or `{language: text}`: the text in `lang`, else the first one the document holds. */
export function localizedText(value: unknown, lang: string): string {
  if (typeof value === "string") return value;
  const map = obj(value);
  const found = map[lang] ?? Object.values(map)[0];
  return typeof found === "string" ? found : "";
}

/** Writes `text` into a plain or localized value without dropping the other languages. */
export function withLocalized(value: unknown, lang: string, text: string): unknown {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const map = obj(value);
    const key = lang in map ? lang : (Object.keys(map)[0] ?? lang);
    return { ...map, [key]: text };
  }
  return text;
}

export function defaultLanguage(commercial: Doc | undefined): string {
  const languages = obj(obj(commercial?.communication).languages);
  return typeof languages.default === "string" ? languages.default : "";
}

export function productsOf(commercial: Doc | undefined): Product[] {
  const lang = defaultLanguage(commercial);
  const list = obj(commercial?.catalog).products;
  if (!Array.isArray(list)) return [];
  return list.map((raw) => {
    const p = obj(raw);
    return {
      id: String(p.id ?? ""),
      status: typeof p.status === "string" ? p.status : "active",
      name: localizedText(p.name, lang),
      summary: localizedText(p.summary, lang),
    };
  });
}

/** Whether a product's name or summary is written in several languages (only the default one is
 *  edited here; the others are kept). */
export function isLocalized(commercial: Doc | undefined, id: string, field: "name" | "summary"): boolean {
  const list = obj(commercial?.catalog).products;
  const found = Array.isArray(list) ? list.map(obj).find((p) => p.id === id) : undefined;
  return !!found && typeof found[field] === "object" && found[field] !== null;
}

export function setProduct(commercial: Doc, id: string, patch: Partial<Omit<Product, "id">>): Doc {
  const lang = defaultLanguage(commercial);
  const catalog = obj(commercial.catalog);
  const list = Array.isArray(catalog.products) ? catalog.products : [];
  const products = list.map((raw) => {
    const p = obj(raw);
    if (p.id !== id) return raw;
    const next: Doc = { ...p };
    if (patch.status !== undefined) next.status = patch.status;
    if (patch.name !== undefined) next.name = withLocalized(p.name, lang, patch.name);
    if (patch.summary !== undefined) next.summary = withLocalized(p.summary, lang, patch.summary);
    return next;
  });
  return { ...commercial, catalog: { ...catalog, products } };
}

export function addProduct(commercial: Doc, product: Product): Doc {
  const catalog = obj(commercial.catalog);
  const list = Array.isArray(catalog.products) ? catalog.products : [];
  const added = { id: product.id, status: product.status, name: product.name, summary: product.summary };
  return { ...commercial, catalog: { ...catalog, products: [...list, added] } };
}

export function setBusiness(commercial: Doc, patch: { name?: string; website?: string; summary?: string }): Doc {
  const lang = defaultLanguage(commercial);
  const business: Doc = { ...obj(commercial.business) };
  if (patch.name !== undefined) business.name = patch.name;
  if (patch.website !== undefined) {
    if (patch.website.trim() === "") delete business.website;
    else business.website = patch.website;
  }
  if (patch.summary !== undefined) {
    if (patch.summary.trim() === "") delete business.summary;
    else business.summary = withLocalized(business.summary, lang, patch.summary);
  }
  return { ...commercial, business };
}

export function setLanguages(commercial: Doc, patch: { default?: string; supported?: string[] }): Doc {
  const communication = obj(commercial.communication);
  const languages = { ...obj(communication.languages), ...patch };
  return { ...commercial, communication: { ...communication, languages } };
}

export function setToneStyle(commercial: Doc, style: string): Doc {
  const communication = obj(commercial.communication);
  const tone: Doc = { ...obj(communication.tone) };
  if (style.trim() === "") delete tone.style;
  else tone.style = style;
  return { ...commercial, communication: { ...communication, tone } };
}

export function splitList(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

/** What a write carries: only the parts that hold a value, and the whole `commercial` document. */
export function settingsToSend(s: {
  timezone: string;
  optOut: string;
  dailyAt: string;
  days: string[];
  commercial: Doc | undefined;
}): ClientSettings {
  const out: ClientSettings = {};
  if (s.timezone.trim() !== "") out.timezone = s.timezone.trim();
  out.opt_out_keywords = splitList(s.optOut);
  const schedule: NonNullable<ClientSettings["schedule"]> = {};
  if (s.dailyAt.trim() !== "") schedule.daily_at = s.dailyAt.trim();
  schedule.days = s.days;
  out.schedule = schedule;
  if (s.commercial) out.commercial = s.commercial;
  return out;
}
