/** A text page the administrator wrote (Contact, Privacy Policy, Terms of Service). Shown as plain
 *  paragraphs, never as HTML. */

import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { publicPage } from "../client/accessApi";
import { useFetched } from "../client/useFetched";
import { useI18n } from "../i18n";
import { BrandMark } from "../ui/Brand";
import "./landing.css";

export function PublicPage() {
  const { key = "" } = useParams();
  const { t, locale } = useI18n();
  const load = useCallback(() => publicPage(key, locale), [key, locale]);
  const page = useFetched(load);
  return (
    <div className="lp lp-page">
      <header className="lp-wrap lp-page__head">
        <Link to="/" className="lp-brand" aria-label={t("brand.name")}>
          <BrandMark size={28} />
          <span dir="ltr">{t("brand.name")}</span>
        </Link>
        <Link to="/" className="lp-nav__link">
          {t("lp.page.back")}
        </Link>
      </header>
      <main className="lp-wrap lp-page__main">
        {page.loading ? (
          <p className="lp-muted" role="status">
            {t("lp.pricing.loading")}
          </p>
        ) : page.failed || !page.value ? (
          <p className="lp-muted">{t("lp.page.missing")}</p>
        ) : (
          <article className="lp-glass lp-page__card">
            <h1 className="lp-h2">{page.value.title}</h1>
            <p className="lp-page__text">{page.value.body}</p>
          </article>
        )}
      </main>
    </div>
  );
}
