/** One product: what it is, what it costs, how to sell it, how it is paid and which documents back
 *  it up. The form edits a copy; nothing is sent until "Save", and what is shown afterwards is what
 *  the server answered. Rules only the Brain knows come back from the server and are listed as they are. */

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { TextInput, Textarea } from "../../ui/Controls";
import { Card, FailureNote, ListEditor, Note, type Failure } from "../kit";
import { defaultLanguage, type Doc } from "../settingsModel";
import { ResourcesPanel } from "./ResourcesPanel";
import { methodsOf } from "./paymentModel";
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

const PRICED = ["fixed", "from", "range"];

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

export function ProductEditor() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const params = useParams();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const isNew = params.id === undefined || params.id === "new";
  const stored = isNew ? undefined : findProduct(commercial, params.id!);
  const methods = methodsOf(commercial).filter((m) => m.status !== "inactive");
  const takenIds = useMemo(() => productList(commercial).map((p) => String(p.id)), [commercial]);

  const initial = useMemo(() => (stored ? formOfProduct(stored, lang) : blankProduct("")), [stored, lang]);
  const [draft, setDraft] = useState<ProductForm>(initial);
  const [base, setBase] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [problems, setProblems] = useState<ProductProblem[]>([]);
  const [done, setDone] = useState(false);

  if (stored && base !== initial && JSON.stringify(base) !== JSON.stringify(initial)) {
    setBase(initial);
    setDraft(initial);
  }
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
          <Link to="/offer/products" className="link-btn">
            {t("rv.prod.backToList")}
          </Link>
        </Note>
      </div>
    );
  }

  async function onSave() {
    const found = productProblems(draft);
    setProblems(found);
    if (found.length > 0) return;
    const id = draft.id || uniqueSlug(slugify(draft.name), takenIds, "produit");
    const form = { ...draft, id };
    const product: Doc = applyProductForm(stored, form, lang);
    setBusy(true);
    setFailure(null);
    setDone(false);
    const problem = await save({ commercial: putProduct(commercial, product) });
    setBusy(false);
    if (problem) {
      setFailure(problem);
      return;
    }
    setDone(true);
    if (isNew) navigate(`/offer/products/${encodeURIComponent(id)}`, { replace: true });
  }

  const payOption = (kind: "online" | "offline") => {
    const enabled = kind === "online" ? draft.payOnline : draft.payOffline;
    const chosen = kind === "online" ? draft.payOnlineMethods : draft.payOfflineMethods;
    const available = methods.filter((m) => (m.kind === "online") === (kind === "online"));
    const toggleEnabled = (on: boolean) => set(kind === "online" ? { payOnline: on } : { payOffline: on });
    const setChosen = (next: string[]) => set(kind === "online" ? { payOnlineMethods: next } : { payOfflineMethods: next });
    return (
      <div className="rv-repeat">
        <label className="rv-checkbox">
          <input type="checkbox" checked={enabled} onChange={(e) => toggleEnabled(e.target.checked)} />
          <span className="rv-checkbox__text">
            <strong>{t(`rv.prod.pay.${kind}` as MessageKey)}</strong>
            <span className="c-3 t-body-sm">{t(`rv.prod.pay.${kind}Help` as MessageKey)}</span>
          </span>
        </label>
        {enabled && available.length === 0 ? (
          <Note tone="warn">
            {t("rv.prod.pay.noMethod")}{" "}
            <Link to="/offer/payments" className="link-btn">
              {t("rv.prod.pay.goPayments")}
            </Link>
          </Note>
        ) : null}
        {enabled && available.length > 0 ? (
          <div className="stack">
            <span className="c-3 t-body-sm">{t("rv.prod.pay.which")}</span>
            {available.map((m) => {
              const id = String(m.id);
              const label = typeof m.label === "string" ? m.label : (Object.values((m.label as Record<string, string>) ?? {})[0] ?? id);
              return (
                <label className="rv-checkbox" key={id}>
                  <input
                    type="checkbox"
                    checked={chosen.length === 0 || chosen.includes(id)}
                    onChange={(e) => {
                      const base = chosen.length === 0 ? available.map((a) => String(a.id)) : chosen;
                      const next = e.target.checked ? [...new Set([...base, id])] : base.filter((x) => x !== id);
                      setChosen(next.length === available.length ? [] : next);
                    }}
                  />
                  <span dir="auto">{label}</span>
                </label>
              );
            })}
          </div>
        ) : null}
        {kind === "offline" && enabled ? (
          <label className="rv-checkbox">
            <input type="checkbox" checked={draft.receiptRequired} onChange={(e) => set({ receiptRequired: e.target.checked })} />
            <span className="rv-checkbox__text">
              <strong>{t("rv.prod.pay.receipt")}</strong>
              <span className="c-3 t-body-sm">{t("rv.prod.pay.receiptHelp")}</span>
            </span>
          </label>
        ) : null}
      </div>
    );
  };

  return (
    <div className="rv-page">
      <div>
        <Link to="/offer/products" className="link-btn">
          <ArrowLeft size={14} className="mirror" aria-hidden /> {t("rv.prod.backToList")}
        </Link>
      </div>
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2" dir="auto">
            {isNew ? t("rv.prod.new") : draft.name || draft.id}
          </h3>
        </div>
      </div>

      {failure ? <FailureNote failure={failure} /> : null}
      {done ? <Note tone="success">{t("rv.common.saved")}</Note> : null}

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.basics")}</h3>
        </div>
        <TextInput label={t("rv.prod.name")} value={draft.name} onChange={(e) => set({ name: e.target.value })} error={err("name")} maxLength={300} />
        <TextInput label={t("rv.prod.summary")} helper={t("rv.prod.summaryHelp")} value={draft.summary} onChange={(e) => set({ summary: e.target.value })} error={err("summary")} maxLength={300} />
        <Textarea label={t("rv.prod.description")} helper={t("rv.prod.descriptionHelp")} value={draft.description} onChange={(e) => set({ description: e.target.value })} rows={6} maxLength={3000} />
        <div className="rv-grid-2">
          <Select
            label={t("rv.prod.status")}
            value={draft.status}
            onChange={(v) => set({ status: v })}
            options={PRODUCT_STATUSES.map((s) => ({ value: s, label: t(`rv.prod.status.${s}` as MessageKey) }))}
            helper={t(`rv.prod.statusHelp.${draft.status}` as MessageKey)}
          />
          <TextInput label={t("rv.prod.url")} helper={t("rv.prod.urlHelp")} optional={t("rv.common.optional")} value={draft.url} onChange={(e) => set({ url: e.target.value })} error={err("url")} placeholder="https://" dir="ltr" inputMode="url" />
        </div>
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.price")}</h3>
        </div>
        <p className="rv-card__sub">{t("rv.prod.price.help")}</p>
        <PriceFields
          model={draft.priceModel}
          amount={draft.amount}
          maxAmount={draft.maxAmount}
          currency={draft.currency}
          onChange={(p) => set({ priceModel: p.model ?? draft.priceModel, amount: p.amount ?? draft.amount, maxAmount: p.maxAmount ?? draft.maxAmount, currency: p.currency ?? draft.currency })}
          error={err("price")}
        />
        <TextInput label={t("rv.prod.price.note")} optional={t("rv.common.optional")} value={draft.priceNote} onChange={(e) => set({ priceNote: e.target.value })} maxLength={300} />
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
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.pitch")}</h3>
        </div>
        <p className="rv-card__sub">{t("rv.prod.pitch.help")}</p>
        <TextInput label={t("rv.prod.proposition")} helper={t("rv.prod.propositionHelp")} optional={t("rv.common.optional")} value={draft.proposition} onChange={(e) => set({ proposition: e.target.value })} maxLength={300} />
        <ListEditor label={t("rv.prod.benefits")} helper={t("rv.prod.benefitsHelp")} values={draft.benefits} onChange={(benefits) => set({ benefits })} addLabel={t("rv.prod.benefits.add")} maxItems={10} />
        <ListEditor label={t("rv.prod.differentiators")} helper={t("rv.prod.differentiatorsHelp")} values={draft.differentiators} onChange={(differentiators) => set({ differentiators })} addLabel={t("rv.prod.differentiators.add")} maxItems={10} />
        <div className="rv-grid-2">
          <ListEditor label={t("rv.prod.suitedFor")} helper={t("rv.prod.suitedForHelp")} values={draft.suitedFor} onChange={(suitedFor) => set({ suitedFor })} addLabel={t("rv.prod.suitedFor.add")} />
          <ListEditor label={t("rv.prod.notFor")} helper={t("rv.prod.notForHelp")} values={draft.notFor} onChange={(notFor) => set({ notFor })} addLabel={t("rv.prod.notFor.add")} />
        </div>
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
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.objections")}</h3>
        </div>
        <p className="rv-card__sub">{t("rv.prod.objections.help")}</p>
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
      </Card>

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.buy")}</h3>
        </div>
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

        <div className="rv-editor__section">
          <h4>{t("rv.prod.pay.title")}</h4>
          <p className="rv-card__sub">{t("rv.prod.pay.help")}</p>
          <Select
            label={t("rv.prod.pay.mode")}
            value={draft.payMode}
            onChange={(v) => set({ payMode: v })}
            options={[
              { value: "all", label: t("rv.prod.pay.mode.all") },
              { value: "custom", label: t("rv.prod.pay.mode.custom") },
            ]}
          />
          {draft.payMode === "custom" ? (
            <>
              {payOption("online")}
              {payOption("offline")}
            </>
          ) : null}
        </div>
      </Card>

      {stored ? <ResourcesPanel productId={String(stored.id)} /> : (
        <Card>
          <div className="rv-card__head">
            <h3>{t("rv.res.title")}</h3>
          </div>
          <Note tone="info">{t("rv.res.saveFirst")}</Note>
        </Card>
      )}

      <Card>
        <div className="rv-card__head">
          <h3>{t("rv.prod.s.limits")}</h3>
        </div>
        <ListEditor label={t("rv.prod.doNotClaim")} helper={t("rv.prod.doNotClaimHelp")} values={draft.doNotClaim} onChange={(doNotClaim) => set({ doNotClaim })} addLabel={t("rv.prod.doNotClaim.add")} />
      </Card>

      <div className="rv-sticky-save">
        <span className="c-2 t-body-sm">{dirty || isNew ? t("rv.common.unsaved") : t("rv.common.upToDate")}</span>
        <div className="rv-actions">
          {dirty && !isNew ? (
            <Button type="button" variant="ghost" onClick={() => setDraft(initial)} disabled={busy}>
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
