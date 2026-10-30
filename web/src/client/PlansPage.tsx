/** A Free client picks a paid plan and pays it by bank transfer: the administrator's bank details, the
 *  amount, then the receipt (PDF or image). Nothing is activated here: the administrator checks the
 *  receipt and approves it, which puts the client on the plan. */

import { useCallback, useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useI18n } from "../i18n";
import { Button } from "../ui/Button";
import { TextInput } from "../ui/Controls";
import { Glass } from "../ui/Glass";
import { bankDetails, paymentRequests, publicPlans, sendPaymentRequest, type PublicPlan } from "./accessApi";
import { ClientApiError } from "./api";
import { FailureNote, Loading, Note, PageHead, failureOf, type Failure as FailureShape } from "./kit";
import { useFetched } from "./useFetched";
import { useClientAccess } from "./UpgradeBanner";

const MAX_BYTES = 3 * 1024 * 1024;
const TYPES = ["application/pdf", "image/png", "image/jpeg", "image/webp"];

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

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function PayForm({ plan, onSent, onCancel }: { plan: PublicPlan; onSent: () => void; onCancel: () => void }) {
  const { t, n, locale } = useI18n();
  const loadBank = useCallback(() => bankDetails(locale), [locale]);
  const bank = useFetched(loadBank);
  const [reference, setReference] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<FailureShape | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setFailure(null);
    if (!file || !TYPES.includes(file.type) || file.size > MAX_BYTES) return setError(t("lp.pay.error.file"));
    setBusy(true);
    try {
      await sendPaymentRequest({ plan_id: plan.plan_id, reference, receipt: await readAsDataUrl(file) });
      onSent();
    } catch (err) {
      if (err instanceof ClientApiError && err.status === 409) setError(t("lp.pay.error.pending"));
      else if (err instanceof ClientApiError && err.status === 400) setError(t("lp.pay.error.file"));
      else setFailure(failureOf(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Glass level={3} tint="agent" as="form" className="rv-card pay-form" onSubmit={submit} noValidate aria-label={t("lp.pay.title", { name: plan.name })}>
      <h3>{t("lp.pay.title", { name: plan.name })}</h3>
      <h4>{t("lp.pay.step1")}</h4>
      {bank.loading ? <Loading /> : null}
      {bank.value ? (
        <div className="pay-bank">
          <strong>{bank.value.title}</strong>
          <p className="pay-text">{bank.value.body}</p>
        </div>
      ) : null}
      {bank.failed ? <Note tone="warn">{t("lp.pay.bank.none")}</Note> : null}
      <p>{t("lp.pay.amount", { amount: planPrice(plan, n) })}</p>
      <h4>{t("lp.pay.step2")}</h4>
      <TextInput label={t("lp.pay.reference")} name="reference" value={reference} maxLength={120} onChange={(e) => setReference(e.target.value)} disabled={busy} />
      <TextInput label={t("lp.pay.receipt")} name="receipt" type="file" accept={TYPES.join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={busy} required />
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {failure ? <FailureNote failure={failure} /> : null}
      <div className="pay-actions">
        <Button type="submit" variant="primary" loading={busy}>
          {t("lp.pay.send")}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
          {t("lp.pay.change")}
        </Button>
      </div>
    </Glass>
  );
}

export function PlansPage() {
  const { t, n } = useI18n();
  const access = useClientAccess();
  const [params] = useSearchParams();
  const selected = params.get("plan");
  const [chosen, setChosen] = useState<PublicPlan | null>(null);
  const [epoch, setEpoch] = useState(0);
  const loadPlans = useCallback(() => publicPlans(), []);
  const loadRequests = useCallback(() => (epoch >= 0 ? paymentRequests() : Promise.resolve([])), [epoch]);
  const plans = useFetched(loadPlans);
  const requests = useFetched(loadRequests);

  if (access?.tier === "paid") return <Navigate to="/account" replace />;

  const history = requests.value ?? [];
  const waiting = history.find((r) => r.status === "pending");
  const lastRejected = !waiting && history[0]?.status === "rejected" ? history[0] : null;

  return (
    <div className="rv-page">
      <PageHead title={t("lp.plans.title")} text={t("lp.plans.text")} />
      {access?.tier === "free" ? <Note tone="info">{t("lp.plans.free")}</Note> : null}
      {waiting ? (
        <Note tone="success" role="status">
          {t("lp.pay.pending", { name: waiting.plan_name })}
        </Note>
      ) : null}
      {lastRejected ? <Note tone="warn">{t("lp.pay.rejected", { reason: lastRejected.reason ?? "" })}</Note> : null}
      {plans.loading ? <Loading /> : null}
      {plans.failed ? <Note tone="warn">{t("lp.pricing.unavailable")}</Note> : null}

      {chosen && !waiting ? (
        <PayForm
          plan={chosen}
          onCancel={() => setChosen(null)}
          onSent={() => {
            setChosen(null);
            setEpoch((e) => e + 1);
          }}
        />
      ) : (
        <div className="plan-grid">
          {(plans.value ?? []).map((plan) => (
            <Glass key={plan.plan_id} level={plan.plan_id === selected ? 3 : 2} tint={plan.plan_id === selected ? "agent" : undefined} className="rv-card plan-card" data-testid={`plan-${plan.plan_id}`}>
              <h3>{plan.name}</h3>
              <p className="plan-card__price">
                {planPrice(plan, n)} <span className="c-3">{plan.billing_period === "annual" ? t("lp.pricing.year") : t("lp.pricing.month")}</span>
              </p>
              {plan.plan_id === selected ? <span className="c-2 t-body-sm">{t("lp.plans.selected")}</span> : null}
              <PlanFacts plan={plan} />
              <Button variant="primary" disabledReason={waiting ? t("lp.pay.error.pending") : undefined} onClick={() => setChosen(plan)}>
                {t("lp.plans.choose", { name: plan.name })}
              </Button>
            </Glass>
          ))}
        </div>
      )}

      {history.length > 0 ? (
        <section aria-label={t("lp.pay.history")}>
          <h3>{t("lp.pay.history")}</h3>
          <ul className="pay-history">
            {history.map((r) => (
              <li key={r.request_id}>
                {r.plan_name} · {r.status === "pending" ? t("lp.pay.status.pending") : r.status === "approved" ? t("lp.pay.status.approved") : t("lp.pay.status.rejected")}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
