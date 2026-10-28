/** The card beside a conversation: everything the owner wants to know about this prospect without
 *  leaving the thread: where they come from (circuit and source), by which channel, which products the
 *  agent offers them, their commercial status, what happens next, whether they announced a payment and
 *  whether something failed. Every line comes from the server; one that is not known is left out. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useI18n, type MessageKey } from "../i18n";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { ALL_PRODUCTS, clientLanes, clientPayments, type ConversationHistory, type Lane, type LanesState, type PaymentCase, type ProspectRow } from "../client/api";

/** The client's circuits and payment cases, read once for the cards of a session. A failure only
 *  removes the lines that depend on them. */
export function useCardContext(): { lanes: LanesState | null; payments: PaymentCase[] | null } {
  const [lanes, setLanes] = useState<LanesState | null>(null);
  const [payments, setPayments] = useState<PaymentCase[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    clientLanes().then(
      (v) => !cancelled && setLanes(v),
      () => undefined,
    );
    clientPayments().then(
      (v) => !cancelled && setPayments(v.cases),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return { lanes, payments };
}

export function laneOf(lanes: LanesState | null, prospect: ProspectRow | null | undefined): Lane | null {
  if (!lanes || !prospect?.lane_id) return null;
  return lanes.lanes.find((l) => l.id === prospect.lane_id) ?? null;
}

export function laneProducts(lanes: LanesState, lane: Lane, allLabel: string): string[] {
  if (lane.offers_all_products || lane.products.includes(ALL_PRODUCTS)) return [allLabel];
  return lane.products.map((id) => lanes.catalog.find((c) => c.id === id)?.name ?? id);
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rv-kv2">
      <span className="rv-kv2__k">{label}</span>
      <span className="rv-kv2__v">{children}</span>
    </div>
  );
}

export function ProspectFiche({ history, lanes, payments }: { history: ConversationHistory | undefined; lanes: LanesState | null; payments: PaymentCase[] | null }) {
  const { t, d } = useI18n();
  const prospect = history?.prospect ?? null;
  if (!prospect) return null;
  const lane = laneOf(lanes, prospect);
  const pay = payments?.find((c) => c.prospect_id === prospect.id) ?? null;
  const when = (iso: string) => d(iso, { dateStyle: "medium", timeStyle: "short" });
  const next = history?.next_follow_up ?? (prospect.next_action_type && prospect.next_action_due_at ? { action_type: prospect.next_action_type, due_at: prospect.next_action_due_at } : null);
  const errors = history?.errors ?? [];
  const contact = prospect.email ?? prospect.phone;

  return (
    <Glass level={1} className="rv-fiche" as="aside" aria-label={t("rv.conv.fiche.title")} data-testid="prospect-fiche">
      <h3 className="rv-fiche__title">{t("rv.conv.fiche.title")}</h3>
      <Line label={t("rv.conv.fiche.status")}>
        <span className="rv-tags">
          <Chip tint={prospect.status === "active" ? "success" : prospect.status === "paused" ? "attention" : prospect.status === "opted_out" ? "danger" : "neutral"}>{t(`crm.status.${prospect.status}` as MessageKey)}</Chip>
          {prospect.stage ? <span dir="auto">{prospect.stage}</span> : null}
        </span>
      </Line>
      <Line label={t("rv.conv.fiche.next")}>
        {next ? (
          <>
            {t(`crm.action.${next.action_type}` as MessageKey)}
            <span className="c-3"> · {when(next.due_at)}</span>
          </>
        ) : (
          <span className="c-3">{t("rv.conv.fiche.none")}</span>
        )}
      </Line>
      {lane && lanes ? (
        <>
          <Line label={t("rv.conv.fiche.circuit")}>
            <span className="rv-tag rv-ent" data-ent="src" dir="auto">
              {lane.source.label}
            </span>
          </Line>
          <Line label={t("rv.conv.fiche.channel")}>
            <span className="rv-tag rv-ent" data-ent="chn">
              {lane.channel.label}
            </span>
          </Line>
          <Line label={t("rv.conv.fiche.products")}>
            <span className="rv-tags">
              {laneProducts(lanes, lane, t("rv.conv.fiche.agentAll")).map((p) => (
                <span key={p} className="rv-tag rv-ent" data-ent="prd" dir="auto">
                  {p}
                </span>
              ))}
            </span>
          </Line>
        </>
      ) : prospect.preferred_channel ? (
        <Line label={t("rv.conv.fiche.channel")}>
          <span className="rv-tag rv-ent" data-ent="chn">
            {prospect.preferred_channel}
          </span>
        </Line>
      ) : null}
      {contact ? (
        <Line label={t("rv.conv.fiche.contact")}>
          <span dir="ltr">{contact}</span>
        </Line>
      ) : null}
      <Line label={t("rv.conv.fiche.payment")}>
        {pay ? (
          <span className="rv-tags">
            <Chip tint={pay.progress === "paid" ? "success" : "attention"}>{t(`rv.conv.fiche.payment.${pay.progress === "paid" ? "paid" : pay.progress === "proof_received" ? "proof" : "claimed"}` as MessageKey)}</Chip>
            {pay.progress !== "paid" ? (
              <Link to="/review/payments" className="link-btn">
                {t("rv.conv.fiche.payment.check")}
              </Link>
            ) : null}
          </span>
        ) : (
          <span className="c-3">{t("rv.conv.fiche.payment.none")}</span>
        )}
      </Line>
      <Line label={t("rv.conv.fiche.followups")}>{prospect.follow_up_count}</Line>
      <Line label={t("rv.conv.fiche.errors")}>
        {errors.length > 0 || prospect.has_error ? (
          <span className="c-danger" data-testid="fiche-errors">
            {errors.length > 0 ? errors.length : t("rv.conv.error")}
          </span>
        ) : (
          <span className="row">
            <span className="rv-dot rv-dot--ok" aria-hidden />
            <span className="c-3">{t("rv.conv.fiche.noErrors")}</span>
          </span>
        )}
      </Line>
    </Glass>
  );
}
