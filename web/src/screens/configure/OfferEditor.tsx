import { useState } from "react";
import { Package } from "lucide-react";
import { useI18n } from "../../i18n";
import type { Offer } from "../../mock/types";
import { useStore } from "../../mock/store";
import { Segmented, TextInput, Textarea, ToggleChips, VerbatimField } from "../../ui/Controls";
import { Group } from "./Group";
import { MarketsDrawer } from "./Markets";
import { OfferAssetEditor } from "./OfferAssets";

export function OfferEditor({ offer }: { offer: Offer }) {
  const { t } = useI18n();
  const { dispatch, state } = useStore();
  const viewer = state.role === "viewer";
  const [pricing, setPricing] = useState<"fixed" | "request" | "free">(offer.price === "On request" ? "request" : offer.price === "Free" ? "free" : "fixed");
  const [marketsOpen, setMarketsOpen] = useState(false);
  const update = (patch: Partial<Offer>) => dispatch({ type: "updateOffer", id: offer.id, patch });
  return (
    <>
      <hr className="divider" />
      <Group title={offer.name} hint={viewer ? t("offerEditor.viewerHint") : t("offerEditor.editHint")} icon={Package}>
        <fieldset className="cfg-fields" disabled={viewer}>
          <legend className="sr-only">{offer.name}</legend>
          <TextInput label={t("field.name")} value={offer.name} onChange={(e) => update({ name: e.target.value })} />
          <Textarea label={t("field.offerSummary")} value={offer.summary} onChange={(e) => update({ summary: e.target.value })} rows={2} />
          <div className="field">
            <span className="t-label c-2">{t("offerEditor.howPrice")}</span>
            <Segmented
              layoutId={`seg-pricing-${offer.id}`}
              label={t("offerEditor.howPrice")}
              value={pricing}
              onChange={(v) => {
                setPricing(v);
                if (v === "request") update({ price: "On request" });
                else if (v === "free") update({ price: "Free" });
                else if (offer.price === "On request" || offer.price === "Free" || offer.price === "Not set") update({ price: "" });
              }}
              options={[
                { value: "fixed", label: t("offerEditor.pricing.fixed") },
                { value: "request", label: t("offerEditor.pricing.request") },
                { value: "free", label: t("offerEditor.pricing.free") },
              ]}
            />
          </div>
          {pricing === "fixed" ? (
            <VerbatimField
              label={t("field.price")}
              value={offer.price === "On request" || offer.price === "Not set" ? "" : offer.price}
              onChange={(v) => update({ price: v || "Not set" })}
              placeholder={t("offerEditor.pricePlaceholder")}
              previewPrefix={t("runway.offerCosts", { name: offer.name })}
              previewSuffix="."
            />
          ) : null}
          <div className="field">
            <div className="field__row">
              <span className="t-label c-2">{t("offerEditor.whereSell")}</span>
              <button type="button" className="link-btn" onClick={() => setMarketsOpen(true)}>
                {t("offerEditor.manageCountries")}
              </button>
            </div>
            <ToggleChips label={t("common.markets")} value={offer.markets} onChange={(v) => update({ markets: v })} options={state.markets.map((m) => ({ value: m, label: m }))} />
          </div>
          <div className="field">
            <span className="t-label c-2">{t("offerEditor.availability")}</span>
            <Segmented
              layoutId={`seg-status-${offer.id}`}
              label={t("offerEditor.availability")}
              value={offer.status}
              onChange={(v) => update({ status: v })}
              options={[
                { value: "active", label: t("offers.status.active") },
                { value: "coming_soon", label: t("offers.status.comingSoon") },
                { value: "inactive", label: t("offers.status.inactive") },
              ]}
            />
          </div>
        </fieldset>
      </Group>
      <OfferAssetEditor offer={offer} />
      <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
    </>
  );
}
