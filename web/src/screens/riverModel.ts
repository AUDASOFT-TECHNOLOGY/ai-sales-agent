/** Which bank of the CRM river a prospect sits in. Funnel stages are free-form labels owned by each
 *  client's Brain, so the bank is read from what the platform knows for sure (status, replies,
 *  follow-ups, payment) and only then from the stage label. */

import type { PaymentCase, ProspectRow } from "../client/api";

export type Bank = "contacted" | "discussing" | "interested" | "converted" | "paid" | "notInterested" | "stopped";

/** In the order the river flows. "Not interested" is the cold bank: the prospect did not answer or
 *  cooled down, nobody asked to stop and the agent has not given up, so follow-ups still come. */
export const BANKS: Bank[] = ["contacted", "discussing", "interested", "converted", "paid", "notInterested", "stopped"];

const COLD = /non[\s-]*int[ée]ress|not[\s-]*interested|perdu|lost|relance|follow[\s-]*up|attente de r[ée]ponse|no[\s-]*repl|cold|froid/i;
const PAID = /paid|pay[ée]|conclue|won|vendu|sold/i;
const CONVERTED = /convert|inscri|regist|commande|order|checkout/i;
const INTERESTED = /int[ée]ress|interested|n[ée]goci|devis|quote|proposal|hot|chaud/i;

export function bankOf(p: ProspectRow, payment?: PaymentCase | null): Bank {
  const stage = p.stage ?? "";
  if (p.status === "opted_out") return "stopped";
  if (payment?.progress === "paid" || (stage && PAID.test(stage) && !COLD.test(stage))) return "paid";
  if (p.status === "closed") return "stopped";
  if (payment || (stage && CONVERTED.test(stage))) return "converted";
  if (p.status === "paused") return p.needs_human ? "discussing" : "notInterested";
  if (stage && COLD.test(stage)) return "notInterested";
  if (stage && INTERESTED.test(stage)) return "interested";
  if (p.status === "new") return "contacted";
  if (p.last_inbound_at === null) return p.follow_up_count >= 2 ? "notInterested" : "contacted";
  return "discussing";
}
