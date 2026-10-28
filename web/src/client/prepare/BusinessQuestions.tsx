/** "Your business" as a short questionnaire: one question at a time, each answer saved as it is given,
 *  then a recap where every answer can be changed. The answers are the same fields as "Business and
 *  rules", written through the same settings document. How the agent speaks and when it writes are
 *  asked in the next phase ("Your agent"). */

import { useMemo, useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { TextInput, Textarea } from "../../ui/Controls";
import { HTTPS, businessChanges, draftOf } from "../offer/BusinessPage";
import { useOffer } from "../offer/OfferLayout";
import { GOAL_KINDS, goalOf, personaOf, setGoal, setPersona, withLanguages, type GoalKind } from "./agentModel";
import { QuestionFlow, type FlowQuestion, type Verdict } from "./QuestionFlow";
import { Choices } from "./Choices";
import { OFFER_KINDS, useOfferKind, type OfferKind } from "./terms";

interface Draft {
  name: string;
  summary: string;
  website: string;
  goal: GoalKind | "";
  persona: string;
}

export function BusinessQuestions() {
  const { t, locale } = useI18n();
  const { state, commercial, save } = useOffer();
  const saved = useMemo<Draft>(() => {
    const base = draftOf(commercial, state.settings);
    return { name: base.name, summary: base.summary, website: base.website, goal: goalOf(commercial), persona: personaOf(commercial) };
  }, [commercial, state.settings]);
  const [draft, setDraft] = useState<Draft>(saved);
  const [base, setBase] = useState(saved);
  if (base !== saved && JSON.stringify(base) !== JSON.stringify(saved)) {
    setBase(saved);
    setDraft(saved);
  }
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const [kind, setKind] = useOfferKind();
  const [pickedKind, setPickedKind] = useState<OfferKind | "">(kind);

  function check(id: string): MessageKey | null {
    if (id === "name" && draft.name.trim() === "") return "rv.biz.err.name";
    if (id === "activity" && draft.summary.trim() === "") return "rv.prep.err.activity";
    if (id === "website" && draft.website.trim() !== "" && !HTTPS.test(draft.website.trim())) return "rv.biz.err.website";
    return null;
  }

  async function onAnswer(index: number, skip: boolean): Promise<Verdict> {
    const id = questions[index]!.id;
    const wrong = skip ? null : check(id);
    if (wrong) return { problem: wrong };
    if (skip) return null;
    // What the client sells only changes the words of the space: it is kept in this browser.
    if (id === "offer") {
      setKind(pickedKind);
      return null;
    }
    // The server refuses a document without languages: the interface language stands in until the next phase.
    const first = businessChanges(withLanguages(commercial, locale), { ...draftOf(commercial, state.settings), name: draft.name, website: draft.website, summary: draft.summary }, ["company"]);
    let next = setPersona(first.commercial as Record<string, unknown>, draft.persona);
    next = setGoal(next, draft.goal, draft.goal === "" ? "" : t(`rv.prep.goal.${draft.goal}` as MessageKey));
    const failed = await save({ commercial: next });
    return failed ? { failure: failed } : null;
  }

  const questions: FlowQuestion[] = [
    {
      id: "name",
      title: t("rv.prep.q.name"),
      label: t("rv.prep.q.name.label"),
      summary: draft.name,
      body: (problem) => <TextInput autoFocus label={t("rv.prep.q.name.label")} value={draft.name} onChange={(e) => set({ name: e.target.value })} error={problem ? t(problem) : undefined} maxLength={300} />,
    },
    {
      id: "activity",
      title: t("rv.prep.q.activity"),
      label: t("rv.prep.q.activity.label"),
      summary: draft.summary,
      body: (problem) => <Textarea autoFocus label={t("rv.prep.q.activity.label")} helper={t("rv.prep.q.activity.hint")} value={draft.summary} onChange={(e) => set({ summary: e.target.value })} error={problem ? t(problem) : undefined} rows={5} maxLength={3000} />,
    },
    {
      id: "offer",
      title: t("rv.prep.q.offer"),
      label: t("rv.prep.q.offer.label"),
      optional: true,
      summary: pickedKind === "" ? "" : t(`rv.prep.offer.${pickedKind}` as MessageKey),
      body: () => (
        <>
          <p className="c-3 t-body-sm">{t("rv.prep.q.offer.hint")}</p>
          <Choices
            label={t("rv.prep.q.offer.label")}
            value={pickedKind}
            onChange={(v) => setPickedKind(v as OfferKind)}
            options={OFFER_KINDS.map((k) => ({ value: k, label: t(`rv.prep.offer.${k}` as MessageKey), hint: t(`rv.prep.offer.${k}.hint` as MessageKey) }))}
          />
        </>
      ),
    },
    {
      id: "website",
      title: t("rv.prep.q.website"),
      label: t("rv.prep.q.website.label"),
      optional: true,
      summary: draft.website,
      body: (problem) => <TextInput autoFocus label={t("rv.prep.q.website.label")} value={draft.website} onChange={(e) => set({ website: e.target.value })} error={problem ? t(problem) : undefined} inputMode="url" dir="ltr" placeholder="https://" />,
    },
    {
      id: "goal",
      title: t("rv.prep.q.goal"),
      label: t("rv.prep.q.goal.label"),
      optional: true,
      summary: draft.goal === "" ? "" : t(`rv.prep.goal.${draft.goal}` as MessageKey),
      body: () => (
        <Choices
          label={t("rv.prep.q.goal.label")}
          value={draft.goal}
          onChange={(goal) => set({ goal: goal as GoalKind })}
          options={GOAL_KINDS.map((k) => ({ value: k, label: t(`rv.prep.goal.${k}` as MessageKey), hint: t(`rv.prep.goal.${k}.hint` as MessageKey) }))}
        />
      ),
    },
    {
      id: "persona",
      title: t("rv.prep.q.persona"),
      label: t("rv.prep.q.persona.label"),
      optional: true,
      summary: draft.persona,
      body: () => <TextInput autoFocus label={t("rv.prep.q.persona.label")} helper={t("rv.prep.q.persona.hint")} value={draft.persona} onChange={(e) => set({ persona: e.target.value })} maxLength={80} />,
    },
  ];

  const first = saved.name.trim() === "" ? 0 : saved.summary.trim() === "" ? 1 : null;
  return <QuestionFlow questions={questions} initial={first} onAnswer={onAnswer} onDiscard={() => {
      setDraft(saved);
      setPickedKind(kind);
    }} recapTitle={t("rv.prep.recap.title")} testId="business" />;
}
