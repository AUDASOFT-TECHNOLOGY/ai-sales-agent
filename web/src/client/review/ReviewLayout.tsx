import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { PageHead, SubTabs } from "../kit";
import { useReviewCounts } from "../useReviewCount";

/** "To review": everything that waits for the client's decision, in one place: messages to approve,
 *  payments to confirm and conversations a person has to take over. */
export function ReviewLayout() {
  const { t } = useI18n();
  const counts = useReviewCounts();
  return (
    <div className="rv-page">
      <PageHead title={t("rv.review.title")} text={t("rv.review.intro")} />
      <SubTabs
        label={t("rv.nav.review")}
        items={[
          { to: "/review", label: t("rv.review.tab.messages"), badge: counts.messages },
          { to: "/review/payments", label: t("rv.review.tab.payments"), badge: counts.payments },
          { to: "/conversations?human=1", label: t("rv.review.tab.human"), badge: counts.human },
        ]}
      />
      <Outlet />
    </div>
  );
}
