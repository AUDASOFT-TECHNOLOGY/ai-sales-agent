/** Home: is my agent working, what is stopping it, and what do I do next. Everything shown comes from
 *  the server (agent, readiness, settings, channel, source, summary, approvals); a figure that could
 *  not be read is said so, never replaced by a guess. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Clock, Pause, Play } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { StatusOrb } from "../agent/StatusOrb";
import { Button, LinkButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import {
  clientAgent,
  clientChannel,
  clientPauseAgent,
  clientProposals,
  clientReadiness,
  clientSettings,
  clientSourceState,
  clientStartAgent,
  clientSummary,
  type AgentState,
  type ChannelState,
  type ClientSummary,
  type Readiness,
  type SettingsState,
  type SourceState,
} from "./api";
import { useClientAuth } from "./ClientAuthContext";
import { Card, FailureNote, Loading, Note, PageHead, failureOf, type Failure as FailureShape } from "./kit";
import { PLATFORM_CHECKS, buildSteps, platformReasons, progressOf, type Step } from "./setupModel";
import type { Doc } from "./settingsModel";

interface Overview {
  agent: AgentState | null;
  readiness: Readiness | null;
  settings: SettingsState | null;
  channel: ChannelState | null;
  source: SourceState | null;
  summary: ClientSummary | null;
  toReview: number | null;
}

const ok = <T,>(r: PromiseSettledResult<T>): T | null => (r.status === "fulfilled" ? r.value : null);

async function loadOverview(): Promise<{ overview: Overview; signedOut: boolean }> {
  const results = await Promise.allSettled([
    clientAgent(),
    clientReadiness(),
    clientSettings(),
    clientChannel(),
    clientSourceState(),
    clientSummary(),
    clientProposals(),
  ]);
  const [agent, readiness, settings, channel, source, summary, proposals] = results;
  const unauthorized = results.some((r) => r.status === "rejected" && failureOf(r.reason).signedOut);
  return {
    signedOut: unauthorized,
    overview: {
      agent: ok(agent!) as AgentState | null,
      readiness: ok(readiness!) as Readiness | null,
      settings: ok(settings!) as SettingsState | null,
      channel: ok(channel!) as ChannelState | null,
      source: ok(source!) as SourceState | null,
      summary: ok(summary!) as ClientSummary | null,
      toReview: proposals!.status === "fulfilled" ? (proposals as PromiseFulfilledResult<{ proposals: unknown[] }>).value.proposals.length : null,
    },
  };
}

const PLATFORM_KEYS = new Set<string>(PLATFORM_CHECKS);
function platformKey(id: string): MessageKey {
  return (PLATFORM_KEYS.has(id) ? `rv.home.platform.${id}` : "rv.home.platform.other") as MessageKey;
}

function stepSubKey(step: Step, state: "todo" | "done"): MessageKey {
  return `rv.home.step.${step.id}.${state}` as MessageKey;
}

export function ClientHomePage() {
  const { t, n, d } = useI18n();
  const { profile, logout } = useClientAuth();
  const [data, setData] = useState<Overview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<FailureShape | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);

  const load = useCallback(async () => {
    const { overview, signedOut } = await loadOverview();
    if (signedOut) void logout();
    setData(overview);
  }, [logout]);

  useEffect(() => {
    let cancelled = false;
    void loadOverview().then(({ overview, signedOut }) => {
      if (cancelled) return;
      if (signedOut) void logout();
      setData(overview);
    });
    return () => {
      cancelled = true;
    };
  }, [logout]);

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
    await load(); // what the server now says, not what was expected
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

  const { agent, readiness, summary } = data;
  const commercial = data.settings?.settings.commercial as Doc | undefined;
  const steps = buildSteps({ commercial, readiness, channel: data.channel, source: data.source });
  const { done, total } = progressOf(steps);
  const platform = platformReasons(readiness);
  const allRequiredDone = done === total;

  let hero: { key: "active" | "paused" | "ready" | "notReady" | "waiting" | "error"; pulse: "working" | "paused" | "idle" | "not_live" };
  if (!agent) hero = { key: "error", pulse: "not_live" };
  else if (agent.active) hero = { key: "active", pulse: "working" };
  else if (agent.state === "paused") hero = { key: "paused", pulse: "paused" };
  else if (agent.state === "running") hero = { key: "waiting", pulse: "idle" };
  else hero = { key: agent.ready ? "ready" : "notReady", pulse: "not_live" };

  const running = agent?.state === "running";
  const startBlocked = agent && !agent.ready ? t("rv.home.start.blocked") : undefined;
  const shownName = profile?.name ?? "";

  return (
    <div className="rv-page">
      <PageHead title={t("rv.home.hello", { name: shownName })} />

      {failure ? <FailureNote failure={failure} /> : null}
      {notice ? <Note tone="success">{t(notice)}</Note> : null}

      <Card className="rv-hero" data-testid="home-hero">
        <StatusOrb state={hero.pulse} size={64} />
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
            <Button
              variant="primary"
              size="lg"
              leading={<Play size={16} aria-hidden />}
              onClick={() => void change("start")}
              loading={busy}
              disabledReason={startBlocked}
            >
              {t(agent.state === "paused" ? "rv.home.resume" : "rv.home.start")}
            </Button>
          )
        ) : (
          <Button onClick={() => void load()}>{t("rv.common.retry")}</Button>
        )}
      </Card>

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

      <Card flush aria-label={t("rv.home.steps.title")}>
        <div className="rv-card" style={{ paddingBlockEnd: 8 }}>
          <div className="rv-card__head">
            <h3>{t("rv.home.steps.title")}</h3>
            <span className="c-2 t-body-sm tabular">{t("rv.home.steps.progress", { done, total })}</span>
          </div>
          <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
            <div className="rv-progress__fill" style={{ width: `${total === 0 ? 0 : (done / total) * 100}%` }} />
          </div>
        </div>
        <ul className="rv-list rv-steps">
          {steps.map((step, i) => (
            <li key={step.id} className="rv-step" data-state={step.state} data-testid={`step-${step.id}`}>
              <span className="rv-step__mark" aria-hidden>
                {step.state === "done" ? <Check size={16} /> : step.state === "waiting" ? <Clock size={15} /> : i + 1}
              </span>
              <div className="rv-step__main">
                <span className="rv-step__title">
                  {t(`rv.home.step.${step.id}` as MessageKey)}{" "}
                  {!step.required ? <Chip>{t("rv.common.recommended")}</Chip> : null}
                </span>
                <span className="rv-step__sub">
                  {step.state === "done"
                    ? t(stepSubKey(step, "done"), { name: String(step.value ?? ""), n: Number(step.value ?? 0) })
                    : step.state === "waiting"
                      ? t("rv.home.platform.other")
                      : t(stepSubKey(step, "todo"))}
                </span>
              </div>
              <LinkButton to={step.to} size="sm" variant={step.state === "todo" && step.required ? "primary" : "secondary"} trailing={<ArrowRight size={14} className="mirror" aria-hidden />}>
                {t(step.state === "done" ? "rv.common.edit" : "rv.home.step.go")}
              </LinkButton>
            </li>
          ))}
        </ul>
      </Card>

      {agent && !agent.ready && allRequiredDone && platform.length === 0 && agent.reasons.length > 0 ? (
        <Note tone="warn">
          <ul className="stack" dir="auto">
            {agent.reasons.map((r) => (
              <li key={r.id}>{r.message ?? r.id}</li>
            ))}
          </ul>
        </Note>
      ) : null}

      <section className="rv-page" aria-labelledby="home-numbers">
        <h3 className="t-h2" id="home-numbers">
          {t("rv.home.numbers.title")}
        </h3>
        {!summary ? (
          <Note tone="info">{t("rv.home.numbers.unavailable")}</Note>
        ) : summary.total_prospects === 0 ? (
          <Note tone="info">{t("rv.home.numbers.empty")}</Note>
        ) : (
          <div className="rv-grid-3">
            <Card className="rv-stat" data-testid="stat-prospects">
              <span className="rv-stat__value">{n(summary.total_prospects)}</span>
              <span className="rv-stat__label">{t("rv.home.numbers.prospects")}</span>
              <Link to="/prospects" className="link-btn">
                {t("rv.home.numbers.seeProspects")} <ArrowRight size={14} className="mirror" aria-hidden />
              </Link>
            </Card>
            <Card className="rv-stat">
              <span className="rv-stat__value">{n(summary.by_status.active)}</span>
              <span className="rv-stat__label">{t("rv.home.numbers.active")}</span>
            </Card>
            <Card className="rv-stat">
              <span className="rv-stat__value">{n(summary.upcoming_actions.scheduled)}</span>
              <span className="rv-stat__label">{t("rv.home.numbers.scheduled")}</span>
              {summary.upcoming_actions.next_due_at ? (
                <span className="c-3 t-body-sm">{t("rv.home.next", { date: d(summary.upcoming_actions.next_due_at, { dateStyle: "medium", timeStyle: "short" }) })}</span>
              ) : null}
            </Card>
            {data.toReview !== null ? (
              <Card className="rv-stat" data-testid="stat-review">
                <span className="rv-stat__value">{n(data.toReview)}</span>
                <span className="rv-stat__label">{t("rv.home.numbers.toReview")}</span>
                {data.toReview > 0 ? (
                  <Link to="/review" className="link-btn">
                    {t("rv.home.numbers.seeReview")} <ArrowRight size={14} className="mirror" aria-hidden />
                  </Link>
                ) : null}
              </Card>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}
