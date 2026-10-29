/** Train your agent: the client teaches the agent to sell like their best salesperson.
 *
 *  Four sections over ONE draft, saved together: an overview (how ready the agent is and what to do
 *  next), the sales method (how it leads and closes, its habits and red lines), the client's own answers
 *  (to doubts, to frequent questions, to any situation), and the correction of a message from a test.
 *  Documents, testimonials and arguments belong to each product (My offer): the agent reads them by itself. What is taught
 *  lives in `communication.playbook` and `communication.training`; the agent is shown only the lessons
 *  that help each message, and its rules always win over a lesson. */

import { useMemo, useState } from "react";
import { GraduationCap, Save, Sparkles } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { FailureNote, Note, PageHead, type Failure } from "../kit";
import { OfferProvider, useOffer } from "../offer/OfferLayout";
import { LearnedCard } from "../learning/LearnedCard";
import "../learning/learning.css";
import { withLanguages } from "../prepare/agentModel";
import { AnswersTab } from "./AnswersTab";
import { FixTab } from "./FixTab";
import { MethodTab } from "./MethodTab";
import { OverviewTab, type TabId } from "./OverviewTab";
import { readinessOf } from "./readiness";
import { countOf, isHalf, lessonProblem, orphans, productOptions, setTraining, signature, trainingOf, type Lesson, type Playbook } from "./trainingModel";

const TABS: TabId[] = ["overview", "method", "answers", "fix"];

export function TrainingPage() {
  const { t } = useI18n();
  return (
    <div className="rv-page" data-testid="training-page">
      <PageHead title={t("rv.train.title")} text={t("rv.train.intro")} />
      <OfferProvider>
        <Training />
      </OfferProvider>
    </div>
  );
}

function Training() {
  const { t, locale } = useI18n();
  const { commercial, save } = useOffer();
  const saved = useMemo(() => trainingOf(commercial), [commercial]);
  const [draft, setDraft] = useState(saved);
  const [base, setBase] = useState(saved);
  const [tab, setTab] = useState<TabId>("overview");
  const [mode, setMode] = useState<"taught" | "auto">("taught");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  // What the server holds changed (a save, a reload): the screen shows it.
  if (base !== saved && JSON.stringify(base) !== JSON.stringify(saved)) {
    setBase(saved);
    setDraft(saved);
  }

  const products = useMemo(() => productOptions(commercial, locale), [commercial, locale]);
  const dirty = signature(commercial, draft) !== signature(commercial, saved);
  const readiness = useMemo(() => readinessOf({ commercial, playbook: draft.playbook, lessons: draft.lessons }), [commercial, draft]);

  const change = (next: Partial<{ playbook: Playbook; lessons: Lesson[] }>) => {
    setDraft({ ...draft, ...next });
    setDone(false);
  };

  async function submit() {
    setBusy(true);
    setFailure(null);
    const problem = await save({ commercial: setTraining(withLanguages(commercial, locale), draft) });
    setFailure(problem);
    setDone(problem === null);
    setBusy(false);
  }

  const half = draft.lessons.filter(isHalf).length;
  const refused = draft.lessons.filter((l) => lessonProblem(l) !== null).length;
  const gone = orphans(draft.lessons, products).length;

  const switcher = (
    <div className="ln-mode" role="tablist" aria-label={t("rv.mode.label")}>
      <button type="button" role="tab" aria-selected={mode === "taught"} data-mode="taught" className="ln-mode__item" onClick={() => setMode("taught")}>
        <GraduationCap size={22} aria-hidden />
        <span>
          <strong>{t("rv.mode.taught")}</strong>
          <small>{t("rv.mode.taught.sub")}</small>
        </span>
      </button>
      <button type="button" role="tab" aria-selected={mode === "auto"} data-mode="auto" className="ln-mode__item" onClick={() => setMode("auto")}>
        <Sparkles size={22} aria-hidden />
        <span>
          <strong>{t("rv.mode.auto")}</strong>
          <small>{t("rv.mode.auto.sub")}</small>
        </span>
      </button>
    </div>
  );

  if (mode === "auto") {
    return (
      <>
        {switcher}
        <LearnedCard />
      </>
    );
  }

  return (
    <>
      {switcher}
      <div className="rv-tabs" role="tablist" aria-label={t("rv.train.tabs")}>
        {TABS.map((id) => (
          <button key={id} type="button" role="tab" id={`train-tab-${id}`} aria-selected={tab === id} aria-controls="train-panel" className={tab === id ? "rv-tabs__item is-active" : "rv-tabs__item"} onClick={() => setTab(id)}>
            {t(`rv.train.tab.${id}` as MessageKey)}
          </button>
        ))}
      </div>

      <div className="stack" id="train-panel" role="tabpanel" aria-labelledby={`train-tab-${tab}`}>
        {tab === "overview" ? <OverviewTab readiness={readiness} onGo={setTab} dirty={dirty} /> : null}
        {tab === "method" ? <MethodTab playbook={draft.playbook} onPlaybook={(playbook) => change({ playbook })} lessons={draft.lessons} onLessons={(lessons) => change({ lessons })} /> : null}
        {tab === "answers" ? <AnswersTab lessons={draft.lessons} onChange={(lessons) => change({ lessons })} products={products} /> : null}
        {tab === "fix" ? <FixTab lessons={draft.lessons} onChange={(lessons) => change({ lessons })} /> : null}
      </div>

      {half > 0 ? <Note tone="warn">{t("rv.train.half", { n: half })}</Note> : null}
      {refused > 0 ? <Note tone="warn">{t("rv.train.lesson.exact")}</Note> : null}
      {gone > 0 ? <Note tone="warn">{t("rv.train.lesson.gone", { n: gone })}</Note> : null}
      {failure ? <FailureNote failure={failure} /> : null}
      {done ? (
        <Note tone="success" title={t("rv.train.saved")}>
          {t("rv.train.saved.text")}
        </Note>
      ) : null}
      <div className="rv-actions">
        <Button variant="primary" leading={<Save size={16} aria-hidden />} loading={busy} disabled={busy || !dirty} disabledReason={dirty ? undefined : t("rv.train.nothing")} onClick={submit}>
          {t("rv.train.save", { n: countOf(draft.lessons) })}
        </Button>
        <LinkButton to="/simulation" variant="secondary">
          {t("rv.train.retest")}
        </LinkButton>
      </div>
    </>
  );
}
