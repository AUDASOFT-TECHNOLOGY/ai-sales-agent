import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead, SubTabs } from "../kit";

/** "Subscription": the plan and the invoices, and the client's own profile. */
export function AccountLayout() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.account.title")} text={t("rv.account.intro")} />
      <SubTabs
        label={t("rv.nav.billing")}
        items={[
          { to: "/account", label: t("rv.account.tab.billing") },
          { to: "/account/profile", label: t("rv.account.tab.profile") },
        ]}
      />
      <Outlet />
    </div>
  );
}
