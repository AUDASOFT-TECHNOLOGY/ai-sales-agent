import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Check, Loader2, Rocket } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { GATES, useStore, type GateId } from "../mock/store";
import { createOffer } from "../mock/offers";
import { AutonomyDial } from "../agent/Autonomy";
import { Pulse } from "../agent/Pulse";
import { RunwayTrack } from "../agent/Runway";
import { Button } from "../ui/Button";
import { Switch, TextInput, VerbatimField } from "../ui/Controls";
import { LanguageStyle, SalesPath } from "../agent/Strategy";
import { PaymentProfileEditor } from "./configure/Payment";
import { Glass } from "../ui/Glass";
import { Wordmark } from "../ui/Brand";

const GATE_INTRO_KEY: Record<GateId, MessageKey> = {
  sheet: "runway.intro.sheet",
  offer: "runway.intro.offer",
  payment: "runway.intro.payment",
  voice: "runway.intro.voice",
  path: "runway.intro.path",
  live: "runway.intro.live",
};

export function Runway() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { state, dispatch } = useStore();
  const firstOpen = GATES.find((g) => !state.gates[g]) ?? "live";
  const [picked, setPicked] = useState<GateId | null>(null);
  const current = picked ?? firstOpen;
  const doneCount = GATES.filter((g) => state.gates[g]).length;

  const finish = (g: GateId) => {
    dispatch({ type: "gate", id: g });
    setPicked(null);
  };

  return (
    <div className="page page--narrow runway-page">
      <header className="runway-hero">
        <Wordmark tagline />
        <Pulse state={state.live ? "idle" : "not_live"} size={96} />
        <h2 className="t-h1">{t("runway.welcome")}</h2>
      </header>

      <RunwayTrack done={state.gates} current={current} onSelect={setPicked} />

      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={current} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }} className="runway-card-wrap">
          <Glass level={3} tint={state.gates[current] ? "success" : "action"} className="gate-card">
            <div className="gate-card__head">
              <h3 className="t-h2">{t(`runway.gate.${current}` as MessageKey)}</h3>
              {state.gates[current] ? (
                <span className="chip" data-tint="success">
                  <Check size={14} aria-hidden /> {t("runway.chipDone")}
                </span>
              ) : null}
            </div>
            <p className="voice voice-m voice-block">{t(GATE_INTRO_KEY[current])}</p>
            <GateForm gate={current} onDone={() => finish(current)} />
          </Glass>
        </motion.div>
      </AnimatePresence>

      {doneCount === GATES.length && state.live ? (
        <div className="runway-done">
          <Button variant="primary" size="lg" onClick={() => navigate("/dashboard")} trailing={<ArrowRight size={18} className="mirror" aria-hidden />}>
            {t("nav.command")}
          </Button>
        </div>
      ) : null}
      <div className="runway-note">
        <p className="c-3">{t("notice.sample.long")}</p>
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            dispatch({ type: "skipToLive" });
            navigate("/dashboard");
          }}
        >
          {t("runway.skipDemo")}
        </button>
      </div>
      <span className="sr-only" aria-live="polite">
        {doneCount === GATES.length ? t("runway.done") : ""}
      </span>
    </div>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="gate-form">{children}</div>;
}

