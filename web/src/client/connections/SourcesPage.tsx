/** Where the client's prospects are. The client says where (Google Sheets or HubSpot) and what to
 *  offer them; how that is wired inside Revansa (which source feeds which products and which channel)
 *  is handled here without ever being named. Two situations, one screen:
 *
 *  - one source (the usual case): choose it, point to the spreadsheet, test;
 *  - several sources (an account Revansa runs that way): a card per source, each with its own
 *    products and sending channel. Adding a source creates the internal link by itself.
 *
 *  "Connected" is shown only after a real test succeeded; HubSpot access is the platform's job. */

import { useCallback, useEffect, useState } from "react";
import { Copy, FileSpreadsheet, Plus, RefreshCw, Users2 } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import {
  ALL_PRODUCTS,
  clientCreateLane,
  clientDeleteLane,
  clientLanes,
  clientReadiness,
  clientSaveSource,
  clientSourceState,
  clientTestSources,
  clientUpdateLane,
  type Lane,
  type LanesState,
  type Readiness,
  type SourceState,
  type SourceTest,
} from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { Card, FailureNote, Loading, Note, failureOf, type Failure } from "../kit";
import { slugify, uniqueSlug } from "../offer/productModel";
import { SheetsAccessCard } from "./SheetsAccessCard";

const SHEETS = "google_sheets";
const HUBSPOT = "hubspot";

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      leading={<Copy size={14} aria-hidden />}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setCopied(true));
      }}
    >
      {copied ? t("rv.src.copied") : t("rv.src.copy")}
    </Button>
  );
}

function SheetsHelp({ shareWith, linkKey }: { shareWith: string | null; linkKey: string }) {
  const { t } = useI18n();
  // A deployment that still uses one service-account file keeps its sharing steps; every other
  // client connects its own Google account.
  if (!shareWith) return <SheetsAccessCard linkKey={linkKey} />;
  return (
    <ol className="stack" style={{ paddingInlineStart: 20, listStyle: "decimal" }}>
      <li>{t("rv.src.sheets.step1")}</li>
      <li>
        {t("rv.src.sheets.step2")}
        {shareWith ? (
          <span className="row row--wrap" style={{ marginBlockStart: 6 }}>
            <code className="t-mono" dir="ltr" data-testid="share-with">
              {shareWith}
            </code>
            <CopyButton text={shareWith} />
          </span>
        ) : (
          <span className="c-3"> {t("rv.src.sheets.noAddress")}</span>
        )}
      </li>
      <li>{t("rv.src.sheets.step3")}</li>
    </ol>
  );
}

/** The connection test. One button, one result: the server makes a real, read-only call. */
function TestCard({ readiness, source, onDone, disabled }: { readiness: Readiness | null; source: SourceState | null; onDone: () => Promise<void>; disabled?: string }) {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SourceTest | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const last = source?.last_test ?? null;
  const connected = readiness?.checks.find((c) => c.id === "source_connected")?.ok === true;

  async function run() {
    setBusy(true);
    setFailure(null);
    try {
      setResult(await clientTestSources());
      await onDone();
    } catch (err) {
      const problem = failureOf(err);
      setFailure(problem);
      if (problem.signedOut) void logout();
    }
    setBusy(false);
  }

  return (
    <Card aria-label={t("rv.src.test.title")}>
      <div className="rv-card__head">
        <h3>{t("rv.src.test.title")}</h3>
        {connected ? <Chip tint="success">{t("rv.src.test.connected")}</Chip> : last && last.current && !last.ok ? <Chip tint="danger">{t("rv.src.test.failedChip")}</Chip> : <Chip>{t("rv.src.test.notTested")}</Chip>}
      </div>
      <p className="rv-card__sub">{t("rv.src.test.help")}</p>
      {last ? (
        <p className="c-3 t-body-sm" data-testid="last-test">
          {t("rv.src.test.last", { result: t(last.ok ? "rv.src.test.ok" : "rv.src.test.ko"), date: d(last.checked_at, { dateStyle: "medium", timeStyle: "short" }) })}
          {last.current ? "" : ` ${t("rv.src.test.outdated")}`}
        </p>
      ) : null}
      {failure ? <FailureNote failure={failure} /> : null}
      {result ? (
        <Note tone={result.ok ? "success" : "danger"} title={t(result.ok ? "rv.src.test.ok" : "rv.src.test.ko")}>
          <span dir="auto">{result.message}</span>
          {(result.lanes ?? []).map((r) => (
            <span key={r.lane} className="t-body-sm" dir="auto" style={{ display: "block" }}>
              {r.lane}: {r.message}
            </span>
          ))}
        </Note>
      ) : null}
      <div>
        <Button variant="primary" onClick={() => void run()} loading={busy} disabledReason={disabled}>
          {t("rv.src.test.run")}
        </Button>
      </div>
    </Card>
  );
}

