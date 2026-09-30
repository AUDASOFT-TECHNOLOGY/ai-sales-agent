import { useState } from "react";
import { X } from "lucide-react";
import { useI18n } from "../../i18n";
import { useStore } from "../../mock/store";
import { Button } from "../../ui/Button";
import { Drawer } from "../../ui/Overlays";

/** Manage the list of countries this client sells into. Shared by the offer editor's market
 *  picker and the per-market payment editor, so it lives behind one drawer rather than being
 *  hardcoded in either place. */
export function MarketsDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const { state, dispatch, toast } = useStore();
  const [draft, setDraft] = useState("");

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title={t("markets.title")}>
      <p className="c-2 t-body-sm">{t("markets.subtitle", { brand: t("brand.name") })}</p>
      <ul className="market-list">
        {state.markets.map((m) => {
          const count = state.offers.filter((o) => o.markets.includes(m)).length;
          return (
            <li key={m} className="market-row">
              <div>
                <p className="t-label">{m}</p>
                <p className="c-3 t-body-sm">{count === 0 ? t("markets.usedByNone") : count === 1 ? t("markets.usedByOne") : t("markets.usedByMany", { n: count })}</p>
              </div>
              <button
                type="button"
                className="icon-btn"
                aria-label={t("markets.removeMarket", { name: m })}
                onClick={() => {
                  dispatch({ type: "removeMarket", name: m });
                  toast({ tone: "info", title: t("markets.toast.removed.title", { name: m }), body: t("markets.toast.removed.body") });
                }}
              >
                <X size={16} />
              </button>
            </li>
          );
        })}
      </ul>
      <form
        className="market-add row row--wrap"
        onSubmit={(e) => {
          e.preventDefault();
          const name = draft.trim();
          if (name) dispatch({ type: "addMarket", name });
          setDraft("");
        }}
      >
        <input className="input well" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t("markets.addPlaceholder")} aria-label={t("markets.addPlaceholder")} />
        <Button type="submit" variant="secondary" disabledReason={draft.trim() ? undefined : t("markets.typeNameFirst")}>
          {t("action.add")}
        </Button>
      </form>
    </Drawer>
  );
}
