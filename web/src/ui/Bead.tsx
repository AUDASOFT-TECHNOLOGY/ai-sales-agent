import { cx } from "./cx";
import type { Attention } from "../mock/types";

/** Initials disc with an attention ring (doc 13.3). All beads are the same size on purpose. */
export function Bead({ initials, attention = "none", size = 40, className }: { initials: string; attention?: Attention; size?: 28 | 36 | 40 | 48 | 56 | 64; className?: string }) {
  return (
    <span className={cx("bead", className)} data-attention={attention} data-size={size} aria-hidden>
      <span className="bead__disc">{initials}</span>
    </span>
  );
}