/* ── One source ───────────────────────────────────────────────────────────────────────────── */

function SingleSource({ source, onChanged }: { source: SourceState; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [choice, setChoice] = useState(source.source);
  const [sheet, setSheet] = useState("");
  const [tab, setTab] = useState(source.google_sheets?.prospects_tab ?? "Prospects");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [done, setDone] = useState(false);

  const savedSheet = source.google_sheets?.spreadsheet_id ?? "";
  const sheetsChosen = choice === SHEETS;
  const dirty = choice !== source.source || (sheetsChosen && (sheet.trim() !== "" || tab !== (source.google_sheets?.prospects_tab ?? "Prospects")));
  const needsSheet = sheetsChosen && savedSheet === "" && sheet.trim() === "";
  const option = source.options.find((o) => o.key === choice);

  async function submit() {
    setBusy(true);
    setFailure(null);
    setDone(false);
    try {
      await clientSaveSource({
        expected_version: source.version,
        source: choice,
        ...(sheetsChosen ? { google_sheets: { ...(sheet.trim() !== "" ? { spreadsheet: sheet.trim() } : {}), prospects_tab: tab.trim() || "Prospects" } } : {}),
      });
      setSheet("");
      setDone(true);
      await onChanged();
    } catch (err) {
      const problem = failureOf(err);
      setFailure(problem);
      if (problem.signedOut) void logout();
      if (problem.reload) await onChanged();
    }
    setBusy(false);
  }

  return (
    <Card aria-label={t("rv.src.where")}>
      <div className="rv-card__head">
        <h3>{t("rv.src.where")}</h3>
      </div>
      <div className="rv-grid-2" role="radiogroup" aria-label={t("rv.src.where")}>
        {source.options.map((o) => (
          <label key={o.key} className="rv-repeat" style={{ cursor: "pointer", borderColor: choice === o.key ? "var(--brand)" : undefined }}>
            <span className="row">
              <input type="radio" name="source" checked={choice === o.key} onChange={() => { setChoice(o.key); setDone(false); }} />
              <FileSpreadsheet size={18} aria-hidden />
              <strong>{o.label}</strong>
            </span>
            <span className="c-3 t-body-sm">{t(o.key === HUBSPOT ? "rv.src.hubspot.help" : o.key === SHEETS ? "rv.src.sheets.help" : "rv.src.other.help")}</span>
          </label>
        ))}
      </div>

      {option && !option.runnable ? <Note tone="warn">{t("rv.src.notRunnable", { name: option.label })}</Note> : null}

      {sheetsChosen ? (
        <>
          <SheetsHelp shareWith={source.google_sheets?.share_with ?? null} linkKey={`${source.version}:${savedSheet}`} />
          <TextInput
            label={t("rv.src.sheets.link")}
            helper={savedSheet ? t("rv.src.sheets.linkSaved") : t("rv.src.sheets.linkHelp")}
            value={sheet}
            onChange={(e) => { setSheet(e.target.value); setDone(false); }}
            placeholder="https://docs.google.com/spreadsheets/d/…"
            dir="ltr"
            inputMode="url"
          />
          <TextInput label={t("rv.src.sheets.tab")} helper={t("rv.src.sheets.tabHelp")} value={tab} onChange={(e) => { setTab(e.target.value); setDone(false); }} maxLength={100} />
        </>
      ) : null}
      {choice === HUBSPOT ? (
        source.access === "waiting_platform" ? (
          <Note tone="warn" title={t("rv.src.hubspot.waiting.title")}>
            {t("rv.src.hubspot.waiting.text")}
          </Note>
        ) : (
          <Note tone="info">{t("rv.src.hubspot.ready")}</Note>
        )
      ) : null}

      {failure ? <FailureNote failure={failure} /> : null}
      {done ? <Note tone="success">{t("rv.src.saved")}</Note> : null}
      <div>
        <Button variant="primary" onClick={() => void submit()} loading={busy} disabled={!dirty} disabledReason={needsSheet && dirty ? t("rv.src.sheets.needLink") : undefined}>
          {t("rv.common.save")}
        </Button>
      </div>
    </Card>
  );
}

/* ── Several sources ──────────────────────────────────────────────────────────────────────── */

function SourceForm({ state, lane, onClose, onSaved }: { state: LanesState; lane: Lane | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [provider, setProvider] = useState(lane?.source.source ?? state.source_options[0]?.key ?? SHEETS);
  const [name, setName] = useState(lane ? lane.source.label : "");
  const [sheet, setSheet] = useState("");
  const [tab, setTab] = useState(lane?.source.google_sheets?.prospects_tab ?? "Prospects");
  const [allProducts, setAllProducts] = useState(lane ? lane.offers_all_products || lane.products.includes(ALL_PRODUCTS) : true);
  const [products, setProducts] = useState<string[]>(lane && !lane.offers_all_products ? lane.products.filter((p) => p !== ALL_PRODUCTS) : []);
  const [channel, setChannel] = useState(lane?.channel.channel ?? state.lanes[0]?.channel.channel ?? state.channel_options[0]?.key ?? "");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const sheets = provider === SHEETS;

  async function submit() {
    if (!lane && name.trim() === "") return setProblem("rv.src.err.name");
    if (sheets && !lane && sheet.trim() === "") return setProblem("rv.src.sheets.needLink");
    if (!allProducts && products.length === 0) return setProblem("rv.src.err.products");
    setProblem(null);
    setBusy(true);
    setFailure(null);
    const chosen = allProducts ? [ALL_PRODUCTS] : products;
    try {
      if (lane) {
        await clientUpdateLane(lane.id, {
          expected_version: state.version,
          channel,
          products: chosen,
          ...(sheets ? { google_sheets: { ...(sheet.trim() !== "" ? { spreadsheet: sheet.trim() } : {}), prospects_tab: tab.trim() || "Prospects" } } : {}),
        });
      } else {
        const taken = [...state.lanes.map((l) => l.id), ...state.lanes.map((l) => l.source.id)];
        const id = uniqueSlug(slugify(name), taken, "source");
        await clientCreateLane({
          expected_version: state.version,
          id,
          channel,
          products: chosen,
          source: { id, provider, ...(sheets ? { google_sheets: { spreadsheet: sheet.trim(), prospects_tab: tab.trim() || "Prospects" } } : {}) },
        });
      }
      await onSaved();
    } catch (err) {
      const f = failureOf(err);
      setFailure(f);
      if (f.signedOut) void logout();
      if (f.reload) await onSaved();
    }
    setBusy(false);
  }

  return (
    <Card aria-label={t(lane ? "rv.src.edit" : "rv.src.new")}>
      <div className="rv-card__head">
        <h3>{t(lane ? "rv.src.edit" : "rv.src.new")}</h3>
      </div>
      {failure ? <FailureNote failure={failure} /> : null}
      {!lane ? (
        <>
          <label className="rv-select-label">
            <span className="t-label c-2">{t("rv.src.kind")}</span>
            <select className="rv-select" value={provider} onChange={(e) => setProvider(e.target.value)}>
              {state.source_options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <TextInput label={t("rv.src.name")} helper={t("rv.src.nameHelp")} value={name} onChange={(e) => setName(e.target.value)} error={problem === "rv.src.err.name" ? t(problem) : undefined} maxLength={60} />
        </>
      ) : null}
      {sheets ? (
        <>
          <SheetsHelp shareWith={state.share_with} linkKey={String(state.version)} />
          <TextInput label={t("rv.src.sheets.link")} helper={lane ? t("rv.src.sheets.linkSaved") : t("rv.src.sheets.linkHelp")} value={sheet} onChange={(e) => setSheet(e.target.value)} error={problem === "rv.src.sheets.needLink" ? t(problem) : undefined} dir="ltr" inputMode="url" placeholder="https://docs.google.com/spreadsheets/d/…" />
          <TextInput label={t("rv.src.sheets.tab")} helper={t("rv.src.sheets.tabHelp")} value={tab} onChange={(e) => setTab(e.target.value)} maxLength={100} />
        </>
      ) : null}
      <div className="stack">
        <span className="t-label c-2">{t("rv.src.offer")}</span>
        <label className="rv-checkbox">
          <input type="radio" name="offer" checked={allProducts} onChange={() => setAllProducts(true)} />
          <span>{t("rv.src.offer.all")}</span>
        </label>
        <label className="rv-checkbox">
          <input type="radio" name="offer" checked={!allProducts} onChange={() => setAllProducts(false)} />
          <span>{t("rv.src.offer.some")}</span>
        </label>
        {!allProducts ? (
          <div className="stack" style={{ paddingInlineStart: 28 }}>
            {state.catalog.map((p) => (
              <label key={p.id} className="rv-checkbox">
                <input type="checkbox" checked={products.includes(p.id)} onChange={(e) => setProducts(e.target.checked ? [...products, p.id] : products.filter((x) => x !== p.id))} />
                <span dir="auto">{p.name ?? p.id}</span>
              </label>
            ))}
            {state.catalog.length === 0 ? <span className="c-3 t-body-sm">{t("rv.src.offer.noProducts")}</span> : null}
            {problem === "rv.src.err.products" ? <span className="c-danger t-body-sm">{t(problem)}</span> : null}
          </div>
        ) : null}
      </div>
      {state.channel_options.length > 1 ? (
        <label className="rv-select-label">
          <span className="t-label c-2">{t("rv.src.sendBy")}</span>
          <select className="rv-select" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {state.channel_options.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <div className="rv-actions">
        <Button variant="primary" onClick={() => void submit()} loading={busy}>
          {t("rv.common.save")}
        </Button>
        <Button variant="ghost" onClick={onClose} disabled={busy}>
          {t("rv.common.cancel")}
        </Button>
      </div>
    </Card>
  );
}

function MultiSource({ state, onChanged }: { state: LanesState; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [form, setForm] = useState<"new" | Lane | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);

  async function remove(lane: Lane) {
    setBusy(lane.id);
    setFailure(null);
    try {
      await clientDeleteLane(lane.id, state.version);
      setNotice("rv.src.removed");
    } catch (err) {
      setFailure(failureOf(err));
    }
    await onChanged();
    setBusy(null);
  }

  return (
    <>
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.src.yours")}</h3>
        </div>
        <Button variant="primary" leading={<Plus size={16} aria-hidden />} onClick={() => { setNotice(null); setForm("new"); }} disabled={form !== null || state.channel_options.length === 0} disabledReason={state.channel_options.length === 0 ? t("rv.src.noChannel") : undefined}>
          {t("rv.src.add")}
        </Button>
      </div>
      {failure ? <FailureNote failure={failure} /> : null}
      {notice ? <Note tone="success">{t(notice)}</Note> : null}
      {form ? (
        <SourceForm
          key={form === "new" ? "new" : form.id}
          state={state}
          lane={form === "new" ? null : form}
          onClose={() => setForm(null)}
          onSaved={async () => {
            setForm(null);
            setNotice("rv.src.saved");
            await onChanged();
          }}
        />
      ) : null}
      <Card flush aria-label={t("rv.src.yours")}>
        <ul className="rv-list">
          {state.lanes.map((lane) => {
            const sheets = lane.source.google_sheets;
            const some = !(lane.offers_all_products || lane.products.includes(ALL_PRODUCTS));
            return (
              <li key={lane.id} className="rv-product" data-testid={`source-${lane.id}`}>
                <span className="rv-channel__icon" aria-hidden>
                  <FileSpreadsheet size={20} />
                </span>
                <div className="rv-row__main">
                  <span className="rv-row__title" dir="auto">
                    {lane.source.label}
                    {lane.id !== lane.source.label ? ` · ${lane.id}` : ""}
                  </span>
                  <span className="rv-row__sub" dir="auto">
                    {sheets ? `${t("rv.src.sheets.tab")}: ${sheets.prospects_tab}` : ""}
                    {sheets ? " · " : ""}
                    {some ? lane.products.map((p) => state.catalog.find((c) => c.id === p)?.name ?? p).join(", ") : t("rv.src.offer.all")}
                    {` · ${t("rv.src.sendBy")}: ${lane.channel.label}`}
                  </span>
                  {lane.channel.problem ? <span className="rv-row__sub">{lane.channel.problem}</span> : null}
                </div>
                <div className="rv-actions">
                  <Button size="sm" onClick={() => { setNotice(null); setForm(lane); }} disabled={form !== null}>
                    {t("rv.common.edit")}
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => void remove(lane)} loading={busy === lane.id} disabledReason={state.lanes.length <= 1 ? t("rv.src.lastOne") : undefined}>
                    {t("rv.common.remove")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

/* ── The page ─────────────────────────────────────────────────────────────────────────────── */

export function SourcesPage() {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [lanes, setLanes] = useState<LanesState | null>(null);
  const [source, setSource] = useState<SourceState | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const [l, s, r] = await Promise.allSettled([clientLanes(), clientSourceState(), clientReadiness()]);
    setReadiness(r.status === "fulfilled" ? r.value : null);
    setSource(s.status === "fulfilled" ? s.value : null);
    if (l.status === "fulfilled") {
      setLanes(l.value);
      setFailure(null);
    } else {
      const problem = failureOf(l.reason);
      setFailure(problem);
      if (problem.signedOut) void logout();
    }
    setLoaded(true);
  }, [logout]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([clientLanes(), clientSourceState(), clientReadiness()]).then(([l, s, r]) => {
      if (cancelled) return;
      setReadiness(r.status === "fulfilled" ? r.value : null);
      setSource(s.status === "fulfilled" ? s.value : null);
      if (l.status === "fulfilled") setLanes(l.value);
      else setFailure(failureOf(l.reason));
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loaded) return <Loading />;
  if (failure || !lanes) return failure ? <FailureNote failure={failure} onRetry={() => void load()} /> : null;

  const several = lanes.editable;
  const waiting = source?.access === "waiting_platform";

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.src.title")}</h3>
          <p>{t("rv.src.intro")}</p>
        </div>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void load()}>
          {t("rv.common.refresh")}
        </Button>
      </div>

      {several ? <MultiSource state={lanes} onChanged={load} /> : source ? <SingleSource key={source.version ?? "none"} source={source} onChanged={load} /> : <Note tone="danger">{t("rv.err.unavailable")}</Note>}

      {waiting && several ? (
        <Note tone="warn" title={t("rv.src.hubspot.waiting.title")}>
          {t("rv.src.hubspot.waiting.text")}
        </Note>
      ) : null}

      <TestCard readiness={readiness} source={source} onDone={load} />
      <p className="c-3 t-body-sm">
        <Users2 size={14} aria-hidden style={{ display: "inline", verticalAlign: "-2px" }} /> {t("rv.src.prospectsHint")}
      </p>
    </div>
  );
}
