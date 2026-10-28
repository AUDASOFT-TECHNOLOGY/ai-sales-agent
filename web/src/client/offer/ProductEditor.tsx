/** One product, on one page, in the order an owner thinks: what it is, what it costs, what backs it up
 *  (photos, documents, testimonials), how it is paid (with its own payment link) and what the agent
 *  may say. The form edits a copy; nothing is sent until "Save", and what is shown afterwards is what
 *  the server answered. Rules only the Brain knows come back from the server and are listed as they are.
 *
 *  The checklist on the side says what the agent already knows about this product and what is missing. */

import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, ExternalLink, Plus } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Glass } from "../../ui/Glass";
import { TextInput, Textarea } from "../../ui/Controls";
import type { ProductResource } from "../api";
import { FailureNote, ListEditor, Note, PageHead, type Failure } from "../kit";
import { defaultLanguage, type Doc } from "../settingsModel";
import { checklistOf, scoreOf, type CheckKey } from "./completeness";
import { ResourcesPanel, zoneCounts } from "./ResourcesPanel";
import { applyMethodForm, blankInstruction, blankMethod, forgetMethod, formOfMethod, methodsOf, putMethod, removeMethod } from "./paymentModel";
import {
  CTA_KINDS,
  DISCLOSURES,
  PRICE_MODELS,
  PRODUCT_STATUSES,
  applyProductForm,
  blankProduct,
  findProduct,
  formOfProduct,
  productList,
  productProblems,
  putProduct,
  slugify,
  uniqueSlug,
  type ProductForm,
  type ProductProblem,
} from "./productModel";
import { useOffer } from "./OfferLayout";
import { useAddress } from "../prepare/links";
import { useTerms } from "../prepare/terms";

type SectionKey = "basics" | "price" | "media" | "pay" | "pitch" | "adv";
const SECTIONS: SectionKey[] = ["basics", "price", "media", "pay", "pitch", "adv"];
/** Which section each checklist line belongs to, and where each validation problem is shown. */
const CHECK_SECTION: Record<CheckKey, SectionKey> = { basics: "basics", price: "price", media: "media", docs: "media", testi: "media", paylink: "pay", payoff: "pay", objections: "pitch" };
const PROBLEM_SECTION: Record<ProductProblem, SectionKey> = {
  name: "basics",
  summary: "basics",
  price: "price",
  variantPrice: "adv",
  variantCountries: "adv",
  url: "pay",
  onlineChoice: "pay",
  onlineUrl: "pay",
  ctaUrl: "adv",
  comingSoonLink: "adv",
  objection: "pitch",
};

