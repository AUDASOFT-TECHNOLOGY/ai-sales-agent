/** The offline payment methods, once for every product: one entry per country (name, description,
 *  payment currency). An online payment is not set here: it is the link of the product's own page,
 *  filled in on the product. The agent gives the details exactly as written here, and a payment is
 *  "paid" only once the client has checked it by hand.
 *
 *  Each country entry is stored as an offline method of its own, scoped to that country, so the
 *  Brain needs nothing new to offer the right one. The payment currency is kept in the method's
 *  metadata: it is not a scope, because a Tunisian buyer pays in dinars a product priced in dollars. */

import { useState } from "react";
import { CreditCard, Landmark, Plus } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput, Textarea } from "../../ui/Controls";
import { Card, FailureNote, Note, type Failure } from "../kit";
import { defaultLanguage, localizedText, type Doc } from "../settingsModel";
import { slugify, uniqueSlug } from "./productModel";
import {
  applyMethodForm,
  blankInstruction,
  blankMethod,
  disableJourney,
  enableJourney,
  forgetMethod,
  formOfMethod,
  journeyOf,
  methodsOf,
  putMethod,
  removeMethod,
} from "./paymentModel";
import { useOffer } from "./OfferLayout";

/** A product's own payment link lives in a method named `pay-<product>`: not listed here. */
const OWN = /^pay-/;
const CURRENCY_KEY = "pay_currency";

interface Entry {
  id: string;
  country: string;
  name: string;
  currency: string;
  text: string;
}
type EntryProblem = "country" | "name" | "description" | "currency";

function currencyOf(method: Doc): string {
  const meta = method.metadata;
  const value = meta && typeof meta === "object" ? (meta as Record<string, unknown>)[CURRENCY_KEY] : undefined;
  return typeof value === "string" ? value : "";
}

function entryOf(method: Doc, lang: string): Entry {
  const form = formOfMethod(method, lang);
  const first = form.instructions[0] ?? blankInstruction("main");
  return { id: form.id, country: first.countries, name: form.label, currency: currencyOf(method), text: first.text };
}

function entryProblems(e: Entry): EntryProblem[] {
  const found: EntryProblem[] = [];
  if (e.country.trim() === "") found.push("country");
  if (e.name.trim() === "") found.push("name");
  if (e.text.trim() === "") found.push("description");
  if (e.currency.trim() !== "" && !/^[A-Za-z]{3}$/.test(e.currency.trim())) found.push("currency");
  return found;
}

function EntryEditor({ initial, onDone, onCancel }: { initial: Entry; onDone: (startedJourney: boolean) => void; onCancel: () => void }) {
  const { t } = useI18n();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const [draft, setDraft] = useState(initial);
  const [problems, setProblems] = useState<EntryProblem[]>([]);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const err = (p: EntryProblem) => (problems.includes(p) ? t(`rv.pay2.err.${p}` as MessageKey) : undefined);

  async function submit() {
    const found = entryProblems(draft);
    setProblems(found);
    if (found.length > 0) return;
    const all = methodsOf(commercial);
    const id = draft.id || uniqueSlug(slugify(`${draft.name}-${draft.country}`), all.map((m) => String(m.id)), "paiement-hors-ligne");
    const original = all.find((m) => m.id === id);
    const form = original ? formOfMethod(original, lang) : blankMethod(id, "offline");
    form.status = "active";
    form.kind = "offline";
    form.label = draft.name.trim();
    const first = form.instructions[0] ?? blankInstruction("main");
    form.instructions = [{ ...first, countries: draft.country.trim(), currencies: "", text: draft.text }, ...form.instructions.slice(1)];
    const method = applyMethodForm(original, { ...form, id }, lang);
    const metadata: Record<string, unknown> = { ...((method.metadata as Record<string, unknown> | undefined) ?? {}) };
    if (draft.currency.trim() !== "") metadata[CURRENCY_KEY] = draft.currency.trim().toUpperCase();
    else delete metadata[CURRENCY_KEY];
    if (Object.keys(metadata).length > 0) method.metadata = metadata;
    else delete method.metadata;
    setBusy(true);
    setFailure(null);
    const firstOne = all.length === 0 && journeyOf(commercial) === "off";
    const base = putMethod(commercial, method);
    const problem = await save({ commercial: firstOne ? enableJourney(base) : base });
    setBusy(false);
    if (problem) setFailure(problem);
    else onDone(firstOne);
  }

  return (
    <div className="rv-repeat">
      {failure ? <FailureNote failure={failure} /> : null}
      <div className="rv-grid-2">
        <TextInput label={t("rv.pay2.country")} helper={t("rv.pay2.countryHelp")} value={draft.country} onChange={(e) => setDraft({ ...draft, country: e.target.value })} error={err("country")} maxLength={100} />
        <TextInput label={t("rv.pay2.currency")} helper={t("rv.pay2.currencyHelp")} value={draft.currency} onChange={(e) => setDraft({ ...draft, currency: e.target.value.toUpperCase() })} error={err("currency")} maxLength={3} placeholder="TND" dir="ltr" />
      </div>
      <TextInput label={t("rv.pay2.name")} helper={t("rv.pay2.nameHelp")} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} error={err("name")} maxLength={300} />
      <Textarea label={t("rv.pay2.description")} helper={t("rv.pay2.descriptionHelp")} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} error={err("description")} rows={4} maxLength={1500} />
      <div className="rv-actions">
        <Button variant="primary" onClick={() => void submit()} loading={busy}>
          {t("rv.common.save")}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t("rv.common.cancel")}
        </Button>
      </div>
    </div>
  );
}

