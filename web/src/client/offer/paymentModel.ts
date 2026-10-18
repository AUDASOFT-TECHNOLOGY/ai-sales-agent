/** Payment methods as a plain form, and how they are written back into `commercial.payment`.
 *
 *  What the client types as payment details (a link, a bank name, an account number, the wording of
 *  an instruction) is *exact data*: the agent hands it to the buyer character for character and the
 *  model never sees it. So these values are kept as typed (no trimming, no reformatting), and only
 *  a value that is blank is dropped. Nothing here talks to a payment provider: an online method is
 *  the client's own payment or order page, opened by the buyer.
 *
 *  Confirmation is always manual: a payment counts as paid only when the client has checked it. */

import { localizedText, withLocalized, type Doc } from "../settingsModel";
import journey from "./purchaseJourney.json";

export type MethodKind = "online" | "offline";

export interface FieldForm {
  label: string;
  value: string;
}

export interface InstructionForm {
  id: string;
  /** Comma-separated; empty means every country. */
  countries: string;
  /** Comma-separated currency codes; empty means every currency. */
  currencies: string;
  /** A payment or order page (online methods). */
  url: string;
  /** What to tell the buyer (offline methods, or a sentence next to a link). */
  text: string;
  fields: FieldForm[];
}

export interface MethodForm {
  id: string;
  status: "active" | "inactive";
  label: string;
  kind: MethodKind;
  instructions: InstructionForm[];
  /** What the buyer is told to expect once they have paid. */
  note: string;
}

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}
const list = (text: string) => text.split(",").map((s) => s.trim()).filter((s) => s !== "");

export function methodsOf(commercial: Doc | undefined): Doc[] {
  const methods = obj(commercial?.payment).methods;
  return Array.isArray(methods) ? methods.map(obj) : [];
}

export function blankInstruction(id: string): InstructionForm {
  return { id, countries: "", currencies: "", url: "", text: "", fields: [] };
}

export function blankMethod(id: string, kind: MethodKind): MethodForm {
  return { id, status: "active", label: "", kind, instructions: [blankInstruction("main")], note: "" };
}

export function formOfMethod(method: Doc, lang: string): MethodForm {
  return {
    id: String(method.id ?? ""),
    status: method.status === "inactive" ? "inactive" : "active",
    label: localizedText(method.label, lang),
    kind: method.kind === "online" ? "online" : "offline",
    instructions: (Array.isArray(method.instructions) ? method.instructions : []).map((raw) => {
      const i = obj(raw);
      const scope = obj(i.applies_to);
      return {
        id: String(i.id ?? ""),
        countries: strings(scope.countries).join(", "),
        currencies: strings(scope.currencies).join(", "),
        url: typeof i.url === "string" ? i.url : "",
        text: localizedText(i.text, lang),
        fields: (Array.isArray(i.fields) ? i.fields : []).map((f) => ({
          label: localizedText(obj(f).label, lang),
          value: localizedText(obj(f).value, lang),
        })),
      };
    }),
    note: localizedText(obj(method.confirmation).note, lang),
  };
}

