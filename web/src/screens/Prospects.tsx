/** The client's prospects, read from `GET /client/v1/prospects` (read-only). The server searches,
 *  filters and paginates; everything shown is what the platform stores. The page and the filters live
 *  in the URL (`?page=2&q=amel&status=active`). */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { clientProspects, type ProspectRow } from "../client/api";
import { crmFailureOf } from "../client/crmFailure";
import { useFetched } from "../client/useFetched";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { ListFilters, filtersFromParams, isFiltered, paramsFromFilters, toApiFilters, type FilterState } from "./ListFilters";

export const PAGE_SIZE = 25;

const STATUS_TINT: Record<ProspectRow["status"], "neutral" | "success" | "attention" | "danger"> = {
  new: "neutral",
  active: "success",
  paused: "attention",
  opted_out: "danger",
  closed: "neutral",
};

export function Prospects() {
  const { t, n, d } = useI18n();
  const { logout } = useClientAuth();
  const [params, setParams] = useSearchParams();
  const requested = Number.parseInt(params.get("page") ?? "1", 10);
  const page = Number.isFinite(requested) && requested >= 1 ? requested : 1;
  const [attempt, setAttempt] = useState(0);
  const filterKey = params.toString().replace(/(^|&)page=\d+/, "");
  const filters = useMemo(() => filtersFromParams(params), [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const apiFilters = useMemo(() => toApiFilters(filters), [filters]);

  // `attempt` makes a retry a new request.
  const load = useCallback(() => clientProspects(PAGE_SIZE, (page - 1) * PAGE_SIZE, apiFilters).then((r) => ({ r, attempt })), [page, attempt, apiFilters]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  const signedOut = failure?.signedOut === true;
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);

  const result = value?.r;
  const go = (next: number) => setParams(paramsFromFilters(filters, next), { replace: true });
  const refilter = (next: FilterState) => setParams(paramsFromFilters(next), { replace: true });
  const narrowed = isFiltered(filters);
  const lastPage = result ? Math.max(1, Math.ceil(result.total / PAGE_SIZE)) : page;
  const when = (iso: string | null) => (iso ? d(iso, { dateStyle: "medium", timeStyle: "short" }) : "—");

  return (
    <div className="page page--focus prospects">
      <div className="page-head">
        <div>
          <h2 className="t-h1">{t("prospects.title")}</h2>
          {result ? (
            <p className="c-2 tabular">{narrowed ? t("prospects.f.matching", { n: n(result.total) }) : t("prospects.live.count", { n: n(result.total) })}</p>
          ) : null}
        </div>
      </div>

      <ListFilters value={filters} onChange={refilter} />

      {failure ? (
        <>
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          <div>
            <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
          </div>
        </>
      ) : loading ? (
        <p className="c-3" role="status">
          {t("prospects.live.loading")}
        </p>
      ) : result && result.total === 0 ? (
        <div className="empty" data-testid={narrowed ? "prospects-no-match" : "prospects-empty"}>
          <h3 className="t-h3">{narrowed ? t("prospects.f.noMatch") : t("prospects.live.empty")}</h3>
        </div>
      ) : result && result.prospects.length === 0 ? (
        <div className="empty" data-testid="prospects-page-empty">
          <h3 className="t-h3">{t("prospects.live.pastEnd")}</h3>
          <button type="button" className="link-btn" onClick={() => go(lastPage)}>
            {t("prospects.live.lastPage")}
          </button>
        </div>
      ) : result ? (
        <>
          <Glass className="ledger" aria-label={t("prospects.title")}>
            <div className="ledger__scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{t("prospects.col.name")}</th>
                    <th scope="col">{t("prospects.col.status")}</th>
                    <th scope="col">{t("prospects.col.stage")}</th>
                    <th scope="col">{t("prospects.col.last")}</th>
                    <th scope="col">{t("prospects.col.next")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.prospects.map((p) => (
                    <tr key={p.id} data-testid={`prospect-${p.id}`}>
                      <th scope="row">
                        <Link to={`/conversations/${encodeURIComponent(p.id)}`} className="ledger__name" aria-label={t("prospects.live.open", { name: p.display_name })}>
                          <span>
                            <span className="ledger__strong" dir="auto">
                              {p.display_name}
                            </span>
                            <span className="c-3 t-body-sm" dir="auto">
                              {p.email ?? p.phone ?? ""}
                            </span>
                          </span>
                        </Link>
                      </th>
                      <td>
                        <Chip tint={STATUS_TINT[p.status] ?? "neutral"}>{t(`crm.status.${p.status}` as MessageKey)}</Chip>
                      </td>
                      <td dir="auto">{p.stage ?? "—"}</td>
                      <td>{when(p.last_contacted_at)}</td>
                      <td>
                        {p.next_action_type ? (
                          <>
                            {t(`crm.action.${p.next_action_type}` as MessageKey)}
                            <span className="c-3 t-body-sm"> · {when(p.next_action_due_at)}</span>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Glass>
          <nav className="row row--wrap" aria-label={t("prospects.live.pages")}>
            <Button leading={<ChevronLeft size={16} aria-hidden className="mirror" />} onClick={() => go(page - 1)} disabled={page <= 1}>
              {t("prospects.live.previous")}
            </Button>
            <span className="c-2 tabular" data-testid="prospects-page">
              {t("prospects.live.page", { page: n(page), pages: n(lastPage) })}
            </span>
            <Button trailing={<ChevronRight size={16} aria-hidden className="mirror" />} onClick={() => go(page + 1)} disabled={page >= lastPage}>
              {t("prospects.live.next")}
            </Button>
          </nav>
        </>
      ) : null}
    </div>
  );
}
