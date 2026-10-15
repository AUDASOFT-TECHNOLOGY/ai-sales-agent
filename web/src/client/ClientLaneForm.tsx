/** Create or edit one lane. Only what the API lets a client choose is offered: a source from the
 *  platform's list, a channel the Admin allows, products from the catalog (or all of them), and for
 *  Google Sheets the spreadsheet and its tab. A lane's id and its source never change after creation.
 *  What a CRM's statuses mean stays with the Admin. Every rule (unknown product, a lane without
 *  product, a bad spreadsheet link...) is the server's: it answers with every problem and writes
 *  nothing, and this form shows them. */

import { useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { LockedField, Segmented, Switch, TextInput, ToggleChips } from "../ui/Controls";
import { Modal } from "../ui/Overlays";
import {
  ALL_PRODUCTS,
  clientCreateLane,
  clientUpdateLane,
  type Lane,
  type LaneUpdate,
  type LanesState,
  type SheetsChanges,
} from "./api";
import { lanesFailureOf, type LanesFailure } from "./lanesFailure";

const SHEETS = "google_sheets";
const HUBSPOT = "hubspot";

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x));
}

interface Props {
  state: LanesState;
  /** The lane to edit, or null to create one. */
  lane: Lane | null;
  onClose: () => void;
  /** The write succeeded: the page reads the lanes again. */
  onSaved: (done: "created" | "updated") => void;
  /** The write failed: the page reads the lanes again when the server's state moved. */
  onFailure: (failure: LanesFailure) => void;
}