function GateForm({ gate, onDone }: { gate: GateId; onDone: () => void }) {
  const { t } = useI18n();
  const { state, dispatch, toast, mode } = useStore();
  const [tested, setTested] = useState(0);
  const [running, setRunning] = useState(false);
  const [name, setName] = useState("Foundation");
  const [summary, setSummary] = useState("The first step for someone starting from zero.");
  const [price, setPrice] = useState("1 500 TND");
  const [requested, setRequested] = useState<"observe" | "ask" | "auto">("ask");
  const done = state.gates[gate];

  const test = () => {
    setRunning(true);
    setTested(0);
    [1, 2, 3].forEach((n, i) =>
      window.setTimeout(() => {
        setTested(n);
        if (n === 3) {
          setRunning(false);
          dispatch({ type: "verifySheet" });
        }
      }, 600 * (i + 1)),
    );
  };

  if (gate === "sheet")
    return (
      <Row>
        <TextInput label={t("field.sheetLink")} value={state.sheet.url} onChange={(e) => dispatch({ type: "setSheetUrl", url: e.target.value })} />
        <div className="row row--wrap">
          <Button variant="secondary" onClick={test} loading={running}>
            {t("action.testConnection")}
          </Button>
          <ol className="test-steps" aria-live="polite">
            {([
              t("runway.sheetTest.open"),
              t("runway.sheetTest.columns", { brand: t("brand.name") }),
              t("runway.sheetTest.linesUp"),
            ]).map((s, i) => (
              <li key={s} data-state={tested > i ? "ok" : running && tested === i ? "run" : "idle"}>
                {tested > i ? <Check size={15} aria-hidden /> : running && tested === i ? <Loader2 size={15} className="btn__spin" aria-hidden /> : <span className="test-steps__dot" aria-hidden />}
                <span className="t-body-sm">{s}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="gate-form__foot">
          <Button variant="primary" disabled={tested < 3 && !done} onClick={onDone}>
            {t("action.continue")}
          </Button>
        </div>
      </Row>
    );

  if (gate === "offer")
    return (
      <Row>
        <TextInput label={t("field.offerName")} value={name} onChange={(e) => setName(e.target.value)} />
        <TextInput label={t("field.offerSummary")} value={summary} onChange={(e) => setSummary(e.target.value)} />
        <VerbatimField label={t("field.price")} value={price} onChange={setPrice} previewPrefix={t("runway.offerCosts", { name: name || t("runway.thisOffer") })} previewSuffix="." />
        <div className="gate-form__foot">
          <Button
            variant="primary"
            disabled={!name.trim()}
            onClick={() => {
              const offer = createOffer({ name, summary, price, status: "active", markets: state.markets.slice(0, 1) });
              dispatch({ type: "addOffer", offer });
              toast({ tone: "success", title: t("runway.toast.offerAdded", { name }), body: t("runway.toast.draftUntilPublish") });
              onDone();
            }}
          >
            {t("action.continue")}
          </Button>
        </div>
      </Row>
    );

  if (gate === "payment") {
    const market = state.markets[0] ?? "Tunisia";
    const profile = state.paymentProfiles.find((p) => p.market === market) ?? { market, methods: [], instructions: "", link: "" };
    return (
      <Row>
        <PaymentProfileEditor market={market} profile={profile} onChange={(patch) => dispatch({ type: "setPaymentProfile", market, patch })} />
        <div className="gate-form__foot">
          <Button variant="primary" onClick={onDone}>
            {t("action.continue")}
          </Button>
        </div>
      </Row>
    );
  }

  if (gate === "voice")
    return (
      <Row>
        <LanguageStyle
          langs={state.languages}
          onLangs={(v) => dispatch({ type: "setLanguages", value: v })}
          defaultLang={state.defaultLanguage}
          onDefaultLang={(v) => dispatch({ type: "setDefaultLanguage", value: v })}
          tone={state.tone}
          onTone={(v) => dispatch({ type: "setTone", value: v })}
          idSuffix="-runway"
        />
        <div className="gate-form__foot">
          <Button variant="primary" onClick={onDone} disabled={state.languages.length === 0}>
            {t("action.continue")}
          </Button>
        </div>
      </Row>
    );

  if (gate === "path")
    return (
      <Row>
        <SalesPath />
        <div className="row switch-row">
          <Switch label={t("runway.handoffLabel")} checked={state.handoffEnabled} onChange={(v) => dispatch({ type: "setHandoff", value: v })} />
          <span>{t("runway.handoffLabel")}</span>
        </div>
        <div className="gate-form__foot">
          <Button variant="primary" onClick={onDone}>
            {t("action.continue")}
          </Button>
        </div>
      </Row>
    );

  return (
    <Row>
      <AutonomyDial requested={requested} effective={requested === "auto" ? "ask" : requested} ceiling="ask" onChange={setRequested} />
      {!state.activationRequested ? (
        <div className="gate-form__foot">
          <Button
            variant="primary"
            size="lg"
            leading={<Rocket size={18} aria-hidden />}
            onClick={() => {
              dispatch({ type: "requestActivation" });
              toast({ tone: "info", title: t("runway.toast.activationRequested.title"), body: t("runway.toast.activationRequested.body") });
            }}
          >
            {t("runway.requestActivation")}
          </Button>
        </div>
      ) : !state.live ? (
        <div className="gate-form__foot">
          <p className="c-attention t-body-sm">{t("runway.waitingActivation")}</p>
          <Button
            variant="agent"
            onClick={() => {
              dispatch({ type: "goLive" });
              toast({ tone: "success", title: t("runway.toast.live", { brand: t("brand.name") }), body: t("runway.toast.runningInMode", { mode: t(`mode.${mode}` as MessageKey) }) });
            }}
          >
            {t("runway.simulateApproval")}
          </Button>
        </div>
      ) : (
        <p className="c-success t-body-sm">
          {t("runway.done")}. {t("runway.agentLive")}
        </p>
      )}
    </Row>
  );
}
