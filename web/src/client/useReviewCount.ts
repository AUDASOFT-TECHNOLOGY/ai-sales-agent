import { useEffect, useState } from "react";
import { clientProposals } from "./api";

/** How many things wait for the client's decision (messages to approve), for the menu badge.
 *  0 when it cannot be read: a badge is never invented. */
export function useReviewCount(): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    clientProposals().then(
      (r) => !cancelled && setCount(r.proposals?.length ?? 0),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return count;
}
