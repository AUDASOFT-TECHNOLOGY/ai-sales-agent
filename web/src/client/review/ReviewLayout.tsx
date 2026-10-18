import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead, SubTabs } from "../kit";
import { useReviewCount } from "../useReviewCount";

/** "To review": what waits for the client's decision: messages to approve and payments to check. */
export function ReviewLayout() {
  const { t } = useI18n();
  const messages = useReviewCount();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.review.title")} text={t("rv.review.intro")} />
      <SubTabs
        label={t("rv.nav.review")}
        items={[
          { to: "/review", label: t("rv.review.tab.messages"), badge: messages },
          { to: "/review/payments", label: t("rv.review.tab.payments") },
        ]}
      />
      <Outlet />
    </div>
  );
}
