/** What backs a product up, in three plain zones: photos and videos, documents and web pages, and
 *  testimonials. Every line says plainly whether the agent can really use it: "ready" only when text
 *  was extracted from it; a video, an image or a page that could not be read is kept for the client
 *  but is *not* read by the agent, and the line says so. */

import { useCallback, useEffect, useRef, useState } from "react";
import { FileImage, FileSpreadsheet, FileText, Globe, Link2, MessageSquareQuote, Paperclip, Plus, Video } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Glass } from "../../ui/Glass";
import { TextInput } from "../../ui/Controls";
import {
  clientAddResourceLink,
  clientDeleteResource,
  clientResources,
  clientRetryResource,
  clientUploadResource,
  resourceDownloadUrl,
  type ProductResource,
  type ResourceCategory,
  type ResourceList,
} from "../api";
import { FailureNote, Loading, failureOf, type Failure } from "../kit";

const ICON: Record<ResourceCategory, typeof FileText> = {
  web: Globe,
  pdf: FileText,
  spreadsheet: FileSpreadsheet,
  document: FileText,
  image: FileImage,
  video: Video,
  testimonial: MessageSquareQuote,
  other: Paperclip,
};

type ZoneId = "media" | "docs" | "testi";
const ZONES: Array<{ id: ZoneId; icon: typeof FileText; categories: ResourceCategory[]; linkCategory: ResourceCategory }> = [
  { id: "media", icon: FileImage, categories: ["image", "video"], linkCategory: "video" },
  { id: "docs", icon: FileText, categories: ["pdf", "document", "spreadsheet", "web", "other"], linkCategory: "web" },
  { id: "testi", icon: MessageSquareQuote, categories: ["testimonial"], linkCategory: "testimonial" },
];

/** How many resources sit in each zone, for the checklist beside the form. */
export function zoneCounts(list: ProductResource[]): Record<ZoneId, number> {
  const out: Record<ZoneId, number> = { media: 0, docs: 0, testi: 0 };
  for (const r of list) {
    const zone = ZONES.find((z) => z.categories.includes(r.category)) ?? ZONES[1]!;
    out[zone.id] += 1;
  }
  return out;
}

