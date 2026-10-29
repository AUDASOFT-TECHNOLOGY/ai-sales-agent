/** `/notifications`: the client's own notifications and their daily summary, in the Client frame. */

import { useI18n } from "../../i18n";
import { PageHead } from "../kit";
import { ClientReportingPage } from "../ClientReportingPage";
import { ClientNotificationsPanel } from "./ClientNotificationsPanel";

export function ClientNotificationsPage() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.nav.notifications")} />
      <ClientNotificationsPanel />
      <ClientReportingPage />
    </div>
  );
}
