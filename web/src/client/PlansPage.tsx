/** A Free client picks a paid plan. The plans are the administrator's (read from the server); choosing
 *  one tells Revansa, which confirms it and assigns it: nothing is charged or activated here. */

import { useCallback, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useI18n } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { publicPlans, requestPlan, type PublicPlan } from "./accessApi";
import { FailureNote, Loading, Note, PageHead, failureOf, type Failure as FailureShape } from "./kit";
import { useFetched } from "./useFetched";
import { useClientAccess } from "./UpgradeBanner";

export function PlanFacts({ plan }: { plan: PublicPlan }) {
  const { t, n } = useI18n();
  return (
    <ul className="plan-facts">
      <li>{t("lp.pricing.credits", { n: n(plan.included_credits) })}</li>
      <li>{t("lp.pricing.quota", { n: n(plan.daily_quota) })}</li>
    </ul>
  );
}

export function planPrice(plan: PublicPlan, n: (v: number, o?: Intl.NumberFormatOptions) => string): string {
  return n(Number(plan.monthly_price), { style: "currency", currency: plan.currency, maximumFractionDigits: 0 });
}

export function PlansPage() {
  const { t, n } = useI18n();
  const access = useClientAccess();
  const [params] = useSearchParams();
  const selected = params.get("plan");
  const load = useCallback(() => publicPlans(), []);
  const plans = useFetched(load);
  const [busy, setBusy] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [failure, setFailure] = useState<FailureShape | null>(null);

  if (access?.tier === "paid") return <Navigate to="/account" replace />;

  async function choose(plan: PublicPlan) {
    setBusy(plan.plan_id);
    setFailure(null);
    try {
      await requestPlan(plan.plan_id);
      setSent(plan.name);
    } catch (err) {
      setFailure(failureOf(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rv-page">
      <PageHead title={t("lp.plans.title")} text={t("lp.plans.text")} />
      {access?.tier === "free" ? <Note tone="info">{t("lp.plans.free")}</Note> : null}
      {sent ? (
        <Note tone="success" role="status">
          {t("lp.plans.sent", { name: sent })}
        </Note>
      ) : null}
      {failure ? <FailureNote failure={failure} /> : null}
      {plans.loading ? <Loading /> : null}
      {plans.failed ? <Note tone="warn">{t("lp.pricing.unavailable")}</Note> : null}
      <div className="plan-grid">
        {(plans.value ?? []).map((plan) => (
          <Glass key={plan.plan_id} level={plan.plan_id === selected ? 3 : 2} tint={plan.plan_id === selected ? "agent" : undefined} className="rv-card plan-card" data-testid={`plan-${plan.plan_id}`}>
            <h3>{plan.name}</h3>
            <p className="plan-card__price">
              {planPrice(plan, n)} <span className="c-3">{plan.billing_period === "annual" ? t("lp.pricing.year") : t("lp.pricing.month")}</span>
            </p>
            {plan.plan_id === selected ? <span className="c-2 t-body-sm">{t("lp.plans.selected")}</span> : null}
            <PlanFacts plan={plan} />
            <Button variant="primary" loading={busy === plan.plan_id} onClick={() => void choose(plan)}>
              {t("lp.plans.choose", { name: plan.name })}
            </Button>
          </Glass>
        ))}
      </div>
    </div>
  );
}
