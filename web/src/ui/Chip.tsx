import type { ReactNode } from "react";
import { cx } from "./cx";
import type { Tint } from "./Glass";

interface ChipProps {
  tint?: Tint;
  glyph?: ReactNode;
  children: ReactNode;
  className?: string;
  outline?: boolean;
  dashed?: boolean;
}

/** Status chip: glyph + word, never color alone (doc 15.1). */
export function Chip({ tint = "neutral", glyph, children, className, outline, dashed }: ChipProps) {
  return (
    <span className={cx("chip", className)} data-tint={tint} data-outline={outline || undefined} data-dashed={dashed || undefined}>
      {glyph}
      <span>{children}</span>
    </span>
  );
}

/** The 32 or 40 px icon tile that gives an icon presence (doc section 10). */
export function GlyphTile({ tint = "action", size = 32, children, ghost }: { tint?: Tint; size?: 28 | 32 | 40; children: ReactNode; ghost?: boolean }) {
  return (
    <span className="glyph-tile" data-tint={tint} data-size={size} data-ghost={ghost || undefined} aria-hidden>
      {children}
    </span>
  );
}
