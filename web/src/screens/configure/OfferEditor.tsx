import { useState } from "react";
import { Package } from "lucide-react";
import type { Offer } from "../../mock/types";
import { useStore } from "../../mock/store";
import { Segmented, TextInput, Textarea, ToggleChips, VerbatimField } from "../../ui/Controls";
import { Group } from "./Group";
import { MarketsDrawer } from "./Markets";
import { OfferAssetEditor } from "./OfferAssets";

export function OfferEditor({ offer }: { offer: Offer }) {
  const { dispatch, state } = useStore();
  const viewer = state.role === "viewer";
  const [pricing, setPricing] = useState<"fixed" | "request" | "free">(offer.price === "On request" ? "request" : offer.price === "Free" ? "free" : "fixed");
  const [marketsOpen, setMarketsOpen] = useState(false);
  const update = (patch: Partial<Offer>) => dispatch({ type: "updateOffer", id: offer.id, patch });
  return (
    <>
      <hr className="divider" />
      <Group title={offer.name} hint={viewer ? "Viewers can look but not edit. Ask an owner to make changes." : "Editing here updates the card above immediately, and stays after you reload."} icon={Package}>
        <fieldset className="cfg-fields" disabled={viewer}>
          <legend className="sr-only">{offer.name}</legend>
          <TextInput label="Name" value={offer.name} onChange={(e) => update({ name: e.target.value })} />
          <Textarea label="One sentence that says who it is for" value={offer.summary} onChange={(e) => update({ summary: e.target.value })} rows={2} />
          <div className="field">
            <span className="t-label c-2">How do you price this offer?</span>
            <Segmented
              layoutId={`seg-pricing-${offer.id}`}
              label="How do you price this offer?"
              value={pricing}
              onChange={(v) => {
                setPricing(v);
                if (v === "request") update({ price: "On request" });
                else if (v === "free") update({ price: "Free" });
                else if (offer.price === "On request" || offer.price === "Free" || offer.price === "Not set") update({ price: "" });
              }}
              options={[
                { value: "fixed", label: "Fixed price" },
                { value: "request", label: "On request" },
                { value: "free", label: "Free" },
              ]}
            />
          </div>
          {pricing === "fixed" ? (
            <VerbatimField label="Price" value={offer.price === "On request" || offer.price === "Not set" ? "" : offer.price} onChange={(v) => update({ price: v || "Not set" })} placeholder="Exactly as a prospect should read it" previewPrefix={`${offer.name} costs `} previewSuffix="." />
          ) : null}
          <div className="field">
            <div className="field__row">
              <span className="t-label c-2">Where do you sell it?</span>
              <button type="button" className="link-btn" onClick={() => setMarketsOpen(true)}>
                Manage target countries
              </button>
            </div>
            <ToggleChips label="Markets" value={offer.markets} onChange={(v) => update({ markets: v })} options={state.markets.map((m) => ({ value: m, label: m }))} />
          </div>
          <div className="field">
            <span className="t-label c-2">Availability</span>
            <Segmented
              layoutId={`seg-status-${offer.id}`}
              label="Availability"
              value={offer.status}
              onChange={(v) => update({ status: v })}
              options={[
                { value: "active", label: "Active" },
                { value: "coming_soon", label: "Coming soon" },
                { value: "inactive", label: "Inactive" },
              ]}
            />
          </div>
        </fieldset>
      </Group>
      <OfferAssetEditor offer={offer} />
      <MarketsDrawer open={marketsOpen} onOpenChange={setMarketsOpen} />
    </>
  );
}