function size(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Zone({
  zone,
  items,
  productId,
  limits,
  busy,
  onRun,
}: {
  zone: (typeof ZONES)[number];
  items: ProductResource[];
  productId: string;
  limits: ResourceList["limits"] | null;
  busy: string | null;
  onRun: (label: string, action: () => Promise<unknown>) => Promise<void>;
}) {
  const { t, n } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState(false);
  const [over, setOver] = useState(false);
  const Icon = zone.icon;

  async function addLink() {
    if (!/^https:\/\/[^\s/$.?#][^\s]*$/.test(url.trim())) return setUrlError(true);
    setUrlError(false);
    await onRun(`link-${zone.id}`, async () => {
      await clientAddResourceLink(productId, { title: title.trim() || url.trim(), url: url.trim(), category: zone.linkCategory });
      setTitle("");
      setUrl("");
      setAdding(false);
    });
  }

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    await onRun(`file-${zone.id}`, () => clientUploadResource(productId, file, file.name));
    if (input.current) input.current.value = "";
  }

  const statusChip = (r: ProductResource) =>
    r.status === "ready" ? <Chip tint="success">{t("rv.res.status.ready")}</Chip> : r.status === "failed" ? <Chip tint="danger">{t("rv.res.status.failed")}</Chip> : <Chip tint="neutral">{t("rv.res.status.stored")}</Chip>;

  const detail = (r: ProductResource): string | null => {
    if (r.status === "ready") return t("rv.res.ready.detail", { n: n(r.text_chars) });
    if (!r.status_detail) return null;
    const key = `rv.res.detail.${r.status_detail}` as MessageKey;
    const text = t(key);
    return text === key ? t("rv.res.detail.other") : text;
  };

  return (
    <Glass
      level={1}
      className="rv-zone rv-ent"
      data-ent="prd"
      data-over={over || undefined}
      aria-label={t(`rv.pe.zone.${zone.id}` as MessageKey)}
      onDragOver={(e: React.DragEvent) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e: React.DragEvent) => {
        e.preventDefault();
        setOver(false);
        void upload(e.dataTransfer.files);
      }}
    >
      <div className="rv-zone__head">
        <span className="rv-zone__icon" aria-hidden>
          <Icon size={20} />
        </span>
        <div className="rv-zone__title">
          <h4>{t(`rv.pe.zone.${zone.id}` as MessageKey)}</h4>
          <p>{t(`rv.pe.zone.${zone.id}.help` as MessageKey)}</p>
        </div>
        <span className="rv-zone__count" aria-label={String(items.length)}>
          {items.length}
        </span>
      </div>

      {items.length === 0 ? (
        <p className="c-3 t-body-sm" data-testid={zone.id === "docs" ? "resources-empty" : undefined}>
          {t("rv.pe.zone.empty")}
        </p>
      ) : (
        <ul className="rv-list" aria-label={t(`rv.pe.zone.${zone.id}` as MessageKey)}>
          {items.map((r) => {
            const ItemIcon = ICON[r.category] ?? Paperclip;
            return (
              <li key={r.id} className="rv-resource" data-testid={`resource-${r.id}`}>
                <span className="rv-resource__icon" aria-hidden>
                  <ItemIcon size={18} />
                </span>
                <div className="rv-row__main">
                  <span className="rv-row__title" dir="auto">
                    {r.title}
                  </span>
                  <span className="rv-row__sub">
                    {t(`rv.res.cat.${r.category}` as MessageKey)}
                    {r.size_bytes !== null ? ` · ${size(r.size_bytes)}` : ""}
                    {r.url ? ` · ${new URL(r.url).hostname}` : ""}
                  </span>
                  {detail(r) ? <span className="rv-row__sub">{detail(r)}</span> : null}
                </div>
                {statusChip(r)}
                <div className="rv-actions">
                  {r.kind === "file" ? (
                    <a className="btn" data-variant="secondary" data-size="sm" href={resourceDownloadUrl(productId, r.id)}>
                      <span className="btn__label">{t("rv.res.download")}</span>
                    </a>
                  ) : r.url ? (
                    <a className="btn" data-variant="secondary" data-size="sm" href={r.url} target="_blank" rel="noreferrer noopener">
                      <span className="btn__label">{t("rv.common.open")}</span>
                    </a>
                  ) : null}
                  {r.status === "failed" && r.kind === "link" ? (
                    <Button size="sm" onClick={() => void onRun(`retry-${r.id}`, () => clientRetryResource(productId, r.id))} loading={busy === `retry-${r.id}`}>
                      {t("rv.res.retry")}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => void onRun(`del-${r.id}`, () => clientDeleteResource(productId, r.id))} loading={busy === `del-${r.id}`}>
                    {t("rv.common.remove")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="rv-actions">
        <input ref={input} type="file" hidden aria-label={`${t("rv.pe.addFile")} · ${t(`rv.pe.zone.${zone.id}` as MessageKey)}`} accept={limits ? limits.accepted_extensions.map((x) => `.${x}`).join(",") : undefined} onChange={(e) => void upload(e.target.files)} />
        <Button size="sm" leading={<Plus size={14} aria-hidden />} onClick={() => input.current?.click()} loading={busy === `file-${zone.id}`}>
          {t("rv.pe.addFile")}
        </Button>
        <Button size="sm" variant="ghost" leading={<Link2 size={14} aria-hidden />} onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
          {t("rv.pe.addLink")}
        </Button>
        {limits ? <span className="c-3 t-body-sm">{t("rv.res.accepted", { types: limits.accepted_extensions.join(", "), size: size(limits.max_file_bytes) })}</span> : null}
      </div>

      {adding ? (
        <div className="rv-repeat">
          <TextInput label={t("rv.pe.link.title")} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
          <TextInput
            label={t("rv.pe.link.url")}
            helper={t("rv.res.urlHelp")}
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setUrlError(false);
            }}
            error={urlError ? t("rv.res.urlError") : undefined}
            placeholder="https://"
            dir="ltr"
            inputMode="url"
          />
          <div>
            <Button variant="primary" size="sm" onClick={() => void addLink()} loading={busy === `link-${zone.id}`} disabled={url.trim() === ""}>
              {t("rv.pe.link.save")}
            </Button>
          </div>
        </div>
      ) : null}
    </Glass>
  );
}

export function ResourcesPanel({ productId, onLoaded }: { productId: string; onLoaded?: (resources: ProductResource[]) => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<ResourceList | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await clientResources(productId);
      setData(next);
      onLoaded?.(next.resources);
      setFailure(null);
    } catch (err) {
      setFailure(failureOf(err));
    }
  }, [productId, onLoaded]);

  useEffect(() => {
    let cancelled = false;
    clientResources(productId).then(
      (value) => {
        if (cancelled) return;
        setData(value);
        onLoaded?.(value.resources);
      },
      (err: unknown) => !cancelled && setFailure(failureOf(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [productId, onLoaded]);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setFailure(null);
    try {
      await action();
    } catch (err) {
      setFailure(failureOf(err));
    }
    await load();
    setBusy(null);
  }

  return (
    <div className="rv-zones" aria-label={t("rv.res.title")}>
      {failure ? <FailureNote failure={failure} /> : null}
      {!data ? (
        failure ? null : <Loading />
      ) : (
        ZONES.map((zone) => (
          <Zone key={zone.id} zone={zone} items={data.resources.filter((r) => (ZONES.find((z) => z.categories.includes(r.category)) ?? ZONES[1]!).id === zone.id)} productId={productId} limits={data.limits} busy={busy} onRun={run} />
        ))
      )}
    </div>
  );
}
