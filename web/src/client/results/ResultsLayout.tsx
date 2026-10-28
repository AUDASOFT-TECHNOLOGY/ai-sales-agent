import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead, SubTabs } from "../kit";

/** "Results": what the agent obtained (by product, source, channel, period) and the daily summary. */
export function ResultsLayout() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.results.title")} text={t("rv.results.intro")} />
      <SubTabs
        label={t("rv.nav.results")}
        items={[
          { to: "/results", label: t("rv.results.tab.figures") },
          { to: "/results/daily", label: t("rv.results.tab.daily") },
        ]}
      />
      <Outlet />
    </div>
  );
}
