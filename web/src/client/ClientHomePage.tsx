/** Home: is my agent working, what needs me, how am I doing, and what is set up. Everything shown comes
 *  from the server (agent, readiness, settings, channel, source, circuits, summary, approvals,
 *  payments and the dashboard); a figure that could not be read is said so, never replaced by a guess.
 *
 *  Order of the page: the setup guide while the agent is not ready, then the agent and its switch, then
 *  what needs the owner, the figures, the circuits, and usage / connections / subscription. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, CalendarClock, CheckSquare, CreditCard, HandHelping, MessagesSquare, Pause, Play, Rocket, Users } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { StatusOrb } from "../agent/StatusOrb";
import { Button, LinkButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass, type Tint } from "../ui/Glass";
import { indicatorText } from "../dashboard/format";
import type { ClientDashboard } from "../dashboard/types";
import { ALL_PRODUCTS, clientPauseAgent, clientStartAgent } from "./api";
import { useClientAuth } from "./ClientAuthContext";
import { clientDashboard } from "./dashboard/clientDashboardApi";
import { FailureNote, Loading, Note, PageHead, failureOf, type Failure as FailureShape } from "./kit";
import { guideSteps, useOverview } from "./overview";
import { PLATFORM_CHECKS, platformReasons } from "./setupModel";

const PLATFORM_KEYS = new Set<string>(PLATFORM_CHECKS);
function platformKey(id: string): MessageKey {
  return (PLATFORM_KEYS.has(id) ? `rv.home.platform.${id}` : "rv.home.platform.other") as MessageKey;
}

function Kpi({ tint, icon, value, label, sub, to, linkLabel, testId }: { tint: Tint; icon: React.ReactNode; value: string; label: string; sub?: string; to?: string; linkLabel?: string; testId?: string }) {
  return (
    <Glass level={1} tint={tint} className="rv-stat" data-testid={testId}>
      <span className="rv-stat__icon" aria-hidden>
        {icon}
      </span>
      <span className="rv-stat__value">{value}</span>
      <span className="rv-stat__label">{label}</span>
      {sub ? <span className="c-3 t-body-sm">{sub}</span> : null}
      {to ? (
        <Link to={to} className="link-btn">
          {linkLabel} <ArrowRight size={14} className="mirror" aria-hidden />
        </Link>
      ) : null}
    </Glass>
  );
}

export function ClientHomePage() {
  const { t, n, d } = useI18n();
  const { profile, logout } = useClientAuth();
  const { data, reload } = useOverview();
  const [dash, setDash] = useState<ClientDashboard | null | "failed">(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<FailureShape | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);

  const loadDash = useCallback(() => clientDashboard().then(setDash, () => setDash("failed")), []);
  useEffect(() => {
    let cancelled = false;
    clientDashboard().then(
      (v) => !cancelled && setDash(v),
      () => !cancelled && setDash("failed"),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function change(action: "start" | "pause") {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      await (action === "start" ? clientStartAgent() : clientPauseAgent());
      setNotice(action === "start" ? "rv.home.started" : "rv.home.paused");
    } catch (err) {
      const problem = failureOf(err);
      setFailure(problem.key === "rv.err.conflict" ? { key: "rv.err.generic" } : problem);
      if (problem.signedOut) void logout();
    }
    await reload(); // what the server now says, not what was expected
    await loadDash();
    setBusy(false);
  }

  if (!data) {
    return (
      <div className="rv-page">
        <PageHead title={t("rv.home.title")} />
        <Loading />
      </div>
    );
  }

  const { agent, readiness, summary, lanes, source, channel } = data;
  const steps = guideSteps(data);
  const stepsDone = steps.filter((s) => s.state === "done").length;
  const setupOpen = stepsDone < steps.length;
  const platform = platformReasons(readiness);
  const dashboard = dash && dash !== "failed" ? dash : null;
  const activity = dashboard?.activity;

  let hero: { key: "active" | "paused" | "ready" | "notReady" | "waiting" | "error"; pulse: "working" | "paused" | "idle" | "not_live" };
  if (!agent) hero = { key: "error", pulse: "not_live" };
  else if (agent.active) hero = { key: "active", pulse: "working" };
  else if (agent.state === "paused") hero = { key: "paused", pulse: "paused" };
  else if (agent.state === "running") hero = { key: "waiting", pulse: "idle" };
  else hero = { key: agent.ready ? "ready" : "notReady", pulse: "not_live" };
  const heroTint: Tint = hero.key === "active" ? "success" : hero.key === "error" ? "danger" : hero.key === "paused" || hero.key === "waiting" ? "attention" : "agent";
  const running = agent?.state === "running";
  const startBlocked = agent && !agent.ready ? t("rv.home.start.blocked") : undefined;

  // What needs the owner, most urgent first. Each line only appears when the server says there is something.
  const humans = dashboard ? (dashboard.activity.needs_human.value ?? dashboard.human_requests.value ?? 0) : 0;
  const incidents = dashboard ? (dashboard.incidents.open.value ?? 0) : 0;
  const attention: Array<{ key: string; icon: React.ReactNode; title: string; sub: string; to: string }> = [];
  if (humans > 0) attention.push({ key: "human", icon: <HandHelping size={18} />, title: t("rv.home.attention.human", { n: humans }), sub: t("rv.home.attention.human.sub"), to: "/conversations?human=1" });
  if ((data.toConfirm ?? 0) > 0) attention.push({ key: "confirm", icon: <CreditCard size={18} />, title: t("rv.home.attention.confirm", { n: data.toConfirm ?? 0 }), sub: t("rv.home.attention.confirm.sub"), to: "/review/payments" });
  if ((data.toApprove ?? 0) > 0) attention.push({ key: "approve", icon: <CheckSquare size={18} />, title: t("rv.home.attention.approve", { n: data.toApprove ?? 0 }), sub: t("rv.home.attention.approve.sub"), to: "/review" });
  if (incidents > 0) attention.push({ key: "incidents", icon: <AlertTriangle size={18} />, title: t("rv.home.attention.incidents", { n: incidents }), sub: t("rv.home.attention.incidents.sub"), to: "/notifications" });

  const circuits =
    lanes && lanes.editable
      ? lanes.lanes.map((l) => ({
          id: l.id,
          source: l.source.label,
          kind: lanes.source_options.find((o) => o.key === l.source.source)?.label ?? l.source.source,
          channel: l.channel.label,
          products: l.offers_all_products || l.products.includes(ALL_PRODUCTS) ? [t("rv.home.circuits.agent")] : l.products.map((p) => lanes.catalog.find((c) => c.id === p)?.name ?? p),
          ready: l.channel.authorized && !l.channel.problem,
        }))
      : source
        ? [
            {
              id: "single",
              source: t("rv.circ.single.name"),
              kind: source.options.find((o) => o.key === source.source)?.label ?? source.source,
              channel: channel?.options.find((o) => o.key === channel.channel)?.label ?? "—",
              products: [t("rv.home.circuits.agent")],
              ready: channel?.status === "ready" && readiness?.checks.find((c) => c.id === "source_connected")?.ok === true,
            },
          ]
        : [];

  const usage = dashboard?.usage;
  const sub = dashboard?.billing.subscription;
  const integrations = dashboard?.agent.integrations ?? [];

  return (
    <div className="rv-page">
      <PageHead eyebrow={t("rv.home.eyebrow")} title={t("rv.home.hello", { name: profile?.name ?? "" })} />

      {failure ? <FailureNote failure={failure} /> : null}
      {notice ? <Note tone="success">{t(notice)}</Note> : null}

      {setupOpen ? (
        <Glass level={2} tint="agent" className="rv-card rv-setupbar" data-testid="home-setup">
          <span className="rv-setupbar__icon" aria-hidden>
            <Rocket size={22} />
          </span>
          <div className="rv-setupbar__text">
            <h3>{t("rv.home.setup.title", { done: stepsDone, total: steps.length })}</h3>
            <p className="c-2">{t("rv.home.setup.text")}</p>
            <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={stepsDone}>
              <div className="rv-progress__fill" style={{ width: `${(stepsDone / steps.length) * 100}%` }} />
            </div>
          </div>
          <LinkButton to="/setup" variant="primary" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
            {t("rv.home.setup.cta")}
          </LinkButton>
        </Glass>
      ) : null}

      <div className="rv-top">
        <Glass level={3} tint={heroTint} className="rv-hero" data-testid="home-hero">
          <div className="rv-hero__orb">
            <StatusOrb state={hero.pulse} size={72} />
          </div>
          <div className="rv-hero__text">
            <h2 data-testid="agent-state">{t(`rv.home.hero.${hero.key}.title` as MessageKey)}</h2>
            <p>{t(`rv.home.hero.${hero.key}.text` as MessageKey)}</p>
          </div>
          {agent ? (
            running ? (
              <Button leading={<Pause size={16} aria-hidden />} onClick={() => void change("pause")} loading={busy}>
                {t("rv.home.pause")}
              </Button>
            ) : (
              <Button variant="primary" size="lg" leading={<Play size={16} aria-hidden />} onClick={() => void change("start")} loading={busy} disabledReason={startBlocked}>
                {t(agent.state === "paused" ? "rv.home.resume" : "rv.home.start")}
              </Button>
            )
          ) : (
            <Button onClick={() => void reload()}>{t("rv.common.retry")}</Button>
          )}
        </Glass>

        <Glass level={1} className="rv-card" data-testid="home-attention" aria-label={t("rv.home.attention.title")}>
          <div className="rv-card__head">
            <h3>{t("rv.home.attention.title")}</h3>
            {attention.length > 0 ? <Chip tint="attention">{attention.length}</Chip> : null}
          </div>
          {attention.length === 0 ? (
            <p className="c-2">{t("rv.home.attention.none")}</p>
          ) : (
            <ul className="rv-todo">
              {attention.map((a) => (
                <li key={a.key}>
                  <Link to={a.to} className="rv-todo__item">
                    <span className="rv-todo__icon" aria-hidden>
                      {a.icon}
                    </span>
                    <span className="rv-todo__text">
                      <strong>{a.title}</strong>
                      <small>{a.sub}</small>
                    </span>
                    <ArrowRight size={16} className="mirror c-3" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Glass>
      </div>

      {platform.length > 0 ? (
        <Note tone="warn" title={t("rv.home.platform.title")}>
          <ul className="stack">
            {platform.map((id) => (
              <li key={id}>{t(platformKey(id))}</li>
            ))}
          </ul>
          <p style={{ marginBlockStart: 6 }}>{t("rv.home.platform.text")}</p>
        </Note>
      ) : null}

      {!summary ? (
        <Note tone="info">{t("rv.home.numbers.unavailable")}</Note>
      ) : summary.total_prospects === 0 ? (
        <Note tone="info">{t("rv.home.numbers.empty")}</Note>
      ) : (
        <section className="rv-page" style={{ gap: 14 }} aria-labelledby="home-numbers">
          <h3 className="rv-section-title" id="home-numbers">
            {t("rv.home.numbers.title")}
          </h3>
          <div className="rv-kpis">
            <Kpi
              testId="stat-prospects"
              tint="action"
              icon={<Users size={18} />}
              value={activity ? indicatorText(activity.prospects_contacted, n) : n(summary.total_prospects)}
              label={t("rv.home.kpi.contacted")}
              sub={t("rv.home.kpi.contacted.sub", { total: n(summary.total_prospects) })}
              to="/conversations"
              linkLabel={t("rv.home.numbers.seeProspects")}
            />
            <Kpi
              tint="agent"
              icon={<MessagesSquare size={18} />}
              value={activity ? indicatorText(activity.prospects_replied, n) : n(summary.by_status.active)}
              label={t("rv.home.kpi.replies")}
              sub={activity && activity.reply_rate_percent.value !== null ? t("rv.home.kpi.replies.sub", { pct: n(activity.reply_rate_percent.value) }) : undefined}
              to="/conversations"
              linkLabel={t("rv.nav.conversations")}
            />
            <Kpi
              tint="success"
              icon={<CalendarClock size={18} />}
              value={n(summary.upcoming_actions.scheduled)}
              label={t("rv.home.numbers.scheduled")}
              sub={summary.upcoming_actions.next_due_at ? t("rv.home.next", { date: d(summary.upcoming_actions.next_due_at, { dateStyle: "medium", timeStyle: "short" }) }) : undefined}
            />
            {activity ? (
              <Kpi
                testId="stat-review"
                tint="attention"
                icon={<CreditCard size={18} />}
                value={indicatorText(activity.conversions_declared, n)}
                label={t("rv.home.kpi.sales")}
                sub={t("rv.home.kpi.sales.sub", { n: n(activity.conversions_confirmed.value ?? 0) })}
                to="/results"
                linkLabel={t("rv.nav.results")}
              />
            ) : null}
          </div>
        </section>
      )}

      {circuits.length > 0 ? (
        <Glass level={1} className="rv-card rv-card--flush" data-testid="home-circuits" aria-label={t("rv.home.circuits.title")}>
          <div className="rv-card rv-card--bare" style={{ paddingBlockEnd: 8 }}>
            <div className="rv-card__head">
              <div>
                <h3>{t("rv.home.circuits.title")}</h3>
                <p className="rv-card__sub">{t("rv.home.circuits.intro")}</p>
              </div>
              <LinkButton to="/circuits" size="sm" trailing={<ArrowRight size={14} className="mirror" aria-hidden />}>
                {t("rv.home.circuits.manage")}
              </LinkButton>
            </div>
          </div>
          <div className="ledger__scroll">
            <table className="rv-table">
              <thead>
                <tr>
                  <th scope="col">{t("rv.home.circuits.col.source")}</th>
                  <th scope="col">{t("rv.home.circuits.col.channel")}</th>
                  <th scope="col">{t("rv.home.circuits.col.products")}</th>
                  <th scope="col">{t("rv.home.circuits.col.state")}</th>
                </tr>
              </thead>
              <tbody>
                {circuits.map((c) => (
                  <tr key={c.id}>
                    <th scope="row">
                      <strong dir="auto">{c.source}</strong>
                      <span className="rv-table__sub">{c.kind}</span>
                    </th>
                    <td>
                      <span className="rv-tag rv-ent" data-ent="chn">
                        {c.channel}
                      </span>
                    </td>
                    <td>
                      <span className="rv-tags">
                        {c.products.map((p) => (
                          <span key={p} className="rv-tag rv-ent" data-ent="prd" dir="auto">
                            {p}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td>
                      <Chip tint={c.ready ? "success" : "attention"}>{t(c.ready ? "rv.home.circuits.ready" : "rv.home.circuits.waiting")}</Chip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Glass>
      ) : null}

      <div className="rv-grid-3">
        <Glass level={1} className="rv-card" data-testid="home-usage">
          <div className="rv-card__head">
            <h3><Link to="/usage">{t("rv.home.usage.title")}</Link></h3>
          </div>
          {usage && usage.availability !== "not_tracked" ? (
            <>
              <div className="rv-meter">
                <span className="rv-meter__row">
                  <span>{t("rv.home.usage.today")}</span>
                  <strong className="tabular">{usage.daily_limit != null ? `${n(usage.daily_used ?? 0)} / ${n(usage.daily_limit)}` : n(usage.daily_used ?? 0)}</strong>
                </span>
                {usage.daily_limit ? (
                  <div className="rv-progress" aria-hidden>
                    <div className="rv-progress__fill" style={{ width: `${Math.min(100, usage.daily_percent ?? 0)}%` }} />
                  </div>
                ) : null}
              </div>
              <div className="rv-meter">
                <span className="rv-meter__row">
                  <span>{t("rv.home.usage.month")}</span>
                  <strong className="tabular">{n(usage.month_used ?? 0)}</strong>
                </span>
              </div>
              {usage.message ? <p className="c-3 t-body-sm">{usage.message}</p> : null}
            </>
          ) : (
            <p className="c-3">{t("rv.home.usage.unknown")}</p>
          )}
        </Glass>

        <Glass level={1} className="rv-card" data-testid="home-connections">
          <div className="rv-card__head">
            <h3>{t("rv.home.conn.title")}</h3>
            <Link to="/circuits" className="link-btn">
              {t("rv.home.circuits.manage")}
            </Link>
          </div>
          {integrations.length === 0 ? (
            <p className="c-3">{t("rv.home.usage.unknown")}</p>
          ) : (
            <ul className="stack" style={{ gap: 10 }}>
              {integrations.map((c) => (
                <li key={c.id} className="row">
                  <span className={c.ok ? "rv-dot rv-dot--ok" : "rv-dot rv-dot--warn"} aria-hidden />
                  <span className="grow">{t(`rv.home.conn.${c.id}` as MessageKey)}</span>
                  <span className="c-3 t-body-sm">{c.ok ? t("rv.home.conn.ok") : (c.message ?? t("rv.home.conn.todo"))}</span>
                </li>
              ))}
            </ul>
          )}
        </Glass>

        <Glass level={1} className="rv-card" data-testid="home-subscription">
          <div className="rv-card__head">
            <h3>{t("rv.home.sub.title")}</h3>
          </div>
          {sub && sub.status === "active" ? (
            <>
              <strong>{sub.plan_name}</strong>
              {sub.next_billing_at ? <p className="c-2 t-body-sm">{t("rv.home.sub.next", { date: d(sub.next_billing_at, { dateStyle: "long" }) })}</p> : null}
            </>
          ) : (
            <p className="c-3">{t("rv.home.sub.none")}</p>
          )}
          <Link to="/account" className="link-btn">
            {t("rv.home.sub.manage")} <ArrowRight size={14} className="mirror" aria-hidden />
          </Link>
        </Glass>
      </div>
    </div>
  );
}
