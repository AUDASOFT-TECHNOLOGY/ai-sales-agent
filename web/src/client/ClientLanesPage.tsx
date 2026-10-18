/** Sources and lanes: the client's commercial circuits. A lane is one source of prospects, one channel
 *  and the products sold on it. Everything shown comes from the server for the logged-in client
 *  (the session cookie is the only identity); the rules are the server's: this page lists, sends what
 *  the client chose with the version it was based on, and says what the server answered.
 *
 *  Honesty rules: "complete" is shown only when the server's readiness says so; a connection is
 *  shown as working only after a real test answered so (the server makes that call, never a
 *  simulation), and only for the lanes the test named. A client that runs no lanes of its own sees
 *  its single implicit `default` lane, read-only. */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { Modal } from "../ui/Overlays";
import { useClientAuth } from "./ClientAuthContext";
import { ClientLaneForm } from "./ClientLaneForm";
import {
  ALL_PRODUCTS,
  clientDeleteLane,
  clientLanes,
  clientReadiness,
  clientSourceState,
  clientTestSources,
  type Lane,
  type LanesState,
  type Readiness,
  type SourceState,
  type SourceTest,
} from "./api";
import { lanesFailureOf, type LanesFailure } from "./lanesFailure";

const IMPLICIT_LANE = "default";

type Fetched = [PromiseSettledResult<LanesState>, PromiseSettledResult<Readiness>, PromiseSettledResult<SourceState>];

function fetchAll(): Promise<Fetched> {
  return Promise.allSettled([clientLanes(), clientReadiness(), clientSourceState()]);
}

function FailureText({ failure }: { failure: LanesFailure }) {
  const { t } = useI18n();
  return (
    <>
      {t(failure.key)}
      {failure.detail && failure.key !== "lanes.error.forbidden" ? ` (${failure.detail})` : ""}
    </>
  );
}

