/** "Fix a message": the agent's messages in a test the client picks. Each one can be corrected
 *  ("I would have said it differently": the client writes their own version, starting from the agent's)
 *  or kept as a model ("this is how it should be said"). Both become a model answer. */

import { useCallback, useState } from "react";
import { FlaskConical, ThumbsUp, Wand2 } from "lucide-react";
import { useI18n } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Textarea } from "../../ui/Controls";
import { clientAgentTest, clientAgentTests } from "../api";
import { Card, Empty, FailureNote, Loading, Note, failureOf } from "../kit";
import { useFetched } from "../useFetched";
import { LESSON_MAX, TEXT_MAX, blankLesson, situationBefore, type Lesson } from "./trainingModel";

export function FixTab({ lessons, onChange }: { lessons: Lesson[]; onChange: (next: Lesson[]) => void }) {
  const { t } = useI18n();
  const tests = useFetched(useCallback(() => clientAgentTests(), []));
  const [picked, setPicked] = useState<string | null>(null);
  // message index -> the lesson it started, and whether it was kept as it was written
  const [started, setStarted] = useState<Record<string, { uid: number; kept: boolean }>>({});
  const list = tests.value?.tests ?? [];
  const id = picked ?? list[0]?.prospect_id ?? null;
  const found = useFetched(useCallback(() => (id ? clientAgentTest(id) : Promise.resolve(null)), [id]));
  const messages = found.value?.messages ?? [];
  const full = lessons.length >= LESSON_MAX;
  const slot = (i: number) => `${id}:${i}`;

  function start(i: number, kept: boolean) {
    const text = messages[i]!.text.trim().slice(0, TEXT_MAX);
    const lesson = blankLesson("example", { when: situationBefore(messages, i) || t("rv.train.correct.first"), text });
    onChange([...lessons, lesson]);
    setStarted({ ...started, [slot(i)]: { uid: lesson.uid, kept } });
  }
  function cancel(i: number) {
    const mine = started[slot(i)];
    if (mine) onChange(lessons.filter((l) => l.uid !== mine.uid));
    setStarted(Object.fromEntries(Object.entries(started).filter(([key]) => key !== slot(i))));
  }

  return (
    <Card data-testid="training-correct">
      <h3>{t("rv.train.correct.title")}</h3>
      <p className="c-2">{t("rv.train.correct.text")}</p>
      {tests.loading ? <Loading /> : null}
      {tests.failed ? <FailureNote failure={failureOf(tests.error)} /> : null}
      {tests.value && list.length === 0 ? (
        <Empty icon={<FlaskConical size={22} />} title={t("rv.train.correct.none")} text={t("rv.train.correct.none.text")} action={<LinkButton to="/simulation" variant="primary">{t("rv.train.correct.go")}</LinkButton>} />
      ) : null}
      {list.length > 1 ? (
        <label className="rv-select-label">
          <span className="t-label c-2">{t("rv.train.correct.pick")}</span>
          <select className="rv-select" value={id ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {list.map((x) => (
              <option key={x.prospect_id} value={x.prospect_id}>{x.name} · {x.contact}</option>
            ))}
          </select>
        </label>
      ) : null}
      {found.failed ? <FailureNote failure={failureOf(found.error)} /> : null}
      {messages.length > 0 ? (
        <ol className="stack" aria-label={t("rv.train.correct.title")}>
          {messages.map((m, i) => {
            const agent = m.direction === "outbound";
            const mine = started[slot(i)];
            const lesson = mine ? lessons.find((l) => l.uid === mine.uid) : undefined;
            return (
              <li key={i} className={agent ? "rv-bubble" : "rv-bubble rv-bubble--you"}>
                <small className="c-3">{agent ? t("rv.test.from.agent") : t("rv.test.from.you")}</small>
                <p dir="auto" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{m.text}</p>
                {agent && !lesson ? (
                  <div className="rv-actions">
                    <Button size="sm" variant="ghost" leading={<Wand2 size={14} aria-hidden />} disabled={full} disabledReason={full ? t("rv.train.lesson.full", { n: LESSON_MAX }) : undefined} onClick={() => start(i, false)}>
                      {t("rv.train.correct.fix")}
                    </Button>
                    <Button size="sm" variant="ghost" leading={<ThumbsUp size={14} aria-hidden />} disabled={full} onClick={() => start(i, true)}>
                      {t("rv.train.correct.keep")}
                    </Button>
                  </div>
                ) : null}
                {agent && lesson && mine ? (
                  <div className="stack">
                    {mine.kept ? (
                      <Note tone="success">{t("rv.train.correct.kept")}</Note>
                    ) : (
                      <Textarea label={t("rv.train.correct.yours")} rows={3} maxLength={TEXT_MAX} dir="auto" value={lesson.text} onChange={(e) => onChange(lessons.map((l) => (l.uid === lesson.uid ? { ...l, text: e.target.value } : l)))} />
                    )}
                    <div>
                      <Button size="sm" variant="ghost" onClick={() => cancel(i)}>
                        {t("rv.train.correct.cancel")}
                      </Button>
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </Card>
  );
}
