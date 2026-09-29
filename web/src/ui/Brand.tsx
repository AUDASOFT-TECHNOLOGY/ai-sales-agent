import { useId } from "react";
import { useI18n } from "../i18n";

/** REVENSA mark: an orb with a rising arc, and the wordmark. */
export function BrandMark({ size = 30 }: { size?: number }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="brand-mark">
      <defs>
        <radialGradient id={`${id}-core`} cx="34%" cy="28%" r="80%">
          <stop offset="0" stopColor="var(--aether-300)" />
          <stop offset="0.46" stopColor="var(--aether-500)" />
          <stop offset="1" stopColor="var(--aether-800)" />
        </radialGradient>
        <linearGradient id={`${id}-ring`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="var(--aether-400)" />
          <stop offset="1" stopColor="var(--ice-400)" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="9.5" fill={`url(#${id}-core)`} />
      <path d="M4.5 22.5 A13 13 0 0 0 27.5 9.5" fill="none" stroke={`url(#${id}-ring)`} strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="27.5" cy="9.5" r="1.7" fill="var(--ice-300)" />
    </svg>
  );
}

export function Wordmark({ tagline, className }: { tagline?: boolean; className?: string }) {
  const { t } = useI18n();
  return (
    <span className={className ? `wordmark ${className}` : "wordmark"} dir="ltr">
      <BrandMark />
      <span className="wordmark__text">
        <span className="wordmark__name">{t("brand.name")}</span>
        {tagline ? <span className="wordmark__tag">{t("brand.tagline")}</span> : null}
      </span>
    </span>
  );
}
