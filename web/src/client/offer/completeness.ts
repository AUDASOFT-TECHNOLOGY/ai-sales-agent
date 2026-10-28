/** What an owner still has to give the agent about a product, as one checklist shared by the product
 *  page (beside the form) and the product list (a bar and a few chips per product). Pure: it only
 *  reads the form, the counts of photos / documents / testimonials and the payment methods. */

import type { Doc } from "../settingsModel";
import type { ProductForm } from "./productModel";

export type CheckKey = "basics" | "price" | "media" | "docs" | "testi" | "paylink" | "payoff" | "objections";

export interface Check {
  key: CheckKey;
  ok: boolean;
  /** For counted items (photos, documents, testimonials, objections). */
  n?: number;
}

export interface ResourceCounts {
  media: number;
  docs: number;
  testi: number;
}

const PRICED = ["fixed", "from", "range"];

export function checklistOf({
  form,
  counts,
  methods,
  ownMethodId,
  ownLink,
}: {
  form: ProductForm;
  counts: ResourceCounts;
  /** The active payment methods of the business. */
  methods: Doc[];
  /** The id of the payment method only this product uses, when it has one. */
  ownMethodId: string;
  /** The payment link typed for this product ("" when none). */
  ownLink: string;
}): Check[] {
  const others = (kind: "online" | "offline") => methods.filter((m) => (m.kind === "online") === (kind === "online") && String(m.id) !== ownMethodId);
  const online = ownLink.trim() !== "" || (form.payMode === "all" ? others("online").length > 0 : form.payOnline && others("online").length > 0);
  const offline = form.payMode === "all" ? others("offline").length > 0 : form.payOffline && others("offline").length > 0;
  const answered = form.objections.filter((o) => o.response.trim() !== "").length;
  return [
    { key: "basics", ok: form.name.trim() !== "" && form.summary.trim() !== "" && form.description.trim() !== "" },
    { key: "price", ok: PRICED.includes(form.priceModel) ? form.amount.trim() !== "" : true },
    { key: "media", ok: counts.media > 0, n: counts.media },
    { key: "docs", ok: counts.docs > 0, n: counts.docs },
    { key: "testi", ok: counts.testi > 0, n: counts.testi },
    { key: "paylink", ok: online },
    { key: "payoff", ok: offline },
    { key: "objections", ok: answered > 0, n: answered },
  ];
}

export function scoreOf(checks: Check[]): number {
  return Math.round((checks.filter((c) => c.ok).length / checks.length) * 100);
}
