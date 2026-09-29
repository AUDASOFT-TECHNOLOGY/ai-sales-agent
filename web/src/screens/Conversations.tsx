import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { AnimatePresence } from "motion/react";
import { ChevronLeft, PanelRight, ShieldCheck, Sparkles } from "lucide-react";
import { useI18n } from "../i18n";
import { USER } from "../mock/data";
import { useStore } from "../mock/store";
import { AgentNotes, Composer, DraftBubble, HandoffBanner, MessageBubble } from "../agent/Conversation";
import { ConfirmPaymentDialog, PaymentSeal } from "../agent/PaymentSeal";
import { ProspectStatusChip } from "../agent/StatusChips";
import { Bead } from "../ui/Bead";
import { Button } from "../ui/Button";
import { Drawer } from "../ui/Overlays";
import { Glass } from "../ui/Glass";
import { useMedia } from "../ui/useMedia";
import { cx } from "../ui/cx";

export function Conversations() {
  const { t } = useI18n();
  const { id } = useParams();
  const navigate = useNavigate();
  const { state, dispatch, toast, canConfirmPayments } = useStore();
  const wide = useMedia("(min-width: 1024px)");
  const [confirming, setConfirming] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const endRef = useRef<HTMLLIElement>(null);

  const convo = state.conversations.find((c) => c.prospectId === id);
  const prospect = state.prospects.find((p) => p.id === id);
  const tookOver = !!prospect && prospect.next === "You are handling this conversation";

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [convo?.messages.length, convo?.draft]);

  if (wide && !id) {
    const first = state.conversations.find((c) => c.needsYou) ?? state.conversations[0];
    if (first) return <Navigate to={`/conversations/${first.prospectId}`} replace />;
  }

  const list = (
    <Glass className="convo-list" aria-label={t("convo.title")}>
      <div className="convo-list__head">
        <h2 className="t-h2">{t("convo.title")}</h2>
      </div>
      <ul>
        {state.conversations.map((c) => {
          const p = state.prospects.find((x) => x.id === c.prospectId)!;
          return (
            <li key={c.id}>
              <Link to={`/conversations/${c.prospectId}`} className={cx("convo-item", c.prospectId === id && "is-active")} aria-current={c.prospectId === id ? "page" : undefined}>
                <Bead initials={p.initials} attention={c.needsYou ? "waiting" : p.attention} size={40} />
                <span className="convo-item__text">
                  <span className="convo-item__name">
                    <span className="t-body-sm ledger__strong">{p.name}</span>
                    {c.needsYou ? <span className="chip" data-tint="attention">{t("convo.needsYou")}</span> : null}
                  </span>
                  <span className="c-3 t-body-sm convo-item__preview">{c.preview}</span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </Glass>
  );

  if (!wide && !id) return <div className="page page--narrow">{list}</div>;
  if (!convo || !prospect) return <Navigate to="/conversations" replace />;

  const showList = wide;
  return (
    <div className="page convo" data-wide={wide || undefined}>
      {showList ? list : null}
      <Glass level={1} className="thread-view" aria-label={prospect.name}>
        <header className="thread-view__head">
          {!wide ? (
            <button type="button" className="icon-btn" aria-label={t("convo.title")} onClick={() => navigate("/conversations")}>
              <ChevronLeft size={22} className="mirror" />
            </button>
          ) : null}
          <Bead initials={prospect.initials} attention={prospect.attention} size={40} />
          <div className="thread-view__who">
            <h2 className="t-h3">{prospect.name}</h2>
            <p className="c-3 t-body-sm">
              {prospect.place} · Oriented to {prospect.interest}
            </p>
          </div>
          <span className="spacer" />
          <ProspectStatusChip status={prospect.status} stage={prospect.stage} />
          <Button variant="secondary" size="sm" leading={<PanelRight size={16} aria-hidden />} onClick={() => setDetailsOpen(true)}>
            Details
          </Button>
        </header>

        {convo.payment === "claimed" || convo.payment === "proof_received" ? (
          <div className="pay-notice">
            <p className="c-attention">
              {prospect.name.split(" ")[0]} says they paid. Not verified yet.
            </p>
            <Button variant="success" size="sm" leading={<ShieldCheck size={16} aria-hidden />} onClick={() => setConfirming(true)} disabledReason={canConfirmPayments ? undefined : t("payment.waitingPermission")}>
              {t("payment.review")}
            </Button>
          </div>
        ) : convo.payment === "paid" ? (
          <p className="pay-notice pay-notice--done c-success">Payment confirmed by {USER.name} K.</p>
        ) : null}

        <AnimatePresence>
          {convo.notes.handoff ? (
            <HandoffBanner
              reason={convo.notes.handoff}
              onTakeOver={() => {
                dispatch({ type: "takeOver", prospectId: convo.prospectId });
                toast({ tone: "info", title: "You're handling this conversation", body: "The agent is paused here." });
              }}
            />
          ) : null}
        </AnimatePresence>

        <ol className="msgs" aria-label="Messages">
          {convo.messages.map((m, i) => {
            const prev = convo.messages[i - 1];
            return <MessageBubble key={m.id} message={m} prospectInitials={prospect.initials} firstOfRun={!prev || prev.author !== m.author} />;
          })}
          <AnimatePresence>
            {convo.draft ? (
              <DraftBubble
                key="draft"
                parts={convo.draft}
                onApprove={(text) => {
                  dispatch({ type: "approveDraft", prospectId: convo.prospectId, text });
                  toast({ tone: "agent", title: `Sent to ${prospect.name.split(" ")[0]}`, body: "Approved by you." });
                }}
                onDecline={() => {
                  dispatch({ type: "declineDraft", prospectId: convo.prospectId });
                  toast({ tone: "info", title: "Draft declined" });
                }}
              />
            ) : null}
          </AnimatePresence>
          <li ref={endRef} aria-hidden />
        </ol>

        {tookOver ? (
          <Composer
            name={prospect.name.split(" ")[0] ?? prospect.name}
            onSend={(text) => dispatch({ type: "sendHuman", prospectId: convo.prospectId, text })}
            onGiveBack={() => {
              dispatch({ type: "giveBack", prospectId: convo.prospectId });
              toast({ tone: "agent", title: "Back with the agent", body: "I'll follow up tomorrow at 10:00." });
            }}
          />
        ) : null}
      </Glass>

      <Drawer open={detailsOpen} onOpenChange={setDetailsOpen} title={prospect.name}>
        <PaymentSeal progress={convo.payment} confirmedBy={USER.name + " K."} canConfirm={canConfirmPayments} onReview={() => { setDetailsOpen(false); setConfirming(true); }} />
        {convo.reasoning ? (
          <section className="why-panel" aria-label="Why REVENSA does this">
            <div className="why-panel__head">
              <h3 className="t-label c-2">
                <Sparkles size={16} aria-hidden className="c-agent" /> Why REVENSA does this
              </h3>
              <span className={`chip why-panel__conf`} data-tint={convo.reasoning.confidence === "high" ? "success" : convo.reasoning.confidence === "medium" ? "attention" : "neutral"}>
                Confidence: {convo.reasoning.confidence}
              </span>
            </div>
            <ul className="why-list">
              {convo.reasoning.factors.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </section>
        ) : null}
        <AgentNotes conversation={convo} />
      </Drawer>

      <ConfirmPaymentDialog
        open={confirming}
        onOpenChange={setConfirming}
        name={prospect.name}
        claimExcerpt={[...convo.messages].reverse().find((m) => m.author === "prospect" && !m.media)?.parts.map((p) => p.text).join("") ?? ""}
        proofName={convo.messages.find((m) => m.media?.possibleProof)?.media?.name}
        canConfirm={canConfirmPayments}
        onConfirm={() => {
          dispatch({ type: "confirmPayment", prospectId: convo.prospectId });
          toast({ tone: "success", title: "Payment confirmed", body: `Recorded with your name, ${USER.name}.` });
          setConfirming(false);
        }}
      />
    </div>
  );
}
