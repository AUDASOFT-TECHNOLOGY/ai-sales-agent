import { useState, useId } from "react";
import { motion } from "motion/react";
import { Check, Paperclip, ShieldCheck } from "lucide-react";
import { useI18n } from "../i18n";
import type { Conversation } from "../mock/types";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Overlays";
import { TextInput } from "../ui/Controls";
import { TENANT } from "../mock/data";

type Progress = Conversation["payment"];

/** The Payment Seal (doc 18.4). The agent never confirms a payment: two amber, hollow, unfinished nodes,
 *  then a solid emerald node with an engraved champagne seal that only a person can produce. */
export function PaymentSeal({
  progress,
  confirmedBy = "Sami K.",
  canConfirm,
  onReview,
}: {
  progress: Progress;
  confirmedBy?: string;
  canConfirm: boolean;
  onReview: () => void;
}) {
  const { t } = useI18n();
  const claimed = progress === "claimed" || progress === "proof_received" || progress === "paid";
  const proof = progress === "proof_received" || progress === "paid";
  const paid = progress === "paid";
  if (progress === "none") return null;
  return (
    <section className="seal" aria-label={t("convo.payment")} data-paid={paid || undefined}>
      <ol className="seal__track">
        <li className="seal__node" data-on={claimed || undefined}>
          <span className="seal__dot seal__dot--hollow" data-on={claimed || undefined} />
          <span className="t-label">{t("payment.claimed")}</span>
        </li>
        <li className="seal__node" data-on={proof || undefined}>
          <span className="seal__dot seal__dot--hollow" data-on={proof || undefined}>
            {proof ? <Paperclip size={11} aria-hidden /> : null}
          </span>
          <span className="t-label">{t("payment.proof")}</span>
        </li>
        <li className="seal__node seal__node--final" data-on={paid || undefined}>
          {paid ? (
            <motion.span
              className="seal__stamp"
              initial={{ scale: 1.35, opacity: 0, rotate: -12 }}
              animate={{ scale: 1, opacity: 1, rotate: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 18 }}
            >
              <SealArt />
              <Check size={16} strokeWidth={3} aria-hidden className="seal__check" />
              <span className="seal__sheen" />
            </motion.span>
          ) : (
            <span className="seal__dot seal__dot--empty" />
          )}
          <span className="t-label">{paid ? t("payment.paid", { name: confirmedBy }) : t("payment.confirmedByPerson")}</span>
        </li>
      </ol>
      {!paid ? (
        <div className="seal__foot">
          <p className="c-attention t-body-sm">{t("payment.unverified", { client: TENANT.name })}</p>
          <Button
            variant="success"
            size="sm"
            leading={<ShieldCheck size={16} aria-hidden />}
            onClick={onReview}
            disabledReason={canConfirm ? undefined : t("payment.waitingPermission")}
          >
            {t("payment.review")}
          </Button>
        </div>
      ) : (
        <p className="c-3 t-body-sm">{t("payment.recorded")}</p>
      )}
    </section>
  );
}

/** Engraved seal ring: conic champagne gradient plus a fine guilloche pattern. */
function SealArt() {
  const id = useId();
  return (
    <svg viewBox="0 0 40 40" width={40} height={40} aria-hidden className="seal__art">
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--champagne-300)" />
          <stop offset="0.5" stopColor="var(--champagne-500)" />
          <stop offset="1" stopColor="var(--emerald-400)" />
        </linearGradient>
        <radialGradient id={`${id}-f`} cx="35%" cy="30%" r="75%">
          <stop offset="0" stopColor="var(--emerald-400)" />
          <stop offset="1" stopColor="var(--emerald-600)" />
        </radialGradient>
      </defs>
      <circle cx="20" cy="20" r="18.2" fill="none" stroke={`url(#${id}-g)`} strokeWidth="2.4" />
      <g fill="none" stroke="var(--champagne-300)" strokeOpacity="0.28" strokeWidth="0.5">
        {Array.from({ length: 12 }, (_, i) => (
          <ellipse key={i} cx="20" cy="20" rx="15" ry="6.2" transform={`rotate(${i * 15} 20 20)`} />
        ))}
      </g>
      <circle cx="20" cy="20" r="11.5" fill={`url(#${id}-f)`} />
    </svg>
  );
}

/** Confirmation dialog: evidence, who confirms, one required attestation. */
export function ConfirmPaymentDialog({
  open,
  onOpenChange,
  name,
  claimExcerpt,
  proofName,
  canConfirm = true,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  name: string;
  claimExcerpt: string;
  proofName?: string;
  canConfirm?: boolean;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  const [checked, setChecked] = useState(false);
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const boxId = useId();
  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setChecked(false);
      }}
      title={t("payment.confirmTitle", { name })}
      description={t("payment.unverified", { client: TENANT.name })}
      size="md"
      tint="success"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("action.cancel")}
          </Button>
          <Button
            variant="success"
            leading={<ShieldCheck size={18} aria-hidden />}
            disabledReason={!canConfirm ? t("payment.waitingPermission") : checked ? undefined : t("payment.tickBox")}
            onClick={() => {
              onConfirm();
              setChecked(false);
            }}
          >
            {t("payment.confirm")}
          </Button>
        </>
      }
    >
      <div className="evidence-block">
        <p className="t-label c-3">{t("payment.whatSaid", { name: name.split(" ")[0] })}</p>
        <blockquote className="evidence__quote">“{claimExcerpt}”</blockquote>
        {proofName ? (
          <p className="row c-2 t-body-sm">
            <Paperclip size={14} aria-hidden /> {proofName}
            <span className="chip" data-tint="attention" data-dashed>
              {t("payment.possibleProof")}
            </span>
          </p>
        ) : null}
      </div>
      <div className="grid-2">
        <TextInput label={t("payment.amount")} optional="optional" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={t("payment.amount.placeholder")} inputMode="decimal" />
        <TextInput label={t("payment.reference")} optional="optional" value={reference} onChange={(e) => setReference(e.target.value)} placeholder={t("payment.reference.placeholder")} />
      </div>
      <div className="row well attest">
        <span className="avatar-sm">SK</span>
        <div>
          <p className="t-label">{t("payment.confirmingAs", { name: "Sami K." })}</p>
          <p className="c-3 t-body-sm">{t("payment.ownerYou")}</p>
        </div>
      </div>
      <label htmlFor={boxId} className="check">
        <input id={boxId} type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        <span className="check__box" aria-hidden>
          <Check size={14} strokeWidth={3} />
        </span>
        <span>{t("payment.checkbox")}</span>
      </label>
      <p className="c-3 t-body-sm">{t("payment.recorded")}</p>
    </Modal>
  );
}
