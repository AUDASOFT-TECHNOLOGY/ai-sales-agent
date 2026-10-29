/** Workflow: what the agent does for a prospect, from the list to the daily report, on one animated
 *  picture and a few words about what it brings. An explanation, not a control: nothing here is read
 *  from or written to the server. */

import { Clock3, GraduationCap, Globe, LineChart, Wrench, type LucideIcon } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { PageHead } from "../kit";
import { WorkflowDiagram } from "./WorkflowDiagram";

const BRINGS: { icon: LucideIcon; label: MessageKey }[] = [
  { icon: GraduationCap, label: "rv.wf2.v1" },
  { icon: Wrench, label: "rv.wf2.v2" },
  { icon: Clock3, label: "rv.wf2.v3" },
  { icon: Globe, label: "rv.wf2.v4" },
  { icon: LineChart, label: "rv.wf2.v5" },
];

export function WorkflowPage() {
  const { t } = useI18n();
  return (
    <div className="rv-page rv-wf" data-testid="workflow">
      <PageHead title={t("rv.wf2.title")} />
      <WorkflowDiagram />
      <ul className="rv-wfa__brings" aria-label={t("rv.wf2.brings")}>
        {BRINGS.map((b) => {
          const Icon = b.icon;
          return (
            <li key={b.label}>
              <Icon size={18} aria-hidden />
              <span>{t(b.label)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
