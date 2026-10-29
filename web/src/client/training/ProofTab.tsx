/** "Proof and material": the testimonials the agent may bring, and the documents and texts it reads to
 *  answer accurately about each product. */

import { useI18n } from "../../i18n";
import type { ProductMaterial } from "../api";
import { Note } from "../kit";
import { LessonList, type LessonCopy } from "./LessonEditor";
import { MaterialPanel } from "./MaterialPanel";
import type { Lesson, Playbook, ProductOption } from "./trainingModel";

export function ProofTab({ lessons, onChange, products, playbook, material, onMaterialChanged }: { lessons: Lesson[]; onChange: (next: Lesson[]) => void; products: ProductOption[]; playbook: Playbook; material: ProductMaterial[] | null; onMaterialChanged: () => void }) {
  const { t } = useI18n();
  const proof: LessonCopy = {
    title: t("rv.train.proof.title"),
    text: t("rv.train.proof.text"),
    answer: { label: t("rv.train.proof.what"), placeholder: t("rv.train.proof.what.placeholder") },
    source: { label: t("rv.train.proof.source"), placeholder: t("rv.train.proof.source.placeholder") },
    add: t("rv.train.proof.add"),
  };
  return (
    <>
      {playbook.social_proof === "never" ? <Note tone="warn">{t("rv.train.proof.never")}</Note> : null}
      <LessonList kind="proof" lessons={lessons} onChange={onChange} products={products} copy={proof} testId="training-proof" />
      <MaterialPanel products={products} material={material} onChanged={onMaterialChanged} lessons={lessons} onLessons={onChange} />
    </>
  );
}
