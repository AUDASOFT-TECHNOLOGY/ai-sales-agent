import { useState } from "react";
import { FileText, Image, Link2, Quote, Trash2, Video } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import type { Offer, OfferAssetKind } from "../../mock/types";
import { useStore } from "../../mock/store";
import type { Tint } from "../../ui/Glass";
import { Button } from "../../ui/Button";
import { Segmented, TextInput } from "../../ui/Controls";

const KIND_META: Record<OfferAssetKind, { labelKey: MessageKey; icon: LucideIcon; tint: Tint }> = {
  doc: { labelKey: "assets.kind.doc", icon: FileText, tint: "action" },
  image: { labelKey: "assets.kind.image", icon: Image, tint: "agent" },
  video: { labelKey: "assets.kind.video", icon: Video, tint: "attention" },
  testimonial: { labelKey: "assets.kind.testimonial", icon: Quote, tint: "success" },
  link: { labelKey: "assets.kind.link", icon: Link2, tint: "neutral" },
};

/** Everything besides the one-sentence summary that helps REVANSA convince a prospect: spec
 *  sheets, photos, demo videos, customer testimonials. A summary alone isn't enough material for
 *  an agent to answer a prospect's real questions — this is where a CEO adds more. */
export function OfferAssetEditor({ offer }: { offer: Offer }) {
  const { t } = useI18n();
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
          <h3 className="t-h3">{t("assets.salesMaterial")}</h3>
          <p className="c-2">{t("assets.salesMaterialDesc", { brand: t("brand.name") })}</p>
        </div>
        {offer.assets.length === 0 ? (
          <p className="c-attention t-body-sm">{t("assets.noneYet", { brand: t("brand.name") })}</p>
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
                  <button type="button" className="icon-btn" aria-label={t("assets.removeLabel", { label: a.label })} onClick={() => dispatch({ type: "removeOfferAsset", offerId: offer.id, assetId: a.id })}>
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
            label={t("assets.whatKind")}
            size="sm"
            value={kind}
            onChange={setKind}
            options={(Object.keys(KIND_META) as OfferAssetKind[]).map((k) => ({ value: k, label: t(KIND_META[k].labelKey) }))}
          />
          <div className="grid-2">
            <TextInput label={t("assets.whatIsIt")} value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t("assets.whatIsIt.placeholder")} />
            <TextInput label={t("assets.kind.link")} type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t("assets.link.placeholder")} />
          </div>
          <Button variant="secondary" onClick={add} disabledReason={label.trim() && url.trim() ? undefined : t("assets.addFirst")}>
            {t("assets.addMaterial")}
          </Button>
        </div>
      </div>
    </>
  );
}