const PRICED = ["fixed", "from", "range"];
const HTTPS = /^https:\/\/[^\s/$.?#][^\s]*$/;

function Select<T extends string>({ label, value, options, onChange, helper }: { label: string; value: T; options: Array<{ value: T; label: string }>; onChange: (v: T) => void; helper?: string }) {
  return (
    <label className="rv-select-label">
      <span className="t-label c-2">{label}</span>
      <select className="rv-select" value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {helper ? <span className="c-3 t-body-sm">{helper}</span> : null}
    </label>
  );
}

function PriceFields({
  model,
  amount,
  maxAmount,
  currency,
  onChange,
  error,
}: {
  model: ProductForm["priceModel"];
  amount: string;
  maxAmount: string;
  currency: string;
  onChange: (patch: { model?: ProductForm["priceModel"]; amount?: string; maxAmount?: string; currency?: string }) => void;
  error?: string;
}) {
  const { t } = useI18n();
  const priced = PRICED.includes(model);
  return (
    <div className="stack">
      <div className="rv-grid-2">
        <Select
          label={t("rv.prod.price.model")}
          value={model}
          onChange={(v) => onChange({ model: v })}
          options={PRICE_MODELS.map((m) => ({ value: m, label: t(`rv.prod.price.${m}` as MessageKey) }))}
        />
        {priced ? (
          <TextInput label={t("rv.prod.price.currency")} helper={t("rv.prod.price.currencyHelp")} value={currency} onChange={(e) => onChange({ currency: e.target.value.toUpperCase() })} maxLength={3} placeholder="TND" dir="ltr" />
        ) : null}
      </div>
      {priced ? (
        <div className="rv-grid-2">
          <TextInput label={t(model === "range" ? "rv.prod.price.min" : "rv.prod.price.amount")} value={amount} onChange={(e) => onChange({ amount: e.target.value })} inputMode="decimal" dir="ltr" />
          {model === "range" ? <TextInput label={t("rv.prod.price.max")} value={maxAmount} onChange={(e) => onChange({ maxAmount: e.target.value })} inputMode="decimal" dir="ltr" /> : null}
        </div>
      ) : null}
      {error ? <span className="c-danger t-body-sm">{error}</span> : null}
    </div>
  );
}

function Section({ title, intro, children, testId }: { title: string; intro?: string; children: React.ReactNode; testId?: string }) {
  return (
    <Glass level={1} className="rv-card" data-testid={testId} aria-label={title}>
      <div className="rv-card__head">
        <div>
          <h3>{title}</h3>
          {intro ? <p className="rv-card__sub">{intro}</p> : null}
        </div>
      </div>
      {children}
    </Glass>
  );
}

export function ProductEditor({ productId }: { productId?: string } = {}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const params = useParams();
  const address = useAddress();
  const tt = useTerms();
  const id = productId ?? params.id;
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const isNew = id === undefined || id === "new";
  const stored = isNew ? undefined : findProduct(commercial, id!);
  const allMethods = methodsOf(commercial);
  const methods = allMethods.filter((m) => m.status !== "inactive");
  const takenIds = useMemo(() => productList(commercial).map((p) => String(p.id)), [commercial]);
  const dedicatedId = stored ? `pay-${String(stored.id)}` : "";
  const dedicated = dedicatedId ? allMethods.find((m) => String(m.id) === dedicatedId) : undefined;
  const storedLink = dedicated ? (formOfMethod(dedicated, lang).instructions.find((i) => i.url.trim() !== "")?.url ?? "") : "";

  const fromStore = stored ? formOfProduct(stored, lang) : blankProduct("");
  // A product already sold online with a payment link but no sheet address starts from that link.
  const initial = fromStore.payOnline && fromStore.url.trim() === "" && storedLink !== "" ? { ...fromStore, url: storedLink } : fromStore;
  const [draft, setDraft] = useState<ProductForm>(initial);
  const [base, setBase] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [problems, setProblems] = useState<ProductProblem[]>([]);
  const [done, setDone] = useState(false);
  const [resources, setResources] = useState<ProductResource[]>([]);
  const [section, setSection] = useState<SectionKey>("basics");

  const onLoaded = useCallback((list: ProductResource[]) => setResources(list), []);

  if (stored && base !== initial && JSON.stringify(base) !== JSON.stringify(initial)) {
    setBase(initial);
    setDraft(initial);
  }
  // The address of the product sheet is also the payment link: that is where the prospect pays online.
  const link = draft.payMode === "custom" && draft.payOnline ? draft.url.trim() : "";
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const set = (patch: Partial<ProductForm>) => {
    setDone(false);
    setDraft({ ...draft, ...patch });
  };
  const err = (p: ProductProblem) => (problems.includes(p) ? t(`rv.prod.err.${p}` as MessageKey) : undefined);

  if (!isNew && !stored) {
    return (
      <div className="rv-page">
        <Note tone="warn" title={t("rv.prod.missing")}>
          <Link to={address({ to: "products" })} className="link-btn">
            {t("rv.prod.backToList")}
          </Link>
        </Note>
      </div>
    );
  }

  async function onSave() {
    const found = productProblems(draft);
    setProblems(found);
    if (found.length > 0) {
      setSection(PROBLEM_SECTION[found[0]]);
      return;
    }
    const id = draft.id || uniqueSlug(slugify(draft.name), takenIds, "produit");
    let form: ProductForm = { ...draft, id };
    let next: Doc = commercial;
    const ownId = `pay-${id}`;
    const explicit = form.payOnlineMethods.filter((m) => m !== ownId);
    if (link !== "") {
      // The product's own payment link lives in an online payment method that only this product uses.
      const existing = allMethods.find((m) => String(m.id) === ownId);
      const mf = existing ? formOfMethod(existing, lang) : blankMethod(ownId, "online");
      mf.status = "active";
      mf.kind = "online";
      if (!existing) mf.label = `${t("rv.pe.pay.online")} · ${draft.name}`;
      const first = mf.instructions[0] ?? blankInstruction("main");
      mf.instructions = [{ ...first, url: link }, ...mf.instructions.slice(1)];
      next = putMethod(next, applyMethodForm(existing, mf, lang));
      form = { ...form, payMode: "custom", payOnline: true, payOnlineMethods: [ownId, ...explicit] };
    } else if (allMethods.some((m) => String(m.id) === ownId)) {
      next = forgetMethod(removeMethod(next, ownId), ownId);
      form = { ...form, payOnlineMethods: explicit };
    }
    const product: Doc = applyProductForm(stored, form, lang);
    setBusy(true);
    setFailure(null);
    setDone(false);
    const problem = await save({ commercial: putProduct(next, product) });
    setBusy(false);
    if (problem) {
      setFailure(problem);
      return;
    }
    setDone(true);
    if (isNew) navigate(address({ to: "product", id }), { replace: true });
  }

  const counts = zoneCounts(resources);
  const list = checklistOf({ form: draft, counts, methods, ownMethodId: dedicatedId || `pay-${draft.id}`, ownLink: link });
  const labels: Record<CheckKey, string> = {
    basics: t("rv.pe.aside.basics"),
    price: t("rv.pe.aside.price"),
    media: t("rv.pe.aside.media", { n: counts.media }),
    docs: t("rv.pe.aside.docs", { n: counts.docs }),
    testi: t("rv.pe.aside.testi", { n: counts.testi }),
    paylink: t("rv.pe.aside.paylink"),
    payoff: t("rv.pe.aside.payoff"),
    objections: t("rv.pe.aside.objections", { n: list.find((c) => c.key === "objections")?.n ?? 0 }),
  };
  const checks = list.map((c) => ({ key: c.key, ok: c.ok, label: labels[c.key] }));
  const score = scoreOf(list);
  const shownLink = link !== "" && HTTPS.test(link) ? link : "";

  return (
    <div className="rv-page">
      <div>
        <Link to={address({ to: "products" })} className="link-btn">
          <ArrowLeft size={14} className="mirror" aria-hidden /> {t("rv.prod.backToList")}
        </Link>
      </div>
      <PageHead title={isNew ? tt("rv.prod.new") : draft.name || draft.id} />

      {failure ? <FailureNote failure={failure} /> : null}
      {done ? <Note tone="success">{t("rv.common.saved")}</Note> : null}

      <div className="rv-split">
        <div className="rv-split__main">
          {section === "basics" ? (
          <Section title={t("rv.pe.s.basics")}>
            <TextInput label={t("rv.prod.name")} value={draft.name} onChange={(e) => set({ name: e.target.value })} error={err("name")} maxLength={300} />
            <TextInput label={t("rv.prod.summary")} helper={t("rv.prod.summaryHelp")} value={draft.summary} onChange={(e) => set({ summary: e.target.value })} error={err("summary")} maxLength={300} />
            <Textarea label={t("rv.prod.description")} helper={t("rv.prod.descriptionHelp")} value={draft.description} onChange={(e) => set({ description: e.target.value })} rows={5} maxLength={3000} />
            <div className="rv-grid-2">
              <Select
                label={t("rv.prod.status")}
                value={draft.status}
                onChange={(v) => set({ status: v })}
                options={PRODUCT_STATUSES.map((s) => ({ value: s, label: t(`rv.prod.status.${s}` as MessageKey) }))}
                helper={t(`rv.prod.statusHelp.${draft.status}` as MessageKey)}
              />
            </div>
          </Section>
          ) : null}

          {section === "price" ? (
          <Section title={t("rv.pe.s.price")} intro={t("rv.prod.price.help")}>
            <PriceFields
              model={draft.priceModel}
              amount={draft.amount}
              maxAmount={draft.maxAmount}
              currency={draft.currency}
              onChange={(p) => set({ priceModel: p.model ?? draft.priceModel, amount: p.amount ?? draft.amount, maxAmount: p.maxAmount ?? draft.maxAmount, currency: p.currency ?? draft.currency })}
              error={err("price")}
            />
            <TextInput label={t("rv.prod.price.note")} optional={t("rv.common.optional")} value={draft.priceNote} onChange={(e) => set({ priceNote: e.target.value })} maxLength={300} />
          </Section>
          ) : null}

          {section === "media" ? (
          <Section title={t("rv.pe.s.media")} intro={t("rv.pe.media.intro")} testId="product-media">
            {stored ? (
              <ResourcesPanel productId={String(stored.id)} onLoaded={onLoaded} />
            ) : (
              <Note tone="info">{t("rv.pe.saveFirst")}</Note>
            )}
          </Section>
          ) : null}

          {section === "pay" ? (
          <Section title={t("rv.pe.s.pay")} intro={t("rv.pe.pay.intro")} testId="product-payment">
            <div className="stack">
              <span className="t-label c-2" id="sold-online">
                {t("rv.pe.sell.question")}
              </span>
              <div className="rv-seg" role="group" aria-labelledby="sold-online" style={{ alignSelf: "flex-start" }}>
                <button type="button" className="rv-seg__btn" aria-pressed={draft.payMode === "custom" && draft.payOnline} onClick={() => set({ payMode: "custom", payOnline: true })}>
                  {t("rv.pe.sell.yes")}
                </button>
                <button type="button" className="rv-seg__btn" aria-pressed={draft.payMode === "custom" && !draft.payOnline} onClick={() => set({ payMode: "custom", payOnline: false })}>
                  {t("rv.pe.sell.no")}
                </button>
              </div>
              {err("onlineChoice") ? <span className="c-danger t-body-sm">{err("onlineChoice")}</span> : null}
            </div>

            {draft.payMode === "custom" && draft.payOnline ? (
              <div className="stack">
                <TextInput label={t("rv.pe.sell.url")} helper={t("rv.pe.sell.urlHelp")} value={draft.url} onChange={(e) => set({ url: e.target.value })} error={err("onlineUrl") ?? err("url")} placeholder="https://" dir="ltr" inputMode="url" />
                {shownLink ? (
                  <div>
                    <a className="btn" data-variant="secondary" data-size="sm" href={shownLink} target="_blank" rel="noreferrer noopener">
                      <ExternalLink size={14} aria-hidden />
                      <span className="btn__label">{t("rv.pe.pay.link.test")}</span>
                    </a>
                  </div>
                ) : null}
              </div>
            ) : null}

            {draft.payMode === "custom" ? (
              <div className="rv-payzone rv-ent" data-ent="chn">
                <label className="rv-payzone__head">
                  <input type="checkbox" checked={draft.payOffline} onChange={(e) => set({ payOffline: e.target.checked, payOfflineMethods: [] })} />
                  <strong>{t("rv.pe.pay.offline")}</strong>
                </label>
                {draft.payOffline ? <p className="c-3 t-body-sm">{t("rv.pe.pay.offline.global")}</p> : null}
              </div>
            ) : null}
          </Section>
          ) : null}

          {section === "pitch" ? (
          <Section title={t("rv.pe.s.pitch")} intro={t("rv.prod.pitch.help")}>
            <ListEditor label={t("rv.prod.benefits")} helper={t("rv.prod.benefitsHelp")} values={draft.benefits} onChange={(benefits) => set({ benefits })} addLabel={t("rv.prod.benefits.add")} maxItems={10} />
            <div className="rv-grid-2">
              <ListEditor label={t("rv.prod.suitedFor")} helper={t("rv.prod.suitedForHelp")} values={draft.suitedFor} onChange={(suitedFor) => set({ suitedFor })} addLabel={t("rv.prod.suitedFor.add")} />
              <ListEditor label={t("rv.prod.notFor")} helper={t("rv.prod.notForHelp")} values={draft.notFor} onChange={(notFor) => set({ notFor })} addLabel={t("rv.prod.notFor.add")} />
            </div>
            <fieldset className="rv-fieldset">
              <legend className="t-label c-2">{t("rv.prod.s.objections")}</legend>
              <p className="c-3 t-body-sm">{t("rv.prod.objections.help")}</p>
              {draft.objections.map((o, i) => (
                <div className="rv-repeat" key={i}>
                  <TextInput label={t("rv.prod.objection.example")} value={o.example} onChange={(e) => set({ objections: draft.objections.map((x, n) => (n === i ? { ...x, example: e.target.value } : x)) })} placeholder={t("rv.prod.objection.examplePlaceholder")} maxLength={300} />
                  <Textarea label={t("rv.prod.objection.response")} value={o.response} onChange={(e) => set({ objections: draft.objections.map((x, n) => (n === i ? { ...x, response: e.target.value } : x)) })} rows={3} maxLength={3000} />
                  <div>
                    <Button type="button" variant="ghost" size="sm" onClick={() => set({ objections: draft.objections.filter((_, n) => n !== i) })}>
                      {t("rv.common.remove")}
                    </Button>
                  </div>
                </div>
              ))}
              {err("objection") ? <span className="c-danger t-body-sm">{err("objection")}</span> : null}
              {draft.objections.length < 20 ? (
                <div>
                  <Button type="button" size="sm" onClick={() => set({ objections: [...draft.objections, { code: "", example: "", response: "" }] })}>
                    {t("rv.prod.objections.add")}
                  </Button>
                </div>
              ) : null}
            </fieldset>
          </Section>
          ) : null}

          {section === "adv" ? (
          <Section title={t("rv.pe.s.adv")} intro={t("rv.pe.s.adv.hint")}>
            <div className="stack" style={{ gap: 20 }}>
              <Select
                label={t("rv.prod.price.disclose")}
                value={draft.disclose}
                onChange={(v) => set({ disclose: v })}
                options={DISCLOSURES.map((d) => ({ value: d, label: t(`rv.prod.price.disclose.${d}` as MessageKey) }))}
              />
              <div className="rv-editor__section">
                <h4>{t("rv.prod.variants.title")}</h4>
                <p className="rv-card__sub">{t("rv.prod.variants.help")}</p>
                {draft.variants.map((v, i) => (
                  <div className="rv-repeat" key={i}>
                    <TextInput
                      label={t("rv.prod.variants.countries")}
                      helper={t("rv.prod.variants.countriesHelp")}
                      value={v.countries}
                      onChange={(e) => set({ variants: draft.variants.map((x, n) => (n === i ? { ...x, countries: e.target.value } : x)) })}
                    />
                    <PriceFields
                      model={v.model}
                      amount={v.amount}
                      maxAmount={v.maxAmount}
                      currency={v.currency}
                      onChange={(p) => set({ variants: draft.variants.map((x, n) => (n === i ? { ...x, model: p.model ?? x.model, amount: p.amount ?? x.amount, maxAmount: p.maxAmount ?? x.maxAmount, currency: p.currency ?? x.currency } : x)) })}
                    />
                    <div>
                      <Button type="button" variant="ghost" size="sm" onClick={() => set({ variants: draft.variants.filter((_, n) => n !== i) })}>
                        {t("rv.common.remove")}
                      </Button>
                    </div>
                  </div>
                ))}
                {err("variantPrice") ? <span className="c-danger t-body-sm">{err("variantPrice")}</span> : null}
                {err("variantCountries") ? <span className="c-danger t-body-sm">{err("variantCountries")}</span> : null}
                <div>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => set({ variants: [...draft.variants, { id: uniqueSlug("prix", draft.variants.map((x) => x.id), "prix"), countries: "", model: "fixed", amount: "", maxAmount: "", currency: draft.currency }] })}
                  >
                    {t("rv.prod.variants.add")}
                  </Button>
                </div>
              </div>
              <TextInput label={t("rv.prod.proposition")} helper={t("rv.prod.propositionHelp")} optional={t("rv.common.optional")} value={draft.proposition} onChange={(e) => set({ proposition: e.target.value })} maxLength={300} />
              <ListEditor label={t("rv.prod.differentiators")} helper={t("rv.prod.differentiatorsHelp")} values={draft.differentiators} onChange={(differentiators) => set({ differentiators })} addLabel={t("rv.prod.differentiators.add")} maxItems={10} />
              <fieldset className="rv-fieldset">
                <legend className="t-label c-2">{t("rv.prod.arguments")}</legend>
                <p className="c-3 t-body-sm">{t("rv.prod.argumentsHelp")}</p>
                {draft.arguments.map((a, i) => (
                  <div className="row" key={i}>
                    <input
                      className="input well"
                      aria-label={`${t("rv.prod.arguments")} ${i + 1}`}
                      value={a.text}
                      maxLength={300}
                      dir="auto"
                      onChange={(e) => set({ arguments: draft.arguments.map((x, n) => (n === i ? { ...x, text: e.target.value } : x)) })}
                    />
                    <Button type="button" variant="ghost" size="sm" onClick={() => set({ arguments: draft.arguments.filter((_, n) => n !== i) })}>
                      {t("rv.common.remove")}
                    </Button>
                  </div>
                ))}
                {draft.arguments.length < 20 ? (
                  <div>
                    <Button type="button" size="sm" onClick={() => set({ arguments: [...draft.arguments, { id: "", text: "" }] })}>
                      {t("rv.prod.arguments.add")}
                    </Button>
                  </div>
                ) : null}
              </fieldset>
              <Select
                label={t("rv.prod.cta")}
                value={draft.ctaKind}
                onChange={(v) => set({ ctaKind: v })}
                options={CTA_KINDS.map((k) => ({ value: k, label: t(`rv.prod.cta.${k}` as MessageKey) }))}
                helper={t(`rv.prod.ctaHelp.${draft.ctaKind}` as MessageKey)}
              />
              {draft.ctaKind === "enrol_link" ? (
                <TextInput label={t("rv.prod.cta.url")} helper={t("rv.prod.cta.urlHelp")} value={draft.ctaUrl} onChange={(e) => set({ ctaUrl: e.target.value })} error={err("ctaUrl") ?? err("comingSoonLink")} placeholder={draft.url || "https://"} dir="ltr" inputMode="url" />
              ) : null}
              <TextInput label={t("rv.prod.cta.label")} optional={t("rv.common.optional")} helper={t("rv.prod.cta.labelHelp")} value={draft.ctaLabel} onChange={(e) => set({ ctaLabel: e.target.value })} maxLength={300} />
              <ListEditor label={t("rv.prod.doNotClaim")} helper={t("rv.prod.doNotClaimHelp")} values={draft.doNotClaim} onChange={(doNotClaim) => set({ doNotClaim })} addLabel={t("rv.prod.doNotClaim.add")} />
            </div>
          </Section>
          ) : null}
        </div>

        <aside className="rv-split__side" aria-label={t("rv.pe.aside.title")}>
          <Glass level={2} className="rv-card" data-testid="product-checklist">
            <div className="rv-card__head" style={{ alignItems: "center" }}>
              <h3>{t("rv.pe.aside.title")}</h3>
              <strong className="tabular">{score} %</strong>
            </div>
            <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score}>
              <div className="rv-progress__fill" style={{ width: `${score}%` }} />
            </div>
            <ul className="rv-check">
              {SECTIONS.map((key) => {
                const own = checks.filter((c) => CHECK_SECTION[c.key] === key);
                const ok = own.length > 0 && own.every((c) => c.ok);
                return (
                  <li key={key} data-ok={ok || undefined}>
                    <button type="button" className="rv-check__nav" aria-current={section === key ? "true" : undefined} onClick={() => setSection(key)}>
                      <span className="rv-check__mark" aria-hidden>
                        {ok ? <Check size={13} strokeWidth={3} /> : <Plus size={13} />}
                      </span>
                      <span className="rv-check__text">
                        <span className="rv-check__title">{t(`rv.pe.s.${key}` as MessageKey)}</span>
                        {own.length > 0 ? <span className="c-3 t-body-sm">{own.map((c) => c.label).join(" · ")}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Glass>
        </aside>
      </div>

      <div className="rv-sticky-save">
        <span className="c-2 t-body-sm">{dirty || isNew ? t("rv.common.unsaved") : t("rv.common.upToDate")}</span>
        <div className="rv-actions">
          {dirty && !isNew ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setDraft(initial);
              }}
              disabled={busy}
            >
              {t("rv.common.discard")}
            </Button>
          ) : null}
          <Button type="button" variant="primary" onClick={() => void onSave()} loading={busy} disabled={!dirty && !isNew}>
            {t("rv.common.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
