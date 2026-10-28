/** Circuits: one card per way of prospecting. A circuit is read left to right, the same three things
 *  in the same three colours everywhere: where the prospects are (source), how the agent writes to
 *  them (channel) and what it offers (products). The channel and the products are changed right on
 *  the card; the sentence at the bottom says in words what the circuit does.
 *
 *  Two situations, one screen: an account with several circuits (a card each, add and remove) and an
 *  account with one source, shown as a single circuit. Nothing technical is asked. */

import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Check, FileSpreadsheet, Mail, MessageCircle, Package, Plus, RefreshCw, Route, Send, Settings2 } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Glass } from "../../ui/Glass";
import {
  ALL_PRODUCTS,
  clientChannel,
  clientDeleteLane,
  clientLanes,
  clientReadiness,
  clientSourceState,
  clientTestSources,
  clientUpdateLane,
  type ChannelState,
  type Lane,
  type LanesState,
  type Readiness,
  type SourceState,
  type SourceTest,
} from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { FailureNote, Loading, Note, PageHead, failureOf, type Failure } from "../kit";
import { ChannelsPage } from "../connections/ChannelsPage";
import { SingleSource, SourceForm, TestCard } from "../connections/SourcesPage";
import { SyncCard } from "../connections/SyncCard";
import { HubSpotAccessCard } from "../connections/HubSpotAccessCard";

function ChannelIcon({ channel, size = 18 }: { channel: string | undefined; size?: number }) {
  if (channel === "email") return <Mail size={size} aria-hidden />;
  if (channel === "whatsapp") return <MessageCircle size={size} aria-hidden />;
  return <Send size={size} aria-hidden />;
}

function Mark({ shape, on }: { shape: "box" | "radio"; on: boolean }) {
  return (
    <span className="rv-mark" data-shape={shape} data-on={on || undefined} aria-hidden>
      {on ? <Check size={12} strokeWidth={3} /> : null}
    </span>
  );
}

function Block({ ent, label, children }: { ent: "src" | "chn" | "prd"; label: string; children: React.ReactNode }) {
  return (
    <div className="rv-blk rv-ent" data-ent={ent}>
      <span className="rv-blk__label">{label}</span>
      {children}
    </div>
  );
}

const Arrow = () => (
  <div className="rv-flow__arrow" aria-hidden>
    <ArrowRight size={18} className="mirror" />
  </div>
);

function productNames(state: LanesState, ids: string[]): string {
  return ids.map((id) => state.catalog.find((c) => c.id === id)?.name ?? id).join(", ");
}

/* ── One circuit ──────────────────────────────────────────────────────────────────────────── */