export function ClientLaneForm({ state, lane, onClose, onSaved, onFailure }: Props) {
  const { t } = useI18n();
  const editing = lane !== null;
  const [id, setId] = useState("");
  const [provider, setProvider] = useState(state.source_options[0]?.key ?? "");
  const [spreadsheet, setSpreadsheet] = useState(lane?.source.google_sheets?.spreadsheet_id ?? "");
  const [tab, setTab] = useState(lane?.source.google_sheets?.prospects_tab ?? "");
  const [channel, setChannel] = useState(lane?.channel.channel ?? state.channel_options[0]?.key ?? "");
  const [all, setAll] = useState(lane ? lane.offers_all_products : true);
  const [picked, setPicked] = useState<string[]>(lane && !lane.offers_all_products ? lane.products : []);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<LanesFailure | null>(null);

  const sourceKey = lane ? lane.source.source : provider;
  const isSheets = sourceKey === SHEETS;

  // The catalog, plus any product the lane still names that the catalog no longer has: it stays visible.
  const known = state.catalog.map((p) => ({ value: p.id, label: p.name ?? p.id }));
  const extra = picked.filter((p) => !known.some((k) => k.value === p)).map((p) => ({ value: p, label: p }));
  const productOptions = [...known, ...extra];
  const products = all ? [ALL_PRODUCTS] : picked;

  // The channel of a lane may no longer be allowed: it is listed (so the lane can be read) but flagged.
  const channelOptions = state.channel_options.map((o) => ({ value: o.key, label: o.label }));
  if (lane && !channelOptions.some((o) => o.value === lane.channel.channel)) {
    channelOptions.unshift({ value: lane.channel.channel, label: `${lane.channel.label} ⚠` });
  }

  /** What changed in an edit, in the shape the API takes; null when nothing did. */
  function changes(): LaneUpdate | null {
    if (!lane) return null;
    const body: LaneUpdate = { expected_version: state.version };
    let changed = false;
    if (channel !== lane.channel.channel) {
      body.channel = channel;
      changed = true;
    }
    if (!sameSet(products, lane.offers_all_products ? [ALL_PRODUCTS] : lane.products)) {
      body.products = products;
      changed = true;
    }
    if (isSheets) {
      const sheets: SheetsChanges = {};
      const current = lane.source.google_sheets;
      if (spreadsheet.trim() !== (current?.spreadsheet_id ?? "")) sheets.spreadsheet = spreadsheet.trim();
      if (tab.trim() !== "" && tab.trim() !== (current?.prospects_tab ?? "")) sheets.prospects_tab = tab.trim();
      if (Object.keys(sheets).length > 0) {
        body.google_sheets = sheets;
        changed = true;
      }
    }
    return changed ? body : null;
  }

  const update = changes();
  const missing: MessageKey | null = editing
    ? null
    : id.trim() === ""
      ? "lanes.form.needId"
      : provider === ""
        ? "lanes.form.needSource"
        : isSheets && spreadsheet.trim() === ""
          ? "lanes.form.needSheet"
          : null;
  const reason: MessageKey | null =
    !all && picked.length === 0
      ? "lanes.form.noProduct"
      : channel === ""
        ? "lanes.form.channelNone"
        : missing ?? (editing && update === null ? "lanes.form.nothingChanged" : null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (reason) return;
    setBusy(true);
    setProblem(null);
    try {
      if (lane && update) {
        await clientUpdateLane(lane.id, update);
        onSaved("updated");
      } else {
        const name = id.trim();
        const sheets: SheetsChanges = { spreadsheet: spreadsheet.trim() };
        if (tab.trim() !== "") sheets.prospects_tab = tab.trim();
        await clientCreateLane({
          expected_version: state.version,
          id: name,
          channel,
          products,
          // One id for the lane and its source: it also prefixes the prospects' ids and never changes.
          source: { id: name, provider, ...(isSheets ? { google_sheets: sheets } : {}) },
        });
        onSaved("created");
      }
    } catch (err) {
      const failure = lanesFailureOf(err);
      setProblem(failure);
      onFailure(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => !open && !busy && onClose()}
      title={t(editing ? "lanes.form.editTitle" : "lanes.form.createTitle")}
      description={t("lanes.form.pauseWarning")}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            {t("lanes.form.cancel")}
          </Button>
          <Button type="submit" form="lane-form" variant="primary" loading={busy} disabledReason={reason ? t(reason) : undefined}>
            {t(editing ? "lanes.form.save" : "lanes.form.create")}
          </Button>
        </>
      }
    >
      <form id="lane-form" className="stack stack--lg" onSubmit={(e) => void submit(e)} noValidate>
        {lane ? (
          <LockedField label={t("lanes.form.id")} value={lane.id} helper={t("lanes.form.idLocked")} />
        ) : (
          <TextInput
            label={t("lanes.form.id")}
            name="lane_id"
            value={id}
            onChange={(e) => setId(e.target.value)}
            maxLength={64}
            helper={t("lanes.form.idHelper")}
            autoComplete="off"
            required
          />
        )}

        {lane ? (
          <LockedField label={t("lanes.form.source")} value={lane.source.label} helper={t("lanes.form.sourceLocked")} />
        ) : (
          <div className="field">
            <p className="t-label c-2">{t("lanes.form.source")}</p>
            <Segmented
              layoutId="seg-lane-source"
              label={t("lanes.form.source")}
              value={provider}
              onChange={setProvider}
              options={state.source_options.map((o) => ({ value: o.key, label: o.label }))}
            />
          </div>
        )}

        {isSheets ? (
          <div className="grid-2">
            <TextInput
              label={t("lanes.form.sheet")}
              name="spreadsheet"
              value={spreadsheet}
              onChange={(e) => setSpreadsheet(e.target.value)}
              helper={t(state.share_with ? "lanes.form.sheetHelper" : "lanes.form.sheetHelperUnknown")}
              autoComplete="off"
              required={!editing}
            />
            <TextInput
              label={t("lanes.form.tab")}
              name="prospects_tab"
              value={tab}
              onChange={(e) => setTab(e.target.value)}
              maxLength={100}
              placeholder={editing ? undefined : "Prospects"}
              helper={t("lanes.form.tabHelper")}
              autoComplete="off"
            />
          </div>
        ) : null}
        {isSheets && state.share_with ? (
          <p className="admin-alert admin-alert--info" role="status" data-testid="share-with">
            {t("lanes.form.shareWith")} <code dir="ltr">{state.share_with}</code>
          </p>
        ) : null}
        {sourceKey === HUBSPOT ? (
          <p className="admin-alert admin-alert--info" role="status">
            {t("lanes.form.hubspotNote")}
          </p>
        ) : null}

        <div className="field">
          <p className="t-label c-2">{t("lanes.form.channel")}</p>
          {channelOptions.length === 0 ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("lanes.form.channelNone")}
            </p>
          ) : (
            <Segmented layoutId="seg-lane-channel" label={t("lanes.form.channel")} value={channel} onChange={setChannel} options={channelOptions} />
          )}
          <p className="c-3 t-body-sm">{t("lanes.form.channelHelper")}</p>
          {lane && !lane.channel.authorized && channel === lane.channel.channel ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("lanes.notAllowed.hint")}
            </p>
          ) : null}
        </div>

        <div className="field">
          <p className="t-label c-2">{t("lanes.form.products")}</p>
          <Switch checked={all} onChange={setAll} label={t("lanes.form.allProducts")} />
          <p className="c-3 t-body-sm">{t("lanes.form.allProductsHelper")}</p>
          {all ? null : productOptions.length === 0 ? (
            <p className="c-3">{t("lanes.form.catalogEmpty")}</p>
          ) : (
            <ToggleChips label={t("lanes.form.products")} options={productOptions} value={picked} onChange={setPicked} />
          )}
        </div>

        {problem ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(problem.key)}
            {problem.detail && problem.key !== "lanes.error.forbidden" ? ` (${problem.detail})` : ""}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
