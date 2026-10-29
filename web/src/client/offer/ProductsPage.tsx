/** The catalogue: every product or offer the agent may talk about, and for each one how complete its
 *  sheet is (photos, documents, testimonials, payment) and where it is sold. A product is never
 *  deleted (the agent's history refers to it): "hide" sets it inactive, which stops the agent from
 *  offering it. */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Check, FileText, ImageIcon, MessageSquareQuote, Package, Plus, CreditCard } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Glass } from "../../ui/Glass";
import { ALL_PRODUCTS, clientLanes, clientResources, type LanesState, type ProductResource } from "../api";
import { Empty, FailureNote, type Failure } from "../kit";
import { defaultLanguage, localizedText } from "../settingsModel";
import { checklistOf, scoreOf } from "./completeness";
import { methodsOf } from "./paymentModel";
import { findProduct, formOfProduct, priceSummary, productList } from "./productModel";
import { zoneCounts } from "./ResourcesPanel";
import { useOffer } from "./OfferLayout";
import { useAddress } from "../prepare/links";
import { useTerms } from "../prepare/terms";

const STATUS_TINT = { active: "success", coming_soon: "attention", inactive: "neutral" } as const;

export function ProductsPage() {
  const { t } = useI18n();
  const address = useAddress();
  const tt = useTerms();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const products = productList(commercial);
  const methods = methodsOf(commercial).filter((m) => m.status !== "inactive");
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [resources, setResources] = useState<Record<string, ProductResource[]>>({});
  const [lanes, setLanes] = useState<LanesState | null>(null);
  const ids = useMemo(() => products.map((p) => String(p.id)).join("|"), [products]);

  useEffect(() => {
    let cancelled = false;
    clientLanes().then(
      (v) => !cancelled && setLanes(v),
      () => undefined,
    );
    for (const id of ids.split("|").filter(Boolean)) {
      clientResources(id).then(
        (r) => !cancelled && setResources((prev) => ({ ...prev, [id]: r.resources })),
        () => undefined,
      );
    }
    return () => {
      cancelled = true;
    };
  }, [ids]);

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
          <h3 className="t-h2">{tt("rv.prod.title")}</h3>
          <p>{t("rv.prod.intro")}</p>
        </div>
        <LinkButton to={address({ to: "product", id: "new" })} variant="primary" leading={<Plus size={16} aria-hidden />}>
          {tt("rv.prod.add")}
        </LinkButton>
      </div>

      {failure ? <FailureNote failure={failure} /> : null}

      {products.length === 0 ? (
        <Glass level={1} className="rv-card">
          <Empty
            icon={<Package size={24} />}
            title={t("rv.prod.empty.title")}
            text={t("rv.prod.empty.text")}
            action={
              <LinkButton to={address({ to: "product", id: "new" })} variant="primary" leading={<Plus size={16} aria-hidden />}>
                {tt("rv.prod.add")}
              </LinkButton>
            }
          />
        </Glass>
      ) : (
        <ul className="rv-products" aria-label={t("rv.prod.title")}>
          {products.map((p) => {
            const id = String(p.id);
            const status = (typeof p.status === "string" ? p.status : "active") as keyof typeof STATUS_TINT;
            const price = priceSummary(p);
            const found = resources[id];
            const form = formOfProduct(p, lang);
            const counts = found ? zoneCounts(found) : { media: 0, docs: 0, testi: 0 };
            const checks = checklistOf({ form, counts, methods, ownMethodId: `pay-${id}`, ownLink: methods.some((m) => String(m.id) === `pay-${id}`) ? "x" : "" });
            const score = scoreOf(checks);
            const soldIn = lanes ? lanes.lanes.filter((l) => l.offers_all_products || l.products.includes(ALL_PRODUCTS) || l.products.includes(id)) : [];
            const chip = (key: string, ok: boolean, icon: React.ReactNode, label: string) => (
              <span key={key} className={ok ? "rv-have rv-have--ok" : "rv-have rv-have--todo"} data-testid={`have-${id}-${key}`}>
                {ok ? <Check size={13} strokeWidth={3} aria-hidden /> : icon}
                {label}
              </span>
            );
            const byKey = (k: string) => checks.find((c) => c.key === k)!;
            return (
              <li key={id}>
                <Glass level={1} className="rv-card rv-productcard" data-testid={`product-${id}`} data-status={status}>
                  <div className="rv-productcard__top">
                    <span className="rv-channel__icon rv-ent" data-ent="prd" aria-hidden>
                      <Package size={22} />
                    </span>
                    <div className="rv-row__main">
                      <span className="rv-productcard__name">
                        <Link to={address({ to: "product", id })} className="rv-row__title" dir="auto">
                          {localizedText(p.name, lang) || id}
                        </Link>
                        <Chip tint={STATUS_TINT[status] ?? "neutral"}>{t(`rv.prod.status.${status}` as MessageKey)}</Chip>
                      </span>
                      <span className="rv-row__sub" dir="auto">
                        {status === "inactive" ? t("rv.pl.hidden.help") : localizedText(p.summary, lang)}
                      </span>
                      <span className="rv-productcard__price" dir="auto">
                        {price.text || t(`rv.prod.price.${price.model}` as MessageKey)}
                      </span>
                    </div>
                    {status !== "inactive" ? (
                      <div className="rv-productcard__score" role="group" aria-label={t("rv.pl.complete", { pct: score })}>
                        <span className="c-2 t-body-sm tabular">{t("rv.pl.complete", { pct: score })}</span>
                        <div className="rv-progress" aria-hidden>
                          <div className="rv-progress__fill" style={{ width: `${score}%` }} />
                        </div>
                      </div>
                    ) : null}
                    <div className="rv-actions">
                      <LinkButton to={address({ to: "product", id })} size="sm" variant={score < 100 && status !== "inactive" ? "primary" : "secondary"}>
                        {score < 100 && status !== "inactive" ? t("rv.pl.edit") : t("rv.common.edit")}
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
                  </div>
                  {status !== "inactive" ? (
                    <div className="rv-productcard__have">
                      {found ? (
                        <>
                          {chip("media", byKey("media").ok, <ImageIcon size={13} aria-hidden />, t("rv.pe.aside.media", { n: counts.media }))}
                          {chip("docs", byKey("docs").ok, <FileText size={13} aria-hidden />, t("rv.pe.aside.docs", { n: counts.docs }))}
                          {chip("testi", byKey("testi").ok, <MessageSquareQuote size={13} aria-hidden />, t("rv.pe.aside.testi", { n: counts.testi }))}
                        </>
                      ) : (
                        <span className="c-3 t-body-sm">{t("rv.pl.checking")}</span>
                      )}
                      {chip("paylink", byKey("paylink").ok, <CreditCard size={13} aria-hidden />, t("rv.pe.aside.paylink"))}
                      {chip("payoff", byKey("payoff").ok, <CreditCard size={13} aria-hidden />, t("rv.pe.aside.payoff"))}
                      <span className="rv-productcard__sold">
                        <span className="c-3 t-body-sm">{t("rv.pl.soldIn")}</span>
                        {lanes ? (
                          soldIn.length > 0 ? (
                            soldIn.map((l) => (
                              <span key={l.id} className="rv-tag rv-ent" data-ent="src" dir="auto">
                                {l.source.label}
                              </span>
                            ))
                          ) : (
                            <span className="c-3 t-body-sm">{t("rv.pl.notSold")}</span>
                          )
                        ) : null}
                      </span>
                    </div>
                  ) : null}
                </Glass>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
