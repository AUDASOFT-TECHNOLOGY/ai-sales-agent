/** The catalogue: every product or offer the agent may talk about. A product is never deleted (the
 *  agent's history refers to it): "hide" sets it inactive, which stops the agent from offering it. */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Package, Plus } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Card, Empty, FailureNote, type Failure } from "../kit";
import { defaultLanguage, localizedText } from "../settingsModel";
import { findProduct, priceSummary, productList } from "./productModel";
import { useOffer } from "./OfferLayout";

const STATUS_TINT = { active: "success", coming_soon: "attention", inactive: "neutral" } as const;

export function ProductsPage() {
  const { t } = useI18n();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const products = productList(commercial);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function setStatus(id: string, status: "active" | "inactive") {
    const product = findProduct(commercial, id);
    if (!product) return;
    setBusy(id);
    setFailure(null);
    const catalog = (commercial.catalog ?? {}) as Record<string, unknown>;
    const next = {
      ...commercial,
      catalog: { ...catalog, products: products.map((p) => (p.id === id ? { ...p, status } : p)) },
    };
    const problem = await save({ commercial: next });
    setBusy(null);
    if (problem) setFailure(problem);
  }

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.prod.title")}</h3>
          <p>{t("rv.prod.intro")}</p>
        </div>
        <LinkButton to="/offer/products/new" variant="primary" leading={<Plus size={16} aria-hidden />}>
          {t("rv.prod.add")}
        </LinkButton>
      </div>

      {failure ? <FailureNote failure={failure} /> : null}

      {products.length === 0 ? (
        <Card>
          <Empty
            icon={<Package size={24} />}
            title={t("rv.prod.empty.title")}
            text={t("rv.prod.empty.text")}
            action={
              <LinkButton to="/offer/products/new" variant="primary" leading={<Plus size={16} aria-hidden />}>
                {t("rv.prod.add")}
              </LinkButton>
            }
          />
        </Card>
      ) : (
        <Card flush aria-label={t("rv.prod.title")}>
          <ul className="rv-list">
            {products.map((p) => {
              const id = String(p.id);
              const status = (typeof p.status === "string" ? p.status : "active") as keyof typeof STATUS_TINT;
              const price = priceSummary(p);
              return (
                <li key={id} className="rv-product" data-testid={`product-${id}`}>
                  <div className="rv-row__main">
                    <Link to={`/offer/products/${encodeURIComponent(id)}`} className="rv-row__title" dir="auto">
                      {localizedText(p.name, lang) || id}
                    </Link>
                    <span className="rv-row__sub" dir="auto">
                      {localizedText(p.summary, lang)}
                    </span>
                  </div>
                  <span className="t-body-sm c-2" dir="auto">
                    {price.text || t(`rv.prod.price.${price.model}` as MessageKey)}
                  </span>
                  <Chip tint={STATUS_TINT[status] ?? "neutral"}>{t(`rv.prod.status.${status}` as MessageKey)}</Chip>
                  <div className="rv-actions">
                    <LinkButton to={`/offer/products/${encodeURIComponent(id)}`} size="sm">
                      {t("rv.common.edit")}
                    </LinkButton>
                    {status === "inactive" ? (
                      <Button size="sm" onClick={() => void setStatus(id, "active")} loading={busy === id}>
                        {t("rv.prod.show")}
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => void setStatus(id, "inactive")} loading={busy === id}>
                        {t("rv.prod.hide")}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}
