import { useState } from "react";
import { X } from "lucide-react";
import { useStore } from "../../mock/store";
import { Button } from "../../ui/Button";
import { Drawer } from "../../ui/Overlays";

/** Manage the list of countries this client sells into. Shared by the offer editor's market
 *  picker and the per-market payment editor, so it lives behind one drawer rather than being
 *  hardcoded in either place. */
export function MarketsDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { state, dispatch, toast } = useStore();
  const [draft, setDraft] = useState("");

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Target countries">
      <p className="c-2 t-body-sm">Where REVANSA sells your products, and where payment details are set separately for each.</p>
      <ul className="market-list">
        {state.markets.map((m) => {
          const count = state.offers.filter((o) => o.markets.includes(m)).length;
          return (
            <li key={m} className="market-row">
              <div>
                <p className="t-label">{m}</p>
                <p className="c-3 t-body-sm">{count === 0 ? "Used by no product yet" : `Used by ${count} product${count === 1 ? "" : "s"}`}</p>
              </div>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Remove ${m}`}
                onClick={() => {
                  dispatch({ type: "removeMarket", name: m });
                  toast({ tone: "info", title: `${m} removed`, body: "It was also removed from any products and its payment details." });
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
        <input className="input well" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add a country" aria-label="Add a country" />
        <Button type="submit" variant="secondary" disabledReason={draft.trim() ? undefined : "Type a country name first"}>
          Add
        </Button>
      </form>
    </Drawer>
  );
}
