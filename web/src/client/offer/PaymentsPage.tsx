/** Payment methods: how a buyer can pay, and what the agent tells them. Two kinds, both the
 *  client's own: an *online* method is a link to the client's payment or order page (the buyer pays
 *  there; Revansa is not a payment gateway), an *offline* one is a set of instructions (a bank
 *  transfer, cash, a deposit). Whichever the buyer picks, the agent gives the details exactly as
 *  written here, and a payment is "paid" only once the client has checked it by hand. */

import { useState } from "react";
import { CreditCard, Landmark, Link2, Plus } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput, Textarea } from "../../ui/Controls";
import { Card, Empty, FailureNote, Note, type Failure } from "../kit";
import { defaultLanguage, localizedText } from "../settingsModel";
import { uniqueSlug, slugify } from "./productModel";
import {
  applyMethodForm,
  blankInstruction,
  blankMethod,
  disableJourney,
  enableJourney,
  forgetMethod,
  formOfMethod,
  journeyOf,
  methodProblems,
  methodsOf,
  putMethod,
  removeMethod,
  type MethodForm,
  type MethodKind,
  type MethodProblem,
} from "./paymentModel";
import { useOffer } from "./OfferLayout";

function MethodEditor({ initial, existing, onDone, onCancel }: { initial: MethodForm; existing: boolean; onDone: (startedJourney: boolean) => void; onCancel: () => void }) {
  const { t } = useI18n();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const [draft, setDraft] = useState(initial);
  const [problems, setProblems] = useState<MethodProblem[]>([]);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const online = draft.kind === "online";
  const err = (p: MethodProblem) => (problems.includes(p) ? t(`rv.pay.err.${p}` as MessageKey) : undefined);
  const patchInstruction = (i: number, patch: Partial<MethodForm["instructions"][number]>) =>
    setDraft({ ...draft, instructions: draft.instructions.map((x, n) => (n === i ? { ...x, ...patch } : x)) });

  async function submit() {
    const found = methodProblems(draft);
    setProblems(found);
    if (found.length > 0) return;
    const taken = methodsOf(commercial).map((m) => String(m.id));
    const id = draft.id || uniqueSlug(slugify(draft.label), taken, online ? "paiement-en-ligne" : "paiement-hors-ligne");
    const original = methodsOf(commercial).find((m) => m.id === id);
    const method = applyMethodForm(original, { ...draft, id }, lang);
    setBusy(true);
    setFailure(null);
    // The first payment method is useless unless the agent may hand it over: the purchase journey
    // starts with it (unless Revansa set the client's own sales process), and the screen says so.
    const firstOne = !existing && methodsOf(commercial).length === 0 && journeyOf(commercial) === "off";
    const base = putMethod(commercial, method);
    const problem = await save({ commercial: firstOne ? enableJourney(base) : base });
    setBusy(false);
    if (problem) setFailure(problem);
    else onDone(firstOne);
  }

  return (
    <Card aria-label={t(existing ? "rv.pay.edit" : "rv.pay.new")}>
      <div className="rv-card__head">
        <h3>{t(existing ? "rv.pay.edit" : online ? "rv.pay.newOnline" : "rv.pay.newOffline")}</h3>
      </div>
      <p className="rv-card__sub">{t(online ? "rv.pay.onlineHelp" : "rv.pay.offlineHelp")}</p>
      {failure ? <FailureNote failure={failure} /> : null}
      <TextInput label={t("rv.pay.label")} helper={t("rv.pay.labelHelp")} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} error={err("label")} maxLength={300} />

      {draft.instructions.map((ins, i) => (
        <div className="rv-repeat" key={i}>
          {online ? (
            <TextInput label={t("rv.pay.url")} helper={t("rv.pay.urlHelp")} value={ins.url} onChange={(e) => patchInstruction(i, { url: e.target.value })} error={err("url") ?? err("onlineNeedsLink")} placeholder="https://" dir="ltr" inputMode="url" />
          ) : null}
          <Textarea
            label={t(online ? "rv.pay.textOnline" : "rv.pay.text")}
            helper={t(online ? "rv.pay.textOnlineHelp" : "rv.pay.textHelp")}
            value={ins.text}
            onChange={(e) => patchInstruction(i, { text: e.target.value })}
            error={err("offlineNeedsDetails")}
            rows={3}
            maxLength={1500}
          />
          {!online ? (
            <fieldset className="rv-fieldset">
              <legend className="t-label c-2">{t("rv.pay.fields")}</legend>
              <p className="c-3 t-body-sm">{t("rv.pay.fieldsHelp")}</p>
              {ins.fields.map((f, n) => (
                <div className="rv-grid-2" key={n}>
                  <input className="input well" aria-label={t("rv.pay.fieldLabel")} placeholder={t("rv.pay.fieldLabel")} value={f.label} maxLength={200} dir="auto" onChange={(e) => patchInstruction(i, { fields: ins.fields.map((x, k) => (k === n ? { ...x, label: e.target.value } : x)) })} />
                  <div className="row">
                    <input className="input well" aria-label={t("rv.pay.fieldValue")} placeholder={t("rv.pay.fieldValue")} value={f.value} maxLength={200} dir="auto" spellCheck={false} onChange={(e) => patchInstruction(i, { fields: ins.fields.map((x, k) => (k === n ? { ...x, value: e.target.value } : x)) })} />
                    <Button type="button" variant="ghost" size="sm" onClick={() => patchInstruction(i, { fields: ins.fields.filter((_, k) => k !== n) })}>
                      {t("rv.common.remove")}
                    </Button>
                  </div>
                </div>
              ))}
              {ins.fields.length < 20 ? (
                <div>
                  <Button type="button" size="sm" onClick={() => patchInstruction(i, { fields: [...ins.fields, { label: "", value: "" }] })}>
                    {t("rv.pay.fieldAdd")}
                  </Button>
                </div>
              ) : null}
            </fieldset>
          ) : null}
          <div className="rv-grid-2">
            <TextInput label={t("rv.pay.countries")} helper={t("rv.pay.countriesHelp")} optional={t("rv.common.optional")} value={ins.countries} onChange={(e) => patchInstruction(i, { countries: e.target.value })} />
            <TextInput label={t("rv.pay.currencies")} helper={t("rv.pay.currenciesHelp")} optional={t("rv.common.optional")} value={ins.currencies} onChange={(e) => patchInstruction(i, { currencies: e.target.value })} error={err("currency")} dir="ltr" placeholder="TND, EUR" />
          </div>
          {draft.instructions.length > 1 ? (
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setDraft({ ...draft, instructions: draft.instructions.filter((_, n) => n !== i) })}>
                {t("rv.pay.removeVariant")}
              </Button>
            </div>
          ) : null}
        </div>
      ))}
      {draft.instructions.length < 10 ? (
        <div>
          <Button type="button" size="sm" onClick={() => setDraft({ ...draft, instructions: [...draft.instructions, blankInstruction(`v${draft.instructions.length + 1}`)] })}>
            {t("rv.pay.addVariant")}
          </Button>
          <p className="c-3 t-body-sm" style={{ marginBlockStart: 6 }}>
            {t("rv.pay.variantHelp")}
          </p>
        </div>
      ) : null}

      <Textarea label={t("rv.pay.note")} helper={t("rv.pay.noteHelp")} value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} rows={2} maxLength={3000} />
      <Note tone="info">{t("rv.pay.manualNote")}</Note>

      <div className="rv-actions">
        <Button variant="primary" onClick={() => void submit()} loading={busy}>
          {t("rv.common.save")}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t("rv.common.cancel")}
        </Button>
      </div>
    </Card>
  );
}

