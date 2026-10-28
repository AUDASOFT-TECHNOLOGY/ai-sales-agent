/** Conversations, read from the platform (`GET /client/v1/conversations` for who, then
 *  `GET /client/v1/conversations/{prospect_id}` for the messages). The server searches, filters, sorts
 *  (latest message first) and pages the list; a long conversation opens on its latest messages and
 *  loads older ones on request.
 *
 *  A person can take a conversation over, write to the prospect and give it back (`ConversationControl`);
 *  the server decides who is in charge and refuses anything unsafe, this screen only shows it. The
 *  follow-up settings sit in a section of the list, opened on demand. */

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { clientConversation, clientProspects, type ConversationHistory, type ConversationMessage, type LanesState, type PaymentCase } from "../client/api";
import { Composer, ControlBar } from "./ConversationControl";
import { whatsappText, type WhatsAppKey } from "../whatsapp/copy";
import { crmFailureOf, type CrmFailure } from "../client/crmFailure";
import { useFetched } from "../client/useFetched";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { ProspectsHead } from "./ProspectsHead";
import { ProspectFiche, laneOf, laneProducts, useCardContext } from "./ProspectFiche";
import { Glass } from "../ui/Glass";
import { useMedia } from "../ui/useMedia";
import { cx } from "../ui/cx";
import { ListFilters, filtersFromParams, isFiltered, paramsFromFilters, toApiFilters, type FilterState } from "./ListFilters";

export const LIST_SIZE = 25;
export const THREAD_PAGE = 50;

function useSignOutOn(failure: CrmFailure | null) {
  const { logout } = useClientAuth();
  const signedOut = failure?.signedOut === true;
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);
}

