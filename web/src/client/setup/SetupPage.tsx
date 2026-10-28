/** The setup guide: seven steps in the order an owner does them, one explained at a time in plain words
 *  with an example from an ordinary business. Each step opens the real screen; nothing is configured
 *  here. What is done comes from the server (see `guideSteps`), so the guide never claims a step the
 *  platform does not see as finished. */

import { useState } from "react";
import { ArrowRight, Check, Clock, Play } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Glass } from "../../ui/Glass";
import { clientStartAgent } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { FailureNote, Loading, Note, PageHead, failureOf, type Failure } from "../kit";
import { guideSteps, useOverview, type GuideStep } from "../overview";

export function SetupPage() {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const { data, reload } = useOverview();
  const [picked, setPicked] = useState<GuideStep["id"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  if (!data) return <Loading />;
  const steps = guideSteps(data);
  const done = steps.filter((s) => s.state === "done").length;
  const firstOpen = steps.find((s) => s.state !== "done") ?? steps[steps.length - 1]!;
  const current = steps.find((s) => s.id === picked) ?? firstOpen;
  const index = steps.findIndex((s) => s.id === current.id);
  const next = steps.slice(index + 1).find((s) => s.state !== "done") ?? null;
  const key = (suffix: string) => `rv.guide.step.${current.id}.${suffix}` as MessageKey;
  const isStart = current.id === "start";
  const everythingElseDone = steps.filter((s) => s.id !== "start").every((s) => s.state === "done" || !s.required);

  async function start() {
    setBusy(true);
    setFailure(null);
    try {
      await clientStartAgent();
    } catch (err) {
      const problem = failureOf(err);
      setFailure(problem.key === "rv.err.conflict" ? { key: "rv.err.generic" } : problem);
      if (problem.signedOut) void logout();
    }
    await reload();
    setBusy(false);
  }

  return (
    <div className="rv-page">
      <PageHead
        title={t("rv.guide.title")}
        text={t("rv.guide.intro")}
        actions={
          <div className="rv-guide__progress">
            <span className="c-2 t-body-sm tabular">{t("rv.guide.progress", { done, total: steps.length })}</span>
            <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done}>
              <div className="rv-progress__fill" style={{ width: `${(done / steps.length) * 100}%` }} />
            </div>
          </div>
        }
      />

      <div className="rv-guide">
        <Glass level={1} className="rv-guide__steps" as="nav" aria-label={t("rv.guide.title")}>
          <ol>
            {steps.map((s, i) => (
              <li key={s.id}>
                <button type="button" className="rv-gstep" data-state={s.state} aria-current={s.id === current.id ? "step" : undefined} onClick={() => setPicked(s.id)}>
                  <span className="rv-gstep__n" aria-hidden>
                    {s.state === "done" ? <Check size={15} strokeWidth={3} /> : s.state === "waiting" ? <Clock size={14} /> : i + 1}
                  </span>
                  <span className="rv-gstep__t">
                    <strong>{t(`rv.guide.step.${s.id}.title` as MessageKey)}</strong>
                    <small>{t(`rv.guide.step.${s.id}.short` as MessageKey)}</small>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </Glass>

        <div className="rv-page">
          <Glass level={3} tint="agent" className="rv-card rv-guide__card" data-testid="guide-current">
            <div className="rv-card__head">
              <span className="rv-section-title">{t("rv.guide.stepOf", { n: index + 1, total: steps.length })}</span>
              <div className="rv-actions">
                {!current.required ? <Chip>{t("rv.guide.optional")}</Chip> : null}
                <Chip tint={current.state === "done" ? "success" : current.state === "waiting" ? "attention" : "neutral"}>{t(`rv.guide.state.${current.state}` as MessageKey)}</Chip>
              </div>
            </div>
            <h2 className="rv-guide__title">{t(key("title"))}</h2>
            <p className="rv-guide__text">{t(key("text"))}</p>

            {isStart && data.agent?.state === "running" ? <Note tone="success">{t("rv.guide.started")}</Note> : null}
            {isStart && !everythingElseDone ? <Note tone="warn">{t("rv.home.start.blocked")}</Note> : null}
            {isStart && data.agent && !data.agent.ready && everythingElseDone && data.agent.reasons.length > 0 ? (
              <Note tone="warn">
                <ul className="stack" dir="auto">
                  {data.agent.reasons.map((r) => (
                    <li key={r.id}>{r.message ?? r.id}</li>
                  ))}
                </ul>
              </Note>
            ) : null}
            {failure ? <FailureNote failure={failure} /> : null}

            <div className="rv-actions">
              {isStart ? (
                data.agent?.state === "running" ? (
                  <LinkButton to="/home" variant="primary" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
                    {t("rv.guide.seeHome")}
                  </LinkButton>
                ) : (
                  <Button variant="primary" size="lg" leading={<Play size={16} aria-hidden />} onClick={() => void start()} loading={busy} disabledReason={data.agent && !data.agent.ready ? t("rv.home.start.blocked") : undefined}>
                    {t("rv.guide.cta.start")}
                  </Button>
                )
              ) : (
                <LinkButton to={current.to} variant="primary" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
                  {t(`rv.guide.cta.${current.id}` as MessageKey)}
                </LinkButton>
              )}
              {next && !isStart ? (
                <Button variant="ghost" onClick={() => setPicked(next.id)}>
                  {t(current.state === "done" || current.required ? "rv.guide.next" : "rv.guide.skip")}
                </Button>
              ) : null}
            </div>
          </Glass>

          <div className="rv-grid-2">
            <Glass level={1} className="rv-card" style={{ gap: 10 }}>
              <span className="rv-section-title">{t("rv.guide.examples")}</span>
              <p className="c-2">{t(key("ex"))}</p>
            </Glass>
            <Glass level={1} className="rv-card" style={{ gap: 10 }}>
              <span className="rv-section-title">{t("rv.guide.tip")}</span>
              <p className="c-2">{t(key("tip"))}</p>
              <p className="c-3 t-body-sm">{t("rv.guide.help.text")}</p>
            </Glass>
          </div>
        </div>
      </div>
    </div>
  );
}
