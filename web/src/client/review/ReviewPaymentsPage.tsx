/** Payments to confirm. A buyer saying they paid, or sending a receipt, proves nothing: the payment
 *  counts as paid only when the client has checked it and confirms it here. Revansa is not a payment
 *  gateway and never confirms a payment by itself; the amount, currency and reference are what the
 *  client types after checking its own account. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BadgeCheck, CreditCard, RefreshCw } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import { Modal } from "../../ui/Overlays";
import { clientConfirmPayment, clientPayments, type PaymentCase, type PaymentsState } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { Card, Empty, FailureNote, Loading, Note, failureOf, type Failure } from "../kit";

export function ReviewPaymentsPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [data, setData] = useState<PaymentsState | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [target, setTarget] = useState<PaymentCase | null>(null);
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("");
  const [reference, setReference] = useState("");
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [modalFailure, setModalFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await clientPayments());
      setFailure(null);
    } catch (err) {
      const f = failureOf(err);
      setFailure(f);
      if (f.signedOut) void logout();
    }
  }, [logout]);

  useEffect(() => {
    let cancelled = false;
    clientPayments().then(
      (value) => !cancelled && setData(value),
      (err: unknown) => !cancelled && setFailure(failureOf(err)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  function open(c: PaymentCase) {
    setTarget(c);
    setAmount("");
    setCurrency("");
    setReference("");
    setProblem(null);
    setModalFailure(null);
  }

  async function confirm() {
    if (!target) return;
    if (!(Number(amount.replace(",", ".")) > 0)) return setProblem("rv.pay2.err.amount");
    if (!/^[A-Za-z]{3}$/.test(currency.trim())) return setProblem("rv.pay2.err.currency");
    if (reference.trim() === "") return setProblem("rv.pay2.err.reference");
    setProblem(null);
    setBusy(true);
    setModalFailure(null);
    try {
      await clientConfirmPayment(target.prospect_id, { amount: amount.trim().replace(",", "."), currency: currency.trim().toUpperCase(), reference: reference.trim() });
      setTarget(null);
      setNotice(true);
      await load();
    } catch (err) {
      const f = failureOf(err);
      setModalFailure(f);
      if (f.signedOut) void logout();
    }
    setBusy(false);
  }

  const pending = data?.cases.filter((c) => c.progress !== "paid") ?? [];
  const paid = data?.cases.filter((c) => c.progress === "paid") ?? [];
  const who = (c: PaymentCase) => c.prospect_name ?? t("rv.pay2.unknownProspect");

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.pay2.title")}</h3>
          <p>{t("rv.pay2.intro")}</p>
        </div>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void load()}>
          {t("rv.common.refresh")}
        </Button>
      </div>

      {failure ? <FailureNote failure={failure} onRetry={() => void load()} /> : null}
      {notice ? <Note tone="success">{t("rv.pay2.done")}</Note> : null}

      {!data ? (
        failure ? null : <Loading />
      ) : (
        <>
          {pending.length === 0 ? (
            <Card>
              <Empty icon={<CreditCard size={24} />} title={t("rv.pay2.empty.title")} text={t("rv.pay2.empty.text")} />
            </Card>
          ) : (
            <Card flush aria-label={t("rv.pay2.title")}>
              <ul className="rv-list">
                {pending.map((c) => (
                  <li key={c.prospect_id} className="rv-product" data-testid={`payment-${c.prospect_id}`}>
                    <div className="rv-row__main">
                      <Link to={`/conversations/${encodeURIComponent(c.prospect_id)}`} className="rv-row__title" dir="auto">
                        {who(c)}
                      </Link>
                      <span className="rv-row__sub">
                        {t(c.progress === "proof_received" ? "rv.pay2.proof" : "rv.pay2.claimed")}
                        {c.signalled_at ? ` · ${d(c.signalled_at, { dateStyle: "medium", timeStyle: "short" })}` : ""}
                      </span>
                    </div>
                    <Chip tint="attention">{t("rv.pay2.toCheck")}</Chip>
                    <Button variant="primary" size="sm" leading={<BadgeCheck size={16} aria-hidden />} onClick={() => open(c)}>
                      {t("rv.pay2.confirm")}
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {paid.length > 0 ? (
            <section className="rv-page" aria-labelledby="paid-h">
              <h3 className="t-h2" id="paid-h">
                {t("rv.pay2.paid")}
              </h3>
              <Card flush>
                <ul className="rv-list">
                  {paid.map((c) => (
                    <li key={c.prospect_id} className="rv-product" data-testid={`paid-${c.prospect_id}`}>
                      <div className="rv-row__main">
                        <Link to={`/conversations/${encodeURIComponent(c.prospect_id)}`} className="rv-row__title" dir="auto">
                          {who(c)}
                        </Link>
                        <span className="rv-row__sub" dir="auto">
                          {[c.amount && c.currency ? `${c.amount} ${c.currency}` : null, c.reference ? `${t("rv.pay2.reference")}: ${c.reference}` : null, c.confirmed_at ? d(c.confirmed_at, { dateStyle: "medium", timeStyle: "short" }) : null]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </div>
                      <Chip tint="success">{t("rv.pay2.confirmed")}</Chip>
                    </li>
                  ))}
                </ul>
              </Card>
            </section>
          ) : null}
        </>
      )}

      <Modal
        open={target !== null}
        onOpenChange={(openState) => !openState && !busy && setTarget(null)}
        title={t("rv.pay2.modal.title", { name: target ? who(target) : "" })}
        description={t("rv.pay2.modal.text")}
        footer={
          <>
            <Button onClick={() => setTarget(null)} disabled={busy}>
              {t("rv.common.cancel")}
            </Button>
            <Button variant="primary" onClick={() => void confirm()} loading={busy}>
              {t("rv.pay2.modal.confirm")}
            </Button>
          </>
        }
      >
        {modalFailure ? <FailureNote failure={modalFailure} /> : null}
        <div className="rv-grid-2">
          <TextInput label={t("rv.pay2.amount")} value={amount} onChange={(e) => setAmount(e.target.value)} error={problem === "rv.pay2.err.amount" ? t(problem) : undefined} inputMode="decimal" dir="ltr" />
          <TextInput label={t("rv.pay2.currency")} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} error={problem === "rv.pay2.err.currency" ? t(problem) : undefined} maxLength={3} placeholder="TND" dir="ltr" />
        </div>
        <TextInput label={t("rv.pay2.reference")} helper={t("rv.pay2.referenceHelp")} value={reference} onChange={(e) => setReference(e.target.value)} error={problem === "rv.pay2.err.reference" ? t(problem) : undefined} maxLength={200} />
      </Modal>
    </div>
  );
}
