/** The Free account's invitation to upgrade, on the Client home. Shown only while the server says the
 *  account is Free (never to a client with a plan); `attempted` adds the reason when the server just
 *  refused to start the agent. */

import { useEffect, useState } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { useI18n } from "../i18n";
import { LinkButton } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { clientAccess, type ClientAccess } from "./accessApi";

export function useClientAccess(refreshKey: unknown = 0): ClientAccess | null {
  const [access, setAccess] = useState<ClientAccess | null>(null);
  useEffect(() => {
    let cancelled = false;
    clientAccess().then(
      (value) => !cancelled && setAccess(value),
      () => !cancelled && setAccess(null), // unknown: nothing is claimed either way
    );
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);
  return access;
}

export function UpgradeBanner({ access, attempted = false }: { access: ClientAccess | null; attempted?: boolean }) {
  const { t, n } = useI18n();
  if (!access || access.tier !== "free") return null;
  return (
    <Glass level={3} tint="agent" className="rv-card up-banner" data-testid="upgrade-banner" aria-labelledby="up-title">
      <span className="up-banner__icon" aria-hidden>
        <Sparkles size={22} />
      </span>
      <div className="up-banner__text">
        <h3 id="up-title">{t("lp.upgrade.title")}</h3>
        <p className="c-2">{t("lp.upgrade.text")}</p>
        {attempted ? (
          <p role="alert" className="up-banner__alert">
            {t("lp.upgrade.blocked")}
          </p>
        ) : null}
        {access.credits_remaining != null ? <p className="c-3 t-body-sm">{t("lp.upgrade.credits", { n: n(access.credits_remaining) })}</p> : null}
      </div>
      <LinkButton to="/plans" variant="primary" size="lg" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
        {t("lp.upgrade.cta")}
      </LinkButton>
    </Glass>
  );
}
