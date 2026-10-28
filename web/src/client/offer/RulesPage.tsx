/** "Business and rules": who the client is, how its agent speaks and when it works (one form with its
 *  own save bar), then how it follows up with people who stay silent (its own card, saved on its own). */

import { useI18n } from "../../i18n";
import { FollowUpSettingsCard } from "../FollowUpSettingsCard";
import { Card } from "../kit";
import { BusinessPage } from "./BusinessPage";

export function RulesPage() {
  const { t } = useI18n();
  return (
    <div className="rv-page">
      <BusinessPage />
      <Card aria-label={t("followup.title")}>
        <div className="rv-card__head">
          <div>
            <h3>{t("followup.title")}</h3>
            <p className="rv-card__sub">{t("followup.hint")}</p>
          </div>
        </div>
        <FollowUpSettingsCard />
      </Card>
    </div>
  );
}
