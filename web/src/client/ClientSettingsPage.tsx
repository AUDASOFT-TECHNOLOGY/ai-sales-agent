/** The client's business settings, read from and written to the server (`GET/PUT /client/v1/settings`).
 *  Nothing is kept in this browser: what is shown is what the server holds, a save is announced only
 *  once the server has answered with the new version, and a refusal shows the server's reasons.
 *  The Brain's document is replaced as a whole by a write, so the parts this screen does not edit
 *  travel back unchanged (see `settingsModel.ts`). */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { TextInput, Textarea, ToggleChips } from "../ui/Controls";
import { Glass } from "../ui/Glass";
import { useClientAuth } from "./ClientAuthContext";
import { clientSaveSettings, clientSettings, type SettingsState } from "./api";
import {
  PRODUCT_STATUSES,
  WEEKDAYS,
  addProduct,
  defaultLanguage,
  isLocalized,
  localizedText,
  productsOf,
  setBusiness,
  setLanguages,
  setProduct,
  setToneStyle,
  settingsToSend,
  splitList,
  type Doc,
} from "./settingsModel";
import { settingsFailureOf, type SettingsFailure } from "./settingsFailure";

interface Draft {
  timezone: string;
  optOut: string;
  dailyAt: string;
  days: string[];
  commercial: Doc;
}

function draftOf(state: SettingsState): Draft {
  const s = state.settings;
  return {
    timezone: s.timezone ?? "",
    optOut: (s.opt_out_keywords ?? []).join(", "),
    dailyAt: s.schedule?.daily_at ?? "",
    days: s.schedule?.days ?? [],
    commercial: (s.commercial as Doc | undefined) ?? {},
  };
}

function obj(value: unknown): Doc {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Doc) : {};
}

const SLUG = /^[a-z0-9][a-z0-9_-]{0,63}$/;

function Failure({ failure }: { failure: SettingsFailure }) {
  const { t } = useI18n();
  return (
    <p className="admin-alert admin-alert--danger" role="alert">
      {t(failure.key)}
      {failure.detail ? ` (${failure.detail})` : ""}
    </p>
  );
}

