import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead } from "../kit";

/** "Results": what the agent obtained (funnel, activity, by product, source and channel). */
export function ResultsLayout() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.results.title")} text={t("rv.results.intro")} />
      <Outlet />
    </div>
  );
}
