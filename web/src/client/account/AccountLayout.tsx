import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead } from "../kit";

/** "Subscription": the plan and the invoices. The profile lives under the client's name. */
export function AccountLayout() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.account.title")} text={t("rv.account.intro")} />
      <Outlet />
    </div>
  );
}
