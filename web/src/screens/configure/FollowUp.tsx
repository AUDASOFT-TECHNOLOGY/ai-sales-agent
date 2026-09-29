import { Ban, Repeat } from "lucide-react";
import { useStore } from "../../mock/store";
import { CadenceEditor } from "../../agent/Offers";
import { ToggleChips } from "../../ui/Controls";
import { Group } from "./Group";

/* ── How REVENSA follows up: a fully editable cadence, then when it should stop ── */
export function FollowUpSection() {
  const { state, dispatch } = useStore();
  const { steps, stopConditions } = state.followUp;
  return (
    <>
      <Group title="The follow-up timeline" hint="Add, remove or move a touch — REVENSA stops on its own after the last one." icon={Repeat}>
        <CadenceEditor steps={steps} onChange={(v) => dispatch({ type: "setFollowUpSteps", value: v })} />
      </Group>
      <hr className="divider" />
      <Group title="Stop following up when" icon={Ban}>
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
      </Group>
    </>
  );
}
