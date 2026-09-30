import { useState } from "react";
import { Ban, Repeat, X } from "lucide-react";
import { useI18n } from "../../i18n";
import { useStore } from "../../mock/store";
import { CadenceEditor } from "../../agent/Offers";
import { ToggleChips } from "../../ui/Controls";
import { Group } from "./Group";

/* ── How REVANSA follows up: a fully editable cadence, then when it should stop ── */
export function FollowUpSection() {
  const { t } = useI18n();
  const { state, dispatch } = useStore();
  const { steps, stopConditions, customStopConditions } = state.followUp;
  const [draft, setDraft] = useState("");
  return (
    <>
      <Group title={t("followup.timeline.title")} hint={t("followup.timeline.hint", { brand: t("brand.name") })} icon={Repeat}>
        <CadenceEditor steps={steps} onChange={(v) => dispatch({ type: "setFollowUpSteps", value: v })} />
      </Group>
      <hr className="divider" />
      <Group title={t("followup.stopWhen.title")} hint={t("followup.stopWhen.hint", { brand: t("brand.name") })} icon={Ban}>
        <ToggleChips
          label={t("followup.stopConditions")}
          value={stopConditions}
          onChange={(v) => dispatch({ type: "setFollowUpStop", value: v })}
          options={[
            { value: "asked_to_stop", label: t("followup.stop.askedToStop") },
            { value: "not_interested", label: t("followup.stop.notInterested") },
            { value: "bought_elsewhere", label: t("followup.stop.boughtElsewhere") },
          ]}
        />
        <div className="row row--wrap">
          {customStopConditions.map((c) => (
            <span key={c} className="phrase">
              {c}
              <button
                type="button"
                aria-label={t("followup.removeReason", { reason: c })}
                onClick={() => dispatch({ type: "setFollowUpCustomStop", value: customStopConditions.filter((x) => x !== c) })}
              >
                <X size={16} aria-hidden />
              </button>
            </span>
          ))}
          <form
            className="phrase-add"
            onSubmit={(e) => {
              e.preventDefault();
              const v = draft.trim();
              if (v && !customStopConditions.includes(v)) dispatch({ type: "setFollowUpCustomStop", value: [...customStopConditions, v] });
              setDraft("");
            }}
          >
            <input
              className="input well"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={t("followup.addReasonPlaceholder")}
              aria-label={t("followup.addStopCondition")}
            />
          </form>
        </div>
      </Group>
    </>
  );
}