export function ClientSettingsPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [state, setState] = useState<SettingsState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadFailure, setLoadFailure] = useState<SettingsFailure | null>(null);
  const [failure, setFailure] = useState<SettingsFailure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [busy, setBusy] = useState<null | "load" | "save">(null);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [newSummary, setNewSummary] = useState("");
  const [newError, setNewError] = useState<MessageKey | null>(null);

  const apply = useCallback(
    (result: PromiseSettledResult<SettingsState>) => {
      if (result.status === "fulfilled") {
        setState(result.value);
        setDraft(draftOf(result.value));
        setLoadFailure(null);
        return;
      }
      const problem = settingsFailureOf(result.reason);
      setLoadFailure(problem);
      if (problem.signedOut) void logout();
    },
    [logout],
  );

  const load = useCallback(async () => apply((await Promise.allSettled([clientSettings()]))[0]!), [apply]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([clientSettings()]).then(([result]) => {
      if (!cancelled) apply(result!);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  async function reload() {
    setFailure(null);
    setNotice(null);
    setBusy("load");
    await load();
    setBusy(null);
  }

  if (!state || !draft) {
    return (
      <div className="page page--focus">
        <div className="page-head">
          <h2 className="t-h1">{t("settings.title")}</h2>
        </div>
        {loadFailure ? (
          <>
            <Failure failure={loadFailure} />
            <div>
              <Button onClick={() => void reload()} loading={busy === "load"}>
                {t("lanes.retry")}
              </Button>
            </div>
          </>
        ) : (
          <p className="c-3" role="status">
            {t("settings.loading")}
          </p>
        )}
      </div>
    );
  }

  const current = draft;
  const commercial = current.commercial;
  const lang = defaultLanguage(commercial);
  const business = obj(commercial.business);
  const languages = obj(obj(commercial.communication).languages);
  const supported = Array.isArray(languages.supported) ? (languages.supported as string[]) : [];
  const tone = obj(obj(commercial.communication).tone);
  const products = productsOf(commercial);
  const working = busy !== null;

  function edit(next: Partial<Draft>) {
    setNotice(null);
    setDraft({ ...current, ...next });
  }
  function editCommercial(next: Doc) {
    edit({ commercial: next });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFailure(null);
    setNotice(null);
    setBusy("save");
    try {
      // The screen then shows what the server answered, not what was typed.
      const saved = await clientSaveSettings(state!.version, settingsToSend(current));
      setState(saved);
      setDraft(draftOf(saved));
      setNotice("settings.saved");
    } catch (err) {
      const problem = settingsFailureOf(err);
      setFailure(problem);
      if (problem.signedOut) void logout();
      // The settings moved on the server: what is on screen is read again (the typed changes are
      // replaced by what is now in force, and the message above says so).
      if (problem.reload) await load();
    }
    setBusy(null);
  }

  function addNewProduct() {
    if (!SLUG.test(newId.trim())) return setNewError("settings.products.badId");
    if (products.some((p) => p.id === newId.trim())) return setNewError("settings.products.duplicate");
    if (newName.trim() === "" || newSummary.trim() === "") return setNewError("settings.products.incomplete");
    setNewError(null);
    editCommercial(addProduct(commercial, { id: newId.trim(), status: "active", name: newName.trim(), summary: newSummary.trim() }));
    setNewId("");
    setNewName("");
    setNewSummary("");
  }

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{t("settings.title")}</h2>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void reload()} loading={busy === "load"} disabled={working && busy !== "load"}>
          {t("lanes.refresh")}
        </Button>
      </div>
      <p className="c-2">{t("settings.intro")}</p>
      <p className="c-3 t-body-sm" data-testid="settings-version">
        {state.version === null
          ? t("settings.version.none")
          : t("settings.version", {
              n: state.version,
              by: t(state.updated_by === "administrator" ? "settings.by.administrator" : "settings.by.client"),
              date: state.updated_at ? d(state.updated_at, { dateStyle: "medium", timeStyle: "short" }) : "—",
            })}
      </p>
      {loadFailure ? <Failure failure={loadFailure} /> : null}
      {failure ? <Failure failure={failure} /> : null}
      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {t(notice)}
        </p>
      ) : null}

      <form className="stack stack--lg" onSubmit={(e) => void save(e)} aria-label={t("settings.title")}>
        <Glass level={1} className="client-card" aria-label={t("settings.business")}>
          <h3 className="t-h2">{t("settings.business")}</h3>
          <TextInput label={t("settings.business.name")} value={String(business.name ?? "")} onChange={(e) => editCommercial(setBusiness(commercial, { name: e.target.value }))} disabled={working} />
          <TextInput label={t("settings.business.website")} value={String(business.website ?? "")} onChange={(e) => editCommercial(setBusiness(commercial, { website: e.target.value }))} disabled={working} />
          <Textarea label={t("settings.business.summary")} value={localizedText(business.summary, lang)} onChange={(e) => editCommercial(setBusiness(commercial, { summary: e.target.value }))} disabled={working} />
        </Glass>

        <Glass level={1} className="client-card" aria-label={t("settings.communication")}>
          <h3 className="t-h2">{t("settings.communication")}</h3>
          <TextInput label={t("settings.languages.supported")} helper={t("settings.languages.help")} value={supported.join(", ")} onChange={(e) => editCommercial(setLanguages(commercial, { supported: splitList(e.target.value) }))} disabled={working} />
          <TextInput label={t("settings.languages.default")} value={lang} onChange={(e) => editCommercial(setLanguages(commercial, { default: e.target.value.trim() }))} disabled={working} />
          <TextInput label={t("settings.tone")} value={String(tone.style ?? "")} onChange={(e) => editCommercial(setToneStyle(commercial, e.target.value))} disabled={working} />
        </Glass>

        <Glass level={1} className="client-card" aria-label={t("settings.schedule")}>
          <h3 className="t-h2">{t("settings.schedule")}</h3>
          <TextInput label={t("settings.timezone")} helper={t("settings.timezone.help")} value={current.timezone} onChange={(e) => edit({ timezone: e.target.value })} disabled={working} />
          <TextInput label={t("settings.dailyAt")} helper={t("settings.dailyAt.help")} value={current.dailyAt} onChange={(e) => edit({ dailyAt: e.target.value })} disabled={working} />
          <ToggleChips label={t("settings.days")} options={WEEKDAYS.map((day) => ({ value: day, label: t(`settings.day.${day}` as MessageKey) }))} value={current.days} onChange={(days) => edit({ days })} />
          <TextInput label={t("settings.optOut")} helper={t("settings.optOut.help")} value={current.optOut} onChange={(e) => edit({ optOut: e.target.value })} disabled={working} />
        </Glass>

        <Glass level={1} className="client-card" aria-label={t("settings.products")}>
          <h3 className="t-h2">{t("settings.products")}</h3>
          <p className="c-3">{t("settings.products.help")}</p>
          {products.length === 0 ? (
            <p className="c-3" data-testid="products-empty">
              {t("settings.products.empty")}
            </p>
          ) : (
            <ul className="stack stack--lg">
              {products.map((p) => (
                <li key={p.id} data-testid={`product-${p.id}`}>
                  <div className="stack">
                    <p className="t-label" dir="auto">
                      {p.id}
                    </p>
                    <TextInput
                      label={t("settings.products.name")}
                      helper={isLocalized(commercial, p.id, "name") ? t("settings.products.localized", { lang }) : undefined}
                      value={p.name}
                      onChange={(e) => editCommercial(setProduct(commercial, p.id, { name: e.target.value }))}
                      disabled={working}
                    />
                    <TextInput
                      label={t("settings.products.summary")}
                      helper={isLocalized(commercial, p.id, "summary") ? t("settings.products.localized", { lang }) : undefined}
                      value={p.summary}
                      onChange={(e) => editCommercial(setProduct(commercial, p.id, { summary: e.target.value }))}
                      disabled={working}
                    />
                    <label className="t-label">
                      {t("settings.products.status")}{" "}
                      <select value={p.status} onChange={(e) => editCommercial(setProduct(commercial, p.id, { status: e.target.value }))} disabled={working}>
                        {PRODUCT_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {t(`settings.products.status.${s}` as MessageKey)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <fieldset className="stack" disabled={working}>
            <legend className="t-label">{t("settings.products.add")}</legend>
            <TextInput label={t("settings.products.id")} helper={t("settings.products.id.help")} value={newId} onChange={(e) => setNewId(e.target.value)} />
            <TextInput label={t("settings.products.newName")} value={newName} onChange={(e) => setNewName(e.target.value)} />
            <TextInput label={t("settings.products.newSummary")} value={newSummary} onChange={(e) => setNewSummary(e.target.value)} />
            {newError ? (
              <p className="admin-alert admin-alert--danger" role="alert">
                {t(newError)}
              </p>
            ) : null}
            <div>
              <Button type="button" leading={<Plus size={16} aria-hidden />} onClick={addNewProduct}>
                {t("settings.products.addButton")}
              </Button>
            </div>
          </fieldset>
        </Glass>

        <div>
          <Button type="submit" variant="primary" loading={busy === "save"} disabled={working && busy !== "save"}>
            {t("settings.save")}
          </Button>
        </div>
      </form>
    </div>
  );
}
