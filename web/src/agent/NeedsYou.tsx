import { motion } from "motion/react";
import { ArrowRight, Check, CreditCard, HandHelping, Pencil, PenLine, RotateCcw, X } from "lucide-react";
import { useI18n } from "../i18n";
import type { NeedItem, Prospect } from "../mock/types";
import { Bead } from "../ui/Bead";
import { Button } from "../ui/Button";
import type { Tint } from "../ui/Glass";
import { Modal } from "../ui/Overlays";

export const NEED_KIND: Record<NeedItem["kind"], { tint: Tint; icon: typeof PenLine; label: string; cta: string }> = {
  draft: { tint: "attention", icon: PenLine, label: "Message to approve", cta: "Review" },
  handoff: { tint: "attention", icon: HandHelping, label: "Conversation for you", cta: "Review" },
  payment: { tint: "attention", icon: CreditCard, label: "Payment to confirm", cta: "Review" },
  failed: { tint: "danger", icon: RotateCcw, label: "Failed action", cta: "Review" },
};

/** One decision that belongs to a person, as a calm row: who, why, one action (doc 19.5).
 *  Everything else (the draft, the secondary actions) opens on demand in NeedDetailDialog. */
export function NeedRow({ item, prospect, onOpen }: { item: NeedItem; prospect: Prospect; onOpen: () => void }) {
  const cfg = NEED_KIND[item.kind];
  const Icon = cfg.icon;
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.2 } }}
      transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
      className="need-row"
      data-tint={cfg.tint}
    >
      <Bead initials={prospect.initials} attention="waiting" size={48} />
      <div className="need-row__text">
        <p className="need-row__kind t-label">
          <Icon size={14} aria-hidden /> {cfg.label}
        </p>
        <p className="t-h3">{prospect.name}</p>
        <p className="voice voice-s">{item.reason}</p>
        <p className="need-row__meta c-3 t-body-sm">
          {item.factors?.[0] ? <>{item.factors[0]} · </> : null}
          {item.waiting}
        </p>
      </div>
      <Button variant="primary" onClick={onOpen} trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
        {cfg.cta}
      </Button>
    </motion.li>
  );
}

interface NeedDetailProps {
  item: NeedItem | null;
  prospect: Prospect | undefined;
  onClose: () => void;
  onApprove: () => void;
  onDecline: () => void;
  onEdit: () => void;
  onTakeOver: () => void;
}

/** The detail behind a row: the reason, the proposed message when there is one, and the actions. */
export function NeedDetailDialog({ item, prospect, onClose, onApprove, onDecline, onEdit, onTakeOver }: NeedDetailProps) {
  const { t } = useI18n();
  const cfg = item ? NEED_KIND[item.kind] : null;
  return (
    <Modal
      open={item !== null && prospect !== undefined}
      onOpenChange={(o) => !o && onClose()}
      title={prospect?.name ?? ""}
      description={cfg ? `${cfg.label} · ${item?.waiting}` : undefined}
      tint="attention"
      footer={
        item?.kind === "draft" ? (
          <>
            <div className="row">
              <Button variant="ghost" leading={<X size={16} aria-hidden />} onClick={onDecline}>
                {t("action.decline")}
              </Button>
              <Button variant="secondary" leading={<Pencil size={16} aria-hidden />} onClick={onEdit}>
                {t("action.edit")}
              </Button>
            </div>
            <Button variant="primary" leading={<Check size={18} aria-hidden />} onClick={onApprove}>
              {t("action.approve")}
            </Button>
          </>
        ) : item?.kind === "handoff" ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              {t("action.cancel")}
            </Button>
            <Button variant="primary" leading={<HandHelping size={18} aria-hidden />} onClick={onTakeOver}>
              {t("action.takeOver")}
            </Button>
          </>
        ) : undefined
      }
    >
      {item ? (
        <>
          <p className="voice voice-m voice-block">{item.reason}</p>
          {item.factors && item.factors.length > 0 ? (
            <div className="stack">
              <p className="t-label c-3">Why now</p>
              <ul className="why-list">
                {item.factors.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {item.preview ? (
            <div className="stack">
              <p className="t-label c-3">Message I wrote</p>
              <p className="need-preview">{item.preview}</p>
            </div>
          ) : null}
          {prospect ? <p className="c-2">{prospect.place} · last message {prospect.last}</p> : null}
        </>
      ) : null}
    </Modal>
  );
}
