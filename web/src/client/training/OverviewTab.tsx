/** "Overview": how ready the agent is to sell, what each part is for, and what to do next. */

import { CheckCircle2, FlaskConical } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Card, Note } from "../kit";
import type { DimensionId, Readiness } from "./readiness";

export type TabId = "overview" | "method" | "answers" | "fix";

/** The tab where each part of the training is done. */
export const TAB_OF: Record<DimensionId, TabId> = { method: "method", pitch: "method", voice: "method", redlines: "method", objections: "answers", faq: "answers", examples: "answers" };

export function OverviewTab({ readiness, onGo, dirty }: { readiness: Readiness; onGo: (tab: TabId) => void; dirty: boolean }) {
  const { t } = useI18n();
  const label = (id: DimensionId) => t(`rv.train.dim.${id}` as MessageKey);
  return (
    <>
      <Card data-testid="training-readiness">
        <div className="rv-meter">
          <div className="rv-meter__row">
            <h3>{t("rv.train.ov.title")}</h3>
            <strong data-testid="readiness-score">{t("rv.train.ov.score", { n: readiness.score })}</strong>
          </div>
          <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={readiness.score} aria-label={t("rv.train.ov.title")}>
            <div className="rv-progress__fill" style={{ width: `${readiness.score}%` }} />
          </div>
          <p className="c-2">{t(`rv.train.ov.level.${readiness.level}` as MessageKey)}</p>
        </div>
        <p className="c-3 t-body-sm">{t("rv.train.ov.note")}</p>
        {dirty ? <Note tone="warn">{t("rv.train.ov.unsaved")}</Note> : null}
      </Card>

      <Card data-testid="training-next">
        <h3>{t("rv.train.ov.next")}</h3>
        {readiness.next.length === 0 ? (
          <Note tone="success">{t("rv.train.ov.allset")}</Note>
        ) : (
          <ul className="stack">
            {readiness.next.map((id) => (
              <li key={id} className="row">
                <div className="rv-row__main">
                  <strong>{label(id)}</strong>
                  <span className="c-2">{t(`rv.train.dim.${id}.text` as MessageKey)}</span>
                </div>
                <Button size="sm" variant="primary" onClick={() => onGo(TAB_OF[id])}>
                  {t("rv.train.ov.go")}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div>
          <LinkButton to="/simulation" variant="secondary" leading={<FlaskConical size={16} aria-hidden />}>
            {t("rv.train.ov.test")}
          </LinkButton>
        </div>
      </Card>

      <Card>
        <ul className="stack">
          {readiness.dimensions.map((d) => (
            <li key={d.id} className="rv-meter" data-testid={`dim-${d.id}`}>
              <div className="rv-meter__row">
                <span className="row">
                  {d.ratio >= 1 ? <CheckCircle2 size={16} aria-hidden /> : null}
                  <strong>{label(d.id)}</strong>
                </span>
                <span className="c-3">{t("rv.train.ov.have", { have: d.have, goal: d.goal })}</span>
              </div>
              <div className="rv-progress" aria-hidden>
                <div className="rv-progress__fill" style={{ width: `${Math.round(d.ratio * 100)}%` }} />
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
