/** Conversations, read from the platform (`GET /client/v1/prospects` for who, then
 *  `GET /client/v1/conversations/{prospect_id}` for the messages). Read-only: nothing here sends,
 *  edits or takes over a conversation. The server has no list of conversations, so the people on the
 *  left are the first prospects it returns, and a prospect that is not one of them still opens by
 *  its link. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { clientConversation, clientProspects, type ProspectRow } from "../client/api";
import { crmFailureOf, type CrmFailure } from "../client/crmFailure";
import { useFetched } from "../client/useFetched";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { useMedia } from "../ui/useMedia";
import { cx } from "../ui/cx";

const LIST_SIZE = 100; // the largest page the server gives

function useSignOutOn(failure: CrmFailure | null) {
  const { logout } = useClientAuth();
  const signedOut = failure?.signedOut === true;
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);
}

function Thread({ id, name }: { id: string; name: string | null }) {
  const { t, d } = useI18n();
  const navigate = useNavigate();
  const wide = useMedia("(min-width: 1024px)");
  const [attempt, setAttempt] = useState(0);
  const load = useCallback(() => clientConversation(id).then((c) => ({ c, attempt })), [id, attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  useSignOutOn(failure);
  const history = value?.c;

  return (
    <Glass level={1} className="thread-view" aria-label={name ?? id}>
      <header className="thread-view__head">
        {!wide ? (
          <button type="button" className="icon-btn" aria-label={t("convo.title")} onClick={() => navigate("/conversations")}>
            <ChevronLeft size={22} className="mirror" />
          </button>
        ) : null}
        <div className="thread-view__who">
          <h2 className="t-h3" dir="auto">
            {name ?? id}
          </h2>
        </div>
      </header>

      {failure ? (
        <div className="stack">
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          {failure.key !== "crm.error.notFound" ? <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button> : null}
        </div>
      ) : loading ? (
        <p className="c-3" role="status">
          {t("convo.live.loading")}
        </p>
      ) : history && history.messages.length === 0 ? (
        <p className="c-3" data-testid="thread-empty">
          {t("convo.live.empty")}
        </p>
      ) : history ? (
        <ol className="msgs" aria-label={t("convo.messagesLabel")}>
          {history.messages.map((m) => {
            const mine = m.direction === "outbound";
            return (
              <li key={m.id} className="msg" data-author={mine ? "agent" : "prospect"} data-mine={mine || undefined} data-testid={`message-${m.id}`}>
                <div className="msg__col">
                  <p className="msg__who t-label c-3">{t(mine ? "convo.live.sent" : "convo.live.received")}</p>
                  <div className="msg__bubble" dir="auto">
                    {m.text}
                  </div>
                  <p className="c-3 t-body-sm">
                    {d(m.timestamp, { dateStyle: "medium", timeStyle: "short" })} · {m.channel}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </Glass>
  );
}

export function Conversations() {
  const { t } = useI18n();
  const { id } = useParams();
  const wide = useMedia("(min-width: 1024px)");
  const [attempt, setAttempt] = useState(0);
  const load = useCallback(() => clientProspects(LIST_SIZE, 0).then((p) => ({ p, attempt })), [attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  useSignOutOn(failure);
  const page = value?.p;
  const rows: ProspectRow[] = page?.prospects ?? [];

  const list = (
    <Glass className="convo-list" aria-label={t("convo.title")}>
      <div className="convo-list__head">
        <h2 className="t-h2">{t("convo.title")}</h2>
      </div>
      {failure ? (
        <div className="stack">
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
        </div>
      ) : loading ? (
        <p className="c-3" role="status">
          {t("prospects.live.loading")}
        </p>
      ) : rows.length === 0 ? (
        <p className="c-3" data-testid="convo-list-empty">
          {t("prospects.live.empty")}
        </p>
      ) : (
        <ul>
          {rows.map((p) => (
            <li key={p.id}>
              <Link to={`/conversations/${encodeURIComponent(p.id)}`} className={cx("convo-item", p.id === id && "is-active")} aria-current={p.id === id ? "page" : undefined}>
                <span className="convo-item__text">
                  <span className="t-body-sm ledger__strong" dir="auto">
                    {p.display_name}
                  </span>
                  <span className="c-3 t-body-sm convo-item__preview">{t(`crm.status.${p.status}` as MessageKey)}</span>
                </span>
              </Link>
            </li>
          ))}
          {page && page.total > rows.length ? (
            <li className="c-3 t-body-sm" data-testid="convo-list-more">
              <Link to="/prospects">{t("convo.live.more", { shown: rows.length, total: page.total })}</Link>
            </li>
          ) : null}
        </ul>
      )}
    </Glass>
  );

  if (!wide && !id) return <div className="page page--narrow">{list}</div>;
  const name = rows.find((p) => p.id === id)?.display_name ?? null;
  return (
    <div className="page convo" data-wide={wide || undefined}>
      {wide ? list : null}
      {id ? (
        <Thread key={id} id={id} name={name} />
      ) : (
        <Glass level={1} className="thread-view">
          <p className="c-3">{t("convo.live.pick")}</p>
        </Glass>
      )}
    </div>
  );
}
