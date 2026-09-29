import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Link, type LinkProps } from "react-router-dom";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "agent" | "secondary" | "ghost" | "success" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  leading?: ReactNode;
  trailing?: ReactNode;
  /** A disabled action always exposes why (doc 11.3). */
  disabledReason?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, leading, trailing, disabledReason, className, children, disabled, ...rest },
  ref,
) {
  const isDisabled = disabled || loading || !!disabledReason;
  return (
    <button
      ref={ref}
      className={cx("btn", className)}
      data-variant={variant}
      data-size={size}
      data-loading={loading || undefined}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      title={disabledReason}
      {...rest}
    >
      {loading ? <Loader2 className="btn__spin" size={16} aria-hidden /> : leading}
      <span className="btn__label">{children}</span>
      {trailing}
    </button>
  );
});

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  children: ReactNode;
  active?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, children, className, active, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cx("icon-btn", className)}
      aria-label={label}
      title={label}
      data-active={active || undefined}
      {...rest}
    >
      {children}
    </button>
  );
});

/** A router link that looks like a button (navigation, not action). */
export function LinkButton({ variant = "secondary", size = "md", leading, trailing, className, children, ...rest }: LinkProps & { variant?: ButtonVariant; size?: "sm" | "md" | "lg"; leading?: ReactNode; trailing?: ReactNode }) {
  return (
    <Link className={cx("btn", className)} data-variant={variant} data-size={size} {...rest}>
      {leading}
      <span className="btn__label">{children}</span>
      {trailing}
    </Link>
  );
}