function CircuitCard({
  lane,
  state,
  verdict,
  testing,
  onTest,
  onChanged,
  onEdit,
  onSync,
  onRemove,
  removing,
  canRemove,
  busy,
}: {
  lane: Lane;
  state: LanesState;
  verdict: { ok: boolean; message: string } | null;
  testing: boolean;
  onTest: () => void;
  onChanged: () => Promise<void>;
  onEdit: () => void;
  onSync: () => void;
  onRemove: () => void;
  removing: boolean;
  canRemove: boolean;
  busy: boolean;
}) {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const chosenAll = lane.offers_all_products || lane.products.includes(ALL_PRODUCTS);
  const [imposed, setImposed] = useState(!chosenAll);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const picked = chosenAll ? [] : lane.products;

  async function save(change: { channel?: string; products?: string[] }) {
    setSaving(true);
    setFailure(null);
    try {
      await clientUpdateLane(lane.id, { expected_version: state.version, ...change });
      await onChanged();
    } catch (err) {
      const problem = failureOf(err);
      setFailure(problem);
      if (problem.signedOut) void logout();
      if (problem.reload) await onChanged();
    }
    setSaving(false);
  }

  function togglePick(id: string, on: boolean) {
    const next = on ? [...picked, id] : picked.filter((p) => p !== id);
    if (next.length === 0) return; // never leave a circuit without a product: switch to "agent's choice" instead
    void save({ products: next });
  }

  const sheets = lane.source.google_sheets;
  const providerLabel = state.source_options.find((o) => o.key === lane.source.source)?.label ?? lane.source.source;
  const ready = lane.channel.authorized && !lane.channel.problem;
  const sentence = t("rv.circ.sum", {
    source: lane.source.label,
    channel: lane.channel.label,
    products: chosenAll ? t("rv.circ.sum.all") : productNames(state, lane.products),
  });

  return (
    <Glass level={1} className="rv-card" data-testid={`source-${lane.id}`} aria-label={lane.source.label}>
      <div className="rv-card__head">
        <div className="row">
          <span className="rv-channel__icon" aria-hidden>
            <Route size={22} />
          </span>
          <div>
            <h3 dir="auto">{lane.source.label}</h3>
            <p className="rv-card__sub">{providerLabel}</p>
          </div>
        </div>
        <div className="rv-actions">
          {ready ? <Chip tint="success">{t("rv.circ.working")}</Chip> : <Chip tint="attention">{t("rv.circ.waiting")}</Chip>}
          <Button size="sm" variant="ghost" onClick={onRemove} loading={removing} disabled={busy || !canRemove} disabledReason={!canRemove ? t("rv.src.lastOne") : undefined}>
            {t("rv.common.remove")}
          </Button>
        </div>
      </div>

      {failure ? <FailureNote failure={failure} /> : null}

      <div className="rv-flow">
        <Block ent="src" label={t("rv.circ.label.src")}>
          <div className="rv-opt__text">
            <strong dir="auto">{providerLabel}</strong>
            <small dir="auto">{sheets ? `${t("rv.src.sheets.tab")} : ${sheets.prospects_tab}` : lane.source.label}</small>
          </div>
          <div className="row">
            <span className={verdict ? (verdict.ok ? "rv-dot rv-dot--ok" : "rv-dot rv-dot--bad") : "rv-dot"} aria-hidden />
            <span className="t-body-sm" dir="auto">
              {verdict ? (verdict.ok ? t("rv.circ.test.ok") : t("rv.circ.test.ko")) : t("rv.circ.test.none")}
            </span>
          </div>
          {verdict && !verdict.ok ? <span className="c-danger t-body-sm" dir="auto">{verdict.message}</span> : null}
          <div className="rv-actions" style={{ marginBlockStart: "auto" }}>
            <Button size="sm" leading={<RefreshCw size={14} aria-hidden />} onClick={onTest} loading={testing}>
              {t("rv.circ.test")}
            </Button>
            <Button size="sm" variant="ghost" onClick={onEdit} disabled={busy}>
              {t("rv.circ.editSource")}
            </Button>
          </div>
        </Block>
        <Arrow />
        <Block ent="chn" label={t("rv.circ.label.chn")}>
          <div role="radiogroup" aria-label={t("rv.circ.label.chn")} className="stack" style={{ gap: 8 }}>
            {state.channel_options.map((o) => {
              const on = lane.channel.channel === o.key;
              return (
                <button key={o.key} type="button" role="radio" aria-checked={on} className="rv-opt" disabled={saving || busy} onClick={() => !on && void save({ channel: o.key })}>
                  <Mark shape="radio" on={on} />
                  <ChannelIcon channel={o.key} />
                  <span className="rv-opt__text">
                    <strong>{o.label}</strong>
                    {on ? <small dir="auto">{ready ? t("rv.circ.chan.ready") : (lane.channel.problem ?? t("rv.circ.chan.todo"))}</small> : null}
                  </span>
                  {on ? <Chip tint={ready ? "success" : "attention"}>{t(ready ? "rv.circ.chan.ready" : "rv.circ.chan.todo")}</Chip> : null}
                </button>
              );
            })}
          </div>
        </Block>
        <Arrow />
        <Block ent="prd" label={t("rv.circ.label.prd")}>
          <div className="rv-seg" role="group" aria-label={t("rv.circ.label.prd")} style={{ alignSelf: "flex-start" }}>
            <button type="button" className="rv-seg__btn" aria-pressed={imposed} data-selected={imposed || undefined} onClick={() => setImposed(true)} disabled={saving || busy}>
              {t("rv.circ.prd.imposed")}
            </button>
            <button
              type="button"
              className="rv-seg__btn"
              aria-pressed={!imposed}
              data-selected={!imposed || undefined}
              onClick={() => {
                setImposed(false);
                if (!chosenAll) void save({ products: [ALL_PRODUCTS] });
              }}
              disabled={saving || busy}
            >
              {t("rv.circ.prd.agent")}
            </button>
          </div>
          {imposed ? (
            <>
              <div className="stack" style={{ gap: 8 }}>
                {state.catalog.map((p) => {
                  const on = picked.includes(p.id);
                  return (
                    <button key={p.id} type="button" className="rv-opt" aria-pressed={on} disabled={saving || busy} onClick={() => togglePick(p.id, !on)}>
                      <Mark shape="box" on={on} />
                      <span className="rv-opt__text">
                        <strong dir="auto">{p.name ?? p.id}</strong>
                      </span>
                    </button>
                  );
                })}
                {state.catalog.length === 0 ? <span className="c-3 t-body-sm">{t("rv.circ.prd.none")}</span> : null}
              </div>
              {picked.length === 0 ? <span className="c-attention t-body-sm">{t("rv.circ.prd.pick")}</span> : <span className="c-2 t-body-sm">{t("rv.circ.prd.imposed.help")}</span>}
            </>
          ) : (
            <span className="c-2 t-body-sm">{t("rv.circ.prd.agent.help")}</span>
          )}
        </Block>
      </div>

      <div className="rv-sum">
        <Route size={18} aria-hidden />
        <span dir="auto">{sentence}</span>
      </div>
      <p className="c-3 t-body-sm">{t("rv.circ.writeback")}</p>
      <div>
        <Button size="sm" variant="ghost" leading={<Settings2 size={14} aria-hidden />} onClick={onSync} disabled={busy}>
          {t("rv.circ.sync")}
        </Button>
      </div>
    </Glass>
  );
}

