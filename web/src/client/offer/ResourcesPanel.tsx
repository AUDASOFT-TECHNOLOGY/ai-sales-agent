/** The documents and links that back a product up. Every line says plainly whether the agent can
 *  really use it: "ready" only when text was extracted from it; a video, an image or a page that
 *  could not be read is kept for the client but is *not* read by the agent, and the line says so. */

import { useCallback, useEffect, useRef, useState } from "react";
import { FileImage, FileSpreadsheet, FileText, Globe, Link2, MessageSquareQuote, Paperclip, Video } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
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
import { Card, FailureNote, Loading, Note, failureOf, type Failure } from "../kit";

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
const LINK_CATEGORIES: ResourceCategory[] = ["web", "video", "testimonial", "other"];

function size(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ResourcesPanel({ productId }: { productId: string }) {
  const { t, n } = useI18n();
  const [data, setData] = useState<ResourceList | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [category, setCategory] = useState<ResourceCategory>("web");
  const [urlError, setUrlError] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await clientResources(productId));
      setFailure(null);
    } catch (err) {
      setFailure(failureOf(err));
    }
  }, [productId]);

  useEffect(() => {
    let cancelled = false;
    clientResources(productId).then(
      (value) => !cancelled && setData(value),
      (err: unknown) => !cancelled && setFailure(failureOf(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [productId]);

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

  async function addLink() {
    if (!/^https:\/\/[^\s/$.?#][^\s]*$/.test(url.trim())) return setUrlError(true);
    setUrlError(false);
    await run("link", async () => {
      await clientAddResourceLink(productId, { title: title.trim() || url.trim(), url: url.trim(), category });
      setTitle("");
      setUrl("");
    });
  }

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    await run("file", () => clientUploadResource(productId, file, title.trim() || file.name));
    setTitle("");
    if (input.current) input.current.value = "";
  }

  const statusChip = (r: ProductResource) =>
    r.status === "ready" ? (
      <Chip tint="success">{t("rv.res.status.ready")}</Chip>
    ) : r.status === "failed" ? (
      <Chip tint="danger">{t("rv.res.status.failed")}</Chip>
    ) : (
      <Chip tint="neutral">{t("rv.res.status.stored")}</Chip>
    );

  const detail = (r: ProductResource): string | null => {
    if (r.status === "ready") return t("rv.res.ready.detail", { n: n(r.text_chars) });
    if (!r.status_detail) return null;
    const key = `rv.res.detail.${r.status_detail}` as MessageKey;
    const text = t(key);
    return text === key ? t("rv.res.detail.other") : text;
  };

  return (
    <Card aria-label={t("rv.res.title")}>
      <div className="rv-card__head">
        <h3>{t("rv.res.title")}</h3>
      </div>
      <p className="rv-card__sub">{t("rv.res.intro")}</p>
      <Note tone="info">{t("rv.res.honest")}</Note>
      {failure ? <FailureNote failure={failure} /> : null}

      {!data ? (
        failure ? null : <Loading />
      ) : data.resources.length === 0 ? (
        <p className="c-3" data-testid="resources-empty">
          {t("rv.res.empty")}
        </p>
      ) : (
        <ul className="rv-list" aria-label={t("rv.res.title")}>
          {data.resources.map((r) => {
            const Icon = ICON[r.category] ?? Paperclip;
            return (
              <li key={r.id} className="rv-resource" data-testid={`resource-${r.id}`}>
                <span className="rv-resource__icon" aria-hidden>
                  <Icon size={18} />
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
                    <Button size="sm" onClick={() => void run(`retry-${r.id}`, () => clientRetryResource(productId, r.id))} loading={busy === `retry-${r.id}`}>
                      {t("rv.res.retry")}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => void run(`del-${r.id}`, () => clientDeleteResource(productId, r.id))} loading={busy === `del-${r.id}`}>
                    {t("rv.common.remove")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="rv-editor__section">
        <h4>{t("rv.res.add")}</h4>
        <TextInput label={t("rv.res.titleField")} optional={t("rv.common.optional")} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        <div className="rv-grid-2">
          <div className="stack">
            <TextInput
              label={t("rv.res.url")}
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
            <label className="rv-select-label">
              <span className="t-label c-2">{t("rv.res.kind")}</span>
              <select className="rv-select" value={category} onChange={(e) => setCategory(e.target.value as ResourceCategory)}>
                {LINK_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {t(`rv.res.cat.${c}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <Button leading={<Link2 size={16} aria-hidden />} onClick={() => void addLink()} loading={busy === "link"} disabled={url.trim() === ""}>
                {t("rv.res.addLink")}
              </Button>
            </div>
          </div>
          <div
            className="rv-dropzone"
            data-active={over || undefined}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              void upload(e.dataTransfer.files);
            }}
          >
            <Paperclip size={22} aria-hidden />
            <strong>{t("rv.res.drop")}</strong>
            <span className="t-body-sm">
              {t("rv.res.accepted", { types: data ? data.limits.accepted_extensions.join(", ") : "", size: data ? size(data.limits.max_file_bytes) : "" })}
            </span>
            <input ref={input} type="file" hidden aria-label={t("rv.res.chooseFile")} accept={data ? data.limits.accepted_extensions.map((x) => `.${x}`).join(",") : undefined} onChange={(e) => void upload(e.target.files)} />
            <Button onClick={() => input.current?.click()} loading={busy === "file"}>
              {t("rv.res.chooseFile")}
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
