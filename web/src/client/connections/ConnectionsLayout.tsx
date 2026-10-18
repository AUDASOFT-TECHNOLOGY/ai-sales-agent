import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead, SubTabs } from "../kit";

/** "Connections": where the prospects are, and how the agent reaches them. */
export function ConnectionsLayout() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.conn.title")} text={t("rv.conn.intro")} />
      <SubTabs
        label={t("rv.nav.connections")}
        items={[
          { to: "/connections/sources", label: t("rv.conn.tab.sources") },
          { to: "/connections/channels", label: t("rv.conn.tab.channels") },
        ]}
      />
      <Outlet />
    </div>
  );
}
