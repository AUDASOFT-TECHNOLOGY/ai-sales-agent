import { useState } from "react";
import { Building2, Check, Copy, Loader2, Table2 } from "lucide-react";
import { TENANT } from "../../mock/data";
import { useStore } from "../../mock/store";
import { Button } from "../../ui/Button";
import { Disclosure } from "../../ui/Disclosure";
import { LockedField, TextInput } from "../../ui/Controls";
import { Group } from "./Group";

/* ── Your business: identity and the Google Sheet (real, wired to state.sheet) ── */
export function BusinessSection() {
  const { state, dispatch } = useStore();
  const [step, setStep] = useState(state.sheet.verified ? 3 : 0);
  const [running, setRunning] = useState(false);
  const steps = ["We can open your Sheet", "It has the columns REVANSA needs", "The information lines up"];
  const run = () => {
    setRunning(true);
    setStep(0);
    [1, 2, 3].forEach((n, i) =>
      window.setTimeout(() => {
        setStep(n);
        if (n === 3) {
          setRunning(false);
          dispatch({ type: "verifySheet" });
        }
      }, 650 * (i + 1)),
    );
  };
  return (
    <>
      <Group title="Your company" icon={Building2}>
        <div className="grid-2">
          <LockedField label="Business name" value={TENANT.name} helper="Set with your platform administrator." />
          <LockedField label="Time zone" value={TENANT.timezone} helper="All times follow this zone." />
        </div>
      </Group>
      <hr className="divider" />
      <Group title="Google Sheet" hint="REVANSA reads your prospects from here, and only from here." icon={Table2}>
        <TextInput
          label="Sheet link"
          value={state.sheet.url}
          onChange={(e) => dispatch({ type: "setSheetUrl", url: e.target.value })}
          helper={state.sheet.verified ? undefined : "Test the connection after changing the link."}
        />
        <Disclosure title="What should my Sheet look like?" hint="One row per prospect">
          <p className="c-2 t-body-sm">
            One row per prospect, with a column for their name, one for their phone number, and one for their country. Extra columns are fine — REVANSA only reads the ones it needs.
          </p>
        </Disclosure>
        <div className="row row--wrap">
          <Button variant="secondary" onClick={run} loading={running}>
            Test connection
          </Button>
          <ol className="test-steps" aria-live="polite">
            {steps.map((s, i) => (
              <li key={s} data-state={step > i ? "ok" : running && step === i ? "run" : "idle"}>
                {step > i ? <Check size={16} aria-hidden /> : running && step === i ? <Loader2 size={16} className="btn__spin" aria-hidden /> : <span className="test-steps__dot" aria-hidden />}
                <span>{s}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="callout callout--sm">
          <p className="c-2">
            Share the Sheet with this address so REVANSA can read it. This is a safe, read-only address REVANSA uses — sharing your Sheet with it gives it access to nothing else.
          </p>
          <p className="row">
            <code className="t-mono">agent@revansa-sample.iam.example</code>
            <button type="button" className="icon-btn" aria-label="Copy address" onClick={() => void navigator.clipboard?.writeText("agent@revansa-sample.iam.example")}>
              <Copy size={18} />
            </button>
          </p>
        </div>
      </Group>
    </>
  );
}
