import { FlaskConical } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "../ui/Button";
import { useI18n } from "../i18n";
import { useStore } from "../mock/store";
import type { AgentStateKey } from "../mock/types";
import { Segmented, Switch } from "../ui/Controls";
import { PopoverCard } from "../ui/Overlays";

const STATES: Array<AgentStateKey | "auto"> = ["auto", "idle", "working", "writing", "waiting", "observing", "off_hours", "paused", "degraded", "not_live"];

/** Prototype-only controls to judge the states visually. Not a product feature; lives in src/prototype/. */
export function PrototypeControls() {
  const { t } = useI18n();
  const { state, dispatch } = useStore();
  const navigate = useNavigate();
  return (
    <div className="proto">
      <PopoverCard
        side="bottom"
        label={t("proto.controls")}
        trigger={
          <button type="button" className="proto__btn glass" data-level="2" aria-label={t("proto.controls")}>
            <FlaskConical size={18} aria-hidden />
            <span className="proto__label">{t("proto.label")}</span>
          </button>
        }
      >
        <div className="stack">
          <p className="t-label">{t("proto.controls")}</p>
          <p className="c-3 t-body-sm">{t("notice.sample.long")}</p>
          <label className="t-label c-2" htmlFor="proto-state">
            {t("proto.agentState")}
          </label>
          <select id="proto-state" className="input well proto__select" value={state.demoAgentState} onChange={(e) => dispatch({ type: "demoState", value: e.target.value as AgentStateKey | "auto" })}>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {s === "auto" ? t("proto.automatic") : s}
              </option>
            ))}
          </select>
          <p className="t-label c-2">{t("common.role")}</p>
          <Segmented
            layoutId="seg-role"
            label={t("common.role")}
            size="sm"
            value={state.role}
            onChange={(v) => dispatch({ type: "role", value: v })}
            options={[
              { value: "owner", label: t("common.owner") },
              { value: "viewer", label: t("common.viewer") },
            ]}
          />
          <div className="row row--wrap">
            <Button size="sm" variant="secondary" onClick={() => { dispatch({ type: "skipToLive" }); navigate("/dashboard"); }}>
              {t("proto.jumpToLiveDemo")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { dispatch({ type: "restart" }); navigate("/setup"); }}>
              {t("proto.restartOnboarding")}
            </Button>
          </div>
          <div className="row">
            <Switch label={t("proto.simulateFailingConnection")} checked={state.degraded} onChange={(v) => dispatch({ type: "degraded", value: v })} />
            <span className="t-body-sm">{t("proto.simulateFailingConnection")}</span>
          </div>
        </div>
      </PopoverCard>
    </div>
  );
}
