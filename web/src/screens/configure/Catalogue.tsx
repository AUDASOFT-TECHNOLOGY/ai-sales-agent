import { useState } from "react";
import { Plus, Search } from "lucide-react";
import { useI18n } from "../../i18n";
import { useStore } from "../../mock/store";
import { createOffer } from "../../mock/offers";
import type { Offer } from "../../mock/types";
import { OfferCard } from "../../agent/Offers";
import { Segmented } from "../../ui/Controls";
import { OfferEditor } from "./OfferEditor";
import { PaymentSection } from "./Payment";

type StatusFilter = "all" | Offer["status"];

/* ── What REVANSA sells: the catalogue, then the editor of the product you picked, then payment ── */
export function OffersSection() {
  const { t } = useI18n();
  const { state, dispatch, toast } = useStore();
  const [selectedId, setSelectedId] = useState(state.offers[0]?.id ?? "");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const selected = state.offers.find((o) => o.id === selectedId);

  const needle = query.trim().toLowerCase();
  const filtered = state.offers.filter((o) => {
    if (statusFilter !== "all" && o.status !== statusFilter) return false;
    if (!needle) return true;
    return `${o.name} ${o.summary}`.toLowerCase().includes(needle);
  });

  const handleDelete = (offer: Offer) => {
    dispatch({ type: "removeOffer", id: offer.id });
    if (selectedId === offer.id) setSelectedId(state.offers.find((o) => o.id !== offer.id)?.id ?? "");
    toast({ tone: "info", title: `${offer.name} removed`, undo: () => dispatch({ type: "addOffer", offer }) });
  };

  return (
    <>
      <p className="c-2 catalogue-note">
        REVANSA orients each prospect toward the product below that matches what they need — from the conversation, or your Sheet if it hasn't come up yet. When the need isn't clear, it recommends the closest match rather than guessing.
      </p>
      <div className="row row--wrap catalogue-toolbar">
        <label className="search-field well">
          <Search size={18} aria-hidden className="c-3" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search products" aria-label="Search products" />
        </label>
        <Segmented
          layoutId="seg-offer-status"
          label="Filter by status"
          size="sm"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "all", label: "All" },
            { value: "active", label: "Active" },
            { value: "coming_soon", label: "Coming soon" },
            { value: "inactive", label: "Inactive" },
          ]}
        />
      </div>
      <ul className="offer-grid">
        {filtered.map((o) => {
          const i = state.offers.findIndex((x) => x.id === o.id);
          return (
            <OfferCard
              key={o.id}
              offer={o}
              index={i}
              selected={o.id === selectedId}
              onSelect={() => setSelectedId(o.id)}
              onDelete={() => handleDelete(o)}
              onMoveUp={() => dispatch({ type: "reorderOffer", id: o.id, direction: "up" })}
              onMoveDown={() => dispatch({ type: "reorderOffer", id: o.id, direction: "down" })}
              canMoveUp={i > 0}
              canMoveDown={i < state.offers.length - 1}
            />
          );
        })}
        <li>
          <button
            type="button"
            className="offer-add"
            onClick={() => {
              const offer = createOffer({ name: `New product ${state.offers.length + 1}` });
              dispatch({ type: "addOffer", offer });
              setSelectedId(offer.id);
              toast({ tone: "info", title: "Product added" });
            }}
          >
            <Plus size={24} aria-hidden />
            <span className="t-label">{t("configure.offers.add")}</span>
          </button>
        </li>
      </ul>
      {selected ? <OfferEditor key={selected.id} offer={selected} /> : null}
      <hr className="divider" />
      <PaymentSection />
    </>
  );
}