const HTTPS = /^https:\/\/[^\s/$.?#][^\s]*$/;

export type MethodProblem = "label" | "url" | "onlineNeedsLink" | "offlineNeedsDetails" | "currency";

export function methodProblems(form: MethodForm): MethodProblem[] {
  const problems: MethodProblem[] = [];
  if (form.label.trim() === "") problems.push("label");
  for (const i of form.instructions) {
    if (i.url.trim() !== "" && !HTTPS.test(i.url.trim())) problems.push("url");
    if (list(i.currencies).some((c) => !/^[A-Za-z]{3}$/.test(c))) problems.push("currency");
  }
  if (form.kind === "online" && !form.instructions.some((i) => HTTPS.test(i.url.trim()))) problems.push("onlineNeedsLink");
  if (form.kind === "offline" && !form.instructions.some((i) => i.text.trim() !== "" || i.fields.some((f) => f.label.trim() !== "" && f.value.trim() !== ""))) {
    problems.push("offlineNeedsDetails");
  }
  return [...new Set(problems)];
}

export function applyMethodForm(original: Doc | undefined, form: MethodForm, lang: string): Doc {
  const method: Doc = { ...(original ?? {}) };
  method.id = form.id;
  method.status = form.status;
  method.kind = form.kind;
  method.label = withLocalized(original?.label, lang, form.label.trim());
  const priorInstr = new Map((Array.isArray(original?.instructions) ? original.instructions : []).map((i) => [String(obj(i).id), obj(i)]));
  const ids: string[] = [];
  const instructions = form.instructions
    .map((i) => ({ ...i, url: i.url.trim() }))
    .filter((i) => i.url !== "" || i.text.trim() !== "" || i.fields.some((f) => f.label.trim() !== "" && f.value.trim() !== ""))
    .map((i) => {
      const id = i.id || `i${ids.length + 1}`;
      ids.push(id);
      const prior = priorInstr.get(id) ?? {};
      const next: Doc = { ...prior, id };
      const scope: Doc = { ...obj(prior.applies_to) };
      const countries = list(i.countries);
      const currencies = list(i.currencies).map((c) => c.toUpperCase());
      if (countries.length > 0) scope.countries = countries;
      else delete scope.countries;
      if (currencies.length > 0) scope.currencies = currencies;
      else delete scope.currencies;
      if (Object.keys(scope).length > 0) next.applies_to = scope;
      else delete next.applies_to;
      if (i.url !== "") next.url = i.url;
      else delete next.url;
      if (i.text.trim() !== "") next.text = withLocalized(prior.text, lang, i.text);
      else delete next.text;
      const fields = i.fields
        .filter((f) => f.label.trim() !== "" && f.value.trim() !== "")
        .map((f, n) => {
          const priorField = obj((Array.isArray(prior.fields) ? prior.fields : [])[n]);
          return { label: withLocalized(priorField.label, lang, f.label.trim()), value: withLocalized(priorField.value, lang, f.value) };
        });
      if (fields.length > 0) next.fields = fields;
      else delete next.fields;
      return next;
    });
  method.instructions = instructions;
  // Always a person at the client who confirms; the note only tells the buyer what to expect.
  const confirmation: Doc = { ...obj(original?.confirmation), mode: "manual" };
  if (form.note.trim() !== "") confirmation.note = withLocalized(obj(original?.confirmation).note, lang, form.note.trim());
  else delete confirmation.note;
  method.confirmation = confirmation;
  return method;
}

export function putMethod(commercial: Doc, method: Doc): Doc {
  const payment = obj(commercial.payment);
  const methods = Array.isArray(payment.methods) ? [...payment.methods] : [];
  const at = methods.findIndex((m) => obj(m).id === method.id);
  if (at >= 0) methods[at] = method;
  else methods.push(method);
  return { ...commercial, payment: { ...payment, methods } };
}

export function removeMethod(commercial: Doc, id: string): Doc {
  const payment = obj(commercial.payment);
  const methods = (Array.isArray(payment.methods) ? payment.methods : []).filter((m) => obj(m).id !== id);
  return { ...commercial, payment: { ...payment, methods } };
}

/** Product options that point at a removed method would be refused by the Brain: drop the reference. */
export function forgetMethod(commercial: Doc, id: string): Doc {
  const catalog = obj(commercial.catalog);
  if (!Array.isArray(catalog.products)) return commercial;
  const products = catalog.products.map((raw) => {
    const product = obj(raw);
    const payment = obj(product.payment);
    if (product.payment === undefined) return raw;
    const trim = (option: unknown) => {
      const o = obj(option);
      return { ...o, methods: strings(o.methods).filter((m) => m !== id) };
    };
    return { ...product, payment: { ...payment, online: trim(payment.online), offline: trim(payment.offline) } };
  });
  return { ...commercial, catalog: { ...catalog, products } };
}

/* ── The purchase journey ────────────────────────────────────────────────────────────────────────
 * Payment methods only reach a buyer through a conversion workflow with a payment step. The client
 * never sees a "workflow": the Payments screen offers one switch, "give my payment methods when a
 * prospect is interested". This is the workflow that switch manages (the same file is read by the
 * backend test that proves the agent really gives the exact link with it). It is only ever set as
 * the default when the client has no sales process of its own: one set by Revansa is never replaced. */

export const JOURNEY_ID = journey.id;

export type JourneyState = "on" | "off" | "custom";

export function journeyOf(commercial: Doc | undefined): JourneyState {
  const chosen = obj(commercial?.conversion).default_workflow;
  if (chosen === JOURNEY_ID) return "on";
  return typeof chosen === "string" && chosen !== "" ? "custom" : "off";
}

export function enableJourney(commercial: Doc): Doc {
  const conversion = obj(commercial.conversion);
  if (typeof conversion.default_workflow === "string" && conversion.default_workflow !== JOURNEY_ID) return commercial;
  const others = (Array.isArray(conversion.workflows) ? conversion.workflows : []).filter((w) => obj(w).id !== JOURNEY_ID);
  return { ...commercial, conversion: { ...conversion, default_workflow: JOURNEY_ID, workflows: [...others, journey] } };
}

export function disableJourney(commercial: Doc): Doc {
  const conversion = obj(commercial.conversion);
  if (conversion.default_workflow !== JOURNEY_ID) return commercial;
  const next: Doc = { ...conversion };
  delete next.default_workflow;
  next.workflows = (Array.isArray(conversion.workflows) ? conversion.workflows : []).filter((w) => obj(w).id !== JOURNEY_ID);
  if ((next.workflows as unknown[]).length === 0) delete next.workflows;
  const { conversion: _removed, ...rest } = commercial;
  void _removed;
  return Object.keys(next).length === 0 ? rest : { ...rest, conversion: next };
}