export function PaymentsPage() {
  const { t } = useI18n();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const all = methodsOf(commercial);
  const offline = all.filter((m) => !OWN.test(String(m.id)) && m.kind !== "online");
  const [editing, setEditing] = useState<Entry | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<null | "plain" | "journey">(null);
  const journey = journeyOf(commercial);
  const activeMethods = all.filter((m) => m.status !== "inactive").length;

  async function commit(next: Doc, key: string) {
    setBusy(key);
    setFailure(null);
    setSaved(null);
    const problem = await save({ commercial: next });
    setBusy(null);
    if (problem) setFailure(problem);
    else setSaved("plain");
  }

  async function switchJourney(turnOn: boolean) {
    await commit(turnOn ? enableJourney(commercial) : disableJourney(commercial), "journey");
  }

  async function remove(id: string) {
    await commit(forgetMethod(removeMethod(commercial, id), id), id);
  }

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.pay.title")}</h3>
          <p>{t("rv.pay.intro")}</p>
        </div>
      </div>

      {failure ? <FailureNote failure={failure} /> : null}
      {saved ? <Note tone="success">{t(saved === "journey" ? "rv.pay.journey.started" : "rv.common.saved")}</Note> : null}

      <Card aria-label={t("rv.pay2.offline.title")} data-testid="pay-offline">
        <div className="rv-card__head">
          <h3>
            <Landmark size={18} aria-hidden /> {t("rv.pay2.offline.title")}
          </h3>
          <Button leading={<Plus size={16} aria-hidden />} onClick={() => { setSaved(null); setEditing({ id: "", country: "", name: "", currency: "", text: "" }); }} disabled={editing !== null}>
            {t("rv.pay2.offline.add")}
          </Button>
        </div>
        <p className="rv-card__sub">{t("rv.pay2.offline.help")}</p>

        {editing ? (
          <EntryEditor
            key={editing.id || "new"}
            initial={editing}
            onCancel={() => setEditing(null)}
            onDone={(startedJourney) => {
              setEditing(null);
              setSaved(startedJourney ? "journey" : "plain");
            }}
          />
        ) : null}

        {offline.length === 0 && !editing ? (
          <p className="c-3 t-body-sm">{t("rv.pay2.offline.empty")}</p>
        ) : (
          <ul className="rv-list">
            {offline.map((m) => {
              const id = String(m.id);
              const e = entryOf(m, lang);
              if (editing?.id === id) return null;
              return (
                <li key={id} className="rv-product" data-testid={`method-${id}`}>
                  <span className="rv-channel__icon" aria-hidden>
                    <CreditCard size={20} />
                  </span>
                  <div className="rv-row__main">
                    <span className="rv-row__title" dir="auto">
                      {e.country || "—"}
                    </span>
                    <span className="rv-row__sub" dir="auto">
                      {localizedText(m.label, lang) || id}
                      {e.currency ? ` · ${e.currency}` : ""}
                    </span>
                  </div>
                  <div className="rv-actions">
                    <Button size="sm" onClick={() => { setSaved(null); setEditing(e); }} disabled={editing !== null}>
                      {t("rv.common.edit")}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => void remove(id)} loading={busy === id}>
                      {t("rv.common.delete")}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {all.length > 0 ? (
        <Card aria-label={t("rv.pay.journey.title")} data-testid="journey">
          <div className="rv-card__head">
            <h3>{t("rv.pay.journey.title")}</h3>
            <Chip tint={journey === "on" ? "success" : "neutral"}>{t(`rv.pay.journey.state.${journey}` as MessageKey)}</Chip>
          </div>
          <p className="rv-card__sub">{t(`rv.pay.journey.text.${journey}` as MessageKey)}</p>
          {journey === "on" && activeMethods === 0 ? <Note tone="warn">{t("rv.pay.journey.needMethod")}</Note> : null}
          {journey !== "custom" ? (
            <div>
              <Button
                variant={journey === "on" ? "secondary" : "primary"}
                loading={busy === "journey"}
                disabledReason={journey === "off" && activeMethods === 0 ? t("rv.pay.journey.needMethod") : undefined}
                onClick={() => void switchJourney(journey === "off")}
              >
                {t(journey === "on" ? "rv.pay.journey.disable" : "rv.pay.journey.enable")}
              </Button>
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
