/** "My answers": how the client answers doubts and refusals, frequent questions, and any other
 *  situation. Each lesson may be tied to one product. */

import { useI18n, type MessageKey } from "../../i18n";
import { LessonList, type LessonCopy } from "./LessonEditor";
import type { Lesson, ProductOption } from "./trainingModel";

const OBJECTION_STARTERS: MessageKey[] = ["rv.train.o1", "rv.train.o2", "rv.train.o3", "rv.train.o4", "rv.train.o5", "rv.train.o6", "rv.train.o7", "rv.train.o8"];

export function AnswersTab({ lessons, onChange, products }: { lessons: Lesson[]; onChange: (next: Lesson[]) => void; products: ProductOption[] }) {
  const { t } = useI18n();
  const objection: LessonCopy = {
    title: t("rv.train.obj.title"),
    text: t("rv.train.obj.text"),
    hint: t("rv.train.obj.framework"),
    when: { label: t("rv.train.obj.when"), placeholder: t("rv.train.obj.when.placeholder") },
    answer: { label: t("rv.train.obj.answer"), placeholder: t("rv.train.obj.answer.placeholder") },
    why: { label: t("rv.train.obj.why"), placeholder: t("rv.train.obj.why.placeholder") },
    add: t("rv.train.obj.add"),
    starters: { label: t("rv.train.obj.starters"), items: OBJECTION_STARTERS },
  };
  const faq: LessonCopy = {
    title: t("rv.train.faq.title"),
    text: t("rv.train.faq.text"),
    when: { label: t("rv.train.faq.when"), placeholder: t("rv.train.faq.when.placeholder") },
    answer: { label: t("rv.train.faq.answer"), placeholder: t("rv.train.faq.answer.placeholder") },
    add: t("rv.train.faq.add"),
  };
  const example: LessonCopy = {
    title: t("rv.train.examples.title"),
    text: t("rv.train.examples.text"),
    when: { label: t("rv.train.examples.when"), placeholder: t("rv.train.examples.when.placeholder") },
    answer: { label: t("rv.train.examples.answer"), placeholder: t("rv.train.examples.answer.placeholder") },
    why: { label: t("rv.train.obj.why"), placeholder: t("rv.train.obj.why.placeholder") },
    add: t("rv.train.examples.add"),
  };
  return (
    <>
      <p className="c-2">{t("rv.train.answers.intro")}</p>
      <LessonList kind="objection" lessons={lessons} onChange={onChange} products={products} copy={objection} testId="training-objections" />
      <LessonList kind="faq" lessons={lessons} onChange={onChange} products={products} copy={faq} testId="training-faq" />
      <LessonList kind="example" lessons={lessons} onChange={onChange} products={products} copy={example} testId="training-examples" />
    </>
  );
}
