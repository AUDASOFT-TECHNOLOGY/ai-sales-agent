import { useState } from "react";
import { Building2, Check, Copy, Loader2, Table2 } from "lucide-react";
import { useI18n } from "../../i18n";
import { TENANT } from "../../mock/data";
import { useStore } from "../../mock/store";
import { Button } from "../../ui/Button";
import { Disclosure } from "../../ui/Disclosure";
import { LockedField, TextInput } from "../../ui/Controls";
import { Group } from "./Group";

/* ── Your business: identity and the Google Sheet (real, wired to state.sheet) ── */
export function BusinessSection() {
  const { t } = useI18n();
  const { state, dispatch } = useStore();
  const [step, setStep] = useState(state.sheet.verified ? 3 : 0);
  const [running, setRunning] = useState(false);
  const steps = [t("runway.sheetTest.open"), t("runway.sheetTest.columns", { brand: t("brand.name") }), t("runway.sheetTest.linesUp")];
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
      <Group title={t("business.yourCompany")} icon={Building2}>
        <div className="grid-2">
          <LockedField label={t("field.businessName")} value={TENANT.name} helper={t("business.setByAdmin")} />
          <LockedField label={t("field.timezone")} value={TENANT.timezone} helper={t("business.allTimesFollowZone")} />
        </div>
      </Group>
      <hr className="divider" />
      <Group title={t("business.googleSheet")} hint={t("business.sheetHint", { brand: t("brand.name") })} icon={Table2}>
        <TextInput
          label={t("field.sheetLink")}
          value={state.sheet.url}
          onChange={(e) => dispatch({ type: "setSheetUrl", url: e.target.value })}
          helper={state.sheet.verified ? undefined : t("business.testAfterChange")}
        />
        <Disclosure title={t("business.sheetLookLike.title")} hint={t("business.sheetLookLike.hint")}>
          <p className="c-2 t-body-sm">
            {t("business.sheetLookLike.body", { brand: t("brand.name") })}
          </p>
        </Disclosure>
        <div className="row row--wrap">
          <Button variant="secondary" onClick={run} loading={running}>
            {t("action.testConnection")}
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
            {t("business.shareAddress", { brand: t("brand.name") })}
          </p>
          <p className="row">
            <code className="t-mono">agent@revansa-sample.iam.example</code>
            <button type="button" className="icon-btn" aria-label={t("business.copyAddress")} onClick={() => void navigator.clipboard?.writeText("agent@revansa-sample.iam.example")}>
              <Copy size={18} />
            </button>
          </p>
        </div>
      </Group>
    </>
  );
}
