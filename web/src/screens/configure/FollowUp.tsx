import { useState } from "react";
import { Ban, Repeat, X } from "lucide-react";
import { useStore } from "../../mock/store";
import { CadenceEditor } from "../../agent/Offers";
import { ToggleChips } from "../../ui/Controls";
import { Group } from "./Group";

/* ── How REVANSA follows up: a fully editable cadence, then when it should stop ── */
export function FollowUpSection() {
  const { state, dispatch } = useStore();
  const { steps, stopConditions, customStopConditions } = state.followUp;
  const [draft, setDraft] = useState("");
  return (
    <>
      <Group title="The follow-up timeline" hint="Add, remove or move a reminder — REVANSA stops on its own after the last one." icon={Repeat}>
        <CadenceEditor steps={steps} onChange={(v) => dispatch({ type: "setFollowUpSteps", value: v })} />
      </Group>
      <hr className="divider" />
      <Group title="Stop following up when" hint="REVANSA reads each reply and recognises these on its own — describe the situation in your own words." icon={Ban}>
        <ToggleChips
          label="Stop conditions"
          value={stopConditions}
          onChange={(v) => dispatch({ type: "setFollowUpStop", value: v })}
          options={[
            { value: "asked_to_stop", label: "They ask me to stop" },
            { value: "not_interested", label: "They say they're not interested" },
            { value: "bought_elsewhere", label: "They bought elsewhere" },
          ]}
        />
        <div className="row row--wrap">
          {customStopConditions.map((c) => (
            <span key={c} className="phrase">
              {c}
              <button
                type="button"
                aria-label={`Remove ${c}`}
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
              placeholder='Add a reason, e.g. "They mention a competitor"'
              aria-label="Add a stop condition"
            />
          </form>
        </div>
      </Group>
    </>
  );
}
