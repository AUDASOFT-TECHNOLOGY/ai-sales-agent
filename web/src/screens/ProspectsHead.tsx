/** The header shared by the two views of the same people: their conversations, and the plain list. */

import { NavLink } from "react-router-dom";
import { MessagesSquare, Rows3, Waves } from "lucide-react";
import { useI18n } from "../i18n";
import { PageHead } from "../client/kit";

export function ProspectsHead({ view, total }: { view: "chat" | "river" | "list"; total: number | null }) {
  const { t, n } = useI18n();
  return (
    <PageHead
      title={t("rv.conv.title")}
      text={t("rv.conv.intro")}
      actions={
        <div className="rv-actions">
          {total !== null ? <span className="c-2 t-body-sm tabular">{t("rv.conv.count", { n: n(total) })}</span> : null}
          <nav className="rv-seg" aria-label={t("rv.conv.title")}>
            <NavLink to="/conversations" className="rv-seg__btn rv-seg__link" aria-pressed={view === "chat"}>
              <MessagesSquare size={15} aria-hidden /> {t("rv.conv.view.chat")}
            </NavLink>
            <NavLink to="/crm" className="rv-seg__btn rv-seg__link" aria-pressed={view === "river"}>
              <Waves size={15} aria-hidden /> {t("rv.conv.view.river")}
            </NavLink>
            <NavLink to="/prospects" className="rv-seg__btn rv-seg__link" aria-pressed={view === "list"}>
              <Rows3 size={15} aria-hidden /> {t("rv.conv.view.list")}
            </NavLink>
          </nav>
        </div>
      }
    />
  );
}
