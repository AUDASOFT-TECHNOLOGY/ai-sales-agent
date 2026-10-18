/** What the client still has to do before its agent can start, worked out from what the server says
 *  (readiness checks, the commercial settings, the channel). Pure functions: nothing is guessed here
 *  and nothing is called, so the Home screen only renders the answer.
 *
 *  Who acts: a check in `PLATFORM_CHECKS` is Revansa's to fix and is never shown as a to-do for the
 *  client; every other failing check is the client's own. */

import type { ChannelState, Readiness, SourceState } from "./api";
import { localizedText, defaultLanguage, productsOf, type Doc } from "./settingsModel";

export const PLATFORM_CHECKS = [
  "enabled_by_administrator",
  "loaded_by_platform",
  "model_assigned",
  "brain_assigned",
  "platform_connections",
] as const;

export type StepId = "business" | "products" | "payments" | "prospects" | "channel";
export type StepState = "done" | "todo" | "waiting";

export interface Step {
  id: StepId;
  state: StepState;
  /** Without it the agent cannot start. */
  required: boolean;
  to: string;
  /** A value to show next to a finished step (a name, a count). */
  value?: string | number;
}

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

export function activePaymentMethods(commercial: Doc | undefined): number {
  const list = obj(commercial?.payment).methods;
  if (!Array.isArray(list)) return 0;
  return list.filter((m) => obj(m).status !== "inactive").length;
}

export interface SetupInput {
  commercial: Doc | undefined;
  readiness: Readiness | null;
  channel: ChannelState | null;
  source: SourceState | null;
}

function checkOk(readiness: Readiness | null, ids: readonly string[]): boolean | null {
  if (!readiness) return null;
  const found = readiness.checks.filter((c) => ids.includes(c.id));
  return found.length === 0 ? null : found.every((c) => c.ok);
}

export function buildSteps({ commercial, readiness, channel, source }: SetupInput): Step[] {
  const lang = defaultLanguage(commercial);
  const business = obj(commercial?.business);
  const name = typeof business.name === "string" ? business.name.trim() : "";
  const summary = localizedText(business.summary, lang).trim();
  const businessDone = name !== "" && summary !== "";

  const sellable = productsOf(commercial).filter((p) => p.status === "active" && p.name.trim() !== "" && p.summary.trim() !== "");
  const methods = activePaymentMethods(commercial);

  const sourceOk = checkOk(readiness, ["source_connected"]);
  const channelOk = checkOk(readiness, ["whatsapp_number", "channel_ready"]);
  // Revansa still has to open the channel or connect the CRM account: the client has nothing to do.
  const channelWaiting = channelOk === false && channel?.status === "waiting_platform";
  const sourceWaiting = sourceOk === false && source?.access === "waiting_platform";

  return [
    { id: "business", state: businessDone ? "done" : "todo", required: true, to: "/offer/business", value: name },
    { id: "products", state: sellable.length > 0 ? "done" : "todo", required: true, to: "/offer/products", value: sellable.length },
    { id: "payments", state: methods > 0 ? "done" : "todo", required: false, to: "/offer/payments", value: methods },
    { id: "prospects", state: sourceOk ? "done" : sourceWaiting ? "waiting" : "todo", required: true, to: "/connections/sources" },
    { id: "channel", state: channelOk ? "done" : channelWaiting ? "waiting" : "todo", required: true, to: "/connections/channels" },
  ];
}

/** The checks Revansa has to settle, in the server's order. */
export function platformReasons(readiness: Readiness | null): string[] {
  if (!readiness) return [];
  return readiness.checks.filter((c) => !c.ok && (PLATFORM_CHECKS as readonly string[]).includes(c.id)).map((c) => c.id);
}

/** Required steps done / total, for the progress bar. */
export function progressOf(steps: Step[]): { done: number; total: number } {
  const required = steps.filter((s) => s.required);
  return { done: required.filter((s) => s.state === "done").length, total: required.length };
}
