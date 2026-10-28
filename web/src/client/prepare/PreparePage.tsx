/** "Prepare your agent": the business, the products, the connections, the circuits and the agent's own
 *  settings on one screen. A journey of five phases drawn as a rail; the phase picked shows the real
 *  screen underneath (nothing is re-implemented here, the pages are the same ones the separate menu
 *  entries open). What is done comes from the server through `guideSteps`, so the rail never claims a
 *  step the platform does not see as finished. "Test my agent" opens once the essentials are done. */

import { useEffect, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bot, Building2, Check, ChevronDown, FlaskConical, Package, Plug, Route } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Glass } from "../../ui/Glass";
import { CircuitsPage } from "../circuits/CircuitsPage";
import { ConnectionsPage } from "../connections/ConnectionsPage";
import { Loading } from "../kit";
import { OfferProvider } from "../offer/OfferLayout";
import { PaymentsPage } from "../offer/PaymentsPage";
import { ProductEditor } from "../offer/ProductEditor";
import { ProductsPage } from "../offer/ProductsPage";
import { guideSteps, useOverview, type GuideStep } from "../overview";
import { AgentQuestions } from "./AgentQuestions";
import { BusinessQuestions } from "./BusinessQuestions";
import { PrepareScope } from "./links";
import { useTerms } from "./terms";

type PhaseId = "business" | "products" | "connect" | "circuits" | "agent";
/** The steps that must be done before the agent can be tested ("start" is the launch, not a preparation). */
type PrepStep = Exclude<GuideStep["id"], "start">;

const PHASES: Array<{ id: PhaseId; icon: typeof Package; steps: PrepStep[] }> = [
  { id: "business", icon: Building2, steps: ["business"] },
  { id: "products", icon: Package, steps: ["products", "payments"] },
  { id: "connect", icon: Plug, steps: ["prospects", "channel"] },
  { id: "circuits", icon: Route, steps: ["circuit"] },
  { id: "agent", icon: Bot, steps: [] },
];

/** Which phase a step belongs to. */
const PHASE_OF: Record<PrepStep, PhaseId> = { business: "business", products: "products", payments: "products", prospects: "connect", channel: "connect", circuit: "circuits" };

const isPhase = (v: string | null): v is PhaseId => PHASES.some((p) => p.id === v);

type PhaseState = "done" | "waiting" | "todo";

function phaseState(steps: GuideStep[]): PhaseState {
  if (steps.length === 0) return "todo";
  if (steps.every((s) => s.state === "done")) return "done";
  if (steps.some((s) => s.state === "waiting") && steps.every((s) => s.state !== "todo")) return "waiting";
  return "todo";
}

function Ring({ done, total }: { done: number; total: number }) {
  const radius = 42;
  const length = 2 * Math.PI * radius;
  const share = total === 0 ? 0 : done / total;
  return (
    <div className="rv-prep__ring" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
      <svg viewBox="0 0 100 100" aria-hidden>
        <circle className="rv-prep__ring-track" cx="50" cy="50" r={radius} />
        <circle className="rv-prep__ring-fill" cx="50" cy="50" r={radius} strokeDasharray={length} strokeDashoffset={length * (1 - share)} />
      </svg>
      <span className="rv-prep__ring-n tabular">
        <strong>{done}</strong>
        <small>/{total}</small>
      </span>
    </div>
  );
}

function More({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="rv-prep__more">
      <summary>
        <span>{title}</span>
        <ChevronDown size={16} aria-hidden />
      </summary>
      <div className="rv-prep__more-body">{children}</div>
    </details>
  );
}

export function PreparePage() {
  const { t } = useI18n();
  const tt = useTerms();
  const navigate = useNavigate();
  const { data, reload } = useOverview();
  const [params, setParams] = useSearchParams();

  const steps = (data ? guideSteps(data) : []).filter((s): s is GuideStep & { id: PrepStep } => s.id !== "start");
  const firstOpen = steps.find((s) => s.state !== "done") ?? null;

  const asked = params.get("p");
  const phase: PhaseId = isPhase(asked) ? asked : firstOpen ? PHASE_OF[firstOpen.id] : "agent";
  const product = phase === "products" ? params.get("product") : null;

  // What the client does in the embedded screens changes what is done: read the guide again when the
  // phase changes, so the rail and the ring are never one step behind.
  useEffect(() => {
    void reload();
  }, [phase, reload]);

  if (!data) return <Loading />;

  const done = steps.filter((s) => s.state === "done").length;
  const go = (next: PhaseId) => setParams({ p: next }, { replace: true });
  // The business and its activity, a product, a payment method, a source, a channel and a circuit.
  const ready = steps.length > 0 && done === steps.length;

  const phases = PHASES.map((p) => {
    const own = steps.filter((s) => p.steps.includes(s.id));
    return { ...p, state: phaseState(own), done: own.filter((s) => s.state === "done").length, total: own.length };
  });

  let body: ReactNode;
  if (phase === "business") {
    body = (
      <OfferProvider>
        <BusinessQuestions />
      </OfferProvider>
    );
  } else if (phase === "products") {
    body = (
      <OfferProvider>
        <div className="rv-page">
          {product ? <ProductEditor key={product} productId={product} /> : <ProductsPage />}
          {/* The payment methods stay under the products, on the list and inside a product alike. */}
          <More title={t("rv.prep.pay.methods")}>
            <PaymentsPage />
          </More>

        </div>
      </OfferProvider>
    );
  } else if (phase === "connect") {
    body = <ConnectionsPage embedded />;
  } else if (phase === "circuits") {
    body = <CircuitsPage embedded />;
  } else {
    body = (
      <OfferProvider>
        <div className="rv-page">
          <AgentQuestions />
        </div>
      </OfferProvider>
    );
  }

  return (
    <PrepareScope>
      <div className="rv-page rv-prep">
        <Glass level={2} tint="agent" className="rv-prep__hero">
          <Ring done={done} total={steps.length} />
          <div className="rv-prep__hero-text">
            <h2>{t("rv.prep.title")}</h2>
            <p>{ready ? t("rv.prep.allDone") : firstOpen ? t("rv.prep.next", { step: tt(`rv.prep.nextTitle.${firstOpen.id}` as MessageKey) }) : null}</p>
          </div>
          <Button variant="primary" leading={<FlaskConical size={16} aria-hidden />} disabled={!ready} disabledReason={ready ? undefined : t("rv.prep.test.blocked")} onClick={() => navigate("/simulation")}>
            {t("rv.prep.test")}
          </Button>
        </Glass>

        <ol className="rv-prep__rail" aria-label={t("rv.prep.title")}>
          {phases.map((p, i) => {
            const active = p.id === phase;
            return (
              <li key={p.id} className="rv-prep__phase" data-state={p.state} data-active={active || undefined}>
                <button type="button" onClick={() => go(p.id)} aria-current={active ? "step" : undefined}>
                  <span className="rv-prep__badge" aria-hidden>
                    {p.state === "done" ? <Check size={20} strokeWidth={3} /> : i + 1}
                  </span>
                  <span className="rv-prep__phase-t">
                    <strong>{tt(`rv.prep.phase.${p.id}` as MessageKey)}</strong>
                    {p.total === 0 ? <em>{t("rv.prep.optional")}</em> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>

        <section className="rv-prep__panel" aria-label={t(`rv.prep.phase.${phase}` as MessageKey)}>
          <p className="rv-prep__lead">{t(`rv.prep.lead.${phase}` as MessageKey)}</p>
          {body}
        </section>
      </div>
    </PrepareScope>
  );
}