export function PaymentsPage() {
  const { t } = useI18n();
  const { commercial, save } = useOffer();
  const lang = defaultLanguage(commercial);
  const methods = methodsOf(commercial);
  const [editing, setEditing] = useState<{ form: MethodForm; existing: boolean } | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<null | "plain" | "journey">(null);
  const journey = journeyOf(commercial);
  const activeMethods = methods.filter((m) => m.status !== "inactive").length;

  const start = (kind: MethodKind) => {
    setSaved(null);
    setEditing({ form: blankMethod("", kind), existing: false });
  };

  async function toggle(id: string, status: "active" | "inactive") {
    setBusy(id);
    setFailure(null);
    const next = putMethod(commercial, { ...methods.find((m) => m.id === id)!, status });
    const problem = await save({ commercial: next });
    setBusy(null);
    if (problem) setFailure(problem);
  }

  async function switchJourney(turnOn: boolean) {
    setBusy("journey");
    setFailure(null);
    setSaved(null);
    const problem = await save({ commercial: turnOn ? enableJourney(commercial) : disableJourney(commercial) });
    setBusy(null);
    if (problem) setFailure(problem);
    else setSaved("plain");
  }

  async function remove(id: string) {
    setBusy(id);
    setFailure(null);
    const problem = await save({ commercial: forgetMethod(removeMethod(commercial, id), id) });
    setBusy(null);
    if (problem) setFailure(problem);
  }

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.pay.title")}</h3>
          <p>{t("rv.pay.intro")}</p>
        </div>
        <div className="rv-actions">
          <Button leading={<Link2 size={16} aria-hidden />} onClick={() => start("online")} disabled={editing !== null}>
            {t("rv.pay.addOnline")}
          </Button>
          <Button leading={<Landmark size={16} aria-hidden />} onClick={() => start("offline")} disabled={editing !== null}>
            {t("rv.pay.addOffline")}
          </Button>
        </div>
      </div>

      <Note tone="info" title={t("rv.pay.how.title")}>
        {t("rv.pay.how.text")}
      </Note>
      {failure ? <FailureNote failure={failure} /> : null}
      {saved ? <Note tone="success">{t(saved === "journey" ? "rv.pay.journey.started" : "rv.common.saved")}</Note> : null}

      {methods.length > 0 ? (
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

      {editing ? (
        <MethodEditor
          key={editing.form.id || "new"}
          initial={editing.form}
          existing={editing.existing}
          onCancel={() => setEditing(null)}
          onDone={(startedJourney) => {
            setEditing(null);
            setSaved(startedJourney ? "journey" : "plain");
          }}
        />
      ) : null}

      {methods.length === 0 && !editing ? (
        <Card>
          <Empty
            icon={<CreditCard size={24} />}
            title={t("rv.pay.empty.title")}
            text={t("rv.pay.empty.text")}
            action={
              <div className="rv-actions">
                <Button variant="primary" leading={<Plus size={16} aria-hidden />} onClick={() => start("online")}>
                  {t("rv.pay.addOnline")}
                </Button>
                <Button leading={<Plus size={16} aria-hidden />} onClick={() => start("offline")}>
                  {t("rv.pay.addOffline")}
                </Button>
              </div>
            }
          />
        </Card>
      ) : null}

      {methods.length > 0 ? (
        <Card flush aria-label={t("rv.pay.title")}>
          <ul className="rv-list">
            {methods.map((m) => {
              const id = String(m.id);
              const form = formOfMethod(m, lang);
              const active = form.status === "active";
              const scope = form.instructions.flatMap((i) => [i.countries, i.currencies]).filter((s) => s !== "");
              return (
                <li key={id} className="rv-product" data-testid={`method-${id}`}>
                  <span className="rv-channel__icon" aria-hidden>
                    {form.kind === "online" ? <Link2 size={20} /> : <Landmark size={20} />}
                  </span>
                  <div className="rv-row__main">
                    <span className="rv-row__title" dir="auto">
                      {localizedText(m.label, lang) || id}
                    </span>
                    <span className="rv-row__sub">
                      {t(form.kind === "online" ? "rv.pay.kind.online" : "rv.pay.kind.offline")}
                      {scope.length > 0 ? ` · ${scope.join(" · ")}` : ""}
                    </span>
                  </div>
                  <Chip tint={active ? "success" : "neutral"}>{t(active ? "rv.pay.active" : "rv.pay.inactive")}</Chip>
                  <div className="rv-actions">
                    <Button size="sm" onClick={() => { setSaved(null); setEditing({ form, existing: true }); }} disabled={editing !== null}>
                      {t("rv.common.edit")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void toggle(id, active ? "inactive" : "active")} loading={busy === id}>
                      {t(active ? "rv.pay.disable" : "rv.pay.enable")}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => void remove(id)} loading={busy === id}>
                      {t("rv.common.delete")}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
