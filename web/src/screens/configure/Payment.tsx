import { useState } from "react";
import { CreditCard } from "lucide-react";
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
  const { state } = useStore();
  const viewer = state.role === "viewer";
  return (
    <Group title={`How you get paid — ${market}`} hint="Exact values REVANSA gives a prospect, never rewritten." icon={CreditCard}>
      <div className="field">
        <span className="t-label c-2">Ways people can pay</span>
        <ToggleChips
          label="Payment methods"
          value={profile.methods}
          onChange={(v) => onChange({ methods: v })}
          options={[
            { value: "bank", label: "Bank transfer" },
            { value: "link", label: "Online payment link" },
            { value: "cash", label: "In person" },
          ]}
        />
      </div>
      <VerbatimField label="Exact payment details" multiline value={profile.instructions} onChange={(v) => onChange({ instructions: v })} previewPrefix="To pay: " />
      {profile.methods.includes("link") ? (
        <VerbatimField label="Online payment link" value={profile.link} onChange={(v) => onChange({ link: v })} placeholder="https://…" previewPrefix="Pay here: " />
      ) : null}
      <p className="c-3 t-body-sm">{viewer ? "Viewers can look but not edit." : "Only a person at your company can confirm that a payment arrived. REVANSA records what people say, never that they paid."}</p>
    </Group>
  );
}

/* ── Payment, scoped per target country: pick a market, edit its profile ── */
export function PaymentSection() {
  const { state, dispatch } = useStore();
  const [marketsOpen, setMarketsOpen] = useState(false);
  const [activeMarket, setActiveMarket] = useState(state.markets[0] ?? "");
  const market = state.markets.includes(activeMarket) ? activeMarket : (state.markets[0] ?? "");
  const profile = state.paymentProfiles.find((p) => p.market === market) ?? EMPTY_PROFILE(market);

  if (state.markets.length === 0) {
    return (
      <Group title="How you get paid" hint="Add a target country first, then set how you get paid there." icon={CreditCard}>
        <Button variant="secondary" onClick={() => setMarketsOpen(true)}>
          Manage target countries
        </Button>
        <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
      </Group>
    );
  }

  return (
    <>
      <div className="row row--wrap payment-market-picker">
        <Segmented layoutId="seg-payment-market" label="Market" value={market} onChange={setActiveMarket} options={state.markets.map((m) => ({ value: m, label: m }))} />
        <Button size="sm" variant="ghost" onClick={() => setMarketsOpen(true)}>
          Manage target countries
        </Button>
      </div>
      <PaymentProfileEditor market={market} profile={profile} onChange={(patch) => dispatch({ type: "setPaymentProfile", market, patch })} />
      <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
    </>
  );
}