function Thread({ id, name, lanes, payments }: { id: string; name: string | null; lanes: LanesState | null; payments: PaymentCase[] | null }) {
  const { t, d, n, locale } = useI18n();
  const navigate = useNavigate();
  const wide = useMedia("(min-width: 1024px)");
  const [attempt, setAttempt] = useState(0);
  // Pages of older messages, loaded on request; the first page is the latest messages.
  const [older, setOlder] = useState<ConversationMessage[]>([]);
  const [olderFailed, setOlderFailed] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const load = useCallback(() => clientConversation(id, { limit: THREAD_PAGE, offset: 0, order: "desc" }).then((c) => ({ c, attempt })), [id, attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  useSignOutOn(failure);
  // After a take-over, a give-back or a sent message the conversation is read again; the last answer
  // stays on screen meanwhile, so the box a person is typing in is never torn down.
  const [kept, setKept] = useState<ConversationHistory | undefined>();
  if (value?.c && value.c !== kept) setKept(value.c);
  const history: ConversationHistory | undefined = value?.c ?? kept;
  const refresh = (messageSent: boolean) => {
    if (messageSent) setOlder([]); // the offsets of older pages moved by one
    setAttempt((a) => a + 1);
  };

  // The server returns newest first; the thread reads oldest first.
  const messages = useMemo(() => (history ? [...older, ...[...history.messages].reverse()] : []), [history, older]);
  const fetched = (history?.messages.length ?? 0) + older.length;
  const total = history?.total_messages ?? messages.length;
  const hasOlder = fetched < total;

  const showOlder = () => {
    setLoadingOlder(true);
    setOlderFailed(false);
    clientConversation(id, { limit: THREAD_PAGE, offset: fetched, order: "desc" }).then(
      (c) => {
        setOlder((prev) => [...[...c.messages].reverse(), ...prev]);
        setLoadingOlder(false);
      },
      () => {
        setOlderFailed(true);
        setLoadingOlder(false);
      },
    );
  };

  const prospect = history?.prospect ?? null;
  const control = history?.control ?? null;
  const when = (iso: string) => d(iso, { dateStyle: "medium", timeStyle: "short" });
  const lane = laneOf(lanes, prospect);
  const fiche = <ProspectFiche history={history} lanes={lanes} payments={payments} />;

  return (
    <>
    <Glass level={1} className="thread-view" aria-label={name ?? id}>
      <header className="thread-view__head">
        {!wide ? (
          <button type="button" className="icon-btn" aria-label={t("convo.title")} onClick={() => navigate("/conversations")}>
            <ChevronLeft size={22} className="mirror" />
          </button>
        ) : null}
        <div className="thread-view__who">
          <h2 className="t-h3" dir="auto">
            {name ?? prospect?.display_name ?? id}
          </h2>
          {prospect ? (
            <div className="rv-tags" data-testid="thread-status">
              <Chip tint={prospect.status === "active" ? "success" : prospect.status === "paused" ? "attention" : prospect.status === "opted_out" ? "danger" : "neutral"}>{t(`crm.status.${prospect.status}` as MessageKey)}</Chip>
              {lane ? (
                <span className="rv-tag rv-ent" data-ent="src" dir="auto">
                  {lane.source.label}
                </span>
              ) : null}
              {lane && lanes
                ? laneProducts(lanes, lane, t("rv.conv.fiche.agentAll")).map((p) => (
                    <span key={p} className="rv-tag rv-ent" data-ent="prd" dir="auto">
                      {p}
                    </span>
                  ))
                : null}
              {lane ? (
                <span className="rv-tag rv-ent" data-ent="chn">
                  {lane.channel.label}
                </span>
              ) : prospect.preferred_channel ? (
                <span className="rv-tag rv-ent" data-ent="chn">
                  {prospect.preferred_channel}
                </span>
              ) : null}
              {prospect.needs_human ? <Chip tint="attention">{t("convo.f.needsPerson")}</Chip> : null}
            </div>
          ) : null}
          {history?.next_follow_up ? (
            <p className="c-3 t-body-sm" data-testid="thread-follow-up">
              {t("convo.f.nextFollowUp", {
                what: t(`crm.action.${history.next_follow_up.action_type}` as MessageKey),
                when: when(history.next_follow_up.due_at),
              })}
            </p>
          ) : null}
        </div>
      </header>

      {history ? <ControlBar id={id} control={control} status={prospect?.status ?? null} onChanged={() => refresh(false)} /> : null}

      {failure ? (
        <div className="stack">
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          {failure.key !== "crm.error.notFound" ? <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button> : null}
        </div>
      ) : loading && !history ? (
        <p className="c-3" role="status">
          {t("convo.live.loading")}
        </p>
      ) : history && messages.length === 0 ? (
        <p className="c-3" data-testid="thread-empty">
          {t("convo.live.empty")}
        </p>
      ) : history ? (
        <>
          {history.errors && history.errors.length > 0 ? (
            <div className="admin-alert admin-alert--danger" role="alert" data-testid="thread-errors">
              <strong>{t("convo.f.errorsTitle")}</strong>
              <ul>
                {history.errors.map((e) => (
                  <li key={e.action_id}>
                    {t(`crm.action.${e.action_type}` as MessageKey)}
                    {e.due_at ? ` · ${when(e.due_at)}` : ""}
                    {e.reason ? ` · ${e.reason}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="c-3 t-body-sm tabular" data-testid="thread-count">
            {t("convo.f.shown", { shown: n(messages.length), total: n(total) })}
          </p>
          {hasOlder ? (
            <div>
              <Button onClick={showOlder} disabled={loadingOlder}>
                {t("convo.f.older")}
              </Button>
              {olderFailed ? (
                <p className="admin-alert admin-alert--danger" role="alert">
                  {t("crm.error.unavailable")}
                </p>
              ) : null}
            </div>
          ) : null}
          <ol className="msgs" aria-label={t("convo.messagesLabel")}>
            {messages.map((m, index) => {
              const mine = m.direction === "outbound";
              const author = m.author === "human" ? "human" : mine ? "agent" : "prospect";
              const day = m.timestamp.slice(0, 10);
              const newDay = index === 0 || messages[index - 1]!.timestamp.slice(0, 10) !== day;
              return (
                <Fragment key={m.id}>
                  {newDay ? (
                    <li className="c-3 t-label" role="presentation" data-testid={`day-${day}`}>
                      {d(m.timestamp, { dateStyle: "full" })}
                    </li>
                  ) : null}
                  <li className="msg" data-author={author} data-mine={mine || undefined} data-testid={`message-${m.id}`}>
                    <div className="msg__col">
                      <p className="msg__who t-label c-3">{author === "human" ? t("convo.ctl.byYou") : t(mine ? "convo.live.sent" : "convo.live.received")}</p>
                      <div className="msg__bubble" dir="auto">
                        {m.text}
                      </div>
                      <p className="c-3 t-body-sm">
                        {when(m.timestamp)} · {m.channel}
                        {mine && m.delivery ? ` · ${whatsappText(locale, `delivery.${m.delivery}` as WhatsAppKey)}` : ""}
                      </p>
                    </div>
                  </li>
                </Fragment>
              );
            })}
          </ol>
        </>
      ) : null}

      {history && control?.mode === "human" ? (
        <Composer id={id} name={name ?? prospect?.display_name ?? id} optedOut={prospect?.status === "opted_out"} onSent={() => refresh(true)} />
      ) : null}
    </Glass>
    {wide ? fiche : null}
    </>
  );
}

export function Conversations() {
  const { t, n, d } = useI18n();
  const { id } = useParams();
  const wide = useMedia("(min-width: 1024px)");
  const [params, setParams] = useSearchParams();
  const requested = Number.parseInt(params.get("page") ?? "1", 10);
  const page = Number.isFinite(requested) && requested >= 1 ? requested : 1;
  const [attempt, setAttempt] = useState(0);
  const key = params.toString();
  const filters = useMemo(() => filtersFromParams(params), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const apiFilters = useMemo(() => toApiFilters(filters, "recent"), [filters]);
  const { lanes, payments } = useCardContext();
  // Every prospect is listed, contacted or not: a list that is empty while prospects exist would hide them.
  const load = useCallback(
    () => clientProspects(LIST_SIZE, (page - 1) * LIST_SIZE, apiFilters).then((p) => ({ p, attempt })),
    [page, apiFilters, attempt],
  );
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  useSignOutOn(failure);
  const result = value?.p;
  const rows = result?.prospects ?? [];
  const lastPage = result ? Math.max(1, Math.ceil(result.total / LIST_SIZE)) : page;
  const narrowed = isFiltered(filters);
  const go = (next: number) => setParams(paramsFromFilters(filters, next), { replace: true });
  const refilter = (next: FilterState) => setParams(paramsFromFilters(next), { replace: true });
  const initials = (name: string) =>
    name
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]!.toUpperCase())
      .join("");

  const list = (
    <Glass className="convo-list" aria-label={t("convo.title")}>
      {failure ? (
        <div className="stack" style={{ padding: 14 }}>
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
        </div>
      ) : loading ? (
        <p className="c-3" role="status" style={{ padding: 14 }}>
          {t("prospects.live.loading")}
        </p>
      ) : rows.length === 0 ? (
        <p className="c-3" data-testid="convo-list-empty" style={{ padding: 14 }}>
          {narrowed ? t("rv.conv.noMatch") : t("rv.conv.empty")}
        </p>
      ) : (
        <>
          <ul>
            {rows.map((p) => {
              const lane = laneOf(lanes, p);
              const first = p.last_contacted_at ?? p.last_inbound_at;
              const last = p.last_inbound_at ?? p.last_contacted_at;
              return (
                <li key={p.id}>
                  <Link to={`/conversations/${encodeURIComponent(p.id)}${key ? `?${key}` : ""}`} className={cx("convo-item", p.id === id && "is-active")} aria-current={p.id === id ? "page" : undefined}>
                    <span className="rv-avatar rv-avatar--sm" aria-hidden>
                      {initials(p.display_name)}
                    </span>
                    <span className="convo-item__text">
                      <span className="convo-item__name">
                        <span className="t-body-sm ledger__strong" dir="auto">
                          {p.display_name}
                        </span>
                        {last ? <span className="c-3 t-body-sm">{d(last, { dateStyle: "short" })}</span> : null}
                      </span>
                      <span className="c-3 t-body-sm convo-item__preview" dir="auto">
                        {first ? t("rv.conv.lastContact", { date: d(first, { dateStyle: "medium" }) }) : t("rv.conv.notContacted")}
                      </span>
                      <span className="rv-tags">
                        <Chip tint={p.status === "active" ? "success" : p.status === "paused" ? "attention" : p.status === "opted_out" ? "danger" : "neutral"}>{t(`crm.status.${p.status}` as MessageKey)}</Chip>
                        {lane ? (
                          <span className="rv-tag rv-ent" data-ent="chn">
                            {lane.channel.label}
                          </span>
                        ) : p.preferred_channel ? (
                          <span className="rv-tag rv-ent" data-ent="chn">
                            {p.preferred_channel}
                          </span>
                        ) : null}
                        {p.needs_human ? (
                          <Chip tint="attention">
                            <span data-testid={`needs-person-${p.id}`}>{t("convo.ctl.needsPerson")}</span>
                          </Chip>
                        ) : null}
                        {p.has_error ? <Chip tint="danger">{t("rv.conv.error")}</Chip> : null}
                      </span>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
          {lastPage > 1 ? (
            <nav className="row row--wrap convo-list__pages" aria-label={t("prospects.live.pages")}>
              <Button size="sm" leading={<ChevronLeft size={16} aria-hidden className="mirror" />} onClick={() => go(page - 1)} disabled={page <= 1}>
                {t("prospects.live.previous")}
              </Button>
              <span className="c-2 tabular" data-testid="convo-list-page">
                {t("convo.f.pageOf", { page: n(page), pages: n(lastPage) })}
              </span>
              <Button size="sm" trailing={<ChevronRight size={16} aria-hidden className="mirror" />} onClick={() => go(page + 1)} disabled={page >= lastPage}>
                {t("prospects.live.next")}
              </Button>
            </nav>
          ) : null}
        </>
      )}
    </Glass>
  );

  const head = (
    <>
      <ProspectsHead view="chat" total={result ? result.total : null} />
      <ListFilters value={filters} onChange={refilter} />
    </>
  );

  if (!wide && !id) {
    return (
      <div className="rv-page">
        {head}
        {list}
      </div>
    );
  }
  const name = rows.find((c) => c.id === id)?.display_name ?? null;
  return (
    <div className="rv-page">
      {wide || !id ? head : null}
      <div className="convo" data-wide={wide || undefined} data-empty={!id || undefined}>
        {wide ? list : null}
        {id ? (
          <Thread key={id} id={id} name={name} lanes={lanes} payments={payments} />
        ) : (
          <Glass level={1} className="thread-view thread-view--empty">
            <p className="c-3">{t("rv.conv.pick")}</p>
          </Glass>
        )}
      </div>
    </div>
  );
}
