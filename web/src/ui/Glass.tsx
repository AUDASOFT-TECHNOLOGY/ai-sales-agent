import type { CSSProperties, ElementType, ReactNode } from "react";
import { cx } from "./cx";

export type Tint = "agent" | "action" | "success" | "attention" | "danger" | "seal" | "neutral";
export type GlassLevel = 1 | 2 | 3 | 4;

interface GlassProps {
  level?: GlassLevel;
  tint?: Tint;
  as?: ElementType;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
  [key: string]: unknown;
}

/** A glass surface (doc section 7). The level chooses fill, blur, border and shadow. */
export function Glass({ level = 1, tint, as, className, style, children, ...rest }: GlassProps) {
  const Tag = (as ?? "div") as ElementType;
  return (
    <Tag className={cx("glass", className)} data-level={level} data-tint={tint} style={style} {...rest}>
      {children}
    </Tag>
  );
}
