import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence } from "motion/react";
import { ArrowDown, ArrowRight, History as HistoryIcon } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { ACTIONS_TODAY, DASHBOARD_PERIODS, FUNNEL, OFFERS, PRODUCT_PERFORMANCE, SINCE_START, STAGE_BREAKDOWN, STAGES, THREAD, TENANT, USER } from "../mock/data";
import { useStore } from "../mock/store";
import type { DashboardPeriodStat, ThreadEvent } from "../mock/types";
import { DailyBudget } from "../agent/Autonomy";
import { BarMeter, MiniTrend } from "../agent/Charts";
import { AnimatedNumber, FunnelRibbon } from "../agent/Kpi";
import { NeedDetailDialog, NeedRow } from "../agent/NeedsYou";
import { ConfirmPaymentDialog } from "../agent/PaymentSeal";
import { Pulse, useAgentLabel } from "../agent/Pulse";
import { MilestoneChip } from "../agent/StatusChips";
import { DayThread } from "../agent/Thread";
import { Bead } from "../ui/Bead";
import { Button, LinkButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { Segmented } from "../ui/Controls";
import { Drawer } from "../ui/Overlays";

const LIVE_EVENTS: ThreadEvent[] = [
  { id: "live1", time: "10:34", kind: "received", who: "Nour J.", voice: "Nour answered my last question about what she wants to learn.", detail: "Reply from Nour Jebali" },
  { id: "live2", time: "10:36", kind: "sent", who: "Nour J.", voice: "I shared a short overview of Foundation and asked one follow-up question.", detail: "Sent to Nour Jebali" },
];

type Period = "7d" | "30d" | "3m" | "all";
type FunnelPeriod = "today" | "7d" | "30d";
const TREND_UNIT_NOUN: Record<DashboardPeriodStat["trendUnit"], { current: string; previous: string }> = {
  day: { current: "today", previous: "yesterday" },
  week: { current: "this week", previous: "last week" },
  month: { current: "this month", previous: "last month" },
};

/** The Dashboard: the single screen a CEO checks on the business. A compact status bar (not a big
 *  hero) opens it, today's action quota sits right under that — the two things that change every
 *  time the agent works. Below that: the filterable commercial numbers, the funnel and pipeline side
 *  by side, decisions ("Needs you") next to prospects nearest a sale ("Closest to paying") so their
 *  contrast is visible rather than explained, and product performance as cards. Results used to be a
 *  separate page; it was folded in here so a CEO never has to remember which of two screens holds
 *  which number. Report history (`/history`) still carries every past daily report. Nothing here is
 *  fabricated: every figure is hand-authored sample data (mock/data.ts), exactly like the rest of
 *  this prototype, and says so in its own comments. */
export function Dashboard() {
  const { t, n } = useI18n();
  const navigate = useNavigate();
  const { state, dispatch, toast, agentState, mode, canConfirmPayments } = useStore();
  const label = useAgentLabel(agentState, state.needs.length);
  const [events, setEvents] = useState<ThreadEvent[]>(THREAD);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [confirmFor, setConfirmFor] = useState<string | null>(null);
  const [dayOpen, setDayOpen] = useState(false);
  const [period, setPeriod] = useState<Period>("all");
  const [product, setProduct] = useState<string>("all");
  const [funnelPeriod, setFunnelPeriod] = useState<FunnelPeriod>("7d");
  const nowTime = events.some((e) => e.id === "live2") ? "10:36" : events.some((e) => e.id === "live1") ? "10:34" : "10:32";

  useEffect(() => {
    if (!state.live || state.paused) return;
    const timers = LIVE_EVENTS.map((event, i) => window.setTimeout(() => setEvents((list) => (list.some((e) => e.id === event.id) ? list : [...list.filter((e) => !e.future), event, ...list.filter((e) => e.future)])), 9000 + i * 14000));
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [state.live, state.paused]);

  const needs = state.needs.length;
  const tint = agentState === "waiting" ? "attention" : agentState === "degraded" ? "danger" : "agent";
  const detailItem = state.needs.find((x) => x.id === detailId) ?? null;
  const detailProspect = state.prospects.find((p) => p.id === detailItem?.prospectId);
  const confirmProspect = state.prospects.find((p) => p.id === confirmFor);
  const confirmConvo = state.conversations.find((c) => c.prospectId === confirmFor);
  const lastClaim = confirmConvo ? [...confirmConvo.messages].reverse().find((m) => m.author === "prospect" && !m.media) : undefined;
  const proof = confirmConvo?.messages.find((m) => m.media?.possibleProof)?.media?.name;

  const needProspectIds = new Set(state.needs.map((x) => x.prospectId));
  const opportunities = state.prospects
    .filter((p) => p.step >= 2 && p.status !== "closed" && p.status !== "lost" && !needProspectIds.has(p.id))
    .sort((a, b) => b.step - a.step)
    .slice(0, 3);

  const stat = DASHBOARD_PERIODS[period];
  const responded = Math.round((stat.contacted * stat.responseRate) / 100);
  const converted = Math.round((stat.contacted * stat.conversionRate) / 100);
  const productPerf = useMemo(() => (product === "all" ? PRODUCT_PERFORMANCE : PRODUCT_PERFORMANCE.filter((p) => p.offerId === product)), [product]);
  const maxRevenue = Math.max(...PRODUCT_PERFORMANCE.map((p) => p.revenueValue ?? 0), 1);
  const lastBucket = stat.trend[stat.trend.length - 1] ?? 0;
  const prevBucket = stat.trend[stat.trend.length - 2] ?? null;
  const trendDelta = prevBucket ? Math.round(((lastBucket - prevBucket) / prevBucket) * 100) : null;
  const trendNoun = TREND_UNIT_NOUN[stat.trendUnit];
  const stageTotal = STAGE_BREAKDOWN.reduce((sum, s) => sum + s.count, 0);

  const heroSentence = !state.live
    ? "I'm not live yet. Finish the setup and I'll start working."
    : agentState === "paused"
      ? "You paused me. I'll wait until you resume."
      : agentState === "observing"
        ? "I'm preparing messages today, and sending nothing."
        : needs > 0
          ? `${needs === 1 ? "1 decision is" : `${n(needs)} decisions are`} waiting for you.`
          : "Nothing needs you right now.";

  return (
    <div className="page page--focus dashboard">
      <div className="page-head">
        <h2 className="t-h1">{t("nav.command")}</h2>
      </div>
      <div className="dash-top">
      <Glass level={3} tint={tint} className="hero" aria-labelledby="hero-title">
        <div className="hero__orb">
          <Pulse state={agentState} size={56} count={needs} className="hero__pulse" />
        </div>
        <div className="hero__text">
          <p className="hero__sentence" id="hero-title">
            <span className="c-3">{t("command.greeting", { name: USER.name })} — </span>
            {heroSentence}
          </p>
          <p className="hero__status c-2">
            {label} · {t(`mode.${mode}` as MessageKey)}
          </p>
        </div>
        {!state.live ? (
          <LinkButton to="/setup" variant="primary" trailing={<ArrowRight size={18} className="mirror" aria-hidden />}>
            {t("nav.setup")}
          </LinkButton>
        ) : needs > 0 ? (
          <Button variant="primary" leading={<ArrowDown size={16} aria-hidden />} onClick={() => document.getElementById("needs")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            {needs === 1 ? "Review 1 decision" : `Review ${n(needs)} decisions`}
          </Button>
        ) : null}
      </Glass>

      <Glass className="panel dash-quota">
        <DailyBudget used={state.dailyUsed} limit={state.dailyLimit} label={t("command.budget")} caption={t("command.budget.of", { used: state.dailyUsed, limit: state.dailyLimit })} />
        <p className="c-2 t-body-sm dash-quota__note">
          {n(ACTIONS_TODAY.newProspects)} new prospects · {n(ACTIONS_TODAY.followUps)} follow-ups · {n(ACTIONS_TODAY.reminders)} reminders · {n(ACTIONS_TODAY.registrationChecks)} registration check
        </p>
        <button type="button" className="link-btn dash-quota__link" onClick={() => setDayOpen(true)}>
          See the full day <ArrowRight size={16} className="mirror" aria-hidden />
        </button>
      </Glass>
      </div>

      <section aria-labelledby="perf-h" className="section">
        <div className="section__head dash-filters">
          <div>
            <h2 className="t-h2" id="perf-h">
              Commercial activity since {SINCE_START.startDate}
            </h2>
            <p className="c-3 t-body-sm">{SINCE_START.months} months of collaboration.</p>
          </div>
          <div className="row row--wrap dash-filters__controls">
            <Segmented
              layoutId="seg-dash-period"
              label="Period"
              size="sm"
              value={period}
              onChange={setPeriod}
              options={[
                { value: "7d", label: "7 days" },
                { value: "30d", label: "30 days" },
                { value: "3m", label: "3 months" },
                { value: "all", label: "Since the start" },
              ]}
            />
            <Segmented
              layoutId="seg-dash-product"
              label="Product"
              size="sm"
              value={product}
              onChange={setProduct}
              options={[{ value: "all", label: "All products" }, ...OFFERS.map((o) => ({ value: o.id, label: o.name }))]}
            />
          </div>
        </div>

        <div className="dash-kpi-grid">
          <Glass level={2} tint="action" className="dash-kpi">
            <span className="c-2 t-body-sm">New prospects contacted</span>
            <span className="t-numeral-l tabular">
              <AnimatedNumber value={stat.contacted} />
            </span>
            <div className="dash-kpi__trend c-action">
              <MiniTrend series={stat.trend} label={`New prospects contacted, by ${stat.trendUnit}: ${stat.trend.join(", ")}`} />
            </div>
            <span className="c-3 t-body-sm">
              {n(lastBucket)} {trendNoun.current}
              {trendDelta !== null ? ` · ${trendDelta >= 0 ? "+" : ""}${trendDelta}% vs ${trendNoun.previous}` : ""}
            </span>
          </Glass>
          <Glass level={2} tint="agent" className="dash-kpi">
            <span className="c-2 t-body-sm">Responded</span>
            <span className="t-numeral-l tabular">
              <AnimatedNumber value={responded} />
            </span>
            <div className="dash-kpi__bar"><div className="dash-kpi__bar-fill" style={{ width: `${stat.responseRate}%`, background: "var(--agent)" }} /></div>
            <Chip tint="agent" outline>{stat.responseRate}% response rate</Chip>
          </Glass>
          <Glass level={2} tint="seal" className="dash-kpi">
            <span className="c-2 t-body-sm">Converted</span>
            <span className="t-numeral-l tabular">
              <AnimatedNumber value={converted} />
            </span>
            <div className="dash-kpi__bar"><div className="dash-kpi__bar-fill" style={{ width: `${stat.conversionRate}%`, background: "var(--seal)" }} /></div>
            <Chip tint="seal" outline>{stat.conversionRate}% conversion rate</Chip>
          </Glass>
          <Glass level={2} tint="success" className="dash-kpi">
            <span className="c-2 t-body-sm">Revenue generated</span>
            <span className="t-numeral-l tabular c-success">{stat.revenue}</span>
            <span className="c-3 t-body-sm">First-time outreach only, {stat.label.toLowerCase()}</span>
          </Glass>
        </div>

        <div className="dash-charts">
          <Glass className="panel dash-chart-panel funnel-panel">
            <div className="dash-chart-panel__head">
              <h3 className="t-h3">From first message to revenue</h3>
              <Segmented
                layoutId="seg-dash-funnel"
                label="Funnel period"
                size="sm"
                value={funnelPeriod}
                onChange={setFunnelPeriod}
                options={[
                  { value: "today", label: "Today" },
                  { value: "7d", label: "7 days" },
                  { value: "30d", label: "30 days" },
                ]}
              />
            </div>
            <FunnelRibbon key={funnelPeriod} steps={FUNNEL[funnelPeriod]} />
          </Glass>
        </div>
      </section>

      {needs > 0 || opportunities.length > 0 ? (
        <div className="dash-action-grid">
          {needs > 0 ? (
            <section aria-labelledby="needs-h" id="needs" className="dash-action-col">
              <div className="dash-action-col__head">
                <h2 className="t-h2" id="needs-h">
                  {t("command.needsYou")}
                </h2>
                <Chip tint="attention">{needs === 1 ? "1 decision" : `${n(needs)} decisions`}</Chip>
              </div>
              <ul className="need-list">
                <AnimatePresence mode="popLayout">
                  {state.needs.map((item) => {
                    const prospect = state.prospects.find((p) => p.id === item.prospectId)!;
                    return <NeedRow key={item.id} item={item} prospect={prospect} onOpen={() => (item.kind === "payment" ? setConfirmFor(item.prospectId) : setDetailId(item.id))} />;
                  })}
                </AnimatePresence>
              </ul>
            </section>
          ) : null}

          {opportunities.length > 0 ? (
            <section aria-labelledby="opps-h" className="dash-action-col">
              <div className="dash-action-col__head">
                <h2 className="t-h2" id="opps-h">
                  Closest to paying
                </h2>
                <Chip tint="neutral">no action needed</Chip>
              </div>
              <p className="c-3 t-body-sm dash-action-col__note">
                The {n(opportunities.length)} prospect{opportunities.length === 1 ? "" : "s"} nearest to paying, out of {n(stageTotal)} open. The agent handles these on its own.
              </p>
              <ul className="opp-list">
                {opportunities.map((p) => (
                  <li key={p.id}>
                    <Link to={`/prospects?open=${p.id}`} className="opp-row">
                      <Bead initials={p.initials} attention={p.attention} size={40} />
                      <span className="opp-row__text">
                        <span className="opp-row__top">
                          <span className="t-h3">{p.name}</span>
                          <MilestoneChip label={STAGES.find((s) => s.id === p.stage)?.label ?? p.stage} />
                        </span>
                        <span className="voice voice-s">{p.read}</span>
                        <span className="opp-row__next c-3 t-body-sm">Next automatic step: {p.next}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}

      <section aria-labelledby="product-h" className="section">
        <h2 className="t-h2 section__title" id="product-h">
          Performance by catalogue product
        </h2>
        <div className="dash-product-grid">
          {productPerf.map((p) => {
            const offer = OFFERS.find((o) => o.id === p.offerId)!;
            const offerTint = offer.id === "foundation" ? "action" : offer.id === "growth" ? "agent" : "seal";
            return (
              <Glass key={p.offerId} className="panel dash-product-card">
                <h3 className="t-body-l">{offer.name}</h3>
                <BarMeter
                  label="Revenue in discussion"
                  valueLabel={p.revenueLabel ?? t("kpi.notTracked")}
                  fraction={(p.revenueValue ?? 0) / maxRevenue}
                  dashed={p.revenueValue === null}
                  tint={offerTint}
                />
                <BarMeter
                  label="Conversion rate"
                  valueLabel={p.conversionRate !== null ? `${p.conversionRate}%` : t("kpi.notTracked")}
                  fraction={(p.conversionRate ?? 0) / 100}
                  dashed={p.conversionRate === null}
                  tint={offerTint}
                />
              </Glass>
            );
          })}
        </div>
      </section>

      <div className="dash-footlinks">
        <Link to="/history" className="link-btn">
          <HistoryIcon size={16} aria-hidden /> See report history <ArrowRight size={16} className="mirror" aria-hidden />
        </Link>
      </div>

      <NeedDetailDialog
        item={detailItem}
        prospect={detailProspect}
        onClose={() => setDetailId(null)}
        onApprove={() => {
          if (!detailItem || !detailProspect) return;
          dispatch({ type: "approveDraft", prospectId: detailItem.prospectId, text: detailItem.preview ?? "" });
          toast({ tone: "agent", title: `Sent to ${detailProspect.name.split(" ")[0]}`, body: "Approved by you." });
          setDetailId(null);
        }}
        onDecline={() => {
          if (!detailItem) return;
          dispatch({ type: "declineDraft", prospectId: detailItem.prospectId });
          toast({ tone: "info", title: "Draft declined", undo: () => toast({ tone: "info", title: "Draft restored (prototype)" }) });
          setDetailId(null);
        }}
        onEdit={() => detailItem && navigate(`/conversations/${detailItem.prospectId}`)}
        onTakeOver={() => {
          if (!detailItem) return;
          dispatch({ type: "takeOver", prospectId: detailItem.prospectId });
          navigate(`/conversations/${detailItem.prospectId}`);
        }}
      />

      <Drawer open={dayOpen} onOpenChange={setDayOpen} title={`${t("command.thread")} · ${TENANT.timezoneLabel}`}>
        <DailyBudget used={state.dailyUsed} limit={state.dailyLimit} label={t("command.budget")} caption={t("command.budget.of", { used: state.dailyUsed, limit: state.dailyLimit })} />
        <DayThread events={events} nowTime={nowTime} agentState={agentState} />
      </Drawer>

      <ConfirmPaymentDialog
        open={confirmFor !== null}
        onOpenChange={(o) => !o && setConfirmFor(null)}
        name={confirmProspect?.name ?? ""}
        claimExcerpt={lastClaim?.parts.map((p) => p.text).join("") ?? ""}
        proofName={proof}
        canConfirm={canConfirmPayments}
        onConfirm={() => {
          if (!confirmFor) return;
          dispatch({ type: "confirmPayment", prospectId: confirmFor });
          toast({ tone: "success", title: "Payment confirmed", body: `Recorded with your name, ${USER.name}.` });
          setConfirmFor(null);
        }}
      />
    </div>
  );
}
