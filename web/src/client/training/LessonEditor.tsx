/** Editing lessons of one kind. `SimpleList` is for one-line lessons (a habit, a red line) with ideas to
 *  add in a click; `LessonList` is for lessons that answer a situation (a doubt, a question, a model
 *  answer). Both write into the whole list of lessons, so the page keeps one draft. */

import { Plus, Trash2 } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Textarea, TextInput } from "../../ui/Controls";
import { Card, Note } from "../kit";
import { LESSON_MAX, SHORT_MAX, SITUATIONAL, TEXT_MAX, blankLesson, lessonProblem, type Lesson, type LessonKind, type ProductOption } from "./trainingModel";

type Change = (next: Lesson[]) => void;

const patchOf = (lessons: Lesson[], uid: number, patch: Partial<Lesson>) => lessons.map((l) => (l.uid === uid ? { ...l, ...patch } : l));
const without = (lessons: Lesson[], uid: number) => lessons.filter((l) => l.uid !== uid);

/** Habits and red lines: one line each, with suggestions to add in a click. */
export function SimpleList({ kind, lessons, onChange, ideas, title, text, placeholder, addLabel, ideasLabel, testId, max }: { kind: "habit" | "never"; lessons: Lesson[]; onChange: Change; ideas: MessageKey[]; title: string; text: string; placeholder: string; addLabel: string; ideasLabel: string; testId: string; max: number }) {
  const { t } = useI18n();
  const mine = lessons.filter((l) => l.kind === kind);
  const offered = ideas.map((k) => t(k)).filter((idea) => !mine.some((l) => l.text.trim() === idea));
  const room = mine.length < max && lessons.length < LESSON_MAX;
  return (
    <Card data-testid={testId}>
      <h3>{title}</h3>
      <p className="c-2">{text}</p>
      <div className="stack">
        {mine.map((lesson, i) => (
          <div className="row" key={lesson.uid}>
            <input className="input well" aria-label={`${title} ${i + 1}`} value={lesson.text} maxLength={TEXT_MAX} dir="auto" placeholder={placeholder} onChange={(e) => onChange(patchOf(lessons, lesson.uid, { text: e.target.value }))} />
            <Button type="button" variant="ghost" size="sm" leading={<Trash2 size={14} aria-hidden />} onClick={() => onChange(without(lessons, lesson.uid))} aria-label={`${t("rv.common.remove")} ${i + 1}`}>
              {t("rv.common.remove")}
            </Button>
          </div>
        ))}
        {room ? (
          <div>
            <Button type="button" size="sm" leading={<Plus size={14} aria-hidden />} onClick={() => onChange([...lessons, blankLesson(kind)])}>
              {addLabel}
            </Button>
          </div>
        ) : (
          <p className="c-3 t-body-sm">{t("rv.train.lesson.full", { n: LESSON_MAX })}</p>
        )}
        {room && offered.length > 0 ? (
          <div>
            <p className="t-label c-2">{ideasLabel}</p>
            <div className="rv-actions">
              {offered.map((idea) => (
                <Button key={idea} type="button" size="sm" variant="secondary" onClick={() => onChange([...lessons.filter((l) => !(l.kind === kind && l.text.trim() === "")), blankLesson(kind, { text: idea })])}>
                  {idea}
                </Button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export interface LessonCopy {
  title: string;
  text: string;
  hint?: string;
  when?: { label: string; placeholder: string };
  answer: { label: string; placeholder: string };
  why?: { label: string; placeholder: string };
  add: string;
  /** Situations to cover first, in a click (the `when` is filled in). */
  starters?: { label: string; items: MessageKey[] };
}

/** Lessons that answer a situation. */
export function LessonList({ kind, lessons, onChange, products, copy, testId, defaultProduct = "" }: { kind: LessonKind; lessons: Lesson[]; onChange: Change; products: ProductOption[]; copy: LessonCopy; testId: string; defaultProduct?: string }) {
  const { t } = useI18n();
  const mine = lessons.filter((l) => l.kind === kind);
  const room = lessons.length < LESSON_MAX;
  const covered = (when: string) => mine.some((l) => l.when.trim().toLowerCase() === when.trim().toLowerCase());
  const starters = (copy.starters?.items ?? []).map((k) => t(k)).filter((when) => !covered(when));
  return (
    <Card data-testid={testId}>
      <h3>{copy.title}</h3>
      <p className="c-2">{copy.text}</p>
      {copy.hint ? <Note>{copy.hint}</Note> : null}
      <div className="stack">
        {mine.length === 0 ? <p className="c-3">{t("rv.train.lesson.none")}</p> : null}
        {mine.map((lesson, i) => (
          <div className="rv-fieldset stack" key={lesson.uid} role="group" aria-label={`${copy.title} ${i + 1}`}>
            {copy.when ? <Textarea label={copy.when.label} rows={2} maxLength={SHORT_MAX} dir="auto" placeholder={copy.when.placeholder} value={lesson.when} onChange={(e) => onChange(patchOf(lessons, lesson.uid, { when: e.target.value }))} /> : null}
            <Textarea label={copy.answer.label} rows={3} maxLength={TEXT_MAX} dir="auto" placeholder={copy.answer.placeholder} value={lesson.text} onChange={(e) => onChange(patchOf(lessons, lesson.uid, { text: e.target.value }))} />
            {copy.why ? <TextInput label={copy.why.label} maxLength={SHORT_MAX} dir="auto" placeholder={copy.why.placeholder} value={lesson.why} onChange={(e) => onChange(patchOf(lessons, lesson.uid, { why: e.target.value }))} /> : null}
            {lessonProblem(lesson) ? <Note tone="warn">{t("rv.train.lesson.exact")}</Note> : null}
            <div className="row">
              {products.length > 0 ? <ProductPick value={lesson.product} products={products} onChange={(product) => onChange(patchOf(lessons, lesson.uid, { product }))} /> : null}
              <Button type="button" variant="ghost" size="sm" leading={<Trash2 size={14} aria-hidden />} onClick={() => onChange(without(lessons, lesson.uid))}>
                {t("rv.common.remove")}
              </Button>
            </div>
          </div>
        ))}
        {room ? (
          <div>
            <Button type="button" size="sm" leading={<Plus size={14} aria-hidden />} onClick={() => onChange([...lessons, blankLesson(kind, { product: defaultProduct })])}>
              {copy.add}
            </Button>
          </div>
        ) : (
          <p className="c-3 t-body-sm">{t("rv.train.lesson.full", { n: LESSON_MAX })}</p>
        )}
        {room && SITUATIONAL.includes(kind) && starters.length > 0 && copy.starters ? (
          <div>
            <p className="t-label c-2">{copy.starters.label}</p>
            <div className="rv-actions">
              {starters.map((when) => (
                <Button key={when} type="button" size="sm" variant="secondary" onClick={() => onChange([...lessons.filter((l) => !(l.kind === kind && l.when.trim() === "" && l.text.trim() === "")), blankLesson(kind, { when, product: defaultProduct })])}>
                  {when}
                </Button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function ProductPick({ value, products, onChange }: { value: string; products: ProductOption[]; onChange: (product: string) => void }) {
  const { t } = useI18n();
  const known = value === "" || products.some((p) => p.id === value);
  return (
    <label className="rv-select-label">
      <span className="t-label c-2">{t("rv.train.lesson.product")}</span>
      <select className="rv-select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("rv.train.lesson.product.all")}</option>
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
        {known ? null : <option value={value}>{t("rv.train.lesson.product.gone")}</option>}
      </select>
    </label>
  );
}