/* ── The account's only source, shown as one circuit ──────────────────────────────────────── */

function SingleCircuit({ source, channel, readiness, onChanged }: { source: SourceState; channel: ChannelState | null; readiness: Readiness | null; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const option = source.options.find((o) => o.key === source.source);
  const chosen = channel?.options.find((o) => o.key === channel.channel);
  const channelReady = channel?.status === "ready";
  const connected = readiness?.checks.find((c) => c.id === "source_connected")?.ok === true;
  return (
    <>
      <Glass level={1} className="rv-card">
        <div className="rv-card__head">
          <div className="row">
            <span className="rv-channel__icon" aria-hidden>
              <Route size={22} />
            </span>
            <h3>{t("rv.circ.single.name")}</h3>
          </div>
          {connected && channelReady ? <Chip tint="success">{t("rv.circ.working")}</Chip> : <Chip tint="attention">{t("rv.circ.waiting")}</Chip>}
        </div>
        <div className="rv-flow">
          <Block ent="src" label={t("rv.circ.label.src")}>
            <div className="rv-opt__text">
              <strong dir="auto">{option?.label ?? source.source}</strong>
              {source.google_sheets ? <small dir="auto">{`${t("rv.src.sheets.tab")} : ${source.google_sheets.prospects_tab}`}</small> : null}
            </div>
            <Chip tint={connected ? "success" : "neutral"}>{t(connected ? "rv.src.test.connected" : "rv.src.test.notTested")}</Chip>
          </Block>
          <Arrow />
          <Block ent="chn" label={t("rv.circ.label.chn")}>
            <div className="rv-opt" style={{ cursor: "default" }}>
              <ChannelIcon channel={chosen?.key} />
              <span className="rv-opt__text">
                <strong>{chosen?.label ?? "—"}</strong>
              </span>
              <Chip tint={channelReady ? "success" : "attention"}>{t(channelReady ? "rv.circ.chan.ready" : "rv.circ.chan.todo")}</Chip>
            </div>
            <a href="#canaux" className="link-btn">
              {t("rv.circ.single.change")}
            </a>
          </Block>
          <Arrow />
          <Block ent="prd" label={t("rv.circ.label.prd")}>
            <span className="rv-opt__text">
              <strong>{t("rv.circ.prd.agent")}</strong>
            </span>
            <span className="c-2 t-body-sm">{t("rv.circ.prd.agent.help")}</span>
          </Block>
        </div>
        <p className="c-3 t-body-sm">{t("rv.circ.writeback")}</p>
      </Glass>
      <SingleSource key={source.version ?? "none"} source={source} onChanged={onChanged} />
    </>
  );
}

/* ── The page ─────────────────────────────────────────────────────────────────────────────── */

export function CircuitsPage() {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [lanes, setLanes] = useState<LanesState | null>(null);
  const [source, setSource] = useState<SourceState | null>(null);
  const [channel, setChannel] = useState<ChannelState | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [test, setTest] = useState<SourceTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [form, setForm] = useState<"new" | Lane | null>(null);
  const [syncing, setSyncing] = useState<Lane | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [problem, setProblem] = useState<Failure | null>(null);

  const apply = useCallback(
    (results: [PromiseSettledResult<LanesState>, PromiseSettledResult<SourceState>, PromiseSettledResult<ChannelState>, PromiseSettledResult<Readiness>]) => {
      const [l, s, c, r] = results;
      setReadiness(r.status === "fulfilled" ? r.value : null);
      setSource(s.status === "fulfilled" ? s.value : null);
      setChannel(c.status === "fulfilled" ? c.value : null);
      if (l.status === "fulfilled") {
        setLanes(l.value);
        setFailure(null);
      } else {
        setFailure(failureOf(l.reason));
      }
      setLoaded(true);
    },
    [],
  );

  const load = useCallback(async () => {
    apply(await Promise.allSettled([clientLanes(), clientSourceState(), clientChannel(), clientReadiness()]));
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([clientLanes(), clientSourceState(), clientChannel(), clientReadiness()]).then((results) => !cancelled && apply(results));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  async function runTest() {
    setTesting(true);
    setProblem(null);
    try {
      setTest(await clientTestSources());
      await load();
    } catch (err) {
      const f = failureOf(err);
      setProblem(f);
      if (f.signedOut) void logout();
    }
    setTesting(false);
  }

  async function remove(lane: Lane, version: number | null) {
    setRemoving(lane.id);
    setProblem(null);
    try {
      await clientDeleteLane(lane.id, version);
      setNotice("rv.src.removed");
    } catch (err) {
      setProblem(failureOf(err));
    }
    await load();
    setRemoving(null);
  }

  if (!loaded) return <Loading />;
  if (failure || !lanes) return failure ? <FailureNote failure={failure} onRetry={() => void load()} /> : null;

  const several = lanes.editable;
  const verdictOf = (id: string) => {
    const hit = test?.lanes?.find((l) => l.lane === id);
    return hit ? { ok: hit.ok, message: hit.message } : null;
  };

  return (
    <div className="rv-page">
      <PageHead
        title={t("rv.circ.title")}
        text={t("rv.circ.intro")}
        actions={
          several ? (
            <Button variant="primary" leading={<Plus size={16} aria-hidden />} onClick={() => { setNotice(null); setForm("new"); }} disabled={form !== null || lanes.channel_options.length === 0} disabledReason={lanes.channel_options.length === 0 ? t("rv.src.noChannel") : undefined}>
              {t("rv.circ.new")}
            </Button>
          ) : undefined
        }
      />

      <div className="rv-howto" aria-label={t("rv.circ.title")}>
        {(
          [
            ["src", FileSpreadsheet, "rv.circ.how.src"],
            ["chn", MessageCircle, "rv.circ.how.chn"],
            ["prd", Package, "rv.circ.how.prd"],
          ] as const
        ).map(([ent, Icon, key], i) => (
          <div key={ent} className="rv-ent" data-ent={ent}>
            <Icon size={22} style={{ color: "rgb(var(--ent))", flex: "none" }} aria-hidden />
            <div>
              <strong>
                {i + 1} · {t(key)}
              </strong>
              <span>{t(`${key}.text` as MessageKey)}</span>
            </div>
          </div>
        ))}
      </div>

      {problem ? <FailureNote failure={problem} /> : null}
      {notice ? <Note tone="success">{t(notice)}</Note> : null}

      {several ? (
        <>
          {form ? (
            <SourceForm
              key={form === "new" ? "new" : form.id}
              state={lanes}
              lane={form === "new" ? null : form}
              onClose={() => setForm(null)}
              onSaved={async () => {
                setForm(null);
                setNotice("rv.circ.saved");
                await load();
              }}
            />
          ) : null}
          {syncing ? <SyncCard key={syncing.id} laneId={syncing.id} laneName={syncing.source.label} onClose={() => setSyncing(null)} onChanged={load} /> : null}
          {lanes.lanes.map((lane) => (
            <CircuitCard
              key={lane.id}
              lane={lane}
              state={lanes}
              verdict={verdictOf(lane.id)}
              testing={testing}
              onTest={() => void runTest()}
              onChanged={load}
              onEdit={() => { setNotice(null); setForm(lane); }}
              onSync={() => { setNotice(null); setSyncing(lane); }}
              onRemove={() => void remove(lane, lanes.version)}
              removing={removing === lane.id}
              canRemove={lanes.lanes.length > 1}
              busy={form !== null}
            />
          ))}
          {lanes.lanes.some((l) => l.source.source === "hubspot") ? <HubSpotAccessCard onChanged={() => void load()} /> : null}
        </>
      ) : source ? (
        <>
          <SingleCircuit source={source} channel={channel} readiness={readiness} onChanged={load} />
          <TestCard readiness={readiness} source={source} onDone={load} />
        </>
      ) : (
        <Note tone="danger">{t("rv.err.unavailable")}</Note>
      )}

      <section id="canaux" className="rv-page" style={{ gap: 14 }} aria-labelledby="circ-channels">
        <div className="rv-head__text">
          <h3 className="rv-section-title" id="circ-channels">
            {t("rv.circ.channels.title")}
          </h3>
          <p className="c-2">{t("rv.circ.channels.intro")}</p>
        </div>
        <ChannelsPage bare />
      </section>
    </div>
  );
}
