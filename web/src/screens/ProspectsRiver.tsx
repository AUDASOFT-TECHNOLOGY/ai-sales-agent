/** The CRM: the client's prospects laid out as a river, one bank per stage of the sale. Clicking a
 *  prospect opens their card in a drawer. Read from `GET /client/v1/prospects` (read-only). */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { MessageSquare, Rows3, Search, Waves } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { clientConversation, clientProspects, type PaymentCase, type ProspectFilters, type ProspectRow } from "../client/api";
import { crmFailureOf } from "../client/crmFailure";
import { useFetched } from "../client/useFetched";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { Drawer } from "../ui/Overlays";
import { ProspectFiche, useCardContext } from "./ProspectFiche";
import { BANKS, bankOf, type Bank } from "./riverModel";

const PAGE = 100;

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase();
}

/** The ring of a bead: purple while the agent works on it, amber when it waits for a person. */
function attentionOf(p: ProspectRow, bank: Bank): string {
  if (bank === "stopped") return "lost";
  if (bank === "paid") return "won";
  if (p.needs_human || p.has_error) return "waiting";
  return bank === "discussing" || bank === "interested" || bank === "converted" ? "working" : "none";
}

function ProspectDrawer({ prospect, onClose }: { prospect: ProspectRow; onClose: () => void }) {
  const { t } = useI18n();
  const { lanes, payments } = useCardContext();
  const load = useCallback(() => clientConversation(prospect.id, { limit: 1, offset: 0, order: "desc" }), [prospect.id]);
  const { value: history } = useFetched(load);
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={prospect.display_name}>
      <div className="stack">
        <ProspectFiche history={history ?? undefined} lanes={lanes} payments={payments} />
        <Link to={`/conversations/${encodeURIComponent(prospect.id)}`} className="btn" data-variant="primary">
          <MessageSquare size={16} aria-hidden /> {t("rv.crm.open")}
        </Link>
      </div>
    </Drawer>
  );
}

export function ProspectsRiver() {
  const { t, n } = useI18n();
  const { logout } = useClientAuth();
  const { lanes, payments } = useCardContext();
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<ProspectRow | null>(null);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [needsYou, setNeedsYou] = useState(false);
  const [product, setProduct] = useState("");
  const filters = useMemo<ProspectFilters>(
    () => ({ sort: "recent", ...(q.trim() ? { q: q.trim() } : {}), ...(needsYou ? { needs_human: true } : {}), ...(product ? { product } : {}) }),
    [q, needsYou, product],
  );
  const load = useCallback(() => clientProspects(PAGE, 0, filters).then((r) => ({ r, attempt })), [filters, attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  const signedOut = failure?.signedOut === true;
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);

  const result = value?.r;
  const paymentOf = (id: string): PaymentCase | null => payments?.find((c) => c.prospect_id === id) ?? null;
  const narrowed = q.trim() !== "" || needsYou || product !== "";

  return (
    <div className="rv-page prospects">
      <header className="rv-head">
        <h1 className="t-h1">{t("prospects.title")}</h1>
        {result ? <p className="c-2">{t("rv.crm.total", { n: n(result.total) })}</p> : null}
      </header>

      <form
        className="rv-filters"
        role="search"
        aria-label={t("prospects.f.label")}
        onSubmit={(e) => {
          e.preventDefault();
          setQ(text);
        }}
      >
        <label className="rv-search">
          <Search size={16} aria-hidden />
          <span className="sr-only">{t("prospects.f.search")}</span>
          <input type="search" value={text} placeholder={t("rv.crm.search")} onChange={(e) => setText(e.target.value)} onBlur={() => setQ(text)} />
        </label>
        <div className="rv-seg" role="group" aria-label={t("prospects.f.human")}>
          <button type="button" className="rv-seg__btn" aria-pressed={!needsYou} onClick={() => setNeedsYou(false)}>
            {t("rv.crm.all")}
          </button>
          <button type="button" className="rv-seg__btn" aria-pressed={needsYou} onClick={() => setNeedsYou(true)}>
            {t("rv.crm.needsYou")}
          </button>
        </div>
      </form>
      <label className="rv-pillselect">
        <span className="sr-only">{t("rv.crm.products")}</span>
        <select value={product} onChange={(e) => setProduct(e.target.value)}>
          <option value="">{t("rv.crm.products")}</option>
          {(lanes?.catalog ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name ?? c.id}
            </option>
          ))}
        </select>
      </label>
      <nav className="rv-seg" aria-label={t("prospects.title")}>
        <NavLink to="/prospects" className="rv-seg__btn rv-seg__link" aria-pressed={false}>
          <Rows3 size={15} aria-hidden /> {t("rv.crm.list")}
        </NavLink>
        <NavLink to="/crm" className="rv-seg__btn rv-seg__link" aria-pressed>
          <Waves size={15} aria-hidden /> {t("rv.conv.view.river")}
        </NavLink>
      </nav>

      {failure ? (
        <>
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          <div>
            <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
          </div>
        </>
      ) : loading && !result ? (
        <p className="c-3" role="status">
          {t("prospects.live.loading")}
        </p>
      ) : result ? (
        <>
          {result.total === 0 ? (
            <p className="c-3" data-testid={narrowed ? "prospects-no-match" : "prospects-empty"}>
              {narrowed ? t("prospects.f.noMatch") : t("prospects.live.empty")}
            </p>
          ) : null}
          <Glass className="river-panel">
            <div className="river" role="group" aria-label={t("rv.crm.river")} style={{ gridTemplateColumns: `repeat(${BANKS.length}, minmax(130px, 1fr))` }}>
              <div className="river__current" aria-hidden />
              {BANKS.map((bank) => {
                const here = result.prospects.filter((p) => bankOf(p, paymentOf(p.id)) === bank);
                const label = t(`rv.crm.bank.${bank}` as MessageKey);
                return (
                  <section key={bank} className="bank" data-milestone={bank === "paid" ? "won" : bank === "stopped" ? "lost" : undefined} aria-label={`${label} · ${here.length}`}>
                    <header className="bank__head">
                      <h3 className="t-label c-2">{label}</h3>
                      <span className="bank__count tabular c-3 t-body-sm">{n(here.length)}</span>
                    </header>
                    <ul className="bank__beads">
                      {here.map((p) => (
                        <li key={p.id}>
                          <button
                            type="button"
                            className="bead-btn"
                            data-selected={selected?.id === p.id || undefined}
                            title={p.display_name}
                            aria-label={`${p.display_name}, ${label}`}
                            aria-pressed={selected?.id === p.id}
                            onClick={() => setSelected(p)}
                          >
                            <span className="bead" data-attention={attentionOf(p, bank)} data-size="48" aria-hidden>
                              <span className="bead__disc">{initialsOf(p.display_name)}</span>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          </Glass>
          {result.total > result.prospects.length ? <p className="c-3 t-body-sm">{t("rv.crm.partial", { shown: n(result.prospects.length), total: n(result.total) })}</p> : null}
        </>
      ) : null}
      {selected ? <ProspectDrawer prospect={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
