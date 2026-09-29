import { Bot, CheckCircle2, CircleDot, CreditCard, Hand, Sparkles, Ban } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import type { LostReason, ProspectStatus } from "../mock/types";
import { Chip } from "../ui/Chip";
import type { Tint } from "../ui/Glass";

const PROSPECT: Record<ProspectStatus, { tint: Tint; icon: typeof Bot }> = {
  new: { tint: "action", icon: CircleDot },
  active: { tint: "agent", icon: Sparkles },
  paused: { tint: "attention", icon: Hand },
  lost: { tint: "danger", icon: Ban },
  closed: { tint: "neutral", icon: CheckCircle2 },
};

/** Prospect status in the client's words, never the internal enum (doc 15.2, 15.6). A "paused"
 *  prospect isn't always waiting on a decision — the agent also pauses when it can't understand the
 *  prospect, or when the prospect asks for a person — so the generic label is "Needs you", not
 *  "Needs your decision". The one case worth naming precisely is a payment claim at the payment
 *  stage: that gets its own "Payment to confirm" chip, matching the label already used for it on the
 *  Dashboard's Needs You list. A "lost" prospect keeps the reason it closed for (declined outright,
 *  went silent through the whole follow-up cadence, or the agent inferred disinterest) so the client
 *  can see why without opening the dossier. */
export function ProspectStatusChip({ status, stage, lostReason }: { status: ProspectStatus; stage?: string; lostReason?: LostReason }) {
  const { t } = useI18n();
  const cfg = PROSPECT[status];
  const isPaymentClaim = status === "paused" && stage === "registration_payment";
  const Icon = isPaymentClaim ? CreditCard : cfg.icon;
  const label = isPaymentClaim
    ? t("status.paused.payment")
    : status === "lost" && lostReason
      ? t(`status.lost.${lostReason}` as MessageKey)
      : t(`status.${status}` as MessageKey);
  return (
    <Chip tint={cfg.tint} glyph={<Icon size={13} aria-hidden />}>
      {label}
    </Chip>
  );
}

export function MilestoneChip({ won, label }: { won?: boolean; label: string }) {
  return <Chip tint={won ? "success" : "neutral"} outline>{label}</Chip>;
}

