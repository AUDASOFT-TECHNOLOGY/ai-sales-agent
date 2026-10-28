/** A questionnaire shell: one question at a time with a progress bar, then a recap where every answer
 *  can be changed. It knows nothing about the answers: the screen that uses it gives the questions,
 *  checks and saves each answer, and says what to show in the recap. */

import { useState, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Check, Pencil } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Glass } from "../../ui/Glass";
import { FailureNote, type Failure } from "../kit";

export interface FlowQuestion {
  id: string;
  /** The question, asked in full. */
  title: string;
  /** A short name for the recap. */
  label: string;
  optional?: boolean;
  /** The answer's fields; `problem` is what the last attempt found wrong. */
  body: (problem: MessageKey | null) => ReactNode;
  /** The answer in a few words, for the recap ("" when not given). */
  summary: string;
}

/** What answering came to: fine, something to fix in the answer, or a refusal from the server. */
export type Verdict = { problem: MessageKey } | { failure: Failure } | null;

export function QuestionFlow({
  questions,
  initial,
  onAnswer,
  onDiscard,
  recapTitle,
  testId,
}: {
  questions: FlowQuestion[];
  /** The question to open on, or null for the recap. */
  initial: number | null;
  onAnswer: (index: number, skip: boolean) => Promise<Verdict>;
  /** Drops what was typed and not saved. */
  onDiscard: () => void;
  recapTitle: string;
  testId: string;
}) {
  const { t } = useI18n();
  const [at, setAt] = useState<number | null>(initial);
  const [fromRecap, setFromRecap] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function answer(index: number, skip = false) {
    setBusy(true);
    setFailure(null);
    setProblem(null);
    const verdict = await onAnswer(index, skip);
    setBusy(false);
    if (verdict && "problem" in verdict) return setProblem(verdict.problem);
    if (verdict && "failure" in verdict) return setFailure(verdict.failure);
    if (fromRecap || index === questions.length - 1) {
      setFromRecap(false);
      setAt(null);
    } else setAt(index + 1);
  }

  function edit(index: number) {
    setFromRecap(true);
    setFailure(null);
    setProblem(null);
    setAt(index);
  }

  if (at === null) {
    return (
      <Glass level={1} className="rv-card rv-qs" data-testid={`${testId}-recap`}>
        <div className="rv-card__head">
          <h3>{recapTitle}</h3>
        </div>
        <dl className="rv-qs__recap">
          {questions.map((q, i) => (
            <div key={q.id} className="rv-qs__row">
              <dt>{q.label}</dt>
              <dd dir="auto" className={q.summary.trim() === "" ? "c-3" : undefined}>
                {q.summary.trim() === "" ? t("rv.prep.recap.empty") : q.summary}
              </dd>
              <Button size="sm" variant="ghost" leading={<Pencil size={14} aria-hidden />} onClick={() => edit(i)} aria-label={`${t("rv.prep.recap.edit")} · ${q.label}`}>
                {t("rv.prep.recap.edit")}
              </Button>
            </div>
          ))}
        </dl>
      </Glass>
    );
  }

  const q = questions[at]!;
  const last = at === questions.length - 1;
  const progress = t("rv.prep.q.progress", { n: at + 1, total: questions.length });
  return (
    <Glass level={2} tint="agent" className="rv-card rv-qs" data-testid={`${testId}-question`}>
      <div className="rv-qs__bar" role="progressbar" aria-valuemin={0} aria-valuemax={questions.length} aria-valuenow={at + 1} aria-label={progress}>
        {questions.map((x, i) => (
          <span key={x.id} data-on={i <= at || undefined} />
        ))}
      </div>
      <span className="rv-section-title">{progress}</span>
      <h3 className="rv-qs__title">{q.title}</h3>
      {failure ? <FailureNote failure={failure} /> : null}
      <form
        className="rv-qs__form"
        onSubmit={(e) => {
          e.preventDefault();
          void answer(at);
        }}
        noValidate
      >
        {q.body(problem)}
        <div className="rv-actions rv-qs__actions">
          {fromRecap ? (
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                onDiscard();
                setFromRecap(false);
                setAt(null);
              }}
            >
              {t("rv.common.discard")}
            </Button>
          ) : at > 0 ? (
            <Button type="button" variant="ghost" disabled={busy} leading={<ArrowLeft size={16} className="mirror" aria-hidden />} onClick={() => setAt(at - 1)}>
              {t("rv.prep.q.back")}
            </Button>
          ) : null}
          {!fromRecap && q.optional ? (
            <Button type="button" variant="ghost" disabled={busy} onClick={() => void answer(at, true)}>
              {t("rv.prep.q.skip")}
            </Button>
          ) : null}
          <Button type="submit" variant="primary" loading={busy} trailing={fromRecap ? <Check size={16} aria-hidden /> : <ArrowRight size={16} className="mirror" aria-hidden />}>
            {fromRecap ? t("rv.common.save") : last ? t("rv.prep.q.finish") : t("rv.prep.q.next")}
          </Button>
        </div>
      </form>
    </Glass>
  );
}
