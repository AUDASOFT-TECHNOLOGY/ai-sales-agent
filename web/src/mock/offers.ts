import type { Offer } from "./types";

/** What still keeps an offer from being ready to sell, recomputed every time it changes (never
 *  left stale). A 4th check (sales material) exists because a benefit, a price and a market are
 *  not enough for the agent to convince anyone — it also needs something to point to. */
export function deriveReadiness(offer: Offer): { completeness: number; missing: string[] } {
  const missing: string[] = [];
  if (!offer.summary || offer.summary.trim().length < 8) missing.push("A benefit to lead with");
  if (offer.price === "Not set") missing.push("A price, or 'On request'");
  if (offer.markets.length === 0) missing.push("A market it sells in");
  if (offer.assets.length === 0) missing.push("A document, photo or testimonial to help REVANSA convince a prospect");
  const total = 4;
  return { completeness: Math.max(0.15, (total - missing.length) / total), missing };
}

let offerSeq = 0;

/** Single source of truth for constructing a new offer, so completeness/missing are never
 *  hand-duplicated at the call site (Configure's "add product" button and Runway's onboarding
 *  gate both go through this). */
export function createOffer(input: { name: string; summary?: string; price?: string; markets?: string[]; status?: Offer["status"] }): Offer {
  offerSeq += 1;
  const draft: Offer = {
    id: `offer-${Date.now().toString(36)}-${offerSeq}`,
    name: input.name,
    summary: input.summary ?? "Describe it in one sentence.",
    status: input.status ?? "coming_soon",
    price: input.price ?? "Not set",
    markets: input.markets ?? [],
    assets: [],
    completeness: 0,
    missing: [],
  };
  return { ...draft, ...deriveReadiness(draft) };
}
