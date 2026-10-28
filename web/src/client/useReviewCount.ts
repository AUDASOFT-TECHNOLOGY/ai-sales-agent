import { useEffect, useState } from "react";
import { clientPayments, clientProposals, clientProspects } from "./api";

export interface ReviewCounts {
  /** Messages the agent wrote that wait for approval. */
  messages: number;
  /** Payments a prospect announced and nobody confirmed yet. */
  payments: number;
  /** Conversations stopped until a person answers. */
  human: number;
}

/** How many things wait for the client's decision, by kind. A kind that cannot be read counts 0: a
 *  badge is never invented. */
export function useReviewCounts(): ReviewCounts {
  const [counts, setCounts] = useState<ReviewCounts>({ messages: 0, payments: 0, human: 0 });
  useEffect(() => {
    let cancelled = false;
    const patch = (next: Partial<ReviewCounts>) => !cancelled && setCounts((prev) => ({ ...prev, ...next }));
    clientProposals().then(
      (r) => patch({ messages: r.proposals?.length ?? 0 }),
      () => undefined,
    );
    clientPayments().then(
      (r) => patch({ payments: (r.cases ?? []).filter((c) => c.confirmed_at === null && c.progress !== "paid").length }),
      () => undefined,
    );
    clientProspects(1, 0, { needs_human: true }).then(
      (r) => patch({ human: r.total }),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return counts;
}

/** The menu badge: everything that waits for a decision. */
export function useReviewCount(): number {
  const c = useReviewCounts();
  return c.messages + c.payments + c.human;
}
