import { useState } from "react";
import { CreditCard } from "lucide-react";
import { useI18n } from "../../i18n";
import type { PaymentProfile } from "../../mock/types";
import { useStore } from "../../mock/store";
import { Button } from "../../ui/Button";
import { Segmented, ToggleChips, VerbatimField } from "../../ui/Controls";
import { Group } from "./Group";
import { MarketsDrawer } from "./Markets";

const EMPTY_PROFILE = (market: string): PaymentProfile => ({ market, methods: [], instructions: "", link: "" });

/** How you get paid, for one target market. Payment differs by country (different methods,
 *  different bank details, a different link), so this editor is scoped to a single market —
 *  reused as-is by Runway's onboarding "payment" gate. */
export function PaymentProfileEditor({ market, profile, onChange }: { market: string; profile: PaymentProfile; onChange: (patch: Partial<Omit<PaymentProfile, "market">>) => void }) {
  const { t } = useI18n();
  const { state } = useStore();
  const viewer = state.role === "viewer";
  return (
    <Group title={t("payment.howYouGetPaidFor", { market })} hint={t("payment.exactValuesHint", { brand: t("brand.name") })} icon={CreditCard}>
      <div className="field">
        <span className="t-label c-2">{t("payment.waysToPay")}</span>
        <ToggleChips
          label={t("payment.methods")}
          value={profile.methods}
          onChange={(v) => onChange({ methods: v })}
          options={[
            { value: "bank", label: t("payment.method.bank") },
            { value: "link", label: t("payment.method.link") },
            { value: "cash", label: t("payment.method.cash") },
          ]}
        />
      </div>
      <VerbatimField label={t("payment.exactDetails")} multiline value={profile.instructions} onChange={(v) => onChange({ instructions: v })} previewPrefix={t("payment.toPayPrefix")} />
      {profile.methods.includes("link") ? (
        <VerbatimField label={t("payment.method.link")} value={profile.link} onChange={(v) => onChange({ link: v })} placeholder={t("assets.link.placeholder")} previewPrefix={t("payment.payHerePrefix")} />
      ) : null}
      <p className="c-3 t-body-sm">{viewer ? t("payment.viewersLookOnly") : t("payment.onlyPersonCanConfirm", { brand: t("brand.name") })}</p>
    </Group>
  );
}

/* ── Payment, scoped per target country: pick a market, edit its profile ── */
export function PaymentSection() {
  const { t } = useI18n();
  const { state, dispatch } = useStore();
  const [marketsOpen, setMarketsOpen] = useState(false);
  const [activeMarket, setActiveMarket] = useState(state.markets[0] ?? "");
  const market = state.markets.includes(activeMarket) ? activeMarket : (state.markets[0] ?? "");
  const profile = state.paymentProfiles.find((p) => p.market === market) ?? EMPTY_PROFILE(market);

  if (state.markets.length === 0) {
    return (
      <Group title={t("payment.howYouGetPaid")} hint={t("payment.addCountryFirst")} icon={CreditCard}>
        <Button variant="secondary" onClick={() => setMarketsOpen(true)}>
          {t("offerEditor.manageCountries")}
        </Button>
        <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
      </Group>
    );
  }

  return (
    <>
      <div className="row row--wrap payment-market-picker">
        <Segmented layoutId="seg-payment-market" label={t("common.market")} value={market} onChange={setActiveMarket} options={state.markets.map((m) => ({ value: m, label: m }))} />
        <Button size="sm" variant="ghost" onClick={() => setMarketsOpen(true)}>
          {t("offerEditor.manageCountries")}
        </Button>
      </div>
      <PaymentProfileEditor market={market} profile={profile} onChange={(patch) => dispatch({ type: "setPaymentProfile", market, patch })} />
      <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
    </>
  );
}
