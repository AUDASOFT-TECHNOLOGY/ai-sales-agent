import { useState } from "react";
import { FileText, Image, Link2, Quote, Trash2, Video } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Offer, OfferAssetKind } from "../../mock/types";
import { useStore } from "../../mock/store";
import type { Tint } from "../../ui/Glass";
import { Button } from "../../ui/Button";
import { Segmented, TextInput } from "../../ui/Controls";

const KIND_META: Record<OfferAssetKind, { label: string; icon: LucideIcon; tint: Tint }> = {
  doc: { label: "Document", icon: FileText, tint: "action" },
  image: { label: "Photo", icon: Image, tint: "agent" },
  video: { label: "Video", icon: Video, tint: "attention" },
  testimonial: { label: "Testimonial", icon: Quote, tint: "success" },
  link: { label: "Link", icon: Link2, tint: "neutral" },
};

/** Everything besides the one-sentence summary that helps REVANSA convince a prospect: spec
 *  sheets, photos, demo videos, customer testimonials. A summary alone isn't enough material for
 *  an agent to answer a prospect's real questions — this is where a CEO adds more. */
export function OfferAssetEditor({ offer }: { offer: Offer }) {
  const { dispatch } = useStore();
  const [kind, setKind] = useState<OfferAssetKind>("doc");
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");

  const add = () => {
    dispatch({ type: "addOfferAsset", offerId: offer.id, asset: { kind, label: label.trim(), url: url.trim() } });
    setLabel("");
    setUrl("");
  };

  return (
    <>
      <hr className="divider" />
      <div className="cfg-group">
        <div>
          <h3 className="t-h3">Sales material</h3>
          <p className="c-2">Documents, photos, videos and testimonials REVANSA can point to when a prospect asks for more.</p>
        </div>
        {offer.assets.length === 0 ? (
          <p className="c-attention t-body-sm">No supporting material yet. Add a document, photo, video or testimonial to help REVANSA convince this prospect.</p>
        ) : (
          <ul className="asset-list">
            {offer.assets.map((a) => {
              const meta = KIND_META[a.kind];
              const Icon = meta.icon;
              return (
                <li key={a.id} className="asset-row">
                  <span className="glyph-tile" data-tint={meta.tint} data-size={32} aria-hidden>
                    <Icon size={16} />
                  </span>
                  <a href={a.url} target="_blank" rel="noopener noreferrer" className="asset-row__label t-body-sm">
                    {a.label}
                  </a>
                  <button type="button" className="icon-btn" aria-label={`Remove ${a.label}`} onClick={() => dispatch({ type: "removeOfferAsset", offerId: offer.id, assetId: a.id })}>
                    <Trash2 size={16} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <div className="asset-add">
          <Segmented
            layoutId={`seg-asset-kind-${offer.id}`}
            label="What kind of material?"
            size="sm"
            value={kind}
            onChange={setKind}
            options={(Object.keys(KIND_META) as OfferAssetKind[]).map((k) => ({ value: k, label: KIND_META[k].label }))}
          />
          <div className="grid-2">
            <TextInput label="What is it?" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Foundation one-pager" />
            <TextInput label="Link" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
          </div>
          <Button variant="secondary" onClick={add} disabledReason={label.trim() && url.trim() ? undefined : "Add a name and a link first"}>
            Add material
          </Button>
        </div>
      </div>
    </>
  );
}