export function ClientLanesPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [state, setState] = useState<LanesState | null>(null);
  const [loadFailure, setLoadFailure] = useState<LanesFailure | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [source, setSource] = useState<SourceState | null>(null);
  const [test, setTest] = useState<SourceTest | null>(null);
  const [form, setForm] = useState<"new" | Lane | null>(null);
  const [toDelete, setToDelete] = useState<Lane | null>(null);
  const [busy, setBusy] = useState<null | "refresh" | "test" | "delete">(null);
  const [failure, setFailure] = useState<LanesFailure | null>(null);
  const [deleteFailure, setDeleteFailure] = useState<LanesFailure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);

  const fail = useCallback(
    (err: LanesFailure) => {
      if (err.signedOut) void logout();
    },
    [logout],
  );

  /** The lanes decide whether the page can be shown; readiness and the source's state only add to it. */
  const apply = useCallback(
    ([lanes, ready, src]: Fetched): LanesFailure | null => {
      setReadiness(ready.status === "fulfilled" ? ready.value : null);
      setSource(src.status === "fulfilled" ? src.value : null);
      if (lanes.status === "fulfilled") {
        setState(lanes.value);
        setLoadFailure(null);
        return null;
      }
      const problem = lanesFailureOf(lanes.reason);
      setLoadFailure(problem);
      fail(problem);
      return problem;
    },
    [fail],
  );

  const loadAll = useCallback(async () => apply(await fetchAll()), [apply]);

  useEffect(() => {
    let cancelled = false;
    void fetchAll().then((results) => {
      if (!cancelled) apply(results);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  /** After a failure that says the server's state moved, what is on screen is read again. */
  async function onFailure(problem: LanesFailure) {
    fail(problem);
    if (problem.reload) await loadAll();
  }

  async function refresh() {
    setFailure(null);
    setNotice(null);
    setBusy("refresh");
    await loadAll();
    setBusy(null);
  }

  async function runTest() {
    setFailure(null);
    setNotice(null);
    setBusy("test");
    try {
      setTest(await clientTestSources());
      await loadAll();
    } catch (err) {
      const problem = lanesFailureOf(err);
      setFailure(problem);
      fail(problem);
    }
    setBusy(null);
  }

  async function confirmDelete() {
    if (!toDelete || !state) return;
    setDeleteFailure(null);
    setBusy("delete");
    try {
      setState(await clientDeleteLane(toDelete.id, state.version));
      setToDelete(null);
      setTest(null); // what was tested no longer describes these lanes
      setNotice("lanes.done.deleted");
      await loadAll();
    } catch (err) {
      const problem = lanesFailureOf(err);
      setDeleteFailure(problem);
      await onFailure(problem);
    }
    setBusy(null);
  }

  if (!state) {
    return (
      <div className="page page--focus">
        <div className="page-head">
          <h2 className="t-h1">{t("lanes.title")}</h2>
        </div>
        {loadFailure ? (
          <>
            <p className="admin-alert admin-alert--danger" role="alert">
              <FailureText failure={loadFailure} />
            </p>
            <div>
              <Button onClick={() => void refresh()} loading={busy === "refresh"}>
                {t("lanes.retry")}
              </Button>
            </div>
          </>
        ) : (
          <p className="c-3" role="status">
            {t("lanes.loading")}
          </p>
        )}
      </div>
    );
  }

  const current = state;
  const working = busy !== null;
  const waitingForPlatform = current.lanes.some((lane) => lane.source.source === "hubspot") && source?.access === "waiting_platform";
  const addBlocked = !current.editable
    ? t("lanes.implicit")
    : current.channel_options.length === 0
      ? t("lanes.add.noChannel")
      : undefined;

  /** The result of the connection test for one lane: this session's, else the stored one when it
   *  still holds and every source passed. Anything else is not claimed. */
  function sourceStatus(lane: Lane): { ok: boolean; message?: string } | "untested" | null {
    const found = test?.lanes?.find((r) => r.lane === lane.id);
    if (found) return { ok: found.ok, message: found.message };
    if (test && test.lanes === null) return { ok: test.ok, message: test.message };
    if (source?.last_test?.current) return source.last_test.ok ? { ok: true } : null;
    return "untested";
  }

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{t("lanes.title")}</h2>
        <div className="row row--wrap">
          <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void refresh()} loading={busy === "refresh"} disabled={working && busy !== "refresh"}>
            {t("lanes.refresh")}
          </Button>
          <Button variant="primary" leading={<Plus size={16} aria-hidden />} onClick={() => setForm("new")} disabled={working} disabledReason={addBlocked}>
            {t("lanes.add")}
          </Button>
        </div>
      </div>
      <p className="c-2">{t("lanes.intro")}</p>

      {loadFailure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          <FailureText failure={loadFailure} />
        </p>
      ) : null}
      {failure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          <FailureText failure={failure} />
        </p>
      ) : null}
      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {t(notice)}
        </p>
      ) : null}
      {!current.editable ? (
        <p className="admin-alert admin-alert--info" role="status" data-testid="lanes-implicit">
          {t("lanes.implicit")}
        </p>
      ) : null}

      <div className="stack stack--lg">
        <Glass level={1} className="client-card" aria-label={t("lanes.readiness.title")}>
          <h3 className="t-h2">{t("lanes.readiness.title")}</h3>
          {readiness === null ? (
            <p className="c-3" role="status" data-testid="readiness-unknown">
              {t("lanes.readiness.unknown")}
            </p>
          ) : readiness.ready ? (
            <p className="c-success" data-testid="readiness-ready">
              <Check size={16} aria-hidden /> {t("lanes.readiness.ready")}
            </p>
          ) : (
            <div data-testid="readiness-incomplete">
              <p className="c-2">{t("lanes.readiness.notReady")}</p>
              <ul className="stack">
                {readiness.checks
                  .filter((c) => !c.ok)
                  .map((c) => (
                    <li key={c.id}>{c.message ?? c.id}</li>
                  ))}
              </ul>
            </div>
          )}
        </Glass>

        {current.lanes.length === 0 ? (
          <p className="c-3" data-testid="lanes-empty">
            {t("lanes.empty")}
          </p>
        ) : (
          <ul className="stack stack--lg approvals" aria-label={t("lanes.title")}>
            {current.lanes.map((lane) => {
              const status = sourceStatus(lane);
              const sheets = lane.source.google_sheets;
              const implicit = !current.editable || lane.id === IMPLICIT_LANE;
              const channelChip = !lane.channel.authorized
                ? { tint: "danger" as const, text: t("lanes.channel.notAllowed") }
                : lane.channel.problem
                  ? { tint: "attention" as const, text: t("lanes.channel.attention") }
                  : { tint: "success" as const, text: t("lanes.channel.ready") };
              return (
                <li key={lane.id}>
                  <Glass level={1} as="article" className="client-card" aria-label={lane.id} data-testid={`lane-${lane.id}`}>
                    <header>
                      <h3 className="t-h2">{lane.id}</h3>
                      <p className="c-3 t-body-sm">
                        {implicit ? t("lanes.managedByAdmin") : t("lanes.position", { n: lane.position })}
                      </p>
                    </header>

                    <dl className="stack">
                      <div>
                        <dt className="t-label c-2">{t("lanes.source")}</dt>
                        <dd>
                          {lane.source.label}
                          {sheets ? (
                            <span className="c-3 t-body-sm" dir="auto">
                              {" "}
                              · {t("lanes.sheet")}: {sheets.spreadsheet_id || "—"} · {t("lanes.tab")}: {sheets.prospects_tab}
                            </span>
                          ) : null}
                        </dd>
                      </div>
                      <div>
                        <dt className="t-label c-2">{t("lanes.channel")}</dt>
                        <dd>{lane.channel.label}</dd>
                      </div>
                      <div>
                        <dt className="t-label c-2">{t("lanes.products")}</dt>
                        <dd className="row row--wrap">
                          {lane.offers_all_products || lane.products.includes(ALL_PRODUCTS) ? (
                            <Chip>{t("lanes.products.all")}</Chip>
                          ) : (
                            lane.products.map((p) => (
                              <Chip key={p}>{current.catalog.find((c) => c.id === p)?.name ?? p}</Chip>
                            ))
                          )}
                        </dd>
                      </div>
                    </dl>

                    <div className="row row--wrap">
                      <Chip tint={channelChip.tint} glyph={channelChip.tint === "success" ? <Check size={14} aria-hidden /> : <AlertTriangle size={14} aria-hidden />}>
                        {channelChip.text}
                      </Chip>
                      {status === "untested" ? (
                        <Chip outline>{t("lanes.source.untested")}</Chip>
                      ) : status ? (
                        <Chip tint={status.ok ? "success" : "danger"} glyph={status.ok ? <Check size={14} aria-hidden /> : <X size={14} aria-hidden />}>
                          {t(status.ok ? "lanes.source.ok" : "lanes.source.failed")}
                        </Chip>
                      ) : null}
                    </div>
                    {!lane.channel.authorized ? (
                      <p className="admin-alert admin-alert--info" role="status">
                        {t("lanes.notAllowed.hint")}
                      </p>
                    ) : null}
                    {lane.channel.problem ? (
                      <p className="admin-alert admin-alert--info" role="status">
                        {lane.channel.problem}
                      </p>
                    ) : null}
                    {status && status !== "untested" && status.message && !status.ok ? (
                      <p className="admin-alert admin-alert--danger" role="alert">
                        {status.message}
                      </p>
                    ) : null}

                    {implicit ? null : (
                      <div className="row row--wrap">
                        <Button leading={<Pencil size={16} aria-hidden />} onClick={() => setForm(lane)} disabled={working}>
                          {t("lanes.edit")}
                        </Button>
                        <Button
                          variant="danger"
                          leading={<Trash2 size={16} aria-hidden />}
                          onClick={() => {
                            setDeleteFailure(null);
                            setToDelete(lane);
                          }}
                          disabled={working}
                          disabledReason={current.lanes.length <= 1 ? t("lanes.delete.lastReason") : undefined}
                        >
                          {t("lanes.delete")}
                        </Button>
                      </div>
                    )}
                  </Glass>
                </li>
              );
            })}
          </ul>
        )}

        <Glass level={1} className="client-card" aria-label={t("lanes.test.title")}>
          <h3 className="t-h2">{t("lanes.test.title")}</h3>
          <p className="c-3">{t("lanes.test.help")}</p>
          {source?.last_test ? (
            <p className={source.last_test.current && source.last_test.ok ? "c-success" : "c-3"} data-testid="last-test">
              {t("lanes.test.last", {
                result: t(source.last_test.ok ? "lanes.test.ok" : "lanes.test.failed"),
                date: d(source.last_test.checked_at, { dateStyle: "medium", timeStyle: "short" }),
              })}
            </p>
          ) : null}
          {source?.last_test && !source.last_test.current ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("lanes.test.outdated")}
            </p>
          ) : null}
          <div>
            <Button variant="primary" onClick={() => void runTest()} loading={busy === "test"} disabled={working && busy !== "test"}>
              {t("lanes.test.run")}
            </Button>
          </div>
          {test ? (
            <div className={`admin-alert admin-alert--${test.ok ? "success" : "danger"}`} role={test.ok ? "status" : "alert"} data-testid="test-result">
              <p>
                <strong>{t(test.ok ? "lanes.test.ok" : "lanes.test.failed")}</strong> {test.message}
              </p>
              {(test.lanes ?? []).map((r) => (
                <p key={r.lane} className="t-body-sm">
                  {r.lane}: {t(r.ok ? "lanes.test.ok" : "lanes.test.failed")} — {r.message}
                </p>
              ))}
              <p className="t-body-sm">{d(test.checked_at, { dateStyle: "medium", timeStyle: "short" })}</p>
            </div>
          ) : null}
        </Glass>

        {waitingForPlatform ? (
          <Glass level={1} className="client-card">
            <h3 className="t-h2">{t("lanes.access.title")}</h3>
            <p data-testid="access-status" className="c-3" role="status">
              {t("lanes.access.waiting")}
            </p>
          </Glass>
        ) : null}
      </div>

      {form ? (
        <ClientLaneForm
          state={current}
          lane={form === "new" ? null : form}
          onClose={() => setForm(null)}
          onFailure={(problem) => void onFailure(problem)}
          onSaved={(done) => {
            setForm(null);
            setTest(null); // what was tested no longer describes these lanes
            setNotice(done === "created" ? "lanes.done.created" : "lanes.done.updated");
            void loadAll();
          }}
        />
      ) : null}

      <Modal
        open={toDelete !== null}
        onOpenChange={(open) => !open && busy !== "delete" && setToDelete(null)}
        title={t("lanes.delete.title", { id: toDelete?.id ?? "" })}
        description={t("lanes.delete.body")}
        footer={
          <>
            <Button onClick={() => setToDelete(null)} disabled={busy === "delete"}>
              {t("lanes.form.cancel")}
            </Button>
            <Button variant="danger" onClick={() => void confirmDelete()} loading={busy === "delete"}>
              {t("lanes.delete.confirm")}
            </Button>
          </>
        }
      >
        {deleteFailure ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            <FailureText failure={deleteFailure} />
          </p>
        ) : null}
      </Modal>
    </div>
  );
}
