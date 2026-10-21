/** Home shortcuts: the dashboard, the invoices that need attention and the open notifications.
 *  Each figure comes from the server; one that cannot be read says so instead of showing 0. */

import { appFetch } from "../demo/transport";

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useI18n } from "../i18n";
import { clientSubscription, type Subscription } from "./billing/billingApi";
import { Card } from "./kit";

type Read<T> = { state: "loading" } | { state: "failed" } | { state: "ok"; value: T };

export function HomeLinks() {
  const { t } = useI18n();
  const [sub, setSub] = useState<Read<Subscription>>({ state: "loading" });
  const [unread, setUnread] = useState<Read<number>>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    clientSubscription().then(
      (value) => !cancelled && setSub({ state: "ok", value }),
      () => !cancelled && setSub({ state: "failed" }),
    );
    appFetch("/client/v1/notifications", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(
        (page: { unread_count?: number }) => !cancelled && setUnread({ state: "ok", value: Number(page.unread_count ?? 0) }),
        () => !cancelled && setUnread({ state: "failed" }),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  let invoices = "";
  if (sub.state === "failed") invoices = t("rv.home.links.invoicesUnavailable");
  else if (sub.state === "ok")
    invoices =
      sub.value.overdue_invoices > 0
        ? t("rv.home.links.invoicesOverdue", { n: sub.value.overdue_invoices })
        : sub.value.unpaid_invoices > 0
          ? t("rv.home.links.invoicesUnpaid", { n: sub.value.unpaid_invoices })
          : t("rv.home.links.invoicesOk");
  const late = sub.state === "ok" && sub.value.overdue_invoices > 0;

  let notes = "";
  if (unread.state === "failed") notes = t("rv.home.links.notificationsUnavailable");
  else if (unread.state === "ok")
    notes = unread.value > 0 ? t("rv.home.links.notificationsOpen", { n: unread.value }) : t("rv.home.links.notificationsNone");

  const open = (to: string) => (
    <Link to={to} className="link-btn">
      {t("rv.home.links.open")} <ArrowRight size={14} className="mirror" aria-hidden />
    </Link>
  );

  return (
    <div className="rv-grid-3" data-testid="home-links">
      <Card className="rv-stat">
        <span className="rv-stat__label">{t("rv.home.links.dashboard")}</span>
        <span className="c-3 t-body-sm">{t("rv.home.links.dashboardText")}</span>
        {open("/dashboard")}
      </Card>
      <Card className="rv-stat" data-testid="home-invoices">
        <span className="rv-stat__label">{t("rv.home.links.invoices")}</span>
        <span className={late ? "t-body-sm c-danger" : "c-3 t-body-sm"} role={late ? "alert" : "status"}>
          {invoices}
        </span>
        {open("/billing")}
      </Card>
      <Card className="rv-stat" data-testid="home-notifications">
        <span className="rv-stat__label">{t("rv.home.links.notifications")}</span>
        <span className="c-3 t-body-sm" role="status">
          {notes}
        </span>
        {open("/notifications")}
      </Card>
    </div>
  );
}
