import { useI18n } from "../i18n";

/** REVANSA mark: the gradient "R" glyph. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <img
      src="/brand/revansa-mark.png"
      alt=""
      aria-hidden
      className="brand-mark"
      width={size}
      height={size}
      style={{ width: size, height: size, objectFit: "contain" }}
    />
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
