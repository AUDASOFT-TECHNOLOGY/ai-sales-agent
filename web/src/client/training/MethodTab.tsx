/** "My way of selling": the choices that hold for every product (how the agent leads, closes, uses
 *  urgency and proof), the business in the client's own words, then writing habits and red lines. */

import { useI18n, type MessageKey } from "../../i18n";
import { Segmented, Textarea } from "../../ui/Controls";
import { Card } from "../kit";
import { SimpleList } from "./LessonEditor";
import { APPROACHES, CLOSINGS, SHORT_MAX, type Lesson, type Playbook } from "./trainingModel";

const HABIT_IDEAS: MessageKey[] = ["rv.train.s1", "rv.train.s2", "rv.train.s3", "rv.train.s4", "rv.train.s5", "rv.train.s6", "rv.train.s7", "rv.train.s8", "rv.train.s9"];
const NEVER_IDEAS: MessageKey[] = ["rv.train.n1", "rv.train.n2", "rv.train.n3", "rv.train.n4", "rv.train.n5"];
const CRAFT: MessageKey[] = ["rv.train.craft.1", "rv.train.craft.2", "rv.train.craft.3", "rv.train.craft.4"];

export function MethodTab({ playbook, onPlaybook, lessons, onLessons }: { playbook: Playbook; onPlaybook: (next: Playbook) => void; lessons: Lesson[]; onLessons: (next: Lesson[]) => void }) {
  const { t } = useI18n();
  const set = (patch: Partial<Playbook>) => onPlaybook({ ...playbook, ...patch, chosen: true });
  return (
    <>
      <Card data-testid="training-method">
        <h3>{t("rv.train.method.title")}</h3>
        <p className="c-2">{t("rv.train.method.text")}</p>

        <div className="stack">
          <Segmented
            label={t("rv.train.approach.label")}
            layoutId="train-approach"
            value={playbook.approach}
            onChange={(approach) => set({ approach })}
            options={APPROACHES.map((a) => ({ value: a, label: t(`rv.train.approach.${a}` as MessageKey) }))}
          />
          <p className="c-2" data-testid="approach-text">{t(`rv.train.approach.${playbook.approach}.text` as MessageKey)}</p>
        </div>

        <div className="stack">
          <Segmented
            label={t("rv.train.closing.label")}
            layoutId="train-closing"
            value={playbook.closing}
            onChange={(closing) => set({ closing })}
            options={CLOSINGS.map((c) => ({ value: c, label: t(`rv.train.closing.${c}` as MessageKey) }))}
          />
          <p className="c-2">{t(`rv.train.closing.${playbook.closing}.text` as MessageKey)}</p>
        </div>

        <div className="stack">
          <Segmented
            label={t("rv.train.urgency.label")}
            layoutId="train-urgency"
            value={playbook.urgency}
            onChange={(urgency) => set({ urgency })}
            options={[
              { value: "none", label: t("rv.train.urgency.none") },
              { value: "real_reasons_only", label: t("rv.train.urgency.real") },
            ]}
          />
          <p className="c-3 t-body-sm">{t("rv.train.urgency.help")}</p>
        </div>

        <Segmented
          label={t("rv.train.socialproof.label")}
          layoutId="train-proof"
          value={playbook.social_proof}
          onChange={(social_proof) => set({ social_proof })}
          options={[
            { value: "when_relevant", label: t("rv.train.socialproof.when") },
            { value: "never", label: t("rv.train.socialproof.never") },
          ]}
        />
      </Card>

      <Card data-testid="training-pitch">
        <Textarea label={t("rv.train.pitch.label")} helper={t("rv.train.pitch.help")} rows={2} maxLength={SHORT_MAX} dir="auto" placeholder={t("rv.train.pitch.placeholder")} value={playbook.pitch} onChange={(e) => set({ pitch: e.target.value })} />
        <Textarea label={t("rv.train.ideal.label")} helper={t("rv.train.ideal.help")} rows={2} maxLength={SHORT_MAX} dir="auto" placeholder={t("rv.train.ideal.placeholder")} value={playbook.ideal_customer} onChange={(e) => set({ ideal_customer: e.target.value })} />
      </Card>

      <Card>
        <h3>{t("rv.train.craft.title")}</h3>
        <ul className="stack c-2">
          {CRAFT.map((k) => (
            <li key={k}>{t(k)}</li>
          ))}
        </ul>
      </Card>

      <SimpleList kind="habit" lessons={lessons} onChange={onLessons} ideas={HABIT_IDEAS} max={10} testId="training-habits" title={t("rv.train.habits.title")} text={t("rv.train.habits.text")} placeholder={t("rv.train.habits.placeholder")} addLabel={t("rv.train.habits.add")} ideasLabel={t("rv.train.habits.suggest")} />
      <SimpleList kind="never" lessons={lessons} onChange={onLessons} ideas={NEVER_IDEAS} max={10} testId="training-never" title={t("rv.train.never.title")} text={t("rv.train.never.text")} placeholder={t("rv.train.never.placeholder")} addLabel={t("rv.train.never.add")} ideasLabel={t("rv.train.never.suggest")} />
    </>
  );
}
