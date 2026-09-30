import { AnimatePresence, motion } from "motion/react";
import { useNavigate, useParams } from "react-router-dom";
import { Building2, CalendarClock, Package, Repeat, X, type LucideIcon } from "lucide-react";
import { useI18n } from "../../i18n";
import { TENANT } from "../../mock/data";
import { useStore } from "../../mock/store";
import { Glass } from "../../ui/Glass";
import { IconButton } from "../../ui/Button";
import { BusinessSection } from "./Business";
import { OffersSection } from "./Catalogue";
import { ScheduleSection } from "./Schedule";
import { FollowUpSection } from "./FollowUp";

type SectionId = "business" | "offers" | "followup" | "schedule";

/** Four business intentions, each a card. Clicking one opens its editor full-width right below
 *  the grid — never a separate screen, never a menu to learn first: the whole setup is visible
 *  at once, and opening one thing never hides the others. */
const CARDS: Array<{ id: SectionId; icon: LucideIcon; title: string; blurb: string }> = [
  { id: "business", icon: Building2, title: "Your business", blurb: "Your company, and where your prospects live." },
  { id: "offers", icon: Package, title: "What REVANSA sells", blurb: "Your catalogue, what convinces a prospect, and how you get paid." },
  { id: "followup", icon: Repeat, title: "How REVANSA follows up", blurb: "What happens when a prospect goes quiet." },
  { id: "schedule", icon: CalendarClock, title: "When REVANSA works", blurb: "Hours, daily limit, and the number it writes from." },
];

export function Configure() {
  const { t } = useI18n();
  const { section } = useParams();
  const navigate = useNavigate();
  const { state } = useStore();
  const current = CARDS.find((c) => c.id === section) ?? null;

  const open = (id: SectionId) => navigate(current?.id === id ? "/configure" : `/configure/${id}`);

  const stat = (id: SectionId): { text: string; bold: string; attention: boolean } => {
    if (id === "business") return { bold: TENANT.name, text: `· Sheet ${state.sheet.verified ? "connected" : "not verified"}`, attention: !state.sheet.verified };
    if (id === "offers") {
      const incomplete = state.offers.some((o) => o.missing.length > 0);
      return { bold: `${state.offers.length} products`, text: `· ${state.markets.join(", ") || "no market yet"}`, attention: incomplete };
    }
    if (id === "followup") return { bold: `${state.followUp.steps.length} reminders`, text: "· then stops", attention: false };
    return { bold: `${state.schedule.days.length} days`, text: `a week · report at ${state.ceoReport.time}`, attention: state.degraded };
  };

  return (
    <div className="page page--focus configure">
      <div className="page-head">
        <div>
          <h2 className="t-h1">{t("configure.title")}</h2>
          <p className="c-2">Changes are saved as you type. Publish makes them official.</p>
        </div>
      </div>

      <div className="cc-grid">
        {CARDS.map((c) => {
          const Icon = c.icon;
          const s = stat(c.id);
          const active = current?.id === c.id;
          return (
            <Glass
              key={c.id}
              as="button"
              type="button"
              level={active ? 3 : 1}
              tint="action"
              className="cc-card"
              data-active={active || undefined}
              aria-pressed={active}
              onClick={() => open(c.id)}
            >
              <div className="cc-card__top">
                <span className="cc-card__icon">
                  <Icon size={20} aria-hidden />
                </span>
                <span className="cc-dot" data-tint={s.attention ? "attention" : "success"} aria-hidden />
              </div>
              <div>
                <h3 className="t-h3">{c.title}</h3>
                <p className="c-2 t-body-sm">{c.blurb}</p>
              </div>
              <div className="cc-card__stat">
                <b>{s.bold}</b> {s.text}
              </div>
            </Glass>
          );
        })}
      </div>

      <AnimatePresence mode="wait">
        {current ? (
          <motion.div key={current.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}>
            <Glass level={2} className="cc-detail" aria-labelledby="cc-h">
              <header className="cc-detail__head">
                <div>
                  <h2 className="t-h2" id="cc-h">
                    {current.title}
                  </h2>
                  <p className="c-2 t-body-sm">{current.blurb}</p>
                </div>
                <IconButton label="Close" onClick={() => navigate("/configure")}>
                  <X size={20} aria-hidden />
                </IconButton>
              </header>
              <div className="cc-detail__body">
                {current.id === "business" ? <BusinessSection /> : null}
                {current.id === "offers" ? <OffersSection /> : null}
                {current.id === "followup" ? <FollowUpSection /> : null}
                {current.id === "schedule" ? <ScheduleSection /> : null}
              </div>
            </Glass>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
